import json
import re
import time

from fastapi import APIRouter, Depends, File, HTTPException, Header, Request, UploadFile
from typing import Annotated, cast
from logging import getLogger

from langchain_core.tools import BaseTool
from app.dependecies import get_user_context
from app.models.property import PropertySchema
from app.tools.property_ops import create_property_worker, get_featured_worker, get_landlord_properties_worker, get_property_details_worker, update_property_worker


logger = getLogger("uvicorn")
router = APIRouter(prefix="/api/property")

@router.post("/create")
async def create_property(data: PropertySchema, context: dict = Depends(get_user_context)):
    tool = cast(BaseTool, create_property_worker)
    result = await tool.ainvoke(
        data.dict(), 
        config={"configurable": {"user_id": context["id"], "user_role": context["role"]}}
    )
    return {"status": "success", "message": result}

@router.get("/landlord/listings")
async def get_my_listings(context: dict = Depends(get_user_context)):
    tool = cast(BaseTool, get_landlord_properties_worker)
    result = await tool.ainvoke({}, config={"configurable": {"user_id": context["id"], "user_role": context["role"]}})
    
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
async def update_property(id: str, update_data: dict, context: dict = Depends(get_user_context)):
    tool = cast(BaseTool, update_property_worker)
    res = await tool.ainvoke(
        {"property_id": id, "update_data": update_data},
        config={"configurable": {"user_id": context["id"], "user_role": context["role"]}}
    )
    return {"message": res}


@router.post("/upload-image")
async def upload_property_image(
    request: Request,
    file: UploadFile = File(...),
    authorization: Annotated[str, Header()] = ""
):
    # Access Supabase from the global state attached to the request
    supabase = request.app.state.system.supabase
    
    token = authorization.replace("Bearer ", "").strip()
    try:
        user = supabase.auth.get_user(token).user
    except Exception:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Read the file
    file_content = await file.read()
    
    safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', file.filename or "upload")
    file_path = f"{user.id}/{int(time.time())}_{safe_name}"
    
    try:
        # Try uploading
        supabase.storage.from_("property-images").upload(
            path=file_path,
            file=file_content,
            file_options={"content-type": file.content_type}
        )
    except Exception as e:
        logger.error(f"Upload failed, attempting update: {str(e)}")
        # If it fails, attempt an update
        supabase.storage.from_("property-images").update(
            path=file_path,
            file=file_content,
            file_options={"content-type": file.content_type}
        )
    
    url_res = supabase.storage.from_("property-images").get_public_url(file_path)
    return {"url": url_res}