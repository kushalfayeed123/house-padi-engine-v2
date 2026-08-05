"""
Channel-agnostic chat entry point.

Backend selection is one env var:
  AGENT_BACKEND=lightweight   (default) — single-call tool-use, cheapest on rate limits
  AGENT_BACKEND=multi_agent              — deepagents supervisor/sub-agent graph

Entity-id memory (property/tour/application/user ids etc. from tool
results) is kept in cache_service, NOT the messages table — the messages
schema only has `is_ai_response: bool`, no way to mark a row as
internal-only context vs. a real chat bubble. Storing refs there would
either need a migration or leak raw JSON into the user-visible transcript.
Cache-based refs expire on their own (ENTITY_REFS_TTL_HOURS) and never
touch the DB, so the chat history a user sees stays exactly what they typed.
"""
import json
import logging
import os
from typing import Optional, Dict, List, Any
import uuid

from app.core.intent_transformer import dynamic_intent_router
from app.core.lightweight_agent import invoke_lightweight_agent
from app.core.ui_registry import INTENT_CONTENT_MAP, INTENT_UI_MAP
from app.services.chat_persistence import load_thread_history_from_db, save_message_to_db
from app.services.token_budget import trim_conversation_history
from app.services.cache_service import cache_get, cache_set


logger = logging.getLogger("uvicorn")

AGENT_BACKEND = os.getenv("AGENT_BACKEND", "lightweight").strip().lower()
MAX_REFS_PER_ENTITY_TYPE = 10
ENTITY_REFS_TTL_HOURS = 12


def _get_multi_agent_invoker():
    from app.core.agent_engine import invoke_housepadi_agent
    return invoke_housepadi_agent


def _clean_tool_key(tool_name: str) -> str:
    """Derive a display key from the tool's own name — e.g.
    'search_properties_worker' -> 'properties' — instead of a hardcoded map."""
    key = tool_name
    for prefix in ("search_", "get_", "list_", "create_", "submit_", "approve_"):
        if key.startswith(prefix):
            key = key[len(prefix):]
            break
    if key.endswith("_worker"):
        key = key[: -len("_worker")]
    return key or tool_name


def _guess_label(entity: dict) -> str:
    for field in ("title", "name", "label", "status", "description"):
        if entity.get(field):
            return str(entity[field])
    return "item"


def _extract_all_tool_data(msgs: List[Any]) -> Dict[str, Any]:
    """Aggregates every tool result in the turn, keyed by tool name — a
    turn calling two tools no longer has the second overwrite the first."""
    data: Dict[str, Any] = {}
    for m in msgs:
        if getattr(m, "type", None) != "tool" or not getattr(m, "content", None):
            continue
        key = _clean_tool_key(getattr(m, "name", None) or "result")
        try:
            data[key] = json.loads(m.content)
        except (TypeError, ValueError):
            data[key] = m.content
    return data


def _extract_entity_refs(msgs: List[Any]) -> Dict[str, List[dict]]:
    """Generic id-bearing-entity scanner across any tool result, plus any
    *_id foreign-key field on those objects (so a user id embedded inside
    e.g. an application's applicant_id is captured as its own reference)."""
    refs: Dict[str, List[dict]] = {}

    for m in msgs:
        if getattr(m, "type", None) != "tool" or not getattr(m, "content", None):
            continue
        try:
            parsed = json.loads(m.content)
        except (TypeError, ValueError):
            continue

        key = _clean_tool_key(getattr(m, "name", None) or "item")
        items = parsed if isinstance(parsed, list) else [parsed] if isinstance(parsed, dict) else []

        for item in items:
            if not isinstance(item, dict):
                continue
            label = _guess_label(item)

            if item.get("id"):
                refs.setdefault(key, [])
                if not any(r["id"] == item["id"] for r in refs[key]):
                    refs[key].append({"id": item["id"], "label": label})

            for field, val in item.items():
                if field == "id" or not field.endswith("_id") or not val:
                    continue
                sub_key = field[:-3]  # "applicant_id" -> "applicant"
                refs.setdefault(sub_key, [])
                if not any(r["id"] == val for r in refs[sub_key]):
                    refs[sub_key].append({"id": val, "label": f"{label} ({field})"})

    for k in refs:
        refs[k] = refs[k][:MAX_REFS_PER_ENTITY_TYPE]

    return refs


def _load_cached_entity_refs(thread_id: str) -> Dict[str, List[dict]]:
    raw = cache_get(f"entity_refs:{thread_id}")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def _save_cached_entity_refs(thread_id: str, refs: Dict[str, List[dict]]) -> None:
    cache_set(f"entity_refs:{thread_id}", json.dumps(refs), ttl_hours=ENTITY_REFS_TTL_HOURS)


async def process_chat_message(
    message: str,
    thread_id: Optional[str] = None,
    user_context: Optional[dict] = None,
) -> dict:
    if not thread_id:
        thread_id = str(uuid.uuid4())

    user_id = user_context.get("user_id") if user_context else None
    user_role = user_context.get("role") if user_context else "renter"

    # 1. Intent-based UI redirect shortcut
    detected_intent = dynamic_intent_router(message)
    if detected_intent in INTENT_UI_MAP:
        await save_message_to_db(thread_id=thread_id, content=message, is_ai_response=False, sender_id=user_id)

        target_url = INTENT_UI_MAP[detected_intent]
        if detected_intent == "TRIGGER_DASHBOARD_UI":
            target_url = "/dashboard/landlord" if user_role == "owner" else "/dashboard/renter"

        response_content = INTENT_CONTENT_MAP.get(detected_intent, "Redirecting...")
        redirect_payload = {"type": "redirect", "content": response_content, "redirect_url": target_url}

        await save_message_to_db(thread_id=thread_id, content=response_content, is_ai_response=True, upsert_thread=False)
        return redirect_payload

    # 2. Load DB-backed chat history (this is the user-visible transcript)
    raw_history = await load_thread_history_from_db(thread_id)
    raw_history.append({"role": "user", "content": message})

    await save_message_to_db(
        thread_id=thread_id,
        content=message,
        is_ai_response=False,
        sender_id=user_id,
        renter_id=user_id if user_role != "owner" else None,
        owner_id=user_id if user_role == "owner" else None
    )

    # 3. Trim to token budget
    trimmed_messages = trim_conversation_history(raw_history, max_tokens=4000)

    role_mapping = {"human": "user", "ai": "assistant", "tool": "tool", "system": "system"}
    agent_messages = [
        {"role": role_mapping.get(m.type, "user"), "content": m.content}
        for m in trimmed_messages
    ]

    # 4. Inject cached entity-id context — ephemeral, never touches the DB,
    # so it never appears in anyone's visible chat history, only in what
    # the model itself sees this call. Placed just before the newest user
    # message so it reads as relevant context for the current question.
    cached_refs = _load_cached_entity_refs(thread_id)
    if cached_refs:
        ref_message = {
            "role": "system",
            "content": (
                "Known entity ids from earlier in this conversation (use these "
                "exact ids for tool calls — never show an id itself to the "
                "user, refer to items by their label): " + json.dumps(cached_refs)
            ),
        }
        if agent_messages and agent_messages[-1]["role"] == "user":
            agent_messages = agent_messages[:-1] + [ref_message] + agent_messages[-1:]
        else:
            agent_messages.append(ref_message)

    # 5. Invoke the selected backend
    if AGENT_BACKEND == "multi_agent":
        logger.info("[CHAT_SERVICE] Using multi_agent backend")
        invoke = _get_multi_agent_invoker()
        raw_result = await invoke(agent_messages, thread_id=thread_id, user_context=user_context)
    else:
        logger.info("[CHAT_SERVICE] Using lightweight backend")
        raw_result = await invoke_lightweight_agent(agent_messages, thread_id=thread_id, user_context=user_context)

    msgs = raw_result.get("messages", [])

    # 6. Aggregate this turn's tool results, update the cached entity refs
    dynamic_data = _extract_all_tool_data(msgs)
    new_refs = _extract_entity_refs(msgs)
    if new_refs:
        merged_refs = {**cached_refs, **new_refs}  # new results for a key fully replace stale ones for that key
        _save_cached_entity_refs(thread_id, merged_refs)
        logger.info(f"[CHAT_SERVICE] Updated entity refs for thread {thread_id}: {list(new_refs.keys())}")

    content = None
    for m in reversed(msgs):
        if getattr(m, "type", None) == "ai" and getattr(m, "content", None):
            content = m.content
            break
    if not content:
        content = f"Found {len(dynamic_data.get('properties', []))} matching properties." if "properties" in dynamic_data else "Done."

    # 7. Persist the assistant's reply to the visible transcript (unchanged)
    if content:
        await save_message_to_db(thread_id=thread_id, content=content, is_ai_response=True, upsert_thread=False)

    return {"type": "response", "content": content, "data": dynamic_data, "thread_id": thread_id }