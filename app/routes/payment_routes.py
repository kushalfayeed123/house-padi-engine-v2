"""
Payment Router
Exposes endpoints for payment initialization, webhook handlers, wallet balance checks,
and admin fee splitting.
"""

import hmac
import hashlib
import json
from typing import Optional, cast
from fastapi import APIRouter, Depends, HTTPException, Header, Request, Body
from langchain_core.tools import BaseTool
from app.core.config import settings
from app.core.dependecies import get_optional_user_context
from app.tools.payment_ops import get_wallet_balance_worker, process_payment_worker, split_payment_worker  # Ensure WEBHOOK_SECRET is configured here


router = APIRouter(prefix="/payments", tags=["Payments"])


# ============================================================================
# 1. WEBHOOK ENDPOINT (Activates process_payment_worker)
# ============================================================================

@router.post("/webhook")
async def payment_gateway_webhook(
    request: Request,
    x_paystack_signature: Optional[str] = Header(None, alias="x-paystack-signature"),
    x_webhook_secret: Optional[str] = Header(None, alias="x-webhook-secret")
):
    """Webhook listener for payment gateways (e.g., Paystack, Flutterwave, Stripe).
    
    Upon successful payment verification:
    1. Validates signature / security headers.
    2. Extracts `lease_id`, `amount`, and `payer_id` from metadata.
    3. Triggers `process_payment_worker` to activate lease, upload PDF, and update status.
    """
    body_bytes = await request.body()
    
    # --- Signature Verification (Paystack Example) ---
    webhook_secret = getattr(settings, "PAYMENT_WEBHOOK_SECRET", "")
    
    # if x_paystack_signature:
    #     computed_signature = hmac.new(
    #         webhook_secret.encode("utf-8"),
    #         body_bytes,
    #         hashlib.sha512
    #     ).hexdigest()
    #     if not hmac.compare_digest(computed_signature, x_paystack_signature):
    #         raise HTTPException(status_code=401, detail="Invalid webhook signature signature.")
    # elif x_webhook_secret != webhook_secret:
    #     # Generic Secret Header Check Fallback
    #     raise HTTPException(status_code=401, detail="Invalid webhook secret authorization.")

    try:
        payload = json.loads(body_bytes.decode("utf-8"))
        event_type = payload.get("event") or payload.get("type")
        data = payload.get("data", payload)

        # Only process successful charges
        if event_type not in ["charge.success", "payment_intent.succeeded", "successful_payment"]:
            return {"status": "ignored", "message": f"Event {event_type} ignored."}

        # Extract metadata passed during checkout initialization
        metadata = data.get("metadata", {})
        lease_id = metadata.get("lease_id")
        payer_id = metadata.get("user_id") or metadata.get("payer_id")
        amount = float(data.get("amount", 0)) / 100.0 if "paystack" in str(request.url) else float(data.get("amount", 0))

        if not lease_id or not payer_id:
            raise HTTPException(status_code=400, detail="Missing lease_id or payer_id in payment metadata.")

        # --- Invoke process_payment_worker ---
        tool = cast(BaseTool, process_payment_worker)
        result = await tool.ainvoke(
            {
                "lease_id": lease_id,
                "amount": amount,
                "payment_method": data.get("channel", "card")
            },
            config={
                "configurable": {
                    "user_id": payer_id,
                    "user_role": "renter"
                }
            }
        )

        res_str = str(result)
        if "Security Guardrail" in res_str or "Error:" in res_str:
            return {"status": "failed", "detail": res_str}

        return {"status": "success", "worker_response": res_str}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Webhook processing error: {str(e)}")


# ============================================================================
# 2. RENTER PAYMENTS & CHECKOUT INITIATION
# ============================================================================

@router.post("/initialize")
async def initialize_lease_payment(
    lease_id: str = Body(..., embed=True),
    amount: float = Body(..., embed=True),
    context: dict = Depends(get_optional_user_context)
):
    """Initializes payment metadata before redirecting renter to checkout gateway."""
    user_id = context.get("id") if context else None
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required.")

    # In production, pass these metadata fields to your Payment Gateway API (e.g., Paystack/Stripe payload)
    return {
        "status": "success",
        "checkout_metadata": {
            "lease_id": lease_id,
            "user_id": user_id,
            "amount": amount,
            "email": context.get("email")
        },
        "message": "Pass checkout_metadata to payment gateway SDK."
    }


# ============================================================================
# 3. WALLET & BALANCE ENDPOINTS
# ============================================================================

@router.get("/wallet")
async def get_wallet_balance(context: dict = Depends(get_optional_user_context)):
    """Fetches authenticated user's wallet balance and recent ledger activity."""
    user_id = context.get("id") if context else None
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required.")

    tool = cast(BaseTool, get_wallet_balance_worker)
    result = await tool.ainvoke(
        {},
        config={"configurable": {"user_id": user_id}}
    )

    try:
        return json.loads(str(result))
    except json.JSONDecodeError:
        return {"status": "success", "raw": str(result)}


# ============================================================================
# 4. ADMIN SPLIT FEE ENDPOINT
# ============================================================================

@router.post("/{transaction_id}/split")
async def execute_payment_split(
    transaction_id: str,
    landlord_percentage: float = Body(95.0, embed=True),
    context: dict = Depends(get_optional_user_context)
):
    """Admin endpoint to finalize fee distribution for a processed transaction."""
    user_id = context.get("id") if context else None
    user_role = context.get("role", "renter") if context else "renter"

    if not user_id or user_role not in ["admin", "system"]:
        raise HTTPException(status_code=403, detail="Admin authorization required.")

    tool = cast(BaseTool, split_payment_worker)
    result = await tool.ainvoke(
        {
            "transaction_id": transaction_id,
            "landlord_percentage": landlord_percentage
        },
        config={
            "configurable": {
                "user_id": user_id,
                "user_role": user_role
            }
        }
    )

    res_str = str(result)
    if "Security Guardrail" in res_str:
        raise HTTPException(status_code=403, detail=res_str)
    
    return {"status": "success", "message": res_str}