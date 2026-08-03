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

from app.intent_transformer import dynamic_intent_router
from app.services.chat_persistence import load_thread_history_from_db, save_message_to_db
from app.services.lightweight_agent import invoke_lightweight_agent
from app.services.token_budget import trim_conversation_history
from app.ui_registry import INTENT_UI_MAP
from app.ui_registry import INTENT_UI_MAP

logger = logging.getLogger("uvicorn")

AGENT_BACKEND = os.getenv("AGENT_BACKEND", "lightweight").strip().lower()
INTENT_CONTENT_MAP = {
    "TRIGGER_PROPERTY_UI": "Opening the property listing form for you...",
    "TRIGGER_PAYMENT_UI": "Taking you to your payments page...",
    "TRIGGER_KYC_UI": "Opening identity verification..."
}


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
    if not thread_id:
        thread_id = str(uuid.uuid4())

    user_id = user_context.get("user_id") if user_context else None
    user_role = user_context.get("role") if user_context else "renter"
    
    # 1. Dynamically check if detected intent maps to a UI route
    detected_intent = dynamic_intent_router(message)
    if detected_intent in INTENT_UI_MAP:
        await save_message_to_db(thread_id=thread_id, content=message, is_ai_response=False, sender_id=user_id)
        
        target_url = INTENT_UI_MAP[detected_intent]
        response_content = INTENT_CONTENT_MAP.get(detected_intent, "Redirecting...")
        
        redirect_payload = {
            "type": "redirect",
            "content": response_content,
            "redirect_url": target_url
        }
        
        await save_message_to_db(thread_id=thread_id, content=response_content, is_ai_response=True)
        return redirect_payload

    # 2. Load multi-turn conversation history from Supabase tables
    raw_history = await load_thread_history_from_db(thread_id)
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
    msgs = raw_result.get("messages", [])
    data = None
    redirect_url = None
    content = None

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

    for m in reversed(msgs):
        if getattr(m, "type", None) == "ai" and getattr(m, "content", None):
            content = m.content
            break

    if not content:
        content = f"Found {len(data)} matching properties." if data else "Done."

    if redirect_url:
        return {"type": "redirect", "content": content, "redirect_url": redirect_url}

    return {"type": "response", "content": content, "data": data}