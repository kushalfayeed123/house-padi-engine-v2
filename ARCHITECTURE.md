# ARCHITECTURE - HousePadi Backend Engine

## Overview
The system is a multi-agent backend that coordinates renter, landlord, and admin workflows for property rental. It uses a supervisor agent for routing and multiple specialist agents for domain tasks.

Components:
- Supervisor agent (Groq) - routes user intents to specialist agents
- Specialist agents (Ollama local workers) - property-specialist, tour-specialist, lease-specialist, payment-specialist, kyc-specialist, chat-specialist
- Tools: property_ops, tour_ops, lease_ops, payment_ops, kyc_ops, chat_ops
- Cache layer: in-memory TTL cache (upgrade path to Redis)
- Database: PostgreSQL with 13 tables (profiles, properties, applications, leases, transactions, wallets, ledger_entries, kyc_verifications, chat_threads, messages, bank_details, message_embeddings, spatial_ref_sys)

## Data Flows
1. User -> Supervisor agent
2. Supervisor routes to appropriate specialist
3. Specialist calls tools which read/write DB and optionally call local/remote LLMs
4. For searches: check cache → if miss, query DB + vector search → store in cache
5. For payments: validate lease & KYC → create transaction & ledger entries → update wallets

## Model Strategy
- Routing: Groq (fast)
- Worker LLMs: Ollama local (unlimited, deterministic)
- Fallback: OpenRouter free tier (if Groq unavailable and Ollama unreachable)
- Exponential backoff and retry logic implemented

## Security
- RBAC enforced in every tool (renter/landlord/admin roles)
- KYC required for lease/payment-sensitive actions
- Ledger entries for audit trail
- JWT-based authentication

## Observability
- Logging tags for major events: [CACHE], [TOUR], [PAYMENT], [KYC], [CHAT]
- Cache stats exposed via a helper function
- DB audit via ledger entries and transactions table

## Scaling notes
- Cache moves to Redis for multi-instance deployments
- Ollama can be hosted on a dedicated GPU/CPU server for performance
- Vector search can be offloaded to a vector database (e.g., Milvus, Pinecone)

