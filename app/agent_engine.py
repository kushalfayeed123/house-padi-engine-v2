import os
from typing import Any, List
from dotenv import load_dotenv

from deepagents import SubAgent, create_deep_agent
from deepagents.backends import StoreBackend
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI  # Swapped from ChatOllama to handle free cloud models
from langgraph.checkpoint.memory import MemorySaver 

from app.tools.property_ops import create_property_worker, search_properties_worker
from app.tools.tour_ops import book_tour_worker, list_tours_worker
from app.tools.lease_ops import lease_database_worker
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
1. PROPERTY OPERATIONS (property-specialist): Handles searching/finding matching listings for renters and creating new property listings for landlords/owners.
2. TOUR MANAGEMENT (tour-specialist): Route requests here when users ask to schedule physical site viewings, book inspection dates, or look up their scheduled appointments. Requires a valid, real UUID retrieved from a property search.
3. LEASE WORKFLOWS (lease-specialist): Handles formal rental lease applications, contract documentation edits, and compliance review approvals.

EXECUTION RULES:
- Trigger exactly ONE tool per conversational turn. Do not generate parallel or duplicate tool calls.
- Trust backend worker payloads. 
- USER-FACING PRESENTATION SAFETY: When summarizing for the user, NEVER display raw database UUID strings (like 'id' or 'property_id'). Instead, hide them behind natural text links, reference numbers (e.g., "Option 1"), or drop them from the text entirely. The user does not need to see the UUID.
"""


# --- 3. OpenRouter Free Infrastructure Configuration ---

groq_model = ChatGroq(
    model="llama-3.1-8b-instant",
    api_key=SecretStr(os.getenv("GROQ_API_KEY") or ""),
    temperature=0,
)


dynamic_router_free = ChatOpenAI(
    model="openrouter/free",
    base_url="https://openrouter.ai/api/v1",
    api_key=SecretStr(os.getenv("OPENROUTER_API_KEY") or ""),
    temperature=0,
)

# Powerful 120B MoE model for high-reasoning supervisor mapping
supervisor_model = dynamic_router_free

# Elite agentic coding/tool model for deterministic sub-agent JSON outputs
worker_model = groq_model

# --- 4. Sub-Agent Definitions ---

property_agent: SubAgent = {
    "name": "property-specialist",
    "description": (
        "Handles real estate repository operations, including searching/finding available "
        "listings for renters, and cataloging/creating new property assets for owners."
    ),
    "system_prompt": (
        "You are a strict single-turn property operations expert for HousePadi.\n\n"
        "CRITICAL EXECUTION RULE:\n"
        "You are permitted exactly ONE tool call per request. Once you call a tool and receive "
        "its output, you MUST immediately accept that data as final truth, summarize the findings, "
        "and stop execution. NEVER execute the same tool twice in a single turn.\n\n"
        "OPERATIONAL CHANNELS:\n"
        "1. For searching or listing matching apartments, invoke `search_properties_worker` once.\n"
        "2. For adding a new property asset, invoke `create_property_worker` once."
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
        "and use `list_tours_worker` when they ask to see their existing viewing schedule history."
    ),
    "tools": [book_tour_worker, list_tours_worker],
    "model": worker_model
}

lease_agent: SubAgent = {
    "name": "lease-specialist",
    "description": "Processes lease applications and approval workflows.",
    "system_prompt": "You are a lease compliance expert. Verify lease data and process approvals or rejections using the lease_database_worker.",
    "tools": [lease_database_worker],
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
    subagents=[property_agent, tour_agent, lease_agent],
    interrupt_on={
        "lease_database_worker": {
            "allowed_decisions": ["approve", "reject"]
        }
    }
)

housepadi_agent_graph.checkpointer = MemorySaver()
