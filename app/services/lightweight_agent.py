"""
Lightweight agent backend — the "least expensive" alternative to the
deepagents multi-agent graph in agentic_graph.py.

Same external contract as invoke_housepadi_agent(): takes a message list
(+ optional thread_id, user_context), returns {"messages": [...]}. This
means chat_service.py can swap between backends via one env var with zero
changes anywhere else — see AGENT_BACKEND in chat_service.py.

Two cost tiers, tried in order:
  1. Rule-based fast path — zero LLM calls, zero rate-limit exposure, for
     confidently-parseable search queries ("2 bedroom in Lekki").
  2. Single-call tool-use — one model call to decide + call a tool, one
     more to synthesize a reply. ~2 calls total vs. 4-5 in the multi-agent
     graph, and all tool schemas are bound once at import time rather than
     rebuilt (and re-paid in tokens) on every request.

To revert to the multi-agent graph: set AGENT_BACKEND=multi_agent in .env.
This file can be left in place either way — it's inert unless selected.
"""
import logging
import os
import re
from typing import List, Optional, cast

from langchain_core.messages import ToolMessage, convert_to_messages
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langchain_groq import ChatGroq
from pydantic import SecretStr

from app.services.token_budget import trim_conversation_history, budget_for_model

from app.tools.property_ops import create_property_worker, search_properties_worker, trigger_property_ui_worker
from app.tools.tour_ops import book_tour_worker, list_tours_worker, approve_tour_worker
from app.tools.lease_ops import create_lease_worker, sign_lease_worker, evaluate_application_worker
from app.tools.payment_ops import process_payment_worker, get_wallet_balance_worker, split_payment_worker
from app.tools.kyc_ops import submit_kyc_worker, get_kyc_status_worker, approve_kyc_worker
from app.tools.chat_ops import create_chat_thread_worker, send_message_worker, get_messages_worker, list_threads_worker

logger = logging.getLogger("uvicorn")

LIGHTWEIGHT_MODEL_NAME = "llama-3.1-8b-instant"

LIGHTWEIGHT_SYSTEM_PROMPT = """You are the friendly and helpful HousePadi Agent. Help renters and landlords directly — call the right tool for the request.

RULES:
- Never fabricate a property_id UUID; if you need one and don't have it, call search_properties_worker first.
- location is a place name only, never a full sentence. bedrooms and base_price are separate fields.
- Never show raw database UUIDs to the user — refer to listings naturally.
- Landlord-only actions require the caller to actually be a landlord — refuse politely otherwise.

CONVERSATION GUIDELINES:
- If required info (like location) is missing, YOU MUST ask the user a follow-up question (e.g., "What area are you looking in?").
- If a tool returns no results, explain that clearly to the user.
- ALWAYS respond in complete, conversational sentences. NEVER reply with single words like "Done." or "Okay."
"""

# --- Tool Groupings ---

SEARCH_TOUR_TOOLS = [
    search_properties_worker, book_tour_worker, list_tours_worker, approve_tour_worker, trigger_property_ui_worker
]
LEASE_PAY_TOOLS = [
    create_lease_worker, sign_lease_worker, evaluate_application_worker,
    process_payment_worker, get_wallet_balance_worker, split_payment_worker
]
KYC_CHAT_TOOLS = [
    submit_kyc_worker, get_kyc_status_worker, approve_kyc_worker,
    create_chat_thread_worker, send_message_worker, get_messages_worker, list_threads_worker
]
LANDLORD_TOOLS = [
    create_property_worker
]

# Keep a global dictionary for the actual execution step
ALL_TOOLS = SEARCH_TOUR_TOOLS + LEASE_PAY_TOOLS + KYC_CHAT_TOOLS + LANDLORD_TOOLS
TOOLS_BY_NAME = {t.name: t for t in ALL_TOOLS}

_lightweight_model = ChatGroq(
    model=LIGHTWEIGHT_MODEL_NAME,
    api_key=SecretStr(os.getenv("GROQ_API_KEY") or ""),
    temperature=0,
    max_retries=3,
    model_kwargs={"parallel_tool_calls": False},
)


# --- Tier 1: rule-based fast path, zero LLM calls ---

_BEDROOM_RE = re.compile(r"(\d+)\s*-?\s*bed(room)?s?", re.I)
_PRICE_RE = re.compile(r"(?:under|below|max(?:imum)?)\s*[₦$]?\s*([\d,.]+\s*[kKmM]?)")
_LOCATION_RE = re.compile(r"(?:in|near|at)\s+([A-Z][a-zA-Z\s]{2,30})")

# Any of these keywords signal something beyond a plain search — route to
# the LLM instead of guessing, since these are write/mutating or
# multi-field actions the regex has no business handling.
_NON_SEARCH_KEYWORDS = (
    "tour", "book", "lease", "sign", "pay", "kyc", "verify",
    "apply", "wallet", "message", "chat with", "approve",
)

def select_tools_for_message(message: str) -> List[BaseTool]:
    """Dynamically select a small subset of tools based on user intent."""
    lowered = message.lower()
    
    # Check for landlord specific actions
    if any(kw in lowered for kw in ["list property", "create property", "add property"]):
        return LANDLORD_TOOLS + SEARCH_TOUR_TOOLS
        
    # Check for lease/payment intent
    if any(kw in lowered for kw in ["lease", "sign", "apply", "pay", "wallet", "balance", "split"]):
        return LEASE_PAY_TOOLS
        
    # Check for KYC/chat intent
    if any(kw in lowered for kw in ["kyc", "verify", "id", "chat", "message", "thread"]):
        return KYC_CHAT_TOOLS
        
    # Default to Search and Tour tools (the most common use case)
    return SEARCH_TOUR_TOOLS


def try_rule_based_search(text: str) -> Optional[dict]:
    """Confident, cheap extraction for plain search queries. None => fall through to the LLM."""
    lowered = text.lower()
    if any(kw in lowered for kw in _NON_SEARCH_KEYWORDS):
        return None

    location_match = _LOCATION_RE.search(text)
    if not location_match:
        return None  # ambiguous location — let the LLM handle phrasing the regex can't

    bedroom_match = _BEDROOM_RE.search(text)
    price_match = _PRICE_RE.search(text)

    return {
        "location": location_match.group(1).strip(),
        "bedrooms": int(bedroom_match.group(1)) if bedroom_match else None,
        "base_price": price_match.group(1).replace(",", "") if price_match else None,
    }

# --- Tier 2: single-call tool-use ---


async def invoke_lightweight_agent(
    messages: List[dict],
    thread_id: Optional[str] = None,
    user_context: Optional[dict] = None,
) -> dict:
    tool_config: RunnableConfig = {
        "configurable": {
            "thread_id": thread_id,
            "user_id": (user_context or {}).get("id"),
            "user_role": (user_context or {}).get("role", "renter"),
        }
    }

    last_user_msg = next(
        (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
    )

    # --- Tier 1: Fast Path Search ---
    fast_path_args = try_rule_based_search(last_user_msg)
    if fast_path_args:
        logger.info(f"[LIGHTWEIGHT] Fast-path search: {fast_path_args}")
        tool = cast(BaseTool, search_properties_worker)
        tool_result = await tool.ainvoke(fast_path_args, config=tool_config)
        return {
            "messages": convert_to_messages(messages) + [
                ToolMessage(content=str(tool_result), tool_call_id="fast-path", name="search_properties_worker"),
            ]
        }

    # --- Tier 2: Dynamic Tool Use ---
    budget = budget_for_model(LIGHTWEIGHT_MODEL_NAME)
    trimmed = trim_conversation_history(messages, max_tokens=budget)

    full_messages = convert_to_messages(
        [{"role": "system", "content": LIGHTWEIGHT_SYSTEM_PROMPT}]
    ) + trimmed

    # 1. Dynamically select and bind ONLY the relevant tools
    active_tools = select_tools_for_message(last_user_msg)
    dynamic_model = _lightweight_model.bind_tools(active_tools)

    logger.info(f"[LIGHTWEIGHT] Bound {len(active_tools)} tools for intent routing.")

    # 2. Invoke the model with the restricted toolset
    response = await dynamic_model.ainvoke(full_messages, config=tool_config)
    
    logger.info(f"[DEBUG] Tool calls triggered: {response.tool_calls}")
    logger.info(f"[DEBUG] Initial text response: {response.content}")

    # 3. Intercept Hallucinated Apologies (Failsafe)
    if not response.tool_calls:
        content_lower = str(response.content).lower()
        if "function" in content_lower and "not available" in content_lower:
            logger.warning("[LIGHTWEIGHT] Caught tool hallucination. Forcing fallback.")
            # If the model gets confused, cleanly ask the user to rephrase rather than dumping broken text
            response.content = "I'm having trouble pulling up that specific action. Could you rephrase what you're trying to do?"
        return {"messages": trimmed + [response]}

    # 4. Execute standard tool calls
    tool_messages = []
    for call in response.tool_calls:
        # We still look up against the global dictionary to ensure execution
        tool = TOOLS_BY_NAME.get(call["name"]) 
        if not tool:
            tool_messages.append(ToolMessage(content=f"Unknown tool: {call['name']}", tool_call_id=call["id"]))
            continue
            
        result = await tool.ainvoke(call["args"], config=tool_config)
        logger.info(f"[DEBUG] Tool '{call['name']}' returned: {result}")
        tool_messages.append(ToolMessage(content=str(result), tool_call_id=call["id"], name=call["name"]))

    # 5. Synthesize final reply using the same dynamic model
    final = await dynamic_model.ainvoke(full_messages + [response] + tool_messages, config=tool_config)
    logger.info(f"[DEBUG] Final text response: {final.content}")
    
    return {"messages": trimmed + [response] + tool_messages + [final]}