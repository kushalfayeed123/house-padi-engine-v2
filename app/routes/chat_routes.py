from fastapi import APIRouter, HTTPException, Depends
from typing import Optional
from app.dependecies import get_optional_user_context
from app.models.chat import ChatRequest, ChatResponse
from app.services.chat_service import process_chat_message
from logging import getLogger

logger = getLogger("uvicorn")
router = APIRouter()

@router.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(
    body: ChatRequest, 
    context: Optional[dict] = Depends(get_optional_user_context)
):
    try:
        service_result = await process_chat_message(body.message, body.thread_id, context)
        
        # Handle Redirect Path
        if service_result["type"] == "redirect":
            target_url = service_result.get("redirect_url", "")
            
            # Condition 1: Action requires authentication entirely (e.g., dashboard, account)
            if "dashboard" in target_url or "account" in target_url:
                if not context:
                    return ChatResponse(
                        status="success",
                        type="redirect",
                        response="Please sign in to continue.",
                        redirect_url="/login"  # Change to your frontend login route
                    )

            # Condition 2: If URL is for landlords, verify user authentication and role
            if "landlord" in target_url:
                if not context:
                    return ChatResponse(
                        status="success",
                        type="redirect",
                        response="Please sign in as a landlord to access this feature.",
                        redirect_url="/login"
                    )
                if context.get("role") != "owner":
                    return ChatResponse(
                        status="error",
                        type="response",
                        response="You are not authorized to access landlord features."
                    )

            # If authorized or public redirect, proceed normally
            return ChatResponse(
                status="success",
                type="redirect",
                response="Redirecting...",
                redirect_url=target_url
            )
            
        # Handle standard Chat Response Path
        return ChatResponse(
            status="success",
            type="response",
            response=service_result["content"],
            data=service_result.get("data")
        )
        
    except Exception as e:
        logger.error(f"Chat error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))