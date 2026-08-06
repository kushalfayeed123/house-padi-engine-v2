"""
Application Router
Endpoints for submitting lease applications, landlord approvals, and rejection handling.
"""

import json
from typing import Optional, cast
from fastapi import APIRouter, Depends, HTTPException, Body
from langchain_core.tools import BaseTool

from app.core.dependecies import get_optional_user_context
from app.tools.application_lease_ops import get_applications_worker, manage_application_worker, submit_application_worker

router = APIRouter(prefix="/api/applications", tags=["Applications"])



@router.get("/landlord")  # Endpoint alias for front-end query compatibility
async def get_user_applications(
    context: dict = Depends(get_optional_user_context)
):
    """Fetches applications based on authenticated user's role.
    
    - Owner: Returns applications for properties owned by landlord.
    - Renter: Returns applications submitted by renter.
    """
    if not context or not context.get("id"):
        raise HTTPException(status_code=401, detail="Authentication required.")

    tool = cast(BaseTool, get_applications_worker)
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
    
    # Error Guardrails
    if "Security Guardrail" in res_str:
        raise HTTPException(status_code=403, detail=res_str)
    if "Database Interface Exception" in res_str:
        raise HTTPException(status_code=500, detail=res_str)

    try:
        return json.loads(res_str)
    except json.JSONDecodeError:
        return {"status": "success", "data": res_str}

@router.post("/properties/{property_id}/apply")
async def apply_for_property(
    property_id: str,
    renter_signature: str = Body(..., embed=True),
    start_date: str = Body(..., embed=True),
    context: dict = Depends(get_optional_user_context)
):
    """Submits a rental application and appends applicant signature."""
    if not context or not context.get("id"):
        raise HTTPException(status_code=401, detail="Authentication required.")

    tool = cast(BaseTool, submit_application_worker)
    result = await tool.ainvoke(
        {
            "property_id": property_id,
            "renter_signature": renter_signature,
            "start_date": start_date
        },
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


@router.post("/{application_id}/approve")
async def approve_application(
    application_id: str,
    landlord_signature: str = Body(..., embed=True),
    context: dict = Depends(get_optional_user_context)
):
    """Landlord approves an application and appends landlord signature."""
    if not context or not context.get("id"):
        raise HTTPException(status_code=401, detail="Authentication required.")

    tool = cast(BaseTool, manage_application_worker)
    result = await tool.ainvoke(
        {
            "application_id": application_id,
            "action": "approve",
            "landlord_signature": landlord_signature
        },
        config={
            "configurable": {
                "user_id": context["id"],
                "user_role": context.get("role", "owner"),
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


@router.post("/{application_id}/reject")
async def reject_application(
    application_id: str,
    context: dict = Depends(get_optional_user_context)
):
    """Landlord rejects an application."""
    if not context or not context.get("id"):
        raise HTTPException(status_code=401, detail="Authentication required.")

    tool = cast(BaseTool, manage_application_worker)
    result = await tool.ainvoke(
        {
            "application_id": application_id,
            "action": "reject",
            "landlord_signature": None
        },
        config={
            "configurable": {
                "user_id": context["id"],
                "user_role": context.get("role", "owner"),
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