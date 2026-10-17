# HousePadi Engine v2 — Production Readiness Plan

Status: **Not production ready.** This document inventories the codebase, lists
hard blockers by severity, gives a target architecture for the money-critical
payment flow (backend + frontend contract), and lays out a phased roadmap with
acceptance criteria. It is written against commit `28fc174`.

---

## 1. What this system is

A FastAPI "agent gateway" for a Nigerian property-rental marketplace.

| Layer | Location | Notes |
|---|---|---|
| HTTP API | `app/routes/*.py` | 10 routers: auth, property, chat, profile, tour, application, lease, payment, landlord, renter |
| Domain logic | `app/tools/*.py` | LangChain `@tool` workers; each route calls a worker via `BaseTool.ainvoke` |
| Agents | `app/core/lightweight_agent.py`, `app/core/agent_engine.py` | Groq routing + tool calling; `agent_engine.py` (deepagents) is the optional `AGENT_BACKEND=multi_agent` path |
| Services | `app/services/*.py` | cache, chat, embeddings, PDF, lease text, AI enrichment, cron |
| Data | `supabase/migrations/*.sql`, `app/core/database.py` | Supabase Postgres (service-role client) + Storage |
| Auth | `app/routes/auth_routes.py`, `app/core/dependecies.py` | Supabase GoTrue; per-request `get_optional_user_context` |

The **frontend is not in this repo**; it is a separate SPA that talks to these
JSON endpoints and renders agent-driven redirects (see §5). That is why the
payment flow is described below as a contract, not just a backend change.

---

## 2. Hard blockers (must fix before any real user touches this)

### B1 — `process_payment_atomic` RPC does not exist. **Payments cannot succeed at all.**
`app/tools/payment_ops.py:139` calls `supabase_client.rpc("process_payment_atomic", ...)`
but no migration in the repo defines it. Every payment attempt falls through to
`"Payment processing failure (Rolled back)"`. The same is true of
`match_properties` (property search) and the `notifications` table (application
flow). **The migration folder is not the real schema** — `supabase/seed.sql`
references columns that do not exist in the migration (`properties.latitude/longitude`,
`leases.end_date/currency/status`, `kyc_verifications.verification_date/verified_by`,
`chat_threads.subject/status`). Fix: `pg_dump --schema-only` the live DB into a
migration, then diff.

### B2 — Webhook signature verification is commented out. **Anyone can activate a lease for free.**
`app/routes/payment_routes.py:43-53` is fully commented out, so a single
unauthenticated `curl -X POST /payments/webhook` with
`{"event":"charge.success","data":{"amount":1,"metadata":{"lease_id":"...","user_id":"..."}}}`
runs `process_payment_worker`. It also trusts the client's `amount` and
`payer_id` rather than the lease record, and reads the amount with a fragile
`/100 if "paystack" in str(request.url)` heuristic. This is the single most
important fix in the codebase.

### B3 — No idempotency anywhere in the payment path.
`transactions.payment_gateway_ref` is `NOT NULL UNIQUE`, but `process_payment_worker`
never passes a gateway reference to the RPC. Gateways retry webhooks (Paystack
retries for 72h). Result today: either the unique constraint throws (500) or,
once B1 is fixed naively, duplicate ledger rows and double lease activation.
Idempotency must be keyed on the provider event/transaction reference.

### B4 — `split_payment_worker` is broken and non-atomic.
`app/tools/payment_ops.py:255-268` does
`.update({"balance": f"balance + {landlord_amount}"})` — PostgREST cannot execute
SQL expressions, so this writes a **string** (or fails) and never credits a
wallet. It also silently swallows the failure (`logger.warning`) and then marks
the transaction `completed`, so money is "settled" that was never moved. There
are no ledger entries and no re-entrancy guard.

### B5 — Secrets/config crash the app or are missing.
`app/core/config.py` makes `SUPABASE_*`, `BACKEND_JWT_SECRET` **and
`OPENAI_API_KEY`** all required, so `settings = Settings()` at import time raises
`ValidationError` if any is absent — the app won't boot, and the process dies
with a stack trace instead of a clear startup error. `PAYMENT_WEBHOOK_SECRET` is
read via `getattr(settings, ..., "")` but never declared, so the fallback is an
empty string. `BACKEND_JWT_SECRET` is declared but never used (dead config).

### B6 — CORS is invalid and insecure.
`app/main.py:70-76`: `allow_origins=["*"]` **with** `allow_credentials=True`.
Browsers reject this combination, and if it were honoured it would allow any
site to make credentialed calls. Must be an explicit allow-list from env.

### B7 — `get_optional_user_context` silently downgrades every error to "guest".
Any DB hiccup, expired token, or profile-tool exception returns `None`, which
routes translate into 401/403. Real outages look like auth failures and are
invisible in monitoring. Auth failures and infrastructure failures must be
distinguishable.

### B8 — Errors leak internals and use the wrong status codes.
`/payments/webhook` returns `detail=f"Webhook processing error: {str(e)}"` (500).
`/api/chat` returns `str(e)`. Tool workers encode errors as human strings
(`"Payment processing failure (Rolled back): ..."`) that routes then sniff with
`if "Security Guardrail" in res_str` — control flow by substring matching is
fragile and will misclassify messages. There is no global exception handler and
no request id.

### B9 — Property creation writes an invalid enum value.
`app/tools/property_ops.py:96` and `property_ai_service.py:121` write
`status: "available"`, but the column type is
`properties_status_enum ('draft','published','rented','delisted')`. Inserts fail
(or, if the live column is actually TEXT, search filters and the enum disagree).
One canonical status vocabulary is required across DB, tools and frontend.

### B10 — `app/core/agent_engine.py` imports a module that doesn't exist.
`from app.tools.lease_ops import ...` but the file is `application_lease_ops.py`.
It is only imported lazily when `AGENT_BACKEND=multi_agent`, so it is dead today
but will 500 the chat endpoint the moment that backend is selected. No test
covers it.

### B11 — No tests, no CI, no `.env.example`, no lockfile-enforced runtime.
`QUICK_START.md` tells users to `pip install -r requirements.txt`; that file does
not exist (deps live in `pyproject.toml` + `uv.lock`). There is no test suite, no
`pytest` config, no CI workflow, no `README` content, no `.env.example`. The
`uv.lock` is 872 KB and pins a heavy ML stack (transformers/onnxruntime/
sentence-transformers) that the runtime no longer uses (embeddings moved to
OpenRouter in `vector_service.py`).

### B12 — Rate limiting, upload validation and webhook replay are absent.
No rate limiting on `/api/auth/login`, `/api/chat`, or the webhook. File uploads
(`/api/property/upload-image`) validate nothing (no size, no MIME sniff, no
magic-byte check). The internal cron endpoint falls back to a hard-coded secret
`"house-padi-super-secret"` when `INTERNAL_CRON_SECRET` is unset.

---

## 3. High-priority correctness & integrity issues

| # | Issue | Where |
|---|---|---|
| H1 | KYC is claimed as a payment precondition (`ARCHITECTURE.md`) but never checked in `process_payment_worker`. | `app/tools/payment_ops.py` |
| H2 | `amount` is fully client-controlled; nothing compares it to `leases.rent`. Partial/underpayment activates a lease. | `payment_routes.py`, `payment_ops.py` |
| H3 | PDF is generated and uploaded to Storage *before* the DB transaction; a rollback leaves an orphan file, and a re-run regenerates it. | `payment_ops.py:92-122` |
| H4 | Currency is hard-coded inconsistently: RPC gets `"NGN"`, wallet returns `"USD"`, schema default is `"USD"`, seed uses `"NGN"`. | `payment_ops.py:135,198` |
| H5 | `get_wallet_balance_worker` uses `.single()` on `wallets`; if no wallet row exists it raises and returns a plain error string that the route wraps as `{"status":"success","raw":...}` — a 200 for a failure. | `payment_ops.py:164-206`, `payment_routes.py:144-147` |
| H6 | `split_payment_worker` lets an admin pass any `landlord_percentage` with no upper bound on the platform's exposure and no ledger audit trail. | `payment_ops.py:209-286` |
| H7 | Application state machine is inconsistent: submit writes `pending_landlord_approval`, payment requires `approved_pending_payment`, dashboards filter `pending_landlord_approval`; no enum/constraint in the DB. | `application_lease_ops.py`, `landlord_routes.py`, `renter_routes.py` |
| H8 | `get_optional_user_context` calls `supabase.auth.get_user` **and** a profile tool on every request → 2 network round-trips per authenticated call. | `app/core/dependecies.py` |
| H9 | Cache is in-process memory by default; with >1 instance, invalidation and rate-limit reduction stop working. `ARCHITECTURE.md` acknowledges this. | `app/services/cache_service.py` |
| H10 | `/api/property/{id}` and `/api/property/featured` `json.loads()` tool output with no guard — a tool error string raises an unhandled 500 with a traceback. | `property_routes.py:83,90` |
| H11 | `landlord_routes.py` / `renter_routes.py` return `config.get("id") or config.get("configurable",{})...` — the `or` binds after `and`, so a falsy `id` silently falls through. Also they read `wallet.balance` with `.single()` unguarded. | `landlord_routes.py`, `renter_routes.py` |
| H12 | `notifications` inserts in `application_lease_ops.py` are fire-and-forget; a failure there aborts the whole application flow after the rows are written (no transaction). | `application_lease_ops.py` |
| H13 | Agent/LLM failures surface as raw provider errors to the client; no circuit breaker, no timeout budget on tool calls. | `chat_routes.py`, `lightweight_agent.py` |

---

## 4. Payment flow — target architecture (the core of this plan)

### 4.1 Principles

1. **The server is the source of truth for price.** The client never sends an
   amount that is trusted; the amount is read from `leases.rent` (or the
   approved application) inside the payment service.
2. **Every state-changing money operation is idempotent**, keyed on a
   deterministic reference (`lease_id + purpose + provider_txn_ref`).
3. **The database is the ledger.** A single Postgres function performs
   transaction insert + ledger entries + wallet credit + lease activation
   atomically. Nothing is "completed" unless the ledger says so.
4. **Webhooks are verified, then reconciled.** Signature is checked before any
   parse; the amount is reconciled against the DB, not trusted; unknown/duplicate
   events are acknowledged and ignored.
5. **The frontend gets a canonical, pollable status object** so it never has to
   guess from redirect params.

### 4.2 Target state machine

```
application.status:  submitted -> pending_landlord_approval -> approved_pending_payment -> completed
                                                           \-> rejected

transaction.status:  initialized -> pending -> processing -> succeeded
                                                          \-> failed
                                                          \-> refunded

lease.is_active:     false -> true   (only on the atomic transition of the
                                      first succeeded transaction)
```

Both vocabularies are enforced by a Postgres `CHECK`/enum and mirrored by a
single Python `StrEnum` shared by tools, routes and the API contract.

### 4.3 Target endpoints (backend)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/payments/initialize` | renter (owner of the lease) | Server computes amount from `leases.rent`, creates an `initialized` transaction with a unique `payment_gateway_ref`, returns the gateway checkout payload. Idempotent per lease+open transaction. |
| `POST` | `/payments/webhook` | signature | Verify HMAC, dedupe by provider event id, reconcile amount, call the atomic RPC. Always `200` for verified events (incl. duplicates); `401` for bad signature. |
| `GET` | `/payments/verify/{reference}` | renter (owner) | Frontend callback target after gateway redirect. Reconciles with the gateway if still `pending`, returns the canonical status object. Idempotent. |
| `GET` | `/payments/transactions/{id}` | payer/owner/admin | Pollable status for the frontend spinner. |
| `GET` | `/payments/wallet` | authenticated | Typed balance + recent ledger activity. |
| `POST` | `/payments/{transaction_id}/split` | admin | Idempotent finalization via RPC; writes ledger entries. |
| `POST` | `/payments/refund` | admin | Out of scope for phase 1; reserved. |

### 4.4 Atomic RPC (new migration)

`process_payment_atomic(p_lease_id, p_payer_id, p_amount, p_platform_fee,
p_landlord_amount, p_landlord_id, p_storage_path, p_renter_name,
p_is_initial_activation, p_currency, p_gateway_ref, p_event_id)`:

- `INSERT ... ON CONFLICT (payment_gateway_ref) DO NOTHING RETURNING id` — the
  idempotency gate. If it conflicts, return the existing transaction unchanged
  (`replayed: true`).
- Insert two `ledger_entries` (renter debit / landlord credit) referencing the
  transaction.
- Upsert the landlord `wallets` row (`balance = balance + landlord_amount`).
- If `p_is_initial_activation`: set `leases.is_active = true`,
  `leases.contract_url = p_storage_path`, mark the application `completed`,
  insert a notification.
- Runs in one implicit transaction; any error rolls all of it back.

### 4.5 Frontend contract (the "frontend aspect")

The SPA must never compute money or infer success from a redirect query string.
The contract:

**Initialize**
```jsonc
// POST /payments/initialize  { "lease_id": "..." }
200 {
  "status": "success",
  "transaction_id": "8f...",
  "reference": "HP-<lease>-<uuid>",
  "amount": 1500000,            // number, major units
  "amount_minor": 150000000,    // integer, for gateway SDKs
  "currency": "NGN",
  "gateway": "paystack",
  "checkout": { "access_code": "...", "authorization_url": "https://checkout.paystack.com/..." },
  "poll_url": "/payments/transactions/8f..."
}
409 { "status": "error", "code": "payment_already_in_progress", "transaction_id": "8f..." }
```

**Verify / poll** (identical shape, so the same React component renders both)
```jsonc
200 {
  "status": "success",                 // HTTP-level: request understood
  "transaction": {
    "id": "8f...",
    "reference": "HP-...",
    "status": "succeeded",             // initialized|pending|processing|succeeded|failed|refunded
    "amount": 1500000,
    "currency": "NGN",
    "lease_id": "...",
    "lease_activated": true,
    "receipt_url": null,
    "created_at": "2026-10-17T10:00:00Z"
  }
}
```

**Webhook ack** — `{"status":"success","replayed":false}` / `{"status":"ignored"}`.

Frontend rules:
- Render the amount from the server response, never from local state.
- After gateway redirect, call `GET /payments/verify/{reference}`; show a spinner
  while `status in {initialized,pending,processing}` and poll `poll_url` with
  backoff (e.g. 1s,2s,3s,5s, cap 30s) until terminal.
- Treat only `succeeded` as success; `failed` shows a retry that re-calls
  `initialize` (which is idempotent and returns the same open transaction).
- Never trust `?reference=`/`?trxref=` alone — always verify server-side.
- All money fields are numbers in **major units**; the `*_minor` field is
  integer for gateway SDKs. The frontend must not divide by 100 itself.

### 4.6 Failure & recovery semantics

| Scenario | Behaviour |
|---|---|
| Gateway retries the same webhook | `ON CONFLICT` returns existing txn, `replayed:true`, HTTP 200. |
| Webhook arrives before `initialize` persisted | 200 `ignored`; the polling `verify` call reconciles and creates the txn from the gateway record. |
| Webhook amount < `leases.rent` | Reject with `amount_mismatch`; mark txn `failed`; notify; no activation. |
| Bad/absent signature | 401, no parse, alert. |
| DB down during webhook | 500 so the gateway retries (this is the one case 500 is correct). |
| Process crash mid-webhook | The RPC is a single DB transaction; nothing partial persists. |
| Duplicate `initialize` while one is open | 409 with the existing `transaction_id`. |

---

## 5. Frontend integration beyond payments

- **Agent redirects.** The chat API returns `type:"redirect"` with a
  `redirect_url` (`INTENT_UI_MAP` in `ui_registry.py`) and the frontend must
  route on it. Gating in `chat_routes._gate_redirect` currently checks the
  literal substrings `"dashboard"`/`"account"`/`"landlord"` in the URL — fragile.
  Replace with an explicit `required_role`/`requires_auth` field on each intent
  entry so the frontend and backend share one policy table.
- **Typed responses.** `app/models/chat.py` (`ChatResponse`) is good; the payment
  endpoints should return Pydantic models the same way (see §4.5), and the SPA
  should be generated from the OpenAPI schema at `/api/v1/docs` to keep types in
  lockstep.
- **Error envelope.** Standardise on
  `{"status":"error","code":"<machine_code>","message":"<human>","request_id":"..."}`
  so the frontend can branch on `code`, not on prose.
- **Idempotency keys.** Mutating endpoints (`apply`, `approve`, `initialize`)
  should accept an `Idempotency-Key` header; the SPA sends a UUID per user
  action and retries safely.

---

## 6. Roadmap

### Phase 0 — Make it runnable & observable (0.5 day)
- `app/core/config.py`: split required vs optional; add `PAYMENT_WEBHOOK_SECRET`,
  `PAYMENT_PROVIDER`, `PLATFORM_FEE_PERCENT`, `DEFAULT_CURRENCY`,
  `CORS_ORIGINS`, `INTERNAL_CRON_SECRET`; fail fast with one clear message.
- Add `.env.example`, a real `README.md`, `requirements.txt` shim (or fix
  `QUICK_START.md` to use `uv sync`).
- `app/main.py`: CORS from env; global exception handler; request-id middleware;
  `/healthz` (liveness) + `/readyz` (checks Supabase reachability).
- Fix `app/core/agent_engine.py` import (B10).

### Phase 1 — Payments (the critical path) (2–3 days)
- New migration: `process_payment_atomic`, `finalize_payment_split`,
  `notifications` table, unique index on `payment_gateway_ref`, status enums
  (B1, B3, B4).
- `app/services/payment_gateway.py`: provider abstraction (Paystack first) with
  `verify_signature`, `verify_transaction`, `init_transaction`; a `MockGateway`
  for dev/test.
- `app/models/payment.py`: request/response models from §4.5.
- Rewrite `app/routes/payment_routes.py` and the payment workers in
  `app/tools/payment_ops.py` per §4.
- Enforce KYC + amount authority in the worker (H1, H2).
- Tests (see §7) covering signature, replay, amount mismatch, idempotent
  initialize, split auth.

### Phase 2 — Data integrity (2 days)
- `pg_dump` the live schema; reconcile migration/seed drift; add FKs,
  `CHECK`/enums, and `updated_at` triggers.
- One canonical status vocabulary (B9, H7) shared by DB + Python + frontend.
- Wrap multi-write flows (application submit/approve) in RPCs so a notification
  failure cannot leave a half-written application (H12).
- Fix currency to a single source of truth (H4).

### Phase 3 — Security & hardening (2 days)
- Rate limiting (`slowapi`/Upstash) on auth, chat and webhook (B12).
- Upload validation: size cap, MIME + magic-byte sniff, per-user quota (B12).
- Require `INTERNAL_CRON_SECRET` with no default (B12).
- Separate auth-failure from infra-failure in `get_optional_user_context` (B7).
- Error envelope everywhere; stop returning `str(e)` (B8).
- Secrets from a manager (not `.env`) in prod; rotate the service-role key.

### Phase 4 — Reliability & performance (2 days)
- Redis (Upstash) cache + rate limit for multi-instance (H9).
- Cache/consolidate the two auth round-trips per request (H8).
- Timeouts + retries + circuit breaker on Groq/OpenRouter/Ollama and on Supabase
  (H13); tool-call timeout budget.
- Structured JSON logging with request id, and Sentry/OTel traces.
- Move PDF generation inside the transaction boundary (generate, upload, then
  commit; delete orphan on rollback) (H3).

### Phase 5 — Delivery (1 day)
- CI: lint (ruff), type check (mypy), tests, `docker build`, `pip-audit`/`trivy`.
- Migrations run in CI against a throwaway Postgres; production uses a migration
  runner (Supabase CLI / Flyway), never manual SQL.
- Staging + production environments, blue/green or rolling deploy, DB backups and
  a tested restore, documented rollback.
- Load test the payment + chat paths; define SLOs (p95 API < 400ms excluding
  LLM; payment success ≥ 99.5%; webhook processing < 2s).

---

## 7. Acceptance criteria (how we know each blocker is fixed)

| ID | Test |
|---|---|
| B1 | `payment` integration test: a valid webhook results in `transactions` row + 2 `ledger_entries` + `wallets.balance` credit + `leases.is_active=true`, all present after one call. |
| B2 | `POST /payments/webhook` with no/bad signature → 401 and **zero** DB writes; with valid signature → processed. |
| B3 | Same webhook posted twice → one transaction row, second response `replayed:true`, balance credited once. |
| B4 | Split credits the wallet with a numeric delta and writes ledger entries; calling it twice does not double-credit. |
| B5 | App boots with only the required vars; missing one → single clear startup error, no stack trace; webhook secret is non-empty or webhook refuses to start. |
| B6 | Preflight from a disallowed origin is rejected; from an allowed origin is accepted; `*`+credentials is impossible. |
| B7 | Supabase outage → 503, expired token → 401, distinguishable in logs. |
| B8 | Any unhandled error → `{"status":"error","code":...,"request_id":...}`, no `str(e)` in the body. |
| B9 | Creating a property persists a value from the canonical status set and search finds it. |
| H2 | Webhook claiming a lower amount than `leases.rent` does **not** activate the lease. |
| H1 | Unverified renter cannot complete a payment. |
| H3 | A forced RPC failure leaves no orphan object in Storage. |
| H5 | Missing wallet row returns a real `0` balance with `status:"success"`, not a 200-wrapped error string. |
| §4.5 | OpenAPI at `/api/v1/docs` matches the documented payment schemas; a contract test asserts the JSON shape. |

---

## 8. Effort summary

| Phase | Scope | Est. |
|---|---|---|
| 0 | Runnable + observable | 0.5 d |
| 1 | Payments | 2–3 d |
| 2 | Data integrity | 2 d |
| 3 | Security hardening | 2 d |
| 4 | Reliability/perf | 2 d |
| 5 | Delivery | 1 d |
| **Total** | | **~10–11 engineer-days** |

Payment flow (Phases 0–1) is the gating path and should ship first behind a
feature flag, with the gateway in test mode, before any other phase.

---

## 9. Appendix — status of this branch

This branch implements Phase 0 and Phase 1 (the payment path) so the plan is
verifiable rather than aspirational. Everything below is covered by
`tests/` (25 tests, `uv run pytest -q`) and was exercised against a live
uvicorn process.

### Implemented

| Item | Change |
|---|---|
| B1 | `supabase/migrations/20261017000000_payment_atomic_and_idempotency.sql` defines `process_payment_atomic`, `finalize_payment_split`, the `notifications` table, a `payment_webhook_events` dedupe table, a status `CHECK` and indexes. SQL validated with `pglast` (DDL + both plpgsql bodies). |
| B2 | Webhook signature verification restored, enforced *before* parsing, for Paystack/Flutterwave/mock (`app/services/payment_gateway.py`). Bad/missing signature → 401 and zero writes. |
| B3 | Idempotency keyed on `payment_gateway_ref`; a replayed webhook returns `replayed:true` and credits the wallet exactly once. Checkout rows are settled in place rather than duplicated. |
| B4 | `split_payment_worker` now calls the atomic `finalize_payment_split` RPC (numeric wallet credit + ledger entries + `FOR UPDATE` guard); a second call is a no-op. |
| B5 | `app/core/config.py` split into required vs optional, added all payment/CORS/cron settings, `validate_for_production()`, and `webhook_secret`/`cors_origins` helpers. |
| B5b | `app/core/lightweight_agent.py` no longer constructs `ChatGroq` at import time, so the app boots without `GROQ_API_KEY` (previously it crashed on import). |
| B6 | CORS is an explicit allow-list from `CORS_ORIGINS`; wildcard + credentials is impossible and wildcard is rejected in production validation. |
| B7 | `require_user_context` distinguishes 401 (bad/missing token) from 503 (datastore/idP unreachable) and is used by all payment routes. |
| B8 | Uniform error envelope `{status, code, message, request_id}`; global handlers for unhandled errors and validation errors; `str(e)` no longer returned to clients. Request-id middleware added. |
| B9 | Property status writes/filters use the valid enum value `published` instead of the invalid `available` (7 sites). |
| B10 | `app/core/agent_engine.py` imports the real `application_lease_ops` tools (`submit_application_worker`, `manage_application_worker`); the phantom `app.tools.lease_ops` is gone. |
| B11 | `README.md`, `.env.example`, `requirements.txt`, pytest config; `QUICK_START.md` corrected to `uv sync`. |
| H1/H2 | KYC gating and server-authoritative amounts enforced in `PaymentService.initialize`; a webhook claiming less than `leases.rent` is rejected and never activates the lease. |
| H5 | Wallet returns a real `0` with `status:"success"` when no row exists (typed `WalletResponse`). |
| — | Frontend contract implemented: `POST /payments/initialize`, `POST /payments/webhook`, `GET /payments/verify/{reference}`, `GET /payments/transactions/{id}`, `GET /payments/wallet`, `POST /payments/{id}/split`, documented in OpenAPI at `/api/v1/docs`. |
| — | Health/readiness: `GET /healthz`, `GET /readyz`. |

### Not yet done (still open)

* B11 (partial): no CI workflow, no lint/type-check gates, no `pip-audit`.
* B12: rate limiting, upload validation, webhook replay window.
* H3: PDF generation still happens before the DB transaction (an orphan object
  is possible on rollback); move it inside the transaction boundary.
* H4/H7: single currency + single application-status vocabulary across DB/Python/frontend.
* H8/H9/H13: consolidate auth round-trips, Redis cache, timeouts/circuit breakers.
* Phase 2–5 of the roadmap generally.
* The pre-existing migration file is still out of sync with the live database
  (`properties.latitude/longitude`, `leases.end_date/currency/status`,
  `kyc_verifications.verification_date/verified_by`, `chat_threads.subject/status`,
  `match_properties`). Run `pg_dump --schema-only` and reconcile before shipping.
