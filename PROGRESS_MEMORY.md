# PROGRESS MEMORY - Session Summary

Timestamp: 2026-07-15T16:38:35+01:00

Overview of work done in this session:
- Designed and implemented a 3-tier LLM fallback (Groq, Ollama local, OpenRouter fallback) to remove rate limits and cut cost to $0/month.
- Implemented an in-memory TTL cache with invalidation for property searches (24-hour TTL).
- Created/updated tools and agents to align with production PostgreSQL schema (13 tables): properties, applications, leases, transactions, wallets, kyc_verifications, chat_threads, messages, bank_details, ledger_entries, message_embeddings, profiles, spatial_ref_sys.
- Added tools for payments, KYC, and chat. Split lease ops into specialized functions.
- Enforced RBAC in all tools; added ledger entries for every transaction for audit.
- Created documentation and testing guide; merged branch into main and prepared files in repo root.

Key Decisions & Rationale:
- Use Ollama locally for unlimited worker LLM compute; use Groq for fast routing decisions; OpenRouter as third fallback.
- Cache high-frequency operations (search) for 24h to cut API calls ~70%.
- Keep KYC and payment operations off-cache for freshness/security.

Next actions:
- Run full end-to-end tests from TESTING_GUIDE.md
- Implement frontend to consume new fields (coords, directions_link, wallet info)
- Integrate a payment gateway for live transactions

Saved artifacts:
- TESTING_GUIDE.md
- MERGE_SUMMARY.md
- QUICK_START.md
- ARCHITECTURE.md

