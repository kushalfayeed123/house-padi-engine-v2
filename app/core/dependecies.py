import json
from logging import getLogger
from typing import Annotated, Optional, cast
from fastapi import Request, Header, Depends, HTTPException, status
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
import asyncio

from app.tools.profile_ops import get_user_profile_worker

logger = getLogger("uvicorn")

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

# ============================================================================
# Strict auth dependency
# ============================================================================
# The permissive `get_optional_user_context` above is fine for chat, where a
# guest is a legitimate caller. Payment endpoints need the opposite: a missing
# or invalid token is a 401, and an infrastructure failure is a 503 so that an
# outage is not silently reported to the user (and to monitoring) as "logged
# out". `get_optional_user_context` is left untouched for backward
# compatibility with the existing routes.

_INFRA_ERROR_HINTS = (
    "connect",
    "timeout",
    "timed out",
    "network",
    "temporarily unavailable",
    "connection",
    "handshake",
    "5",  # HTTP 5xx surfaced in the message
)


def _is_infrastructure_error(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    if any(token in name for token in ("connect", "timeout", "network")):
        return True
    message = str(exc).lower()
    return any(hint in message for hint in _INFRA_ERROR_HINTS)


async def require_user_context(
    authorization: Annotated[Optional[str], Header()] = None,
    supabase=Depends(get_supabase),
) -> dict:
    """Resolve the authenticated user or fail with an explicit status code.

    * no/invalid token  -> 401
    * datastore outage  -> 503
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "error", "code": "auth_required", "message": "Authentication required."},
        )

    token = authorization.replace("Bearer ", "").strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "error", "code": "auth_required", "message": "Malformed authorization header."},
        )

    try:
        user_response = await asyncio.to_thread(supabase.auth.get_user, token)
    except Exception as exc:
        if _is_infrastructure_error(exc):
            logger.error(f"[AUTH] identity provider unreachable: {exc}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"status": "error", "code": "auth_unavailable", "message": "Authentication service unavailable."},
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "error", "code": "invalid_token", "message": "Invalid or expired token."},
        ) from exc

    user = getattr(user_response, "user", None)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "error", "code": "invalid_token", "message": "Invalid or expired token."},
        )

    try:
        res = await asyncio.to_thread(
            supabase.table("profiles").select("*").eq("id", user.id).single().execute
        )
        profile = res.data if res and res.data else {}
    except Exception as exc:
        if _is_infrastructure_error(exc):
            logger.error(f"[AUTH] profile lookup failed (infra): {exc}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"status": "error", "code": "datastore_unavailable", "message": "Datastore unavailable."},
            ) from exc
        profile = {}

    return {
        "id": user.id,
        "email": getattr(user, "email", None),
        "role": (profile or {}).get("role", "renter"),
        "profile": profile or {},
    }
