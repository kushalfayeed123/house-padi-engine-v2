
from deepagents import HarnessProfile, SubAgent, create_deep_agent, register_harness_profile
from deepagents.backends import StoreBackend
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langgraph.checkpoint.memory import MemorySaver 

from app.tools.property_ops import property_database_worker
from app.tools.tour_ops import tour_database_worker
from app.tools.lease_ops import lease_database_worker

SYSTEM_PROMPT = """You are the HousePadi Supervisor Agent. Your job is to orchestrate tasks by routing requests to specialized database workers.

PLANNING COMPLIANCE:
Before executing any external database queries or user actions, you must first initialize a clear structural execution plan using your built-in `write_todos` tool. Track your progress cleanly through this planner as you coordinate workers.

ORCHESTRATION & ROUTING RULES:
1. To search, find, or list properties (such as when a tenant looks for an apartment), add a task to your todo list, then route the request by calling the `property_database_worker` tool with action="list".
2. NEVER ask the user for their "tenant identity", "user_id", or "config token". The backend infrastructure injects this contextual state automatically behind the scenes.
3. Construct your tool call payload string using the location or queries provided by the user (e.g., payload_json='{"query": "Abuja"}').
4. Trust the output returned by the worker tool, update your todo list to mark the step completed, and format the final result into a friendly response for the user.
"""

# 1. Initialize the Groq Chat Model
# Ensure GROQ_API_KEY is set in your environment variables (.env file)
groq_model = ChatGroq(
    model="llama-3.3-70b-versatile",  # Large context window and exceptional tool calling
    temperature=0,  # Set to 0 for stable, deterministic routing decisions
)

gemini_model = ChatGoogleGenerativeAI(
    model="gemini-2.0-flash",
    temperature=0
)

property_agent: SubAgent = {
    "name": "property-specialist",
    "description": "Handles searching, finding, and listing property data.",
    "system_prompt": "You are a property search expert. Use the property_database_worker to list or find apartments based on user queries.",
    "tools": [property_database_worker],
}

tour_agent: SubAgent = {
    "name": "tour-specialist",
    "description": "Manages scheduling and information regarding apartment tours.",
    "system_prompt": "You are a tour scheduling assistant. Use the tour_database_worker to check availability and book tours.",
    "tools": [tour_database_worker],
}

lease_agent: SubAgent = {
    "name": "lease-specialist",
    "description": "Processes lease applications and approval workflows.",
    "system_prompt": "You are a lease compliance expert. Verify lease data and process approvals or rejections using the lease_database_worker.",
    "tools": [lease_database_worker],
}

# Isolate user storage sandboxes natively matching runtime context variables
tenant_isolated_store = StoreBackend(
    namespace=lambda runtime: (
        getattr(runtime, "config", {}).get("configurable", {}).get("user_id", "anonymous_system_boundary"),
    )
)

# 2. Instantiate the agent passing the Groq model instance directly
housepadi_agent_graph = create_deep_agent(
    model=groq_model,
    tools=[],
    system_prompt=SYSTEM_PROMPT,
    backend=tenant_isolated_store,
    subagents=[property_agent, tour_agent, lease_agent],
    interrupt_on={
        "lease_database_worker": {
            "allowed_decisions": ["approve", "reject"]
        }
    }
)

# Attach the required checkpointer layer to track the multi-agent state across turns
housepadi_agent_graph.checkpointer = MemorySaver()
