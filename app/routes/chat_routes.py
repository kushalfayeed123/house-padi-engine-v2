from fastapi import APIRouter, HTTPException, Request
from app.models.chat import ChatRequest, ChatResponse
from app.services.chat_service import process_chat_message
from logging import getLogger

logger = getLogger("uvicorn")
router = APIRouter()

@router.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(body: ChatRequest):
    try:

        service_result = await process_chat_message(body.message, body.thread_id)
        
        # Handle Redirect Path
        if service_result["type"] == "redirect":
            return ChatResponse(
                status="success",
                type="redirect",
                response='Redirecting...',
                redirect_url=service_result["redirect_url"]
            )
            
        # Handle Chat Response Path
        return ChatResponse(
            status="success",
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