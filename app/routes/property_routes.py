import json
import re
import time
from typing import Annotated, Optional, cast
from logging import getLogger

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Header, Request, UploadFile
from langchain_core.tools import BaseTool

from app.core.dependecies import get_optional_user_context
from app.models.property import PropertySchema
from app.services.property_ai_service import PropertyAIService
from app.tools.property_ops import (
    create_property_worker,
    get_featured_worker,
    get_landlord_properties_worker,
    get_property_details_worker,
    update_property_worker,
)

logger = getLogger("uvicorn")
router = APIRouter(prefix="/api/property")

_ai_service: Optional[PropertyAIService] = None

def get_ai_service() -> PropertyAIService:
    global _ai_service
    if _ai_service is None:
        _ai_service = PropertyAIService()
    return _ai_service

@router.post("/create")
async def create_property(
    request: Request,
    data: PropertySchema,
    background_tasks: BackgroundTasks,
    context: dict = Depends(get_optional_user_context)
):
    if not context or not context.get("id"):
        raise HTTPException(status_code=401, detail="Authentication required")

    tool = cast(BaseTool, create_property_worker)
    result = await tool.ainvoke(
        data.model_dump(), 
        config={"configurable": {"user_id": context["id"], "user_role": context.get("role")}}
    )
    
    supabase = request.app.state.system.supabase
    try:
        recent_prop = supabase.table("properties") \
            .select("id") \
            .eq("owner_id", context["id"]) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()
        
        if recent_prop.data:
            property_id = recent_prop.data[0]["id"]
            background_tasks.add_task(get_ai_service().enrich_property, property_id)
    except Exception as e:
        logger.error(f"Failed to queue background enrichment for new property: {e}")

    return {"status": "success", "message": result}


@router.get("/landlord/listings")
async def get_my_listings(context: dict = Depends(get_optional_user_context)):
    if not context or not context.get("id"):
        raise HTTPException(status_code=401, detail="Authentication required")

    tool = cast(BaseTool, get_landlord_properties_worker)
    result = await tool.ainvoke({}, config={"configurable": {"user_id": context["id"], "user_role": context.get("role")}})
    
    if "Security Guardrail" in str(result):
        raise HTTPException(status_code=403, detail=result)
    return json.loads(result)


@router.get("/featured")
async def get_featured():
    tool = cast(BaseTool, get_featured_worker)
    res = await tool.ainvoke({})
    return json.loads(res)


@router.get("/{id}")
async def get_details(id: str):
    tool = cast(BaseTool, get_property_details_worker)
    res = await tool.ainvoke({"property_id": id})
    return json.loads(res)


@router.patch("/{id}")
async def update_property(
    id: str,
    update_data: dict,
    request: Request,
    background_tasks: BackgroundTasks,
    context: dict = Depends(get_optional_user_context)
):
    if not context or not context.get("id"):
        raise HTTPException(status_code=401, detail="Authentication required")

    tool = cast(BaseTool, update_property_worker)
    res = await tool.ainvoke(
        {"property_id": id, "update_data": update_data},
        config={"configurable": {"user_id": context["id"], "user_role": context.get("role")}}
    )
    
    background_tasks.add_task(get_ai_service().enrich_property, id)

    return {"message": res}


@router.post("/upload-image")
async def upload_property_image(
    request: Request,
    file: UploadFile = File(...),
    authorization: Annotated[str, Header()] = ""
):
    supabase = request.app.state.system.supabase
    
    token = authorization.replace("Bearer ", "").strip()
    try:
        user = supabase.auth.get_user(token).user
    except Exception:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    file_content = await file.read()
    
    safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', file.filename or "upload")
    file_path = f"properties/{user.id}/{int(time.time())}_{safe_name}"
    
    try:
        supabase.storage.from_("house-padi-assets").upload(
            path=file_path,
            file=file_content,
            file_options={"content-type": file.content_type}
        )
    except Exception as e:
        logger.error(f"Upload failed, attempting update: {str(e)}")
        supabase.storage.from_("house-padi-assets").update(
            path=file_path,
            file=file_content,
            file_options={"content-type": file.content_type}
        )
    
    url_res = supabase.storage.from_("house-padi-assets").get_public_url(file_path)
    return {"url": url_res}