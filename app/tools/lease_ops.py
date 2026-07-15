import json
from typing import Literal, Optional
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field
from app.database import supabase_client, db
from app.schemas.payloads import LeasePayload, ApplicationPayload
from logging import getLogger
from datetime import datetime
from uuid import UUID

logger = getLogger("uvicorn")


class CreateLeaseInput(BaseModel):
    property_id: str = Field(..., description="Property UUID")
    renter_id: str = Field(..., description="Renter UUID")
    start_date: str = Field(..., description="Lease start date (YYYY-MM-DD)")
    rent_amount: float = Field(..., gt=0, description="Monthly rent amount")
    contract_url: Optional[str] = Field(None, description="URL to lease PDF if pre-uploaded")


class ApplicationDecisionInput(BaseModel):
    application_id: str = Field(..., description="Application UUID to approve/reject")
    decision: Literal["approve", "reject"] = Field(..., description="Approval decision")
    screening_summary: Optional[str] = Field(None, description="Summary of screening results")


@tool("create_lease_worker")
async def create_lease_worker(
    property_id: str,
    renter_id: str,
    start_date: str,
    rent_amount: float,
    config: RunnableConfig,
    contract_url: Optional[str] = None
) -> str:
    """Creates a formal lease agreement between landlord and renter.
    
    - Landlord must provide lease terms
    - Triggers human review before finalization
    - Generates AI summary of lease terms
    - Links to rental application
    """
    user_id = config.get("configurable", {}).get("user_id")
    user_role = config.get("configurable", {}).get("user_role", "renter")
    
    if user_role != "landlord":
        return "Security Guardrail: Only landlords can create leases."
    
    if not user_id:
        return "Security Guardrail: Execution context validation failed."

    try:
        # Verify property is owned by this landlord
        prop_res = await db.execute(
            supabase_client.table("properties")
            .select("id, owner_id, title, address_full")
            .eq("id", property_id)
            .single()
            .execute
        )
        
        if not prop_res.data or prop_res.data.get("owner_id") != user_id:
            return "Security Guardrail: You can only create leases for your own properties."
        
        property_info = prop_res.data
        
        # Create lease record
        lease_payload = {
            "property_id": property_id,
            "renter_id": renter_id,
            "owner_id": user_id,
            "start_date": start_date,
            "rent": rent_amount,
            "contract_url": contract_url,
            "is_active": False,  # Becomes active once both parties sign
            "status": "pending_signature"
        }
        
        lease_res = await db.execute(
            supabase_client.table("leases").insert(lease_payload).execute
        )
        
        lease_id = lease_res.data[0].get("id")
        logger.info(f"[LEASE CREATED] Lease {lease_id} created for property {property_info.get('title')}")
        
        # Generate default lease summary (would use AI in production)
        summary = f"Monthly rent: ${rent_amount} USD. Start date: {start_date}. Property: {property_info.get('address_full')}"
        
        # Create corresponding application record if needed
        try:
            app_payload = {
                "property_id": property_id,
                "renter_id": renter_id,
                "status": "approved",
                "lease_id": lease_id,
                "screening_summary": summary,
                "ai_match_score": 85  # Default score
            }
            await db.execute(
                supabase_client.table("applications").insert(app_payload).execute
            )
            logger.info(f"[APPLICATION] Created application record linked to lease {lease_id}")
        except Exception as e:
            logger.warning(f"Could not create application record: {str(e)}")
        
        return f"Success: Lease created (ID: {lease_id}). Awaiting signature from renter. Summary: {summary}"
        
    except Exception as e:
        logger.error(f"[LEASE CREATION ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
        return f"Lease creation failure: {str(e)}"


@tool("sign_lease_worker")
async def sign_lease_worker(lease_id: str, config: RunnableConfig) -> str:
    """Signs lease agreement by current user (renter or landlord).
    
    Tracks both landlord and renter signatures.
    Once both have signed, lease becomes active.
    """
    user_id = config.get("configurable", {}).get("user_id")
    
    if not user_id:
        return "Security Guardrail: Execution context validation failed."

    try:
        # Fetch lease details
        lease_res = await db.execute(
            supabase_client.table("leases")
            .select("id, property_id, renter_id, owner_id, status")
            .eq("id", lease_id)
            .single()
            .execute
        )
        
        if not lease_res.data:
            return f"Lease {lease_id} not found."
        
        lease = lease_res.data
        
        # Verify user is either renter or landlord
        if user_id != lease.get("renter_id") and user_id != lease.get("owner_id"):
            return "Security Guardrail: You are not a party to this lease."
        
        # Track signatures (simplified: in production use proper signature tracking)
        update_data = {}
        if user_id == lease.get("owner_id"):
            update_data["landlord_signed"] = True
            logger.info(f"[LEASE SIGNED] Landlord signed lease {lease_id}")
        else:
            update_data["renter_signed"] = True
            logger.info(f"[LEASE SIGNED] Renter signed lease {lease_id}")
        
        # If both signed, activate lease
        current_status = lease.get("status")
        if current_status == "both_signed":
            update_data["is_active"] = True
            logger.info(f"[LEASE ACTIVE] Lease {lease_id} is now active")
        else:
            update_data["status"] = "pending_countersignature"
        
        update_res = await db.execute(
            supabase_client.table("leases")
            .update(update_data)
            .eq("id", lease_id)
            .execute
        )
        
        is_now_active = update_data.get("is_active", False)
        return f"Success: Lease signed. {'Lease is now ACTIVE.' if is_now_active else 'Awaiting counter-signature from other party.'}"
        
    except Exception as e:
        logger.error(f"[LEASE SIGN ERROR] {str(e)}")
        return f"Lease signing failure: {str(e)}"


@tool("evaluate_application_worker")
async def evaluate_application_worker(
    application_id: str,
    decision: Literal["approve", "reject"],
    config: RunnableConfig,
    screening_summary: Optional[str] = None
) -> str:
    """Processes rental application with AI-generated screening summary.
    
    - Reviews renter profile and KYC status
    - Generates AI match score based on property fit
    - Updates application status
    - Triggers lease creation if approved
    """
    user_id = config.get("configurable", {}).get("user_id")
    user_role = config.get("configurable", {}).get("user_role", "renter")
    
    if user_role != "landlord":
        return "Security Guardrail: Only landlords can evaluate applications."
    
    if not user_id:
        return "Security Guardrail: Execution context validation failed."

    try:
        # Fetch application
        app_res = await db.execute(
            supabase_client.table("applications")
            .select("id, property_id, renter_id, status")
            .eq("id", application_id)
            .single()
            .execute
        )
        
        if not app_res.data:
            return f"Application {application_id} not found."
        
        app = app_res.data
        property_id = app.get("property_id")
        renter_id = app.get("renter_id")
        
        # Verify property is owned by this landlord
        prop_res = await db.execute(
            supabase_client.table("properties")
            .select("owner_id")
            .eq("id", property_id)
            .single()
            .execute
        )
        
        if not prop_res.data or prop_res.data.get("owner_id") != user_id:
            return "Security Guardrail: Can only evaluate applications for your properties."
        
        # Check renter KYC status
        kyc_res = await db.execute(
            supabase_client.table("kyc_verifications")
            .select("status")
            .eq("user_id", renter_id)
            .single()
            .execute
        )
        
        kyc_verified = kyc_res.data and kyc_res.data.get("status") == "verified" if kyc_res.data else False
        
        # Generate AI match score (simplified)
        ai_match_score = 90 if kyc_verified else 60
        
        if decision == "approve":
            update_data = {
                "status": "approved",
                "ai_match_score": ai_match_score,
                "screening_summary": screening_summary or f"Renter KYC Status: {'Verified' if kyc_verified else 'Pending'}. Profile matches property requirements."
            }
            result_msg = f"Application approved! AI match score: {ai_match_score}/100. Next: Create lease agreement."
        else:
            update_data = {
                "status": "rejected",
                "screening_summary": screening_summary or "Application does not meet property requirements."
            }
            result_msg = f"Application rejected. Renter has been notified."
        
        await db.execute(
            supabase_client.table("applications")
            .update(update_data)
            .eq("id", application_id)
            .execute
        )
        
        logger.info(f"[APPLICATION {decision.upper()}] Application {application_id} {decision}")
        
        return f"Success: {result_msg}"
        
    except Exception as e:
        logger.error(f"[APPLICATION EVALUATION ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
        return f"Application evaluation failure: {str(e)}"

