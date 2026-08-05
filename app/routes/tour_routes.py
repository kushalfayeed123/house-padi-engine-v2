import json
from logging import getLogger
from typing import cast

from fastapi import APIRouter, Depends, HTTPException
from langchain_core.tools import BaseTool

from app.core.dependecies import get_optional_user_context
from app.tools.tour_ops import (
    BookTourInput,
    approve_tour_worker,
    book_tour_worker,
    list_tours_worker,
)

logger = getLogger("uvicorn")
router = APIRouter(prefix="/api/tours", tags=["Tours"])


@router.post("/book")
async def book_tour(
    data: BookTourInput,
    context: dict = Depends(get_optional_user_context)
):
    """Book or schedule a viewing tour for a property."""
    if not context or not context.get("id"):
        raise HTTPException(status_code=401, detail="Authentication required to book a tour.")

    tool = cast(BaseTool, book_tour_worker)
    result = await tool.ainvoke(
        data.model_dump(),
        config={
            "configurable": {
                "user_id": context["id"],
                "user_role": context.get("role", "renter"),
            }
        },
    )

    res_str = str(result)
    if "Security Guardrail" in res_str:
        raise HTTPException(status_code=403, detail=res_str)
    if "Execution Error" in res_str or "Database Interface Exception" in res_str:
        raise HTTPException(status_code=400, detail=res_str)

    return {"status": "success", "message": res_str}


@router.get("/landlord/listings")
async def list_tours(
    context: dict = Depends(get_optional_user_context)
):
    """Retrieve scheduled property tours for the authenticated user (renter or landlord)."""
    if not context or not context.get("id"):
        raise HTTPException(status_code=401, detail="Authentication required.")

    tool = cast(BaseTool, list_tours_worker)
    result = await tool.ainvoke(
        {},
        config={
            "configurable": {
                "user_id": context["id"],
                "user_role": context.get("role", "renter"),
            }
        },
    )

    res_str = str(result)
    if "Security Guardrail" in res_str:
        raise HTTPException(status_code=403, detail=res_str)
    if "Database Interface Exception" in res_str:
        raise HTTPException(status_code=500, detail=res_str)

    # Tool returns a JSON-serialized list string
    return json.loads(res_str)


@router.post("/{tour_id}/approve")
async def approve_tour(
    tour_id: str,
    context: dict = Depends(get_optional_user_context)
):
    """Approve a pending tour request. Restricted strictly to landlords/owners."""
    if not context or not context.get("id"):
        raise HTTPException(status_code=401, detail="Authentication required.")

    tool = cast(BaseTool, approve_tour_worker)
    result = await tool.ainvoke(
        {"tour_id": tour_id},
        config={
            "configurable": {
                "user_id": context["id"],
                "user_role": context.get("role", "renter"),
            }
        },
    )

    res_str = str(result)
    if "Security Guardrail" in res_str:
        raise HTTPException(status_code=403, detail=res_str)
    if "not found" in res_str.lower():
        raise HTTPException(status_code=404, detail=res_str)
    if "Database Interface Exception" in res_str:
        raise HTTPException(status_code=500, detail=res_str)

    return {"status": "success", "message": res_str}