# 🧪 HousePadi Backend - Comprehensive Testing Guide

Status: All changes merged to branch `agents/multi-agent-property-rental-system`

## Prerequisites
- Python 3.10+
- PostgreSQL (with PostGIS if using spatial queries)
- Ollama installed and running (for local LLM)
- Supabase or PostgreSQL credentials configured in .env

Quick commands:
```bash
# start Ollama model (example)
ollama run llama3.1:8b

# copy env
cp .env.example .env
# fill SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, GROQ_API_KEY, BACKEND_JWT_SECRET

# start backend
python -m uvicorn app.main:app --reload
# docs: http://localhost:8000/docs
```

---

## Test Flows (high level)
1. Property search (cache miss → cache hit)
2. Property creation (landlord flow + cache invalidation)
3. Tour booking and directions (Google Maps link)
4. Tour approval (landlord approves, renter notified)
5. KYC submission and admin approval
6. Application → Lease creation → Dual signatures
7. Payment processing → fee splitting → ledger entries
8. Chat: create thread, send messages, list history

Each flow includes expected responses and verification steps. Use the OpenAPI docs to call endpoints or the WebSocket agent interface for chat-style flows.

---

## Property Search (example)
- First identical search should be a cache MISS (~300-700ms)
- Second identical search should be a cache HIT (~<50ms)

Verify:
- Check backend logs for "CACHE HIT" / "CACHE MISS"
- Use cache stats endpoint or call cache_service.cache_stats() in a REPL

---

## Tour Booking (example)
When booking a tour, response includes a directions link:
```
https://www.google.com/maps/search/?api=1&query=<lat>,<lng>
```
Verify that the landlord receives a notification record in DB and that the tour entry status transitions from pending_approval → approved.

---

## KYC Flow (example)
- Renter submits KYC → record status `pending`
- Admin approves via admin endpoint → updates profile.kyc_status to `verified`

---

## Application & Lease (example)
- Renter applies for a property → application created with ai_match_score
- Landlord evaluates → approves → create lease
- Both parties sign → lease.is_active becomes true

---

## Payments (example)
- process_payment creates transaction with platform_fee (5%) and ledger entries
- split_payment finalizes distribution and marks transaction completed

---

## Chat (example)
- create_chat_thread returns thread_id
- send_message persists messages and updates chat_threads.last_message_at
- get_messages returns ordered history

---

## Debugging tips
- Inspect logs for tags: [CACHE], [TOUR], [KYC], [PAYMENT], [CHAT]
- Query DB tables: properties, tours, applications, leases, transactions, ledger_entries, chat_threads, messages

---

For full step-by-step examples and sample curl/WebSocket scripts refer to the project's TESTING_GUIDE in the repo root.
