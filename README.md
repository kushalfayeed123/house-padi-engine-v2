# HousePadi Engine v2

Multi-agent backend for the HousePadi property-rental marketplace: a FastAPI
gateway that fronts a Supabase/Postgres datastore and a set of LangChain tool
"workers" driven by a Groq-backed agent.

> **Production readiness:** see [`PRODUCTION_READINESS_PLAN.md`](./PRODUCTION_READINESS_PLAN.md)
> for the full audit, the payment-flow target architecture, the frontend
> contract and the phased rollout plan.

## Quick start

```bash
# 1. Install dependencies (uv is the source of truth: pyproject.toml + uv.lock)
uv sync                       # or: pip install -r requirements.txt

# 2. Configure
cp .env.example .env          # then fill in SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY

# 3. Run
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

OpenAPI docs: <http://localhost:8000/api/v1/docs>
Health: `GET /healthz` (liveness) and `GET /readyz` (datastore reachability).

## Tests

```bash
uv run pytest -q
```

The payment suite (`tests/test_payment_flow.py`) covers signature enforcement,
idempotent webhooks, amount reconciliation, KYC/state gating and the frontend
response contract. It runs entirely against the in-memory repository — no
database or network required.

## Payment flow (summary)

1. `POST /payments/initialize` — server reads the amount from the lease, creates
   an `initialized` transaction, returns the gateway checkout payload.
2. The renter pays on the gateway.
3. `POST /payments/webhook` — signature verified first, then the atomic,
   idempotent Postgres function `process_payment_atomic` settles the ledger,
   credits the landlord wallet and activates the lease.
4. `GET /payments/verify/{reference}` — the frontend's post-redirect callback;
   reconciles with the gateway and returns the canonical status object.
5. `GET /payments/transactions/{id}` — poll while the status is non-terminal.

Money amounts are numbers in major units (naira); `amount_minor` is the integer
minor-unit value for gateway SDKs. The frontend must never compute amounts.

## Database migrations

SQL lives in `supabase/migrations/`. Apply with the Supabase CLI
(`supabase db push`) or your migration runner — never by hand in production.
