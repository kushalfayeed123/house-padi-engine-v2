"""
Channel-agnostic chat entry point.

Used today by the web /api/chat route (chat_routes.py). When the WhatsApp
integration lands, its webhook handler should call process_chat_message()
the same way — resolve the WhatsApp phone number to a user_context and a
stable thread_id upstream, pass them in here, nothing else changes.

Backend selection is one env var:
  AGENT_BACKEND=lightweight   (default) — single-call tool-use, cheapest on rate limits
  AGENT_BACKEND=multi_agent              — original deepagents supervisor/sub-agent graph

NOTE: this file is a best-effort reconstruction of the chat_service module
chat_routes.py imports from, since it wasn't included in what was shared —
if your real chat_service.py does more than this (e.g. persists chat
threads to Supabase), fold that logic back in around process_chat_message.
"""
import json
import logging
import os
from typing import Optional
import uuid

from app.services.chat_persistence import load_thread_history_from_db, save_message_to_db
from app.services.lightweight_agent import invoke_lightweight_agent
from app.services.token_budget import trim_conversation_history

logger = logging.getLogger("uvicorn")

AGENT_BACKEND = os.getenv("AGENT_BACKEND", "lightweight").strip().lower()


def _get_multi_agent_invoker():
    # Imported lazily so the lightweight backend can run even if the
    # deepagents graph has an import-time issue — keeps the "just flip the
    # env var back" revert path cheap and isolated.
    from app.agent_engine import invoke_housepadi_agent

    return invoke_housepadi_agent


async def process_chat_message(
    message: str,
    thread_id: Optional[str] = None,
    user_context: Optional[dict] = None,
) -> dict:
    # Generate a stable thread ID if none is provided by the client
    if not thread_id:
        thread_id = str(uuid.uuid4())

    user_id = user_context.get("user_id") if user_context else None
    user_role = user_context.get("role") if user_context else "renter"

    # 1. Load multi-turn conversation history from Supabase tables
    raw_history = await load_thread_history_from_db(thread_id)
    
    # 2. Append the new incoming user message to history
    raw_history.append({"role": "user", "content": message})

    # 3. Persist incoming user message to the 'messages' table
    await save_message_to_db(
        thread_id=thread_id,
        content=message,
        is_ai_response=False,
        sender_id=user_id,
        renter_id=user_id if user_role != "owner" else None,
        owner_id=user_id if user_role == "owner" else None
    )

    # 4. Trim history to fit token budget constraints
    trimmed_messages = trim_conversation_history(raw_history, max_tokens=4000)

    # Convert LangChain BaseMessages back to List[dict] for the agent backend
    role_mapping = {"human": "user", "ai": "assistant", "tool": "tool", "system": "system"}
    agent_messages = [
        {
            "role": role_mapping.get(m.type, "user"),
            "content": m.content
        }
        for m in trimmed_messages
    ]

    # 5. Invoke the selected agent backend
    if AGENT_BACKEND == "multi_agent":
        logger.info("[CHAT_SERVICE] Using multi_agent backend")
        invoke = _get_multi_agent_invoker()
        raw_result = await invoke(agent_messages, thread_id=thread_id, user_context=user_context)
    else:
        logger.info("[CHAT_SERVICE] Using lightweight backend")
        raw_result = await invoke_lightweight_agent(agent_messages, thread_id=thread_id, user_context=user_context)

    # 6. Extract the final assistant reply
    normalized = _normalize_agent_output(raw_result)
    ai_response_content = normalized.get("content", "")

    # 7. Persist the AI assistant's response to the 'messages' table
    if ai_response_content:
        await save_message_to_db(
            thread_id=thread_id,
            content=ai_response_content,
            is_ai_response=True
        )

    return normalized


def _normalize_agent_output(raw_result: dict) -> dict:
    """
    Both backends return {"messages": [...]} (a LangChain message list).
    Reduce that down to what chat_routes.py / ChatResponse expects:
    {"type": "response"|"redirect", "content": str, "data": list|None, "redirect_url": str|None}
    """
    msgs = raw_result.get("messages", [])

    data = None
    redirect_url = None
    content = None

    # Scan tool results for structured payloads: a JSON list is treated as
    # search results (surfaced to the frontend as `data`); a JSON object
    # with a redirect_url key triggers a redirect response.
    for m in msgs:
        m_type = getattr(m, "type", None)
        m_content = getattr(m, "content", None)
        if m_type != "tool" or not m_content:
            continue
        try:
            parsed = json.loads(m_content)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, list):
            data = parsed
        elif isinstance(parsed, dict) and "redirect_url" in parsed:
            redirect_url = parsed["redirect_url"]

    # Last AI message is the natural-language reply, if the backend
    # produced one (the fast path doesn't — see fallback below).
    for m in reversed(msgs):
        if getattr(m, "type", None) == "ai" and getattr(m, "content", None):
            content = m.content
            break

    if not content:
        content = f"Found {len(data)} matching properties." if data else "Done."

    if redirect_url:
        return {"type": "redirect", "content": content, "redirect_url": redirect_url}

    return {"type": "response", "content": content, "data": data}