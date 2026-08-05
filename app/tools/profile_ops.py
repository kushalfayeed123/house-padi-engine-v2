import json
from typing import Any, Dict, Optional
import asyncio
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from app.core.database import supabase_client
from pydantic import BaseModel, Field
from logging import getLogger

logger = getLogger("uvicorn")

# ==========================================
# 1. GET USER PROFILE TOOL
# ==========================================
class GetUserProfileInput(BaseModel):
    pass

@tool("get_user_profile_worker", args_schema=GetUserProfileInput)
async def get_user_profile_worker(config: RunnableConfig) -> str:
    """Retrieves the profile details (KYC status, role, names, phone, avatar) of the authenticated user."""
    safe_config = config or {}
    user_id = safe_config.get("configurable", {}).get("user_id")
    
    if not user_id:
        return "Security Guardrail: Identity context missing."

    try:
        res = await asyncio.to_thread(
            supabase_client.table("profiles")
            .select("*")
            .eq("id", user_id)
            .single()
            .execute
        )
        
        if not res.data:
            return json.dumps({"error": "Profile not found for this user."})
            
        return json.dumps(res.data)
        
    except Exception as e:
        logger.error(f"[GET PROFILE ERROR] {str(e)}")
        return json.dumps({"error": str(e)})


# ==========================================
# 2. UPDATE USER PROFILE TOOL
# ==========================================
class UpdateUserProfileInput(BaseModel):
    first_name: Optional[str] = Field(None, description="User's first name.")
    last_name: Optional[str] = Field(None, description="User's last name.")
    phone_number: Optional[str] = Field(None, description="User's contact phone number.")
    avatar_url: Optional[str] = Field(None, description="URL to the user's avatar image.")

@tool("update_user_profile_worker", args_schema=UpdateUserProfileInput)
async def update_user_profile_worker(
    config: RunnableConfig,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    phone_number: Optional[str] = None,
    avatar_url: Optional[str] = None
) -> str:
    """Updates the authenticated user's profile details (first name, last name, phone number, avatar)."""
    safe_config = config or {}
    user_id = safe_config.get("configurable", {}).get("user_id")
    
    if not user_id:
        return "Security Guardrail: Identity context missing."

    update_data = {}
    if first_name is not None:
        update_data["first_name"] = first_name
    if last_name is not None:
        update_data["last_name"] = last_name
    if phone_number is not None:
        update_data["phone_number"] = phone_number
    if avatar_url is not None:
        update_data["avatar_url"] = avatar_url

    if not update_data:
        return "No valid profile fields provided for update."

    try:
        await asyncio.to_thread(
            supabase_client.table("profiles")
            .update(update_data)
            .eq("id", user_id)
            .execute
        )
        
        return f"Success: Profile successfully updated for user {user_id}."
        
    except Exception as e:
        logger.error(f"[UPDATE PROFILE ERROR] {str(e)}")
        return f"Database error during profile update: {str(e)}"