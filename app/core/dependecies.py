import json
from typing import Annotated, Optional, cast
from fastapi import Request, Header, Depends
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
import asyncio

from app.tools.profile_ops import get_user_profile_worker

def get_supabase(request: Request):
    return request.app.state.system.supabase

async def get_optional_user_context(
    authorization: Annotated[Optional[str], Header()] = None,
    supabase = Depends(get_supabase)
):
    if not authorization:
        return None
        
    token = authorization.replace("Bearer ", "").strip()
    if not token:
        return None
        
    try:
        # 1. Verify user token via Supabase Auth
        user_response = await asyncio.to_thread(supabase.auth.get_user, token)
        user = user_response.user if user_response else None
        
        if not user:
            return None
            
        # 2. Build the config context required by the tool
        config: RunnableConfig = {
                    "configurable": {
                        "user_id": user.id
                    }
                }
        
        # 3. Invoke the LangChain tool programmatically
        tool = cast(BaseTool, get_user_profile_worker)
        tool_output_str = await tool.ainvoke({}, config=config)
        
        # 4. Parse the JSON string output from the tool
        profile_data = json.loads(tool_output_str) if tool_output_str else {}
        
        # Handle case where tool returns an error message or string instead of profile dict
        if not isinstance(profile_data, dict) or "error" in profile_data:
            profile_data = {}
            
        user_role = profile_data.get("role", "renter")
        
        return {
            "id": user.id,
            "email": user.email,
            "role": user_role,
            "profile": profile_data
        }
        
    except Exception as e:
        return None  # Invalid token, expired token, or unexpected error treated as guest