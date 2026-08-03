import asyncio
from typing import List, Optional
from app.database import supabase_client
from logging import getLogger

logger = getLogger("uvicorn")


async def load_thread_history_from_db(thread_id: str) -> List[dict]:
    """
    Fetches stored message history for a given thread ID from the 
    'messages' table, mapped to standard LLM chat role formats.
    """
    try:
        res = await asyncio.to_thread(
            lambda: supabase_client.table("messages")
            .select("content, is_ai_response, created_at")
            .eq("thread_id", thread_id)
            .order("created_at", desc=False)
            .execute()
        )
        
        history = []
        if res.data:
            for msg in res.data:
                # guard against non-dict rows to satisfy static type checkers
                if isinstance(msg, dict):
                    is_ai = bool(msg.get("is_ai_response"))
                    content = msg.get("content")
                else:
                    is_ai = False
                    content = None

                if content is None:
                    # skip malformed rows
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
    renter_id: Optional[str] = None
) -> None:
    """
    Ensures the chat thread exists in 'chat_threads' and appends 
    the new message to the 'messages' table.
    """
    try:
        # 1. Upsert thread record to maintain relation constraints
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

        # 2. Insert the message into the messages table
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