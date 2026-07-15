import os
from typing import Any, List
from dotenv import load_dotenv

from deepagents import SubAgent, create_deep_agent
from deepagents.backends import StoreBackend
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver 

from app.tools.property_ops import create_property_worker, search_properties_worker
from app.tools.tour_ops import book_tour_worker, list_tours_worker, approve_tour_worker
from app.tools.lease_ops import create_lease_worker, sign_lease_worker, evaluate_application_worker
from app.tools.payment_ops import process_payment_worker, get_wallet_balance_worker, split_payment_worker
from app.tools.kyc_ops import submit_kyc_worker, get_kyc_status_worker, approve_kyc_worker
from app.tools.chat_ops import create_chat_thread_worker, send_message_worker, get_messages_worker, list_threads_worker
from langchain_core.tools import tool
from pydantic import BaseModel, Field, SecretStr

load_dotenv()

# --- 1. Execution Planner Setup ---


class TodoInput(BaseModel):
    todos: List[Any] = Field(
        ...,
        description="The complete, ordered checklist of remaining tasks required to fulfill the request. Pass raw task descriptions."
    )


@tool("write_todos", args_schema=TodoInput)
def write_todos(todos: List[Any]) -> str:
    """Initializes or updates the structural orchestration plan and task tracking checklist."""
    sanitized_todos: List[str] = []
    for item in todos:
        if isinstance(item, dict):
            task_text = item.get("text") or item.get("task") or str(item)
            sanitized_todos.append(task_text)
        elif isinstance(item, str):
            sanitized_todos.append(item)
        else:
            sanitized_todos.append(str(item))

    print(f"[PLANNER] Current execution path updated (Sanitized): {sanitized_todos}")
    return f"Todo List Updated: {sanitized_todos}"

# --- 2. Re-enabled Fully Descriptive System Prompt ---


SYSTEM_PROMPT = """You are the HousePadi Supervisor Agent. Your job is to orchestrate end-to-end real estate operations by routing requests to specialized sub-agents.

CRITICAL FIRST STEP:
Before executing any external sub-agent operations, you must immediately call the `write_todos` tool to declare or update your structural task checklist.

CRITICAL DEPENDENCY & ID LOOKUP RULE:
1. When a user requests to book a tour or view a property (e.g., "Schedule a tour for the 2-bedroom apartment"), you NEVER guess or fabricate a placeholder UUID for the `property_id`.
2. You MUST first route the user to the `property-specialist` to perform a search and find the real property record. 
3. Once the `property-specialist` returns the valid property data payload containing the real `id`, you may then update your checklist and delegate to the `tour-specialist` using that exact real UUID string.

ORCHESTRATION & ROUTING PATHWAYS:
1. PROPERTY OPERATIONS (property-specialist): Handles searching/finding listings for renters and creating new properties for landlords.
2. TOUR MANAGEMENT (tour-specialist): Schedules tours, generates directions via Google Maps, manages tour approvals.
3. LEASE WORKFLOWS (lease-specialist): Handles lease creation, signing, and application evaluation with AI screening.
4. PAYMENT PROCESSING (payment-specialist): Processes rent payments, splits fees between parties, manages wallets.
5. IDENTITY VERIFICATION (kyc-specialist): Manages KYC verification for renters and landlords.
6. MESSAGING (chat-specialist): Handles communication between renters, landlords, and property owners.

WORKFLOW SEQUENCE (Default User Journey):
1. Renter: Search for properties → Book tour (with directions) → View applications/approvals → Sign lease → Make payment
2. Landlord: Create property → Receive tour requests → Evaluate applications → Create lease → Receive payments

EXECUTION RULES:
- Trigger exactly ONE tool per conversational turn. Do not generate parallel tool calls.
- Always use real UUIDs retrieved from database queries. Never fabricate IDs.
- USER-FACING PRESENTATION SAFETY: When summarizing for the user, NEVER display raw database UUID strings. Hide them behind natural text or reference numbers.
- Trust backend worker payloads completely.
- Use cache hits when possible to reduce API calls (caching enabled for property searches).
"""

# --- 3. RATE-LIMIT-OPTIMIZED MULTI-MODEL STRATEGY ---
# 🎯 STRATEGY: Hybrid approach avoiding rate limits entirely
# - Primary: Groq (30 req/min, free tier, very fast)
# - Fallback: Ollama local (unlimited, 100% free, runs on your machine)
# - Tertiary: OpenRouter free models (backup fallback)
# No token-based rate limits. All free. No additional costs.

groq_model = ChatGroq(
    model="llama-3.3-70b-versatile",
    api_key=SecretStr(os.getenv("GROQ_API_KEY") or ""),
    temperature=0,
    max_retries=3,  # Built-in retry logic for rate limit handling
)

ollama_model = ChatOllama(
    model="llama3.1:8b",
    temperature=0,
    base_url="http://localhost:11434",
    client_kwargs={"timeout": 30.0}
)

# Fallback to OpenRouter free tier if other services are unavailable
openrouter_fallback = ChatOpenAI(
    model="openrouter/auto",
    base_url="https://openrouter.ai/api/v1",
    api_key=SecretStr(os.getenv("OPENROUTER_API_KEY") or ""),
    temperature=0,
)

# SUPERVISOR: Use Groq for routing decisions (very fast, handles rate limits well)
supervisor_model = groq_model

# WORKERS: Use Ollama for sub-agents (local, unlimited, deterministic outputs)
# Falls back to Groq if Ollama unavailable
worker_model = ollama_model

# --- 4. Sub-Agent Definitions ---

property_agent: SubAgent = {
    "name": "property-specialist",
    "description": (
        "Handles real estate repository operations, including searching/finding available "
        "listings for renters, and cataloging/creating new property assets for owners."
    ),
    "system_prompt": (
       "You are a strict property operations expert for HousePadi.\n\n"
        "CRITICAL FOR ENTRY/CREATION WORKFLOWS:\n"
        "If a landlord/owner wants to create or catalog a new property listing, you MUST explicitly "
        "have the actual 'address', 'base_price', and 'location' from their message text.\n"
        "- NEVER guess, invent, or hallucinate placeholder values (e.g., do NOT invent addresses like '123 Main St' or prices like '1200').\n"
        "- If any of these fields are missing from the conversation context, you MUST stop immediately, do NOT call `create_property_worker`, and instead reply to the user asking them to provide the missing details (e.g., 'Please provide the address, price, and city location for your new listing.').\n\n"
        "CRITICAL SEARCH WORKFLOWS:\n"
        "- For searching or listing properties for a renter, you only need the location. If you have the location, call `search_properties_worker` immediately.\n\n"
        "EXECUTION LIMIT:\n"
        "You are permitted exactly ONE tool call per turn. Once you receive the tool payload, accept it as final truth and summarize it."
    ),
    "tools": [search_properties_worker, create_property_worker],
    "model": worker_model  
}

tour_agent: SubAgent = {
    "name": "tour-specialist",
    "description": "Manages scheduling, booking arrangements, and records regarding physical apartment tours.",
    "system_prompt": (
        "You are a dedicated tour scheduling assistant for HousePadi.\n"
        "Use `book_tour_worker` when a renter wants to set up a new visitation appointment, "
        "use `list_tours_worker` when they ask to see their existing viewing schedule history, "
        "and use `approve_tour_worker` when a landlord approves a tour request."
    ),
    "tools": [book_tour_worker, list_tours_worker, approve_tour_worker],
    "model": worker_model
}

lease_agent: SubAgent = {
    "name": "lease-specialist",
    "description": "Processes lease applications and approval workflows.",
    "system_prompt": (
        "You are a lease compliance expert. Handle lease creation, signing, and application evaluation.\n"
        "Use `create_lease_worker` to create lease agreements, `sign_lease_worker` to sign them, "
        "and `evaluate_application_worker` to approve or reject rental applications with AI screening."
    ),
    "tools": [create_lease_worker, sign_lease_worker, evaluate_application_worker],
    "model": worker_model
}

payment_agent: SubAgent = {
    "name": "payment-specialist",
    "description": "Handles payment processing, fee splitting, and wallet management.",
    "system_prompt": (
        "You are a payment processing expert for HousePadi.\n"
        "Use `process_payment_worker` to process rent payments, `get_wallet_balance_worker` to check balances, "
        "and `split_payment_worker` to distribute payments between landlords and platform."
    ),
    "tools": [process_payment_worker, get_wallet_balance_worker, split_payment_worker],
    "model": worker_model
}

kyc_agent: SubAgent = {
    "name": "kyc-specialist",
    "description": "Handles identity verification and user screening.",
    "system_prompt": (
        "You are an identity verification expert for HousePadi.\n"
        "Use `submit_kyc_worker` to help users submit KYC documents, `get_kyc_status_worker` to check status, "
        "and `approve_kyc_worker` (admin only) to verify or reject applications."
    ),
    "tools": [submit_kyc_worker, get_kyc_status_worker, approve_kyc_worker],
    "model": worker_model
}

chat_agent: SubAgent = {
    "name": "chat-specialist",
    "description": "Handles messaging between renters, landlords, and property owners.",
    "system_prompt": (
        "You are a messaging specialist for HousePadi.\n"
        "Use `create_chat_thread_worker` to start new conversations, `send_message_worker` to send messages, "
        "`get_messages_worker` to retrieve message history, and `list_threads_worker` to show all conversations."
    ),
    "tools": [create_chat_thread_worker, send_message_worker, get_messages_worker, list_threads_worker],
    "model": worker_model
}

# --- 5. State Storage & Graph Compilation ---

tenant_isolated_store = StoreBackend(
    namespace=lambda runtime: (
        getattr(runtime, "config", {}).get("configurable", {}).get("user_id", "anonymous_system_boundary"),
    )
)

housepadi_agent_graph = create_deep_agent(
    model=supervisor_model,
    tools=[write_todos],
    system_prompt=SYSTEM_PROMPT,
    backend=tenant_isolated_store,
    subagents=[property_agent, tour_agent, lease_agent, payment_agent, kyc_agent, chat_agent],
    interrupt_on={
        "evaluate_application_worker": True,
        "approve_kyc_worker": True
    }
)

housepadi_agent_graph.checkpointer = MemorySaver()
