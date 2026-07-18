from typing import Any, Dict, Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None

    
class ChatResponse(BaseModel):
    type: str = "response"  # Default to 'response'
    status: str = "success"  # Default to 'response'
    message: Optional[str] = None
    response: Optional[str] = None  # Make optional
    redirect_url: Optional[str] = None  # Make optional
    data: Optional[Dict[str, Any]] = None  # Make optional

