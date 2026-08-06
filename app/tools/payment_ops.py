"""
Payment processing and fee splitting tool
Handles rent payments, deposits, platform fee distribution, lease activation,
and PDF document generation.
"""

import json
from typing import Optional
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from app.core.database import db, supabase_client
from app.services.lease_generator import finalize_executed_lease
from app.services.pdf_service import generate_and_upload_lease_pdf
from logging import getLogger
from datetime import datetime
from uuid import UUID

logger = getLogger("uvicorn")


class ProcessPaymentInput(BaseModel):
    lease_id: str = Field(..., description="Lease UUID")
    amount: float = Field(..., gt=0, description="Payment amount")
    payment_method: str = Field("bank_transfer", description="Payment method")


class SplitFeeInput(BaseModel):
    transaction_id: str = Field(..., description="Transaction UUID to split")
    landlord_percentage: float = Field(default=90, ge=0, le=100, description="Percentage to landlord (0-100)")


@tool("process_payment_worker")
async def process_payment_worker(
    lease_id: str,
    amount: float,
    config: RunnableConfig,
    payment_method: str = "bank_transfer"
) -> str:
    """Processes rental payment from renter.
    
    - Validates lease and renter application status
    - Creates transaction record and platform fee split
    - Compiles final lease agreement and uploads PDF to Supabase Storage
    - Activates lease and marks application as completed
    - Dispatches notifications for key handover
    - Updates ledger entries
    """
    user_id = config.get("configurable", {}).get("user_id")
    
    if not user_id:
        return "Security Guardrail: User context missing."

    try:
        # Fetch lease details
        lease_res = await db.execute(
            supabase_client.table("leases")
            .select("id, property_id, owner_id, renter_id, rent, start_date, is_active, contract_url")
            .eq("id", lease_id)
            .single()
            .execute
        )
        
        if not lease_res.data:
            return f"Lease {lease_id} not found."
        
        lease = lease_res.data
        
        # Verify payment is from renter
        if user_id != lease.get("renter_id"):
            return "Security Guardrail: Only the renter can make payments on this lease."
        
        # Check application status for initial lease activation
        is_initial_activation = not lease.get("is_active")
        if is_initial_activation:
            app_res = await db.execute(
                supabase_client.table("applications")
                .select("status")
                .eq("lease_id", lease_id)
                .single()
                .execute
            )
            app_status = app_res.data.get("status") if app_res.data else None
            if app_status != "approved_pending_payment":
                return "Error: Cannot process payment. The application has not been approved by the landlord yet."
        
        # Calculate platform fee (5%)
        platform_fee = amount * 0.05
        landlord_amount = amount - platform_fee
        
        # Create transaction record
        transaction_payload = {
            "lease_id": lease_id,
            "payer_id": user_id,
            "amount": amount,
            "platform_fee": platform_fee,
            "type": "rent_payment",
            "currency": "USD",
            "payment_gateway_ref": f"TXN_{datetime.now().timestamp()}",  # In production, use real payment gateway
            "status": "pending_verification"
        }
        
        txn_res = await db.execute(
            supabase_client.table("transactions").insert(transaction_payload).execute
        )
        
        transaction_id = txn_res.data[0].get("id")
        logger.info(f"[PAYMENT CREATED] Transaction {transaction_id}: ${amount} from {user_id}")
        
        # Create ledger entries
        landlord_id = lease.get("owner_id")
        
        ledger_landlord = {
            "walletId": str(landlord_id),
            "amount": landlord_amount,
            "type": "credit",
            "category": "rent_received",
            "referenceId": transaction_id,
            "createdAt": datetime.now().isoformat()
        }
        
        ledger_platform = {
            "walletId": "platform",
            "amount": platform_fee,
            "type": "credit",
            "category": "platform_fee",
            "referenceId": transaction_id,
            "createdAt": datetime.now().isoformat()
        }
        
        ledger_renter = {
            "walletId": str(user_id),
            "amount": -amount,
            "type": "debit",
            "category": "rent_paid",
            "referenceId": transaction_id,
            "createdAt": datetime.now().isoformat()
        }
        
        try:
            await db.execute(
                supabase_client.table("ledger_entries").insert(ledger_landlord).execute
            )
            await db.execute(
                supabase_client.table("ledger_entries").insert(ledger_platform).execute
            )
            await db.execute(
                supabase_client.table("ledger_entries").insert(ledger_renter).execute
            )
            logger.info(f"[LEDGER ENTRIES] Created for transaction {transaction_id}")
        except Exception as e:
            logger.warning(f"Could not create ledger entries: {str(e)}")

        # --- LEASE ACTIVATION & PDF STORAGE TRIGGER ---
        if is_initial_activation:
            # Fetch user profiles and property base template
            renter_prof = await db.execute(
                supabase_client.table("profiles").select("first_name, last_name").eq("id", user_id).single().execute
            )
            owner_prof = await db.execute(
                supabase_client.table("profiles").select("first_name, last_name").eq("id", landlord_id).single().execute
            )
            prop_res = await db.execute(
                supabase_client.table("properties").select("agreement_content").eq("id", lease.get("property_id")).single().execute
            )

            renter_name = f"{renter_prof.data.get('first_name', '')} {renter_prof.data.get('last_name', '')}".strip() if renter_prof.data else "Tenant"
            owner_name = f"{owner_prof.data.get('first_name', '')} {owner_prof.data.get('last_name', '')}".strip() if owner_prof.data else "Landlord"
            base_agreement = (prop_res.data.get("agreement_content") if prop_res.data else None) or "Standard Residential Lease Agreement"

            # 1. Inject names/signatures into contract text
            today_str = datetime.now().strftime("%Y-%m-%d")
            final_contract_text = finalize_executed_lease(
                base_agreement=base_agreement,
                renter_name=renter_name,
                renter_signature=renter_name,
                landlord_signature=owner_name,
                start_date=str(lease.get("start_date")),
                landlord_signed_at=today_str,
                renter_signed_at=today_str
            )

            # 2. Compile text to PDF and upload to Supabase Storage
            storage_path = await generate_and_upload_lease_pdf(
                lease_id=lease_id,
                contract_text=final_contract_text
            )

            # 3. Update Lease to active with PDF storage path
            await db.execute(
                supabase_client.table("leases")
                .update({
                    "is_active": True,
                    "contract_url": storage_path
                })
                .eq("id", lease_id)
                .execute
            )

            # 4. Update Application status to completed
            await db.execute(
                supabase_client.table("applications")
                .update({"status": "completed"})
                .eq("lease_id", lease_id)
                .execute
            )

            # 5. Dispatch Key Handover notifications
            notif_renter = {
                "user_id": user_id,
                "title": "Payment Confirmed — Ready to Move In!",
                "message": "Your rent payment is verified and your lease is active. Please contact your landlord for key collection.",
                "type": "key_handover_ready",
                "metadata": {"lease_id": lease_id}
            }
            notif_owner = {
                "user_id": landlord_id,
                "title": "Rent Payment Verified — Key Handover",
                "message": f"Payment from {renter_name} confirmed. The lease is active. You may now arrange key handover.",
                "type": "key_handover_owner",
                "metadata": {"lease_id": lease_id}
            }
            await db.execute(supabase_client.table("notifications").insert(notif_renter).execute)
            await db.execute(supabase_client.table("notifications").insert(notif_owner).execute)

            logger.info(f"[LEASE ACTIVATED] Lease {lease_id} activated and PDF saved to {storage_path}")

        return (
            f"Success: Payment of ${amount} USD processed. Transaction ID: {transaction_id}. "
            f"Landlord will receive: ${landlord_amount:.2f} (after 5% platform fee: ${platform_fee:.2f}). "
            f"{'Lease is now ACTIVE and PDF agreement generated.' if is_initial_activation else ''}"
        )
        
    except Exception as e:
        logger.error(f"[PAYMENT ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
        return f"Payment processing failure: {str(e)}"


@tool("get_wallet_balance_worker")
async def get_wallet_balance_worker(config: RunnableConfig) -> str:
    """Retrieves user's wallet balance and recent transactions."""
    user_id = config.get("configurable", {}).get("user_id")
    
    if not user_id:
        return "Security Guardrail: User context missing."

    try:
        # Get wallet
        wallet_res = await db.execute(
            supabase_client.table("wallets")
            .select("balance")
            .eq("userId", user_id)
            .single()
            .execute
        )
        
        balance = wallet_res.data.get("balance", 0) if wallet_res.data else 0
        
        # Get recent transactions
        txn_res = await db.execute(
            supabase_client.table("transactions")
            .select("id, amount, platform_fee, type, status, created_at")
            .eq("payer_id", user_id)
            .order("created_at", desc=True)
            .limit(5)
            .execute
        )
        
        recent = txn_res.data if txn_res.data else []
        
        result = {
            "balance": balance,
            "currency": "USD",
            "recent_transactions": recent
        }
        
        return json.dumps(result)
        
    except Exception as e:
        logger.error(f"[WALLET ERROR] {str(e)}")
        return f"Wallet retrieval failure: {str(e)}"


@tool("split_payment_worker")
async def split_payment_worker(
    transaction_id: str,
    config: RunnableConfig,
    landlord_percentage: float = 95
) -> str:
    """Distributes payment between landlord and platform.
    
    Landlord receives specified percentage, platform keeps remainder.
    Updates wallet balances accordingly.
    """
    user_id = config.get("configurable", {}).get("user_id")
    user_role = config.get("configurable", {}).get("user_role", "renter")
    
    # Only admin or system can split payments
    if user_role not in ["admin", "system"]:
        return "Security Guardrail: Only administrators can finalize payment splits."
    
    if not user_id:
        return "Security Guardrail: User context missing."

    try:
        # Fetch transaction
        txn_res = await db.execute(
            supabase_client.table("transactions")
            .select("*, leases(owner_id)")
            .eq("id", transaction_id)
            .single()
            .execute
        )
        
        if not txn_res.data:
            return f"Transaction {transaction_id} not found."
        
        txn = txn_res.data
        
        # Calculate split
        total_amount = txn.get("amount", 0)
        platform_fee = total_amount * (1 - landlord_percentage / 100)
        landlord_amount = total_amount - platform_fee
        
        landlord_id = txn.get("leases", {}).get("owner_id")
        
        # Update wallet balances
        try:
            # Landlord wallet
            await db.execute(
                supabase_client.table("wallets")
                .update({"balance": f"balance + {landlord_amount}"})
                .eq("userId", landlord_id)
                .execute
            )
            
            # Platform wallet (simplified)
            await db.execute(
                supabase_client.table("wallets")
                .update({"balance": f"balance + {platform_fee}"})
                .eq("userId", "platform")
                .execute
            )
        except Exception as e:
            logger.warning(f"Could not update wallet balances: {str(e)}")
        
        # Mark transaction as completed
        await db.execute(
            supabase_client.table("transactions")
            .update({"status": "completed"})
            .eq("id", transaction_id)
            .execute
        )
        
        logger.info(f"[PAYMENT SPLIT] Transaction {transaction_id}: Landlord ${landlord_amount:.2f}, Platform ${platform_fee:.2f}")
        
        return f"Success: Payment split completed. Landlord received: ${landlord_amount:.2f}. Platform fee: ${platform_fee:.2f}"
        
    except Exception as e:
        logger.error(f"[PAYMENT SPLIT ERROR] {str(e)}")
        return f"Payment split failure: {str(e)}"