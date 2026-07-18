"""
Chat management tool
Handles messaging between renters, landlords, and AI supervisor agent
"""

import json
from typing import Optional, List
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from app.database import supabase_client, db
from logging import getLogger
from datetime import datetime
from uuid import UUID

logger = getLogger("uvicorn")


class CreateThreadInput(BaseModel):
    property_id: Optional[str] = Field(None, description="Property UUID if property-related")
    recipient_id: Optional[str] = Field(None, description="UUID of person to message")


class SendMessageInput(BaseModel):
    thread_id: str = Field(..., description="Chat thread UUID")
    content: str = Field(..., description="Message content")


class GetMessagesInput(BaseModel):
    thread_id: str = Field(..., description="Chat thread UUID")
    limit: int = Field(50, ge=1, le=200, description="Number of messages to retrieve")


@tool("create_chat_thread_worker")
async def create_chat_thread_worker(
    config: RunnableConfig,
    property_id: Optional[str] = None,
    recipient_id: Optional[str] = None
) -> str:
    """Creates a new chat thread.
    
    - Between renter and landlord (property-specific)
    - Between user and AI agent
    - Stores all messages in thread for history
    """
    user_id = config.get("configurable", {}).get("user_id")
    
    if not user_id:
        return "Security Guardrail: User context missing."

    try:
        # Check if thread already exists
        if property_id and recipient_id:
            existing_res = await db.execute(
                supabase_client.table("chat_threads")
                .select("id")
                .eq("property_id", property_id)
                .or_(f"renter_id.eq.{user_id},owner_id.eq.{user_id}")
                .execute
            )
            
            if existing_res.data:
                existing_thread_id = existing_res.data[0].get("id")
                logger.info(f"[THREAD EXISTS] Returning existing thread {existing_thread_id}")
                return json.dumps({
                    "thread_id": existing_thread_id,
                    "is_new": False,
                    "message": "Using existing chat thread"
                })
        
        # Create new thread
        thread_payload = {
            "property_id": property_id,
            "renter_id": None,
            "owner_id": None,
            "last_message_at": datetime.now().isoformat()
        }
        
        # Determine roles
        user_role = config.get("configurable", {}).get("user_role", "renter")
        if user_role == "landlord":
            thread_payload["owner_id"] = user_id
            if recipient_id:
                thread_payload["renter_id"] = recipient_id
        else:
            thread_payload["renter_id"] = user_id
            if recipient_id:
                thread_payload["owner_id"] = recipient_id
        
        thread_res = await db.execute(
            supabase_client.table("chat_threads").insert(thread_payload).execute
        )
        
        thread_id = thread_res.data[0].get("id")
        logger.info(f"[THREAD CREATED] New thread {thread_id} for user {user_id}")
        
        return json.dumps({
            "thread_id": thread_id,
            "is_new": True,
            "message": f"Chat thread created. You can now exchange messages."
        })
        
    except Exception as e:
        logger.error(f"[THREAD CREATION ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
        return f"Thread creation failure: {str(e)}"


@tool("send_message_worker")
async def send_message_worker(
    thread_id: str,
    content: str,
    config: RunnableConfig
) -> str:
    """Sends a message in a chat thread.
    
    - Stores message in database
    - Updates thread last_message_at
    - Triggers notifications
    """
    user_id = config.get("configurable", {}).get("user_id")
    
    if not user_id:
        return "Security Guardrail: User context missing."

    if not content.strip():
        return "Error: Message cannot be empty."

    try:
        # Verify thread exists and user has access
        thread_res = await db.execute(
            supabase_client.table("chat_threads")
            .select("id, renter_id, owner_id")
            .eq("id", thread_id)
            .single()
            .execute
        )
        
        if not thread_res.data:
            return f"Chat thread {thread_id} not found."
        
        thread = thread_res.data
        renter_id = thread.get("renter_id")
        owner_id = thread.get("owner_id")
        
        # Verify user is part of thread
        if user_id != renter_id and user_id != owner_id:
            return "Security Guardrail: You don't have access to this thread."
        
        # Create message record
        message_payload = {
            "thread_id": thread_id,
            "sender_id": user_id,
            "content": content,
            "is_ai_response": False,
            "created_at": datetime.now().isoformat()
        }
        
        msg_res = await db.execute(
            supabase_client.table("messages").insert(message_payload).execute
        )
        
        message_id = msg_res.data[0].get("id")
        
        # Update thread last_message_at
        await db.execute(
            supabase_client.table("chat_threads")
            .update({"last_message_at": datetime.now().isoformat()})
            .eq("id", thread_id)
            .execute
        )
        
        # Determine recipient
        recipient_id = owner_id if user_id == renter_id else renter_id
        
        # Create notification for recipient
        if recipient_id:
            try:
                notification = {
                    "recipient_id": recipient_id,
                    "type": "new_message",
                    "content": f"New message in chat: {content[:50]}...",
                    "related_thread_id": thread_id,
                    "status": "unread"
                }
                await db.execute(
                    supabase_client.table("notifications").insert(notification).execute
                )
            except Exception as e:
                logger.warning(f"Could not create notification: {str(e)}")
        
        logger.info(f"[MESSAGE SENT] Message {message_id} in thread {thread_id}")
        
        return json.dumps({
            "message_id": message_id,
            "status": "sent",
            "timestamp": datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"[MESSAGE SEND ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
        return f"Message sending failure: {str(e)}"


@tool("get_messages_worker")
async def get_messages_worker(
    thread_id: str,
    config: RunnableConfig,
    limit: int = 50
) -> str:
    """Retrieves messages from a chat thread.
    
    - Returns message history
    - Marks messages as read
    - Ordered by timestamp
    """
    user_id = config.get("configurable", {}).get("user_id")
    
    if not user_id:
        return "Security Guardrail: User context missing."

    try:
        # Verify thread exists and user has access
        thread_res = await db.execute(
            supabase_client.table("chat_threads")
            .select("id, renter_id, owner_id")
            .eq("id", thread_id)
            .single()
            .execute
        )
        
        if not thread_res.data:
            return f"Chat thread {thread_id} not found."
        
        thread = thread_res.data
        renter_id = thread.get("renter_id")
        owner_id = thread.get("owner_id")
        
        # Verify user is part of thread
        if user_id != renter_id and user_id != owner_id:
            return "Security Guardrail: You don't have access to this thread."
        
        # Fetch messages
        msg_res = await db.execute(
            supabase_client.table("messages")
            .select("id, sender_id, content, is_ai_response, created_at")
            .eq("thread_id", thread_id)
            .order("created_at", desc=False)
            .limit(limit)
            .execute
        )
        
        messages = msg_res.data if msg_res.data else []
        
        # Format response
        formatted_messages = []
        for msg in messages:
            formatted_messages.append({
                "id": msg.get("id"),
                "sender_id": msg.get("sender_id"),
                "content": msg.get("content"),
                "is_ai_response": msg.get("is_ai_response"),
                "timestamp": msg.get("created_at"),
                "sender_role": "landlord" if msg.get("sender_id") == owner_id else ("renter" if msg.get("sender_id") == renter_id else "ai")
            })
        
        logger.info(f"[MESSAGES RETRIEVED] {len(formatted_messages)} messages from thread {thread_id}")
        
        return json.dumps(formatted_messages)
        
    except Exception as e:
        logger.error(f"[MESSAGES RETRIEVAL ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
        return f"Messages retrieval failure: {str(e)}"


@tool("list_threads_worker")
async def list_threads_worker(config: RunnableConfig) -> str:
    """Lists all chat threads for the user.
    
    - Shows all conversations
    - Displays last message and timestamp
    - Shows unread message count
    """
    user_id = config.get("configurable", {}).get("user_id")
    
    if not user_id:
        return "Security Guardrail: User context missing."

    try:
        # Get all threads user is part of
        thread_res = await db.execute(
            supabase_client.table("chat_threads")
            .select("id, property_id, renter_id, owner_id, last_message_at")
            .or_(f"renter_id.eq.{user_id},owner_id.eq.{user_id}")
            .order("last_message_at", desc=True)
            .execute
        )
        
        threads = thread_res.data if thread_res.data else []
        
        # Enhance with property and unread info
        enhanced_threads = []
        for thread in threads:
            thread_info = {
                "thread_id": thread.get("id"),
                "last_message_at": thread.get("last_message_at")
            }
            
            # Add property info if available
            if thread.get("property_id"):
                prop_res = await db.execute(
                    supabase_client.table("properties")
                    .select("id, title")
                    .eq("id", thread.get("property_id"))
                    .single()
                    .execute
                )
                if prop_res.data:
                    thread_info["property"] = {
                        "id": prop_res.data.get("id"),
                        "title": prop_res.data.get("title")
                    }
            
            # Determine other participant
            if user_id == thread.get("renter_id"):
                thread_info["other_participant"] = f"Landlord ({thread.get('owner_id')})"
            else:
                thread_info["other_participant"] = f"Renter ({thread.get('renter_id')})"
            
            enhanced_threads.append(thread_info)
        
        logger.info(f"[THREADS LISTED] {len(enhanced_threads)} threads for user {user_id}")
        
        return json.dumps(enhanced_threads)
        
    except Exception as e:
        logger.error(f"[THREADS LISTING ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
        return f"Threads listing failure: {str(e)}"
