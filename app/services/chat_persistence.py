import asyncio
from typing import List, Optional
from logging import getLogger
from app.core.database import supabase_client

logger = getLogger("uvicorn")

# Bounds the DB fetch regardless of how long a thread has been running —
# relevant once WhatsApp threads can stay open for weeks/months. This is
# intentionally larger than trim_conversation_history's 4000-token budget
# leaves room for, so trimming still has real history to choose from; it
# just stops the DB call itself from growing unbounded with thread age.
DEFAULT_HISTORY_FETCH_LIMIT = 40


async def load_thread_history_from_db(thread_id: str, limit: int = DEFAULT_HISTORY_FETCH_LIMIT) -> List[dict]:
    """
    Fetches the most recent `limit` messages for a thread, oldest-first,
    mapped to standard LLM chat role formats.
    """
    try:
        res = await asyncio.to_thread(
            lambda: supabase_client.table("messages")
            .select("content, is_ai_response, created_at")
            .eq("thread_id", thread_id)
            .order("created_at", desc=True)  # newest first, so LIMIT keeps the most recent messages
            .limit(limit)
            .execute()
        )

        history = []
        if res.data:
            for msg in reversed(res.data):  # back to chronological order for the model
                if isinstance(msg, dict):
                    is_ai = bool(msg.get("is_ai_response"))
                    content = msg.get("content")
                else:
                    is_ai = False
                    content = None

                if content is None:
                    continue

                role = "assistant" if is_ai else "user"
                history.append({"role": role, "content": content})
        return history
    except Exception as e:
        logger.error(f"[CHAT DB LOAD ERROR] {str(e)}")
        return []


async def save_message_to_db(
    thread_id: str,
    content: str,
    is_ai_response: bool,
    sender_id: Optional[str] = None,
    property_id: Optional[str] = None,
    owner_id: Optional[str] = None,
    renter_id: Optional[str] = None,
    upsert_thread: bool = True,
) -> None:
    """
    Appends a message to 'messages', ensuring the parent 'chat_threads' row
    exists first — unless upsert_thread=False, for callers that know the
    thread was already touched earlier in the same turn (e.g. the
    assistant-reply save right after the user-message save).
    """
    try:
        if upsert_thread:
            thread_payload = {
                "id": thread_id,
                "last_message_at": "now()"
            }
            if property_id:
                thread_payload["property_id"] = property_id
            if owner_id:
                thread_payload["owner_id"] = owner_id
            if renter_id:
                thread_payload["renter_id"] = renter_id

            await asyncio.to_thread(
                lambda: supabase_client.table("chat_threads")
                .upsert(thread_payload, on_conflict="id")
                .execute()
            )

        message_payload = {
            "thread_id": thread_id,
            "sender_id": sender_id if not is_ai_response else None,
            "content": content,
            "is_ai_response": is_ai_response
        }

        await asyncio.to_thread(
            lambda: supabase_client.table("messages")
            .insert(message_payload)
            .execute()
        )

    except Exception as e:
        logger.error(f"[CHAT DB SAVE ERROR] {str(e)}")