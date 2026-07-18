from fastapi import APIRouter, HTTPException, Depends # Ensure Depends is imported
from app.dependecies import get_user_context # Assuming this is where your dependency lives
from app.models.chat import ChatRequest, ChatResponse
from app.services.chat_service import process_chat_message
from logging import getLogger

logger = getLogger("uvicorn")
router = APIRouter()

@router.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(body: ChatRequest, context: dict = Depends(get_user_context)):
    try:
        service_result = await process_chat_message(body.message, body.thread_id)
        
        # Handle Redirect Path
        if service_result["type"] == "redirect":
            target_url = service_result.get("redirect_url", "")
            
            # Condition: If URL is for landlords, verify user role
            if "landlord" in target_url and context.get("role") != "landlord":
                return ChatResponse(
                    status="error",
                    type="response",
                    response="You are not authorized to access landlord features."
                )

            # If authorized, or if it's a non-landlord redirect, proceed
            return ChatResponse(
                status="success",
                type="redirect",
                response="Redirecting...",
                redirect_url=target_url
            )
            
        # Handle Chat Response Path
        return ChatResponse(
            status="success",
            type="response",
            response=service_result["content"],
            data=service_result["data"]
        )
        
    except Exception as e:
        logger.error(f"Chat error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    
    
    
    
# @app.post("/api/chat")
# async def chat_endpoint(body: ChatRequest):
#     try:
#         service_result = await process_chat_message(body.message, body.thread_id)
        
#         # Handle Redirect Path
#         if service_result["type"] == "redirect":
#             return {
#                 "status": "success",
#                 "type": "redirect",
#                 "redirect_url": service_result["redirect_url"],
#                 "message": "Redirecting..."
#             }
            
#         # Handle Chat Response Path
#         return ChatResponse(
#             status="success",
#             response=service_result["content"],
#             data=service_result["data"]
#         )
        
#     except Exception as e:
#         logger.error(f"Chat error: {str(e)}")
#         raise HTTPException(status_code=500, detail=str(e))