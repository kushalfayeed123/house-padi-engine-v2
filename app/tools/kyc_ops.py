"""
KYC (Know Your Customer) verification tool
Handles identity verification and user screening
"""

import json
from typing import Optional
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from app.database import supabase_client, db
from logging import getLogger
from datetime import datetime

logger = getLogger("uvicorn")


class SubmitKYCInput(BaseModel):
    id_type: str = Field(..., description="Type of ID (passport, driver_license, national_id, etc)")
    id_number: str = Field(..., description="ID number")
    id_image_url: str = Field(..., description="URL to ID image file")
    full_name: Optional[str] = Field(None, description="Full name on ID")


class ApproveKYCInput(BaseModel):
    kyc_id: str = Field(..., description="KYC verification record UUID")
    status: str = Field("verified", description="Verification status (verified or rejected)")
    rejection_reason: Optional[str] = Field(None, description="Reason for rejection if applicable")


@tool("submit_kyc_worker")
async def submit_kyc_worker(
    id_type: str,
    id_number: str,
    id_image_url: str,
    config: RunnableConfig,
    full_name: Optional[str] = None
) -> str:
    """Submits KYC verification documents for review.
    
    - Validates document type
    - Stores ID image for review
    - Creates verification record
    - Sets status to pending
    - Triggers manual review by admin
    """
    user_id = config.get("configurable", {}).get("user_id")
    
    if not user_id:
        return "Security Guardrail: User context missing."

    try:
        # Check if user already has pending KYC
        existing_res = await db.execute(
            supabase_client.table("kyc_verifications")
            .select("id, status")
            .eq("user_id", user_id)
            .execute
        )
        
        if existing_res.data:
            existing = existing_res.data[0]
            if existing.get("status") == "verified":
                return "You have already completed KYC verification."
            elif existing.get("status") == "pending":
                return "Your KYC verification is already pending review."
            elif existing.get("status") == "rejected":
                logger.info(f"[KYC RESUBMIT] User {user_id} resubmitting after rejection")
        
        # Validate ID type
        valid_id_types = ["passport", "driver_license", "national_id", "residence_permit", "other"]
        if id_type.lower() not in valid_id_types:
            return f"Invalid ID type. Accepted types: {', '.join(valid_id_types)}"
        
        # Create KYC record
        kyc_payload = {
            "user_id": user_id,
            "id_type": id_type.lower(),
            "id_number": id_number,
            "id_image_url": id_image_url,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
        
        kyc_res = await db.execute(
            supabase_client.table("kyc_verifications").insert(kyc_payload).execute
        )
        
        kyc_id = kyc_res.data[0].get("id")
        logger.info(f"[KYC SUBMITTED] User {user_id} submitted KYC verification (ID: {kyc_id})")
        
        # Update user profile KYC status to pending
        try:
            await db.execute(
                supabase_client.table("profiles")
                .update({"kyc_status": "pending"})
                .eq("id", user_id)
                .execute
            )
        except Exception as e:
            logger.warning(f"Could not update profile KYC status: {str(e)}")
        
        # Create admin notification (in production, this triggers a review queue)
        try:
            notification = {
                "recipient_id": "admin_queue",
                "type": "kyc_pending_review",
                "content": f"KYC verification pending review for user {user_id}",
                "related_kyc_id": kyc_id,
                "status": "unread"
            }
            await db.execute(
                supabase_client.table("notifications").insert(notification).execute
            )
        except Exception as e:
            logger.warning(f"Could not create admin notification: {str(e)}")
        
        return f"Success: KYC verification submitted (ID: {kyc_id}). Your application is now under review. This typically takes 1-2 business days. You will be notified once verification is complete."
        
    except Exception as e:
        logger.error(f"[KYC SUBMISSION ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
        return f"KYC submission failure: {str(e)}"


@tool("get_kyc_status_worker")
async def get_kyc_status_worker(config: RunnableConfig) -> str:
    """Retrieves user's KYC verification status."""
    user_id = config.get("configurable", {}).get("user_id")
    
    if not user_id:
        return "Security Guardrail: User context missing."

    try:
        kyc_res = await db.execute(
            supabase_client.table("kyc_verifications")
            .select("id, status, id_type, created_at, updated_at, rejection_reason")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute
        )
        
        if not kyc_res.data:
            return json.dumps({
                "status": "not_submitted",
                "message": "You have not yet submitted KYC verification. Submit your ID to complete identity verification."
            })
        
        kyc = kyc_res.data[0]
        
        result = {
            "kyc_id": kyc.get("id"),
            "status": kyc.get("status"),
            "id_type": kyc.get("id_type"),
            "submitted_at": kyc.get("created_at"),
            "updated_at": kyc.get("updated_at")
        }
        
        if kyc.get("status") == "rejected":
            result["rejection_reason"] = kyc.get("rejection_reason", "Please resubmit with clearer images")
        
        return json.dumps(result)
        
    except Exception as e:
        logger.error(f"[KYC STATUS ERROR] {str(e)}")
        return f"KYC status retrieval failure: {str(e)}"


@tool("approve_kyc_worker")
async def approve_kyc_worker(
    kyc_id: str,
    config: RunnableConfig,
    status: str = "verified",
    rejection_reason: Optional[str] = None
) -> str:
    """Approves or rejects KYC verification (admin only).
    
    - Validates document
    - Updates KYC status
    - Notifies user
    - Updates profile KYC status
    """
    user_id = config.get("configurable", {}).get("user_id")
    user_role = config.get("configurable", {}).get("user_role", "renter")
    
    if user_role != "admin":
        return "Security Guardrail: Only administrators can approve KYC verifications."
    
    if not user_id:
        return "Security Guardrail: User context missing."

    try:
        # Fetch KYC record
        kyc_res = await db.execute(
            supabase_client.table("kyc_verifications")
            .select("id, user_id, status")
            .eq("id", kyc_id)
            .single()
            .execute
        )
        
        if not kyc_res.data:
            return f"KYC record {kyc_id} not found."
        
        kyc = kyc_res.data
        verified_user_id = kyc.get("user_id")
        
        # Update KYC status
        update_data = {
            "status": status,
            "updated_at": datetime.now().isoformat()
        }
        
        if status == "rejected" and rejection_reason:
            update_data["rejection_reason"] = rejection_reason
        
        await db.execute(
            supabase_client.table("kyc_verifications")
            .update(update_data)
            .eq("id", kyc_id)
            .execute
        )
        
        # Update user profile KYC status
        profile_kyc_status = "verified" if status == "verified" else "rejected"
        try:
            await db.execute(
                supabase_client.table("profiles")
                .update({"kyc_status": profile_kyc_status})
                .eq("id", verified_user_id)
                .execute
            )
        except Exception as e:
            logger.warning(f"Could not update profile: {str(e)}")
        
        # Notify user
        notification_content = f"Your KYC verification has been {status}."
        if status == "rejected":
            notification_content += f" Reason: {rejection_reason}. Please resubmit with updated documents."
        
        try:
            notification = {
                "recipient_id": verified_user_id,
                "type": f"kyc_{status}",
                "content": notification_content,
                "related_kyc_id": kyc_id,
                "status": "unread"
            }
            await db.execute(
                supabase_client.table("notifications").insert(notification).execute
            )
            logger.info(f"[NOTIFICATION] Notified user {verified_user_id} of KYC {status}")
        except Exception as e:
            logger.warning(f"Could not send notification: {str(e)}")
        
        logger.info(f"[KYC {status.upper()}] KYC record {kyc_id} marked as {status}")
        
        return f"Success: KYC verification marked as {status}. User {verified_user_id} has been notified."
        
    except Exception as e:
        logger.error(f"[KYC APPROVAL ERROR] {str(e)}")
        return f"KYC approval failure: {str(e)}"
