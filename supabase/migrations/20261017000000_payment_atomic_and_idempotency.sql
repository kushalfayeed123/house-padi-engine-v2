-- ============================================================================
-- Payment atomicity + idempotency
-- ============================================================================
-- Adds the Postgres functions the application has always called but which were
-- never version-controlled (`process_payment_atomic`, `finalize_payment_split`),
-- plus the supporting tables/indexes that make them safe under webhook retries.
--
-- All statements are idempotent so this migration can be re-run.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. Raw webhook event dedupe (gateway event ids are unique per provider)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.payment_webhook_events (
    event_id TEXT PRIMARY KEY,
    gateway_ref TEXT,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------------------------
-- 2. Notifications (referenced by the application flow, missing from schema)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    type TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS notifications_user_id_idx
    ON public.notifications (user_id, created_at DESC);

-- ----------------------------------------------------------------------------
-- 3. Transaction status vocabulary (kept permissive for legacy rows)
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    ALTER TABLE public.transactions
        ADD CONSTRAINT transactions_status_check
        CHECK (status IN (
            'initialized', 'pending', 'processing', 'succeeded',
            'failed', 'refunded', 'completed'
        ));
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN check_violation THEN
        RAISE NOTICE 'Existing transaction rows violate the status vocabulary; constraint skipped.';
END $$;

CREATE INDEX IF NOT EXISTS transactions_lease_id_idx
    ON public.transactions (lease_id);
CREATE INDEX IF NOT EXISTS transactions_payer_id_idx
    ON public.transactions (payer_id, created_at DESC);
CREATE INDEX IF NOT EXISTS transactions_status_idx
    ON public.transactions (status);

-- ----------------------------------------------------------------------------
-- 4. process_payment_atomic
-- ----------------------------------------------------------------------------
-- Single transaction that:
--   * returns the existing transaction if the gateway reference was already
--     processed (idempotent under webhook retries),
--   * inserts the transaction row,
--   * writes the ledger entries (renter debit, landlord credit, platform fee),
--   * credits the landlord wallet,
--   * activates the lease / completes the application on first payment.
-- Any error rolls the whole thing back.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.process_payment_atomic(
    p_lease_id UUID,
    p_payer_id UUID,
    p_amount NUMERIC,
    p_platform_fee NUMERIC,
    p_landlord_amount NUMERIC,
    p_landlord_id UUID,
    p_storage_path TEXT,
    p_renter_name TEXT,
    p_is_initial_activation BOOLEAN,
    p_currency TEXT,
    p_gateway_ref TEXT,
    p_event_id TEXT DEFAULT NULL
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_txn_id UUID;
    v_existing_id UUID;
    v_status TEXT;
    v_lease_renter UUID;
BEGIN
    IF p_gateway_ref IS NULL OR p_gateway_ref = '' THEN
        RAISE EXCEPTION 'gateway_ref is required';
    END IF;

    -- Idempotency gate: have we already *settled* this gateway reference?
    -- A row created by /payments/initialize is not yet settled, so it is
    -- transitioned in place below instead of being treated as a replay.
    SELECT id, status INTO v_existing_id, v_status
    FROM public.transactions
    WHERE payment_gateway_ref = p_gateway_ref
    LIMIT 1;

    IF v_existing_id IS NOT NULL AND v_status IN ('succeeded', 'completed') THEN
        RETURN jsonb_build_object(
            'transaction_id', v_existing_id,
            'replayed', TRUE,
            'status', v_status
        );
    END IF;

    -- Defensive check: the payer must be the renter on the lease.
    SELECT renter_id INTO v_lease_renter FROM public.leases WHERE id = p_lease_id;
    IF v_lease_renter IS NULL THEN
        RAISE EXCEPTION 'lease % not found', p_lease_id;
    END IF;
    IF v_lease_renter <> p_payer_id THEN
        RAISE EXCEPTION 'payer % is not the renter on lease %', p_payer_id, p_lease_id;
    END IF;

    IF v_existing_id IS NOT NULL THEN
        -- Settle the transaction row created at checkout initialisation.
        UPDATE public.transactions
        SET status = 'succeeded',
            amount = p_amount,
            platform_fee = p_platform_fee,
            currency = COALESCE(p_currency, currency)
        WHERE id = v_existing_id
        RETURNING id INTO v_txn_id;
    ELSE
        INSERT INTO public.transactions (
            lease_id, payer_id, amount, platform_fee, type, currency,
            payment_gateway_ref, status
        ) VALUES (
            p_lease_id, p_payer_id, p_amount, p_platform_fee, 'rent_payment',
            COALESCE(p_currency, 'NGN'), p_gateway_ref, 'succeeded'
        )
        RETURNING id INTO v_txn_id;
    END IF;

    -- Ledger: renter debit, landlord credit, platform fee credit.
    INSERT INTO public.ledger_entries (amount, type, category, "walletId", "referenceId")
    VALUES (p_amount, 'debit', 'rent', p_payer_id::TEXT, v_txn_id::TEXT);

    INSERT INTO public.ledger_entries (amount, type, category, "walletId", "referenceId")
    VALUES (p_landlord_amount, 'credit', 'rent', p_landlord_id::TEXT, v_txn_id::TEXT);

    IF p_platform_fee > 0 THEN
        INSERT INTO public.ledger_entries (amount, type, category, "walletId", "referenceId")
        VALUES (p_platform_fee, 'credit', 'fee', 'platform', v_txn_id::TEXT);
    END IF;

    -- Credit the landlord wallet (upsert keeps this safe for first payments).
    INSERT INTO public.wallets ("userId", balance, "updatedAt")
    VALUES (p_landlord_id, p_landlord_amount, NOW())
    ON CONFLICT ("userId")
    DO UPDATE SET balance = public.wallets.balance + EXCLUDED.balance,
                  "updatedAt" = NOW();

    IF p_is_initial_activation THEN
        UPDATE public.leases
        SET is_active = TRUE,
            contract_url = COALESCE(p_storage_path, contract_url)
        WHERE id = p_lease_id;

        UPDATE public.applications
        SET status = 'completed'
        WHERE lease_id = p_lease_id::TEXT;
    END IF;

    IF p_event_id IS NOT NULL THEN
        INSERT INTO public.payment_webhook_events (event_id, gateway_ref, processed_at)
        VALUES (p_event_id, p_gateway_ref, NOW())
        ON CONFLICT (event_id) DO NOTHING;
    END IF;

    RETURN jsonb_build_object(
        'transaction_id', v_txn_id,
        'replayed', FALSE,
        'status', 'succeeded'
    );
EXCEPTION
    WHEN unique_violation THEN
        -- Concurrent webhook for the same reference won the race.
        SELECT id, status INTO v_existing_id, v_status
        FROM public.transactions
        WHERE payment_gateway_ref = p_gateway_ref
        LIMIT 1;
        IF v_existing_id IS NULL THEN
            RAISE;
        END IF;
        RETURN jsonb_build_object(
            'transaction_id', v_existing_id,
            'replayed', TRUE,
            'status', v_status
        );
END;
$$;

GRANT EXECUTE ON FUNCTION public.process_payment_atomic(
    UUID, UUID, NUMERIC, NUMERIC, NUMERIC, UUID, TEXT, TEXT, BOOLEAN, TEXT, TEXT, TEXT
) TO service_role, authenticated;

-- ----------------------------------------------------------------------------
-- 5. finalize_payment_split
-- ----------------------------------------------------------------------------
-- Admin fee distribution. Idempotent: a transaction already marked 'completed'
-- is returned untouched, so double-clicks cannot double-credit a wallet.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.finalize_payment_split(
    p_transaction_id UUID,
    p_landlord_percentage NUMERIC,
    p_actor_id UUID
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_txn public.transactions;
    v_landlord_id UUID;
    v_platform_fee NUMERIC;
    v_landlord_amount NUMERIC;
BEGIN
    SELECT * INTO v_txn
    FROM public.transactions
    WHERE id = p_transaction_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'transaction % not found', p_transaction_id;
    END IF;

    IF v_txn.status IN ('completed') THEN
        RETURN jsonb_build_object(
            'transaction_id', v_txn.id,
            'status', v_txn.status,
            'replayed', TRUE
        );
    END IF;

    IF p_landlord_percentage < 0 OR p_landlord_percentage > 100 THEN
        RAISE EXCEPTION 'landlord_percentage must be between 0 and 100';
    END IF;

    SELECT owner_id INTO v_landlord_id FROM public.leases WHERE id = v_txn.lease_id;

    v_platform_fee := ROUND(v_txn.amount * (1 - p_landlord_percentage / 100.0), 2);
    v_landlord_amount := v_txn.amount - v_platform_fee;

    INSERT INTO public.wallets ("userId", balance, "updatedAt")
    VALUES (v_landlord_id, v_landlord_amount, NOW())
    ON CONFLICT ("userId")
    DO UPDATE SET balance = public.wallets.balance + EXCLUDED.balance,
                  "updatedAt" = NOW();

    INSERT INTO public.ledger_entries (amount, type, category, "walletId", "referenceId")
    VALUES (v_landlord_amount, 'credit', 'rent', v_landlord_id::TEXT, v_txn.id::TEXT);

    IF v_platform_fee > 0 THEN
        INSERT INTO public.ledger_entries (amount, type, category, "walletId", "referenceId")
        VALUES (v_platform_fee, 'credit', 'fee', 'platform', v_txn.id::TEXT);
    END IF;

    UPDATE public.transactions SET status = 'completed' WHERE id = p_transaction_id;

    RETURN jsonb_build_object(
        'transaction_id', p_transaction_id,
        'landlord_amount', v_landlord_amount,
        'platform_fee', v_platform_fee,
        'status', 'completed',
        'replayed', FALSE
    );
END;
$$;

GRANT EXECUTE ON FUNCTION public.finalize_payment_split(UUID, NUMERIC, UUID)
    TO service_role, authenticated;
