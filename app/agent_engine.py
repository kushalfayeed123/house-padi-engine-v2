import os
import logging
from typing import Annotated, Any, List, Optional, TypedDict, cast
from datetime import datetime, timedelta
from dotenv import load_dotenv

from deepagents import SubAgent, create_deep_agent
from deepagents.backends import StoreBackend
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from langchain_groq import ChatGroq
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import add_messages 

from app.tools.property_ops import create_property_worker, search_properties_worker
from app.tools.tour_ops import book_tour_worker, list_tours_worker, approve_tour_worker
from app.tools.lease_ops import create_lease_worker, sign_lease_worker, evaluate_application_worker
from app.tools.payment_ops import process_payment_worker, get_wallet_balance_worker, split_payment_worker
from app.tools.kyc_ops import submit_kyc_worker, get_kyc_status_worker, approve_kyc_worker
from app.tools.chat_ops import create_chat_thread_worker, send_message_worker, get_messages_worker, list_threads_worker
from langchain_core.tools import tool
from pydantic import BaseModel, Field, SecretStr

from app.services.token_budget import trim_conversation_history, budget_for_model

load_dotenv()


class InputAgentState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]

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

SYSTEM_PROMPT = """You are the HousePadi Supervisor Agent.

Route real estate requests to specialized sub-agents — never call worker tools directly.

FIRST STEP: Call `write_todos` before any sub-agent delegation.

ID LOOKUP RULE: Never fabricate a `property_id` UUID. For tours/viewings, route to `property-specialist` first to get the real `id`, then delegate to `tour-specialist` with that exact UUID.

SUB-AGENTS:
- property-specialist: search listings (renters), create listings (landlords)
- tour-specialist: schedule/list/approve tours, directions
- lease-specialist: create/sign leases, evaluate applications
- payment-specialist: process payments, wallets, fee splits
- kyc-specialist: identity verification
- chat-specialist: messaging between users

DEFAULT JOURNEYS:
- Renter: search → tour → apply → sign lease → pay
- Landlord: list property → tours → evaluate applications → lease → get paid

RULES:
- One tool call per turn, no parallel calls.
- Only real UUIDs from database queries — never fabricate.
- Never show raw UUIDs to the user; use natural references instead.
- Trust worker payloads as final.
- If required info (location, price, dates) is missing, STOP and ask the user — do not call a sub-agent until you have it.
- Your only tools are `write_todos` and the sub-agents.
"""

# --- 3. RATE-LIMIT-OPTIMIZED MULTI-MODEL STRATEGY ---
# 🎯 STRATEGY: Hybrid approach avoiding rate limits entirely
# - Primary: Groq (30 req/min, free tier, very fast)
# - Fallback: Ollama local (unlimited, 100% free, runs on your machine)
# - Tertiary: OpenRouter free models (backup fallback)
# No token-based rate limits. All free. No additional costs.

groq_model_supervisor = ChatGroq(
    model="llama-3.3-70b-versatile",
    api_key=SecretStr(os.getenv("GROQ_API_KEY") or ""),
    temperature=0,
    max_retries=3,
    model_kwargs={"parallel_tool_calls": False}
)
groq_model = ChatGroq(
    model="llama-3.1-8b-instant",
    api_key=SecretStr(os.getenv("GROQ_API_KEY") or ""),
    temperature=0,
    max_retries=3,
    model_kwargs={"parallel_tool_calls": False}
)





# --- 3b. Intelligent Model Selection at Request Time ---
logger = logging.getLogger(__name__)


class ModelSelectorCache:
    """Caches model availability checks to avoid redundant health checks within a time window."""

    def __init__(self, cache_duration_seconds: int=300):
        self.cache_duration = timedelta(seconds=cache_duration_seconds)
        self.last_check: dict[str, datetime] = {}
        self.availability: dict[str, bool] = {}
    
    def is_cached(self, model_name: str) -> bool:
        """Check if cached availability is still valid."""
        if model_name not in self.last_check:
            return False
        return datetime.now() - self.last_check[model_name] < self.cache_duration
    
    def get(self, model_name: str) -> Optional[bool]:
        """Get cached availability status if available."""
        if self.is_cached(model_name):
            return self.availability.get(model_name)
        return None
    
    def set(self, model_name: str, available: bool) -> None:
        """Cache availability status."""
        self.last_check[model_name] = datetime.now()
        self.availability[model_name] = available


model_cache = ModelSelectorCache(cache_duration_seconds=300)


# --- 4. Sub-Agent Definitions ---
# NOTE: `description` fields are exposed to the SUPERVISOR as part of each
# sub-agent's tool schema, and are therefore paid as fixed overhead on
# *every* supervisor turn regardless of conversation length. Kept terse for
# that reason. `system_prompt` fields only cost tokens when that specific
# sub-agent is actually invoked, so they can stay fuller.

property_agent: SubAgent = {
    "name": "property-specialist",
    "description": "Search listings for renters; create listings for landlords.",
    "system_prompt": (
       "You are a strict property operations expert for HousePadi.\n\n"
        "CRITICAL FIELD MAPPING: `location` is a place name only (e.g. 'Lekki Phase 1'), "
        "never a full description. Bedroom count goes in `bedrooms`, budget in `base_price`. "
        "Never combine these into one field.\n\n"
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
    "model": groq_model  
}

tour_agent: SubAgent = {
    "name": "tour-specialist",
    "description": "Schedule, list, and approve property tours.",
    "system_prompt": (
        "You are a dedicated tour scheduling assistant for HousePadi.\n"
        "Use `book_tour_worker` when a renter wants to set up a new visitation appointment, "
        "use `list_tours_worker` when they ask to see their existing viewing schedule history, "
        "and use `approve_tour_worker` when a landlord approves a tour request."
    ),
    "tools": [book_tour_worker, list_tours_worker, approve_tour_worker],
    "model": groq_model
}

lease_agent: SubAgent = {
    "name": "lease-specialist",
    "description": "Create leases, handle signing, evaluate applications.",
    "system_prompt": (
        "You are a lease compliance expert. Handle lease creation, signing, and application evaluation.\n"
        "Use `create_lease_worker` to create lease agreements, `sign_lease_worker` to sign them, "
        "and `evaluate_application_worker` to approve or reject rental applications with AI screening."
    ),
    "tools": [create_lease_worker, sign_lease_worker, evaluate_application_worker],
    "model": groq_model
}

payment_agent: SubAgent = {
    "name": "payment-specialist",
    "description": "Process payments, check wallets, split fees.",
    "system_prompt": (
        "You are a payment processing expert for HousePadi.\n"
        "Use `process_payment_worker` to process rent payments, `get_wallet_balance_worker` to check balances, "
        "and `split_payment_worker` to distribute payments between landlords and platform."
    ),
    "tools": [process_payment_worker, get_wallet_balance_worker, split_payment_worker],
    "model": groq_model
}

kyc_agent: SubAgent = {
    "name": "kyc-specialist",
    "description": "Handle identity verification and screening.",
    "system_prompt": (
        "You are an identity verification expert for HousePadi.\n"
        "Use `submit_kyc_worker` to help users submit KYC documents, `get_kyc_status_worker` to check status, "
        "and `approve_kyc_worker` (admin only) to verify or reject applications."
    ),
    "tools": [submit_kyc_worker, get_kyc_status_worker, approve_kyc_worker],
    "model": groq_model
}

chat_agent: SubAgent = {
    "name": "chat-specialist",
    "description": "Handle messaging between users.",
    "system_prompt": (
        "You are a messaging specialist for HousePadi.\n"
        "Use `create_chat_thread_worker` to start new conversations, `send_message_worker` to send messages, "
        "`get_messages_worker` to retrieve message history, and `list_threads_worker` to show all conversations."
    ),
    "tools": [create_chat_thread_worker, send_message_worker, get_messages_worker, list_threads_worker],
    "model": groq_model
}

# --- 5. State Storage & Graph Compilation ---

tenant_isolated_store = StoreBackend(
    namespace=lambda runtime: (
        getattr(runtime, "config", {}).get("configurable", {}).get("user_id", "anonymous_system_boundary"),
    )
)



# --- 6. Dynamic Graph Invocation with Model Fallback ---


def create_graph_with_selected_model():
    """
    Create a fresh graph instance with the currently available model.
    Called at runtime to ensure the graph uses a working model.
    """
    
    logger.info(f"[GRAPH_FACTORY] Creating agent graph with supervisor model: {groq_model_supervisor.model}")
    
    graph = create_deep_agent(
        model=groq_model_supervisor,
        tools=[write_todos],
        system_prompt=SYSTEM_PROMPT,
        backend=tenant_isolated_store,
        subagents=[property_agent, tour_agent, lease_agent, payment_agent, kyc_agent, chat_agent],
        interrupt_on={
            "evaluate_application_worker": True,
            "approve_kyc_worker": True
        }
    )
    graph.checkpointer = MemorySaver()
    return graph


async def invoke_housepadi_agent(
    messages: List[dict],
    thread_id: Optional[str] = None,
    user_context: Optional[dict] = None,
) -> dict:
    # Start from the tightest ceiling in the pipeline (the sub-agent worker
    # model), since a request that fits the supervisor can still blow up
    # once delegated to a sub-agent carrying its own tool schemas.
    budget = budget_for_model("llama-3.1-8b-instant")
    invocation_config: RunnableConfig = {
        "configurable": {
            "thread_id": thread_id,
            "user_id": (user_context or {}).get("id"),
            "user_role": (user_context or {}).get("role", "renter"),
        }
    }
    graph = create_graph_with_selected_model()

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            converted_messages = trim_conversation_history(messages, max_tokens=budget)
            state_dict: InputAgentState = {"messages": converted_messages}
            result = await graph.ainvoke(input=cast(Any, state_dict), config=invocation_config)
            return result
        except Exception as e:
            last_error = e
            err_str = str(e)

            if "tool_use_failed" in err_str and attempt == 0:
                logger.warning("[INVOKE] tool_use_failed — retrying once")
                continue

            if "rate_limit_exceeded" in err_str and "tokens per minute" in err_str and attempt < 2:
                budget = int(budget * 0.6)
                logger.warning(f"[INVOKE] TPM exceeded, retrying with budget={budget}")
                continue

            logger.error(f"[INVOKE] Agent invocation failed: {err_str}")
            raise

    if last_error is not None:
        raise last_error

    raise RuntimeError("Agent invocation failed")