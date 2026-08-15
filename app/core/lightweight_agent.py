import asyncio
import inspect
import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, cast
import uuid

import numpy as np
from langchain_core.messages import AIMessage, ToolCall, ToolMessage, convert_to_messages
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langchain_groq import ChatGroq
from pydantic import SecretStr
from sentence_transformers import SentenceTransformer

from app.core.intent_transformer import dynamic_intent_router
from app.services.token_budget import trim_conversation_history, budget_for_model

from app.tools.application_lease_ops import manage_application_worker, submit_application_worker
from app.tools.property_ops import create_property_worker, search_properties_worker, trigger_property_ui_worker
from app.tools.tour_ops import book_tour_worker, list_tours_worker, \
    manage_tour_request_worker
from app.tools.payment_ops import process_payment_worker, get_wallet_balance_worker, split_payment_worker
from app.tools.kyc_ops import submit_kyc_worker, get_kyc_status_worker, approve_kyc_worker
from app.tools.chat_ops import create_chat_thread_worker, send_message_worker, get_messages_worker, list_threads_worker

logger = logging.getLogger("uvicorn")

WRITE_TOOLS = {
    "book_tour_worker",
    "approve_tour_worker",
    "create_property_worker",
    "create_lease_worker",
    "submit_kyc_worker",
    "process_payment_worker",
}

LIGHTWEIGHT_MODEL_NAME = "llama-3.1-8b-instant"

LIGHTWEIGHT_SYSTEM_PROMPT = """You are the friendly and helpful HousePadi Agent. Help renters and landlords directly — call the right tool for the request when appropriate, while maintaining natural, open-ended conversational flow.

RULES:
- Never fabricate a property_id UUID; if you need one and don't have it, call search_properties_worker first.
- location is a place name only, never a full sentence. bedrooms and base_price are separate fields.
- Never show raw database UUIDs to the user — refer to listings naturally.
- Landlord-only actions require the caller to actually be a landlord — refuse politely otherwise.
- For most actions (leases, payments, KYC, creating listings), do not invent or assume missing values —
  ask the user for them directly instead of calling the tool with guessed data.
- EXCEPTION — scheduling a tour:
  - As soon as you know which property the user wants to visit, call book_tour_worker.
  - If the user has not explicitly provided a date or time, DO NOT invent one.
  - Omit the tour_date argument entirely.
  - The application will automatically display a calendar/date picker when tour_date is omitted.
CONVERSATION GUIDELINES:
- Engage naturally: warmly greet users, handle small talk, answer general questions about HousePadi, and guide users smoothly without forcing premature tool execution unless their intent clearly calls for it.
- If required info is missing for a tool OTHER than book_tour_worker, ask specifically for THAT missing
  field — don't default to asking about location unless location is actually what's missing.
- If a tool returns no results, explain that clearly to the user.
- ALWAYS respond in complete, conversational sentences. NEVER reply with single words like "Done." or "Okay."
"""

ALL_TOOLS: List[BaseTool] = [
    search_properties_worker, trigger_property_ui_worker,
    book_tour_worker, list_tours_worker, manage_tour_request_worker,
    manage_application_worker, submit_application_worker,
    get_wallet_balance_worker, process_payment_worker, split_payment_worker,
    submit_kyc_worker, get_kyc_status_worker, approve_kyc_worker,
    create_chat_thread_worker, send_message_worker, get_messages_worker, list_threads_worker,
    create_property_worker,
]
TOOLS_BY_NAME = {t.name: t for t in ALL_TOOLS}

_lightweight_model = ChatGroq(
    model=LIGHTWEIGHT_MODEL_NAME,
    api_key=SecretStr(os.getenv("GROQ_API_KEY") or ""),
    temperature=0,
    max_retries=3,
    model_kwargs={"parallel_tool_calls": False},
)

# --- Dedicated tool-selector embedding model ---

_TOOL_SELECTOR_MODEL_NAME = "multi-qa-MiniLM-L6-cos-v1"
_tool_selector_model: Optional[SentenceTransformer] = None
_tool_selector_lock = threading.Lock()


def _get_tool_selector_model() -> SentenceTransformer:
    global _tool_selector_model
    if _tool_selector_model is None:
        with _tool_selector_lock:
            if _tool_selector_model is None:
                logger.info(f"Loading tool-selector model '{_TOOL_SELECTOR_MODEL_NAME}'...")
                _tool_selector_model = SentenceTransformer(_TOOL_SELECTOR_MODEL_NAME)
    return _tool_selector_model


TOP_K_TOOLS = 5
_tool_embeddings_cache: Optional[Dict[str, list]] = None


def _compute_tool_embeddings() -> Dict[str, list]:
    model = _get_tool_selector_model()
    names = list(TOOLS_BY_NAME.keys())
    descriptions = [(TOOLS_BY_NAME[n].description or n) for n in names]
    vectors = model.encode(descriptions)
    return {name: vec.tolist() for name, vec in zip(names, vectors)}


async def _get_tool_embeddings() -> Dict[str, list]:
    global _tool_embeddings_cache
    if _tool_embeddings_cache is None:
        _tool_embeddings_cache = await asyncio.to_thread(_compute_tool_embeddings)
    return _tool_embeddings_cache


def _cosine(a: list, b: list) -> float:
    a_arr, b_arr = np.array(a), np.array(b)
    denom = np.linalg.norm(a_arr) * np.linalg.norm(b_arr)
    return float(np.dot(a_arr, b_arr) / denom) if denom else 0.0


async def select_tools_for_message(message: str, top_k: int=TOP_K_TOOLS) -> List[Tuple[BaseTool, float]]:
    tool_embeddings = await _get_tool_embeddings()
    msg_embedding = await asyncio.to_thread(lambda: _get_tool_selector_model().encode(message).tolist())

    scored = [
        (t, _cosine(msg_embedding, tool_embeddings[t.name]))
        for t in ALL_TOOLS
    ]
    scored.sort(key=lambda x: x[1], reverse=True)

    ranked = [(t.name, round(score, 3)) for t, score in scored]
    logger.info(f"[TOOL_SELECT] '{message[:60]}' -> {ranked}")

    return scored[:top_k]

# --- UI Fallback Registry ---


@dataclass
class ToolUIFallback:
    ui_component: str
    action: str
    passthrough_fields: List[str] = field(default_factory=list)
    uuid_fields: List[str] = field(default_factory=list)
    prompt_message: str = "I need a bit more information to continue — please use the panel below."
    force_threshold: float = 0.4


TOOL_UI_FALLBACKS: Dict[str, ToolUIFallback] = {
    "book_tour_worker": ToolUIFallback(
        ui_component="calendar_picker",
        action="book_tour",
        passthrough_fields=["property_id"],
        uuid_fields=["property_id"],
        prompt_message="I'd be happy to help schedule a tour. Please select a valid property or pick your preferred date and time:",
        force_threshold=0.35,
    ),
    
    "submit_application_worker": ToolUIFallback(
    ui_component="lease_application_signer",
    action="lease_ui",
    passthrough_fields=["property_id"],
    uuid_fields=["property_id"],
    prompt_message="Let's get your rental application started — please review and sign below:",
    force_threshold=0.4,
    ),
}


UI_FALLBACK_TOOL_NAME = "ui_fallback_worker"


def _build_ui_fallback_message(call: ToolCall, fallback: ToolUIFallback) -> Tuple[str, ToolMessage]:
    """
    Builds the synthetic tool result the frontend renders as a fallback
    widget (calendar picker, lease signer, etc).

    The ToolMessage name is always the fixed UI_FALLBACK_TOOL_NAME — never
    derived from fallback.action — so _clean_tool_key always resolves it
    to the same `data.ui_fallback` key regardless of which fallback fired.
    The frontend discriminates which widget to render using payload["action"]
    (e.g. "book_tour" vs "lease_ui"), which was already being sent — it just
    used to be redundantly (and inconsistently) baked into the key name too.
    """
    args = call.get("args") or {}
    payload: Dict[str, Optional[str]] = {"ui_component": fallback.ui_component, "action": fallback.action}
    for field_name in fallback.passthrough_fields:
        raw_val = args.get(field_name)
        if field_name in fallback.uuid_fields:
            payload[field_name] = raw_val if is_valid_uuid(raw_val) else None
        else:
            payload[field_name] = raw_val

    tool_msg = ToolMessage(
        content=json.dumps(payload),
        tool_call_id=call["id"],
        name=UI_FALLBACK_TOOL_NAME,
    )
    return fallback.prompt_message, tool_msg

# --- Helpers & Field Extraction ---


_BEDROOM_RE = re.compile(r"(\d+)\s*-?\s*bed(room)?s?", re.I)
_PRICE_RE = re.compile(r"(?:under|below|max(?:imum)?)\s*[₦$]?\s*([\d,.]+\s*[kKmM]?)")
_LOCATION_RE = re.compile(r"(?:in|near|at)\s+([a-zA-Z\s]{2,30})", re.I)


def is_valid_uuid(val: object) -> bool:
    if not val or not isinstance(val, str):
        return False
    try:
        parsed = uuid.UUID(val)
        val_lower = val.lower()
        # Reject common LLM mock/placeholder patterns
        if "12345678" in val_lower or parsed.int == 0:
            return False
        return True
    except (ValueError, AttributeError):
        return False


def _arg_is_grounded_in_conversation(field_name: str, value: object, messages: List[dict]) -> bool:
    if _is_empty_value(value):
        return False

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return False

        if is_valid_uuid(text):
            # A UUID must actually appear somewhere in the conversation history (e.g., search results)
            for msg in messages:
                content = ""
                if isinstance(msg, dict):
                    content = str(msg.get("content", ""))
                else:
                    content = str(getattr(msg, "content", ""))
                
                if text in content:
                    return True
            return False  # UUID is syntactically valid but never came from search results/history

        user_contents = []
        for msg in reversed(messages):
            if isinstance(msg, dict):
                role = msg.get("role")
                content = msg.get("content")
            else:
                role = getattr(msg, "type", None) or getattr(msg, "role", None)
                content = getattr(msg, "content", None)

            if role in ("user", "human") and isinstance(content, str):
                user_contents.append(content)
                if len(user_contents) >= 3:
                    break

        if _looks_like_datetime_field(field_name):
            latest_user_content = user_contents[0] if user_contents else ""
            has_date_match = bool(_DATETIME_RE.search(latest_user_content))
            has_time_match = bool(_SPECIFIC_TIME_RE.search(latest_user_content))

            if not has_date_match:
                return False

            has_specific_time_in_val = bool(re.search(r"\b\d{1,2}:\d{2}\b|T\d{2}:\d{2}", text))
            if has_specific_time_in_val and not has_time_match:
                return False

            return True

        lowered = text.lower()
        for content in user_contents:
            if lowered in content.lower():
                return True
        return False

    return True


def try_rule_based_search(text: str, top_tool: BaseTool) -> Optional[dict]:
    if top_tool.name != "search_properties_worker":
        return None

    location_match = _LOCATION_RE.search(text)
    if not location_match:
        return None

    bedroom_match = _BEDROOM_RE.search(text)
    price_match = _PRICE_RE.search(text)

    return {
        "location": location_match.group(1).strip(),
        "bedrooms": int(bedroom_match.group(1)) if bedroom_match else None,
        "base_price": price_match.group(1).replace(",", "") if price_match else None,
    }


_DATETIME_RE = re.compile(
    r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b|"
    r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b|"
    r"\b\d{1,2}:\d{2}(?::\d{2})?(?:\s?[ap]\.?m\.?)?\b|"
    r"\b\d{1,2}\s?[ap]\.?m\.?\b|"
    r"\b\d{1,2}\s?o'?clock\b|"
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b|"
    r"\b(?:today|tomorrow|yesterday|tonight|this morning|this evening|next week|next month|next year)\b",
    re.I,
)

_SPECIFIC_TIME_RE = re.compile(
    r"\b\d{1,2}(?::\d{2})?\s*(?:[ap]\.?m\.?|o'?clock)\b|\b\d{1,2}:\d{2}\b|\b(?:morning|afternoon|evening|night)\b",
    re.I,
)


def _looks_like_datetime_field(name: str) -> bool:
    name = name.lower()
    return (
        name in {"tour_date", "preferred_date_time", "date", "time"}
        or name.endswith("date")
        or name.endswith("time")
    )


def _is_identifier_field(name: str) -> bool:
    name = name.lower()
    return name.endswith("_id") or name in {"id", "thread_id", "user_id"}


def _friendly_arg_name(name: str) -> str:
    if name == "property_id":
        return "the property"
    if name in {"tour_date", "preferred_date_time"}:
        return "the preferred date/time"
    if name.endswith("_id"):
        return name[:-3].replace("_", " ")
    return name.replace("_", " ")


def _tool_requires_user_context(tool: BaseTool) -> bool:
    func = getattr(tool, "coroutine", None) or getattr(tool, "func", None)
    if func:
        try:
            sig = inspect.signature(func)
            if "config" in sig.parameters:
                return True
        except ValueError:
            pass

    schema = getattr(tool, "args_schema", None)
    if schema is None:
        return False

    model_fields = getattr(schema, "model_fields", None) or getattr(schema, "__fields__", None)
    if not model_fields:
        return False

    return "user_id" in model_fields or "userId" in model_fields


def _is_empty_value(value: object) -> bool:
    return value in (None, "", [], {}, set())



def _remove_ungrounded_datetime_args(args: dict, messages: List[dict]) -> dict:
    cleaned = dict(args)

    for key in list(cleaned.keys()):
        if (
            _looks_like_datetime_field(key)
            and not _arg_is_grounded_in_conversation(
                key,
                cleaned[key],
                messages,
            )
        ):
            logger.info(
                f"[GROUNDING] Removing hallucinated datetime: {key}={cleaned[key]}"
            )
            cleaned.pop(key)

    return cleaned


def _get_missing_or_unconfirmed_args(tool, args, messages):
    schema = getattr(tool, "args_schema", None)
    if schema is None:
        return []

    model_fields = getattr(schema, "model_fields", None) or getattr(schema, "__fields__", None) or {}
    missing = []

    for field_name, field_info in model_fields.items():
        if field_name in {"user_id", "thread_id"}:
            continue

        required = False
        if hasattr(field_info, "is_required"):
            required = field_info.is_required()
        elif getattr(field_info, "required", False):
            required = True

        value = args.get(field_name)

        if _is_empty_value(value):
            if required:
                missing.append(_friendly_arg_name(field_name))
            continue

        if _is_identifier_field(field_name):
            if field_name == "property_id" or field_name.endswith("_id"):
                if not is_valid_uuid(value):
                    missing.append(_friendly_arg_name(field_name))
            continue

        if not _arg_is_grounded_in_conversation(field_name, value, messages):
            missing.append(_friendly_arg_name(field_name))

    return missing

# --- Tier 2: Agent Execution ---


# Tools scoring within this margin of the top score are considered "tied" —
# ranking noise at this scale isn't a reliable enough signal to force a
# specific tool over the model's own judgment.
_FORCE_CLUSTER_MARGIN = 0.05


def _find_force_candidate(ranked: List[Tuple[BaseTool, float]]) -> Optional[str]:
    if not ranked:
        return None

    top_score = ranked[0][1]
    tied_leaders = [t for t, s in ranked if s >= top_score - _FORCE_CLUSTER_MARGIN]

    if len(tied_leaders) > 1:
        logger.info(
            f"[LIGHTWEIGHT] Ranking too ambiguous to force "
            f"({len(tied_leaders)} tools within {_FORCE_CLUSTER_MARGIN} of top score {top_score:.3f}: "
            f"{[t.name for t in tied_leaders]}) — leaving tool_choice to the model."
        )
        return None

    top_tool = tied_leaders[0]
    if top_tool.name.startswith(("search_", "get_", "list_")) and top_score >= _FORCE_CLUSTER_MARGIN:
        return top_tool.name

    fallback = TOOL_UI_FALLBACKS.get(top_tool.name)
    if fallback and top_score >= fallback.force_threshold:
        return top_tool.name

    return None


async def invoke_lightweight_agent(
    messages: List[dict],
    thread_id: Optional[str]=None,
    user_context: Optional[dict]=None,
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
    
    
    intent = await dynamic_intent_router(last_user_msg)
    is_informational = (intent == "supervisor")

    active_tools_with_scores = await select_tools_for_message(last_user_msg)
    active_tools = [t for t, _ in active_tools_with_scores]
    top_tool = active_tools_with_scores[0][0] if active_tools_with_scores else None

    fast_path_args = None
    
    if not is_informational:
        fast_path_args = try_rule_based_search(last_user_msg, top_tool) if top_tool else None
        
    if fast_path_args:
        logger.info(f"[LIGHTWEIGHT] Fast-path search: {fast_path_args}")
        tool = cast(BaseTool, search_properties_worker)
        tool_result = await tool.ainvoke(fast_path_args, config=tool_config)

        content_str = tool_result if isinstance(tool_result, str) else json.dumps(tool_result)

        try:
            res_data = tool_result if isinstance(tool_result, (dict, list)) else json.loads(content_str)

            if isinstance(res_data, list):
                props = res_data
            elif isinstance(res_data, dict):
                props = (
                    res_data.get("properties")
                    or (res_data.get("data", {}).get("properties") if isinstance(res_data.get("data"), dict) else [])
                    or []
                )
            else:
                props = []

            count = len(props) if isinstance(props, list) else 0

            if count == 0:
                summary = "I couldn't find any properties matching your criteria."
            elif count == 1:
                summary = "I found 1 property matching your search:"
            else:
                summary = f"I found {count} properties matching your search:"
        except Exception as parse_err:
            logger.error(f"[LIGHTWEIGHT FAST-PATH PARSE ERROR] {parse_err}")
            summary = "Here are the properties matching your search:"

        tool_msg = ToolMessage(content=content_str, tool_call_id="fast-path", name="search_properties_worker")
        ai_msg = AIMessage(content=summary)

        return {"messages": convert_to_messages(messages) + [tool_msg, ai_msg]}

    budget = budget_for_model(LIGHTWEIGHT_MODEL_NAME)
    trimmed = trim_conversation_history(messages, max_tokens=budget)

    full_messages = convert_to_messages(
        [{"role": "system", "content": LIGHTWEIGHT_SYSTEM_PROMPT}]
    ) + trimmed

    logger.info(f"[LIGHTWEIGHT] Semantically selected tools: {[t.name for t in active_tools]}")

    if is_informational:
        logger.info(f"[LIGHTWEIGHT] Informational query detected (intent: {intent}). Bypassing tool binding.")
        dynamic_model = _lightweight_model
    else:
        force_tool_name = _find_force_candidate(active_tools_with_scores)
        if force_tool_name:
            logger.info(f"[LIGHTWEIGHT] Forcing tool '{force_tool_name}' from ranked candidates")
            dynamic_model = _lightweight_model.bind_tools(active_tools, tool_choice=force_tool_name)
        else:
            dynamic_model = _lightweight_model.bind_tools(active_tools)

    try:
        response = await dynamic_model.ainvoke(full_messages, config=tool_config)
        # Remove hallucinated datetime arguments before validation.
        for call in response.tool_calls:
            call["args"] = _remove_ungrounded_datetime_args(
                call.get("args", {}),
                messages,
            )
    except Exception as e:
        error_str = str(e)
        if "tool_use_failed" in error_str and "failed_generation" in error_str:
            logger.warning("[LIGHTWEIGHT] Intercepted tool_use_failed. Attempting recovery from failed_generation string.")

            match = re.search(r"<function=([^>]+)>(.*)", error_str, re.DOTALL)
            if match:
                tool_name = match.group(1).strip()
                raw_payload = match.group(2).strip()

                json_match = re.search(r"(\{.*\})", raw_payload, re.DOTALL)
                if json_match:
                    clean_json_str = json_match.group(1).strip()
                    try:
                        parsed_args = json.loads(clean_json_str)
                        call_id = f"call_fallback_{uuid.uuid4().hex[:8]}"
                        logger.info(f"[LIGHTWEIGHT RECOVERY] Successfully recovered '{tool_name}' with args: {parsed_args}")

                        response = AIMessage(
                            content="",
                            tool_calls=[{"name": tool_name, "args": parsed_args, "id": call_id}]
                        )
                    except Exception as parse_err:
                        logger.error(f"[LIGHTWEIGHT RECOVERY FAILED] Could not parse JSON args: {parse_err}")
                        return {
                            "messages": trimmed + [
                                AIMessage(content="I couldn't process that tool request automatically. Could you please specify your preferred date and time?")
                            ]
                        }
                else:
                    return {
                        "messages": trimmed + [
                            AIMessage(content="I'm having trouble understanding those parameters. Please try rephrasing your request.")
                        ]
                    }
            else:
                return {
                    "messages": trimmed + [
                        AIMessage(content="Something went wrong while setting up your request. Please try again.")
                    ]
                }
        else:
            raise e

    logger.info(f"[DEBUG] Tool calls triggered: {response.tool_calls}")
    logger.info(f"[DEBUG] Initial text response: {response.content}")

    if not response.tool_calls:
        content_lower = str(response.content).lower()
        if "function" in content_lower and "not available" in content_lower:
            logger.warning("[LIGHTWEIGHT] Caught tool hallucination. Forcing fallback.")
            response.content = "I'm having trouble pulling up that specific action. Could you rephrase what you're trying to do?"
        return {"messages": trimmed + [response]}

    tool_messages = []
    for call in response.tool_calls:
        tool = TOOLS_BY_NAME.get(call["name"])
        if not tool:
            tool_messages.append(ToolMessage(content=f"Unknown tool: {call['name']}", tool_call_id=call["id"]))
            continue

        missing_args = _get_missing_or_unconfirmed_args(tool, call.get("args", {}) or {}, messages)
        if missing_args:
            logger.info(
                f"[LIGHTWEIGHT] Blocking tool '{call['name']}' because required args are missing or not grounded: {missing_args}"
            )

            fallback = TOOL_UI_FALLBACKS.get(call["name"])
            if fallback:
                prompt_message, synthetic_tool_msg = _build_ui_fallback_message(call, fallback)
                response.content = prompt_message
                response.tool_calls = []  # Clears hallucinated tool call payload before returning to UI
                return {"messages": trimmed + [response, synthetic_tool_msg]}

            response.content = (
                f"I can help with that, but I still need {', '.join(missing_args)} from you before I can proceed."
            )
            response.tool_calls = []
            return {"messages": trimmed + [response]}

        result = await tool.ainvoke(call["args"], config=tool_config)
        result_text = str(result).lower()
        if not user_context and "user context missing" in result_text and _tool_requires_user_context(tool):
            existing_content = str(response.content or "").strip()
            logger.info(
                "[LIGHTWEIGHT] Missing user context for auth-required tool; preserving the agent's response if one exists."
            )
            response.content = existing_content or "To continue, please sign in first."
            response.tool_calls = []
            return {"messages": trimmed + [response]}

        logger.info(f"[DEBUG] Tool '{call['name']}' returned: {result}")

        content_str = result if isinstance(result, str) else json.dumps(result)
        tool_msg = ToolMessage(
    content=content_str,
    tool_call_id=call["id"],
    name=call["name"],
)

        tool_messages.append(tool_msg)

        if call["name"] in WRITE_TOOLS:
            logger.info(
                f"[LIGHTWEIGHT] Returning authoritative tool output for {call['name']}"
            )

            # Ensure response.content isn't empty so chat_service doesn't pick up previous turn's text
            if not str(response.content).strip():
                parsed_res = None
                if isinstance(content_str, str):
                    try:
                        parsed_res = json.loads(content_str)
                    except Exception:
                        # Plain text string returned by tool
                        parsed_res = content_str
                else:
                    parsed_res = content_str

                # Dynamically set response.content based on return type
                if isinstance(parsed_res, dict):
                    # e.g., UI components like {"status": "awaiting_datetime", "message": "..."}
                    response.content = parsed_res.get("message", "Action completed successfully.")
                elif isinstance(parsed_res, str) and parsed_res.strip():
                    # e.g., "Success: Tour scheduled for 2026-08-07 16:55..."
                    response.content = parsed_res
                else:
                    response.content = "Action completed successfully."

            return {
                "messages": trimmed + [response] + [tool_msg]
            }
    # Final Turn using UNBOUND raw model
    final = await _lightweight_model.ainvoke(full_messages + [response] + tool_messages, config=tool_config)

    final_text = str(final.content or "").strip()

    if tool_messages:
        last_tool_msg = tool_messages[-1]
        content_str = str(last_tool_msg.content)

        if not final_text or "I hope this helps" in final_text:
            logger.warning("[LIGHTWEIGHT] Overriding generic/empty final LLM turn with clean tool output.")
            if "Database Interface Exception" in content_str or "22P02" in content_str or "Execution Error" in content_str:
                final.content = "I couldn't process that tour request because the property details were missing or invalid. Please select a property to continue."
            elif last_tool_msg.name == "search_properties_worker":
                final.content = "Here are the properties matching your request:"
            elif not content_str.startswith("{"):
                final.content = content_str
            else:
                final.content = "Action processed successfully."

    logger.info(f"[DEBUG] Final text response: {final.content}")

    return {"messages": trimmed + [response] + tool_messages + [final]}
