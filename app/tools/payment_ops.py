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
    """Processes rental payment from renter atomically.
    
    - Validates lease and renter application status
    - Compiles final lease agreement and uploads PDF to Supabase Storage
    - Atomically writes transaction, ledger entries, lease updates, application completion, and notifications via PostgreSQL RPC
    """
    user_id = config.get("configurable", {}).get("user_id")
    
    if not user_id:
        return "Security Guardrail: User context missing."

    try:
        # 1. Fetch lease details
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
        landlord_id = lease.get("owner_id")
        
        # 2. Verify payment is from renter
        if user_id != lease.get("renter_id"):
            return "Security Guardrail: Only the renter can make payments on this lease."
        
        # 3. Check application status for initial lease activation
        is_initial_activation = lease.get("is_active")
        if not is_initial_activation:
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
        
        # 4. Calculate platform fee (5%)
        platform_fee = amount * 0.05
        landlord_amount = amount - platform_fee
        
        storage_path = lease.get("contract_url")
        renter_name = "Tenant"
        
        # 5. Handle PDF Generation & Storage Upload prior to DB transaction commit
        if is_initial_activation:
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

            storage_path = await generate_and_upload_lease_pdf(
                lease_id=lease_id,
                contract_text=final_contract_text
            )

        # 6. Execute Atomic PostgreSQL RPC Transaction
        rpc_payload = {
            "p_lease_id": lease_id,
            "p_payer_id": user_id,
            "p_amount": amount,
            "p_platform_fee": platform_fee,
            "p_landlord_amount": landlord_amount,
            "p_landlord_id": landlord_id,
            "p_storage_path": storage_path,
            "p_renter_name": renter_name,
            "p_is_initial_activation": is_initial_activation,
            "p_currency": "NGN",
        }

        rpc_res = await db.execute(
            supabase_client.rpc("process_payment_atomic", rpc_payload).execute
        )

        if not rpc_res or not rpc_res.data:
            return "Payment processing failure: Database transaction returned no response."

        result_data = rpc_res.data
        if isinstance(result_data, str):
            result_data = json.loads(result_data)

        transaction_id = result_data.get("transaction_id")
        logger.info(f"[ATOMIC PAYMENT SUCCESS] Transaction {transaction_id} processed successfully for lease {lease_id}")

        return (
            f"Success: Payment of ${amount}  processed atomically. Transaction ID: {transaction_id}. "
            f"Landlord will receive: ${landlord_amount:.2f} (after 5% platform fee: ${platform_fee:.2f}). "
            f"{'Lease is now ACTIVE and PDF agreement generated.' if is_initial_activation else ''}"
        )
        
    except Exception as e:
        logger.error(f"[PAYMENT ATOMIC ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
        return f"Payment processing failure (Rolled back): {str(e)}"

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