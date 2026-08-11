"""
Application & Lease Management Tool
Handles renter application submission, KYC screening, landlord evaluation/countersigning,
and lease lifecycle state.
"""

import json
from typing import Optional, Literal
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from app.core.database import db, supabase_client
from app.services.lease_generator import generate_base_lease_template
from logging import getLogger
from datetime import datetime

logger = getLogger("uvicorn")


class SubmitApplicationInput(BaseModel):
    property_id: str = Field(..., description="Property UUID to apply for")
    renter_signature: str = Field(..., description="Digital signature of applicant")
    start_date: str = Field(..., description="Requested start date (YYYY-MM-DD)")


class ManageApplicationInput(BaseModel):
    application_id: str = Field(..., description="Application UUID to evaluate")
    action: Literal["approve", "reject"] = Field(..., description="Approval decision")
    landlord_signature: Optional[str] = Field(None, description="Landlord digital signature (required if approving)")
    screening_summary: Optional[str] = Field(None, description="Optional notes on evaluation decision")


@tool
async def get_applications_worker(config: RunnableConfig) -> str:
    """Fetches lease/rental applications scoped by the caller's role — a
    landlord sees applications received for their properties, a renter
    sees applications they've submitted. Use this for requests like 'show
    me my applications', 'what applications have I received', 'check the
    status of my rental application', 'view pending applications for my
    properties'."""
    
    configurable = config.get("configurable", {})
    user_id = configurable.get("user_id")
    user_role = configurable.get("user_role", "renter")

    if not user_id:
        return "Error: Authentication user ID missing from context."

    try:
        if user_role in ["owner", "landlord"]:
            # Query applications for properties owned by this landlord
            res = await db.execute(
                supabase_client.table("applications")
                .select("*, properties!inner(id, title, address_full, owner_id)")
                .eq("properties.owner_id", user_id)
                .order("applied_at", desc=True)
                .execute
            )
        else:
            # Query applications submitted by this renter
            res = await db.execute(
                supabase_client.table("applications")
                .select("*, properties(id, title, address_full)")
                .eq("renter_id", user_id)
                .order("applied_at", desc=True)
                .execute
            )

        return json.dumps(res.data if res.data else [])

    except Exception as e:
        return f"Database Interface Exception: {str(e)}"


@tool("submit_application_worker")
async def submit_application_worker(
    property_id: str,
    renter_signature: str,
    start_date: str,
    config: RunnableConfig
) -> str:
    """STRICTLY FOR RENTERS: Submits a formal rental application for a
    specific property, running automated KYC match scoring and creating a
    draft lease awaiting landlord countersignature..."""
    
    user_id = config.get("configurable", {}).get("user_id")
    
    if not user_id:
        return "Security Guardrail: Execution context validation failed."

    try:
        # 1. Fetch Property Details
        prop_res = await db.execute(
            supabase_client.table("properties")
            .select("id, owner_id, price, title, agreement_content, profiles!owner_id(first_name, last_name)")
            .eq("id", property_id)
            .maybe_single()
            .execute
        )
        if not prop_res or not prop_res.data:
            return f"Property {property_id} not found."
        
        prop = prop_res.data
        owner_id = prop.get("owner_id")

        if user_id == owner_id:
            return "Security Guardrail: Landlords cannot apply to their own properties."

        # 2. Perform Automated KYC Verification Check
        kyc_res = await db.execute(
            supabase_client.table("kyc_verifications")
            .select("status")
            .eq("user_id", user_id)
            .maybe_single()
            .execute
        )
        kyc_data = kyc_res.data if kyc_res else None
        kyc_verified = bool(kyc_data and kyc_data.get("status") == "verified")
        ai_match_score = 90 if kyc_verified else 60
        screening_notes = f"KYC Status: {'Verified' if kyc_verified else 'Pending'}. Applied on {datetime.now().strftime('%Y-%m-%d')}."

        # 3. Ensure Property Has Base Lease Agreement
        base_agreement = prop.get("agreement_content")
        if not base_agreement:
            owner_profile = prop.get("profiles", {}) or {}
            owner_name = f"{owner_profile.get('first_name', '')} {owner_profile.get('last_name', '')}".strip() or "Landlord"
            base_agreement = generate_base_lease_template(prop, owner_name)
            
            await db.execute(
                supabase_client.table("properties")
                .update({"agreement_content": base_agreement})
                .eq("id", property_id)
                .execute
            )

        # 4. Create Inactive Draft Lease Record
        lease_payload = {
            "property_id": property_id,
            "owner_id": owner_id,
            "renter_id": user_id,
            "start_date": start_date,
            "rent": prop.get("price", 0),
            "is_active": False,
            "contract_url": f"RENTER_SIGNED:{renter_signature}"
        }
        
        lease_res = await db.execute(
            supabase_client.table("leases")
            .insert(lease_payload)
            .select()
            .execute
        )
        if not lease_res or not lease_res.data:
            return "Database Error: Failed to create lease draft record."

        lease_id = lease_res.data[0].get("id")

        # 5. Create Application Record
        app_payload = {
            "property_id": property_id,
            "renter_id": user_id,
            "status": "pending_landlord_approval",
            "lease_id": str(lease_id),
            "ai_match_score": ai_match_score,
            "screening_summary": screening_notes
        }

        app_res = await db.execute(
            supabase_client.table("applications")
            .insert(app_payload)
            .select()
            .execute
        )
        if not app_res or not app_res.data:
            return "Database Error: Failed to create application record."

        app_id = app_res.data[0].get("id")

        # 6. Notify Landlord
        notif = {
            "user_id": owner_id,
            "title": "New Rental Application Received",
            "message": f"New application for {prop.get('title')}. AI Match Score: {ai_match_score}/100. Review and countersign.",
            "type": "application_submitted",
            "metadata": {"application_id": str(app_id), "property_id": property_id}
        }
        await db.execute(supabase_client.table("notifications").insert(notif).execute)

        logger.info(f"[APPLICATION SUBMITTED] App ID {app_id} created for property {property_id}")
        
        # Return structured JSON string for the backend route to parse
        return json.dumps({
            "status": "success",
            "data": {
                "application_id": str(app_id),
                "lease_id": str(lease_id),
                "ai_match_score": ai_match_score,
            },
            "message": f"Application submitted successfully."
        })

    except Exception as e:
        logger.error(f"[SUBMIT APPLICATION ERROR] {str(e)}")
        return f"Application submission failure: {str(e)}"

    
@tool("manage_application_worker")
async def manage_application_worker(
    application_id: str,
    action: Literal["approve", "reject"],
    config: RunnableConfig,
    landlord_signature: Optional[str]=None,
    screening_summary: Optional[str]=None
) -> str:
    """STRICTLY FOR LANDLORDS: Approves or rejects a renter's pending
    rental application, appending the landlord's digital countersignature
    if approved, and notifying the applicant to proceed to payment. Use
    this when a LANDLORD wants to accept or decline someone who applied to
    rent their property. Example requests: 'approve this application',
    'reject this applicant', 'accept the renter's application', 'decline
    this rental request', 'countersign the lease for this applicant'."""
    
    user_id = config.get("configurable", {}).get("user_id")

    if not user_id:
        return "Security Guardrail: Execution context validation failed."

    try:
        # 1. Fetch Application & Property (Removed nested leases(*) query)
        app_res = await db.execute(
            supabase_client.table("applications")
            .select("*, properties(owner_id, price, title)")
            .eq("id", application_id)
            .maybe_single()
            .execute
        )
        if not app_res or not app_res.data:
            return f"Application {application_id} not found."

        app = app_res.data
        owner_id = app.get("properties", {}).get("owner_id")

        if owner_id != user_id:
            return "Security Guardrail: You can only evaluate applications for properties you own."

        renter_id = app.get("renter_id")
        lease_id = app.get("lease_id")

        # 2. Handle Rejection Flow
        if action == "reject":
            summary = screening_summary or "Application does not meet requirements."
            await db.execute(
                supabase_client.table("applications")
                .update({"status": "rejected", "screening_summary": summary})
                .eq("id", application_id)
                .execute
            )
            # Notify Renter
            notif = {
                "user_id": renter_id,
                "title": "Application Update",
                "message": f"Your application for {app.get('properties', {}).get('title')} was not approved.",
                "type": "application_rejected",
                "metadata": {"application_id": application_id}
            }
            await db.execute(supabase_client.table("notifications").insert(notif).execute)
            return f"Success: Application {application_id} rejected."

        # 3. Handle Approval Flow
        if not landlord_signature:
            return "Error: Landlord digital signature is required to approve and countersign the lease."

        summary = screening_summary or app.get("screening_summary") or "Approved by landlord."

        # Update application status
        await db.execute(
            supabase_client.table("applications")
            .update({"status": "approved_pending_payment", "screening_summary": summary})
            .eq("id", application_id)
            .execute
        )

        # 4. Fetch Lease separately and Append Landlord Signature
        if lease_id:
            lease_res = await db.execute(
                supabase_client.table("leases")
                .select("contract_url")
                .eq("id", lease_id)
                .maybe_single()
                .execute
            )
            
            existing_url = ""
            if lease_res and lease_res.data:
                existing_url = lease_res.data.get("contract_url", "")

            updated_signatures = f"{existing_url}|LANDLORD_SIGNED:{landlord_signature}"
            await db.execute(
                supabase_client.table("leases")
                .update({"contract_url": updated_signatures, "is_active": True})
                .eq("id", lease_id)
                .execute
            )

        # 5. Notify Renter to Make Payment
        rent_amount = app.get("properties", {}).get("price", 0)
        notif_renter = {
            "user_id": renter_id,
            "title": "Application Approved! Complete Rent Payment",
            "message": f"Your application is approved! Please pay ${rent_amount:,.2f} to finalize your lease and access key handover.",
            "type": "application_approved",
            "metadata": {"application_id": application_id, "lease_id": lease_id, "amount": rent_amount}
        }
        await db.execute(supabase_client.table("notifications").insert(notif_renter).execute)

        logger.info(f"[APPLICATION APPROVED] App {application_id} approved by owner {user_id}")
        return f"Success: Application approved and lease countersigned. Renter notified to pay ${rent_amount:,.2f} USD."

    except Exception as e:
        logger.error(f"[MANAGE APPLICATION ERROR] {str(e)}")
        return f"Application evaluation failure: {str(e)}"
    
    
@tool("get_application_details_worker")
async def get_application_details_worker(
    application_id: str,
    config: RunnableConfig
) -> str:
    """Fetches details and current status of a specific rental application,
    ensuring proper authorization for landlords or renters."""
    
    configurable = config.get("configurable", {})
    user_id = configurable.get("user_id")
    user_role = configurable.get("user_role", "renter")

    if not user_id:
        return "Security Guardrail: Execution context validation failed."

    try:
        res = await db.execute(
            supabase_client.table("applications")
            .select("*, properties(id, title, address_full, owner_id, price)")
            .eq("id", application_id)
            .maybe_single()
            .execute
        )
        
        if not res or not res.data:
            return f"Application {application_id} not found."
        
        app = res.data
        owner_id = app.get("properties", {}).get("owner_id")
        renter_id = app.get("renter_id")

        # Restrict access to the application owner (renter) or property owner (landlord)
        if user_role not in ["admin", "system"] and user_id != owner_id and user_id != renter_id:
            return "Security Guardrail: You are not authorized to view this application."

        return json.dumps({
            "status": "success",
            "data": app
        })

    except Exception as e:
        logger.error(f"[GET APPLICATION DETAILS ERROR] {str(e)}")
        return f"Database Interface Exception: {str(e)}"
