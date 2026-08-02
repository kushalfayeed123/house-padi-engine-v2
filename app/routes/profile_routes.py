from fastapi import APIRouter, HTTPException, Header, Request, Depends
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field
from typing import Optional, Annotated, cast
from langchain_core.tools import BaseTool
import json
import asyncio
from logging import getLogger

from app.tools.profile_ops import get_user_profile_worker, update_user_profile_worker

logger = getLogger("uvicorn")
router = APIRouter(prefix="/api/profile", tags=["Profile"])


# ==========================================
# DEPENDENCY: Strict Auth Context
# ==========================================
def get_supabase(request: Request):
    return request.app.state.system.supabase


async def get_strict_user_context(
    authorization: Annotated[Optional[str], Header()]=None,
    supabase=Depends(get_supabase)
):
    """Strict authentication dependency. Throws 401 if unauthenticated."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
        
    token = authorization.replace("Bearer ", "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Invalid token format")
        
    try:
        user_response = await asyncio.to_thread(supabase.auth.get_user, token)
        user = user_response.user if user_response else None
        
        if not user:
            raise HTTPException(status_code=401, detail="User not found or token expired")
            
        return {
            "id": user.id,
            "email": user.email,
        }
    except HTTPException as he:
        raise he
    except Exception:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ==========================================
# PYDANTIC INPUT MODELS
# ==========================================
class UpdateProfileRequest(BaseModel):
    first_name: Optional[str] = Field(None, description="User's first name")
    last_name: Optional[str] = Field(None, description="User's last name")
    phone_number: Optional[str] = Field(None, description="User's contact phone number")
    avatar_url: Optional[str] = Field(None, description="URL to the user's avatar image")


# ==========================================
# 1. GET PROFILE ENDPOINT
# ==========================================
@router.get("", response_model=dict)
async def get_profile(context: dict=Depends(get_strict_user_context)):
    """Retrieves the profile details of the currently authenticated user."""
    user_id = context["id"]
    
    try:
        # Build RunnableConfig for the tool execution
        config: RunnableConfig = {
                    "configurable": {
                        "user_id": user_id
                    }
                }
        
        # Invoke the profile fetch tool
        tool = cast(BaseTool, get_user_profile_worker)
        tool_output_str = await tool.ainvoke({}, config=config)
        
        profile_data = json.loads(tool_output_str)
        
        if not isinstance(profile_data, dict) or "error" in profile_data:
            raise HTTPException(status_code=404, detail="Profile not found.")
            
        return {
            "status": "success",
            "profile": profile_data
        }
        
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"[GET PROFILE ENDPOINT ERROR] {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# 2. UPDATE PROFILE ENDPOINT
# ==========================================
@router.patch("", response_model=dict)
async def update_profile(
    body: UpdateProfileRequest,
    context: dict=Depends(get_strict_user_context)
):
    """Updates editable details of the authenticated user's profile."""
    user_id = context["id"]
    
    try:
        # Build RunnableConfig for the tool execution
        config: RunnableConfig = {
                    "configurable": {
                        "user_id": user_id
                    }
                }
        
        # Pass payload fields to the update tool
        tool = cast(BaseTool, update_user_profile_worker)
        tool_output_str = await tool.ainvoke({
            "first_name": body.first_name,
            "last_name": body.last_name,
            "phone_number": body.phone_number,
            "avatar_url": body.avatar_url
        }, config=config)
        
        if "Security Guardrail" in tool_output_str or "error" in tool_output_str.lower():
            raise HTTPException(status_code=400, detail=tool_output_str)
            
        return {
            "status": "success",
            "message": tool_output_str
        }
        
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"[UPDATE PROFILE ENDPOINT ERROR] {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
