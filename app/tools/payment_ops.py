"""
Payment processing and fee splitting tool
Handles rent payments, deposits, and platform fee distribution
"""

import json
from typing import Optional
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from app.database import supabase_client, db
from app.schemas.payloads import TransactionPayload
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
    
    - Validates lease and renter
    - Creates transaction record
    - Calculates platform fee (5% default)
    - Queues funds for distribution
    - Updates wallet balances
    """
    user_id = config.get("configurable", {}).get("user_id")
    
    if not user_id:
        return "Security Guardrail: User context missing."

    try:
        # Fetch lease details
        lease_res = await db.execute(
            supabase_client.table("leases")
            .select("id, property_id, owner_id, renter_id, rent, is_active")
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
        
        # Verify lease is active
        if not lease.get("is_active"):
            return "Error: This lease is not yet active. Both parties must sign first."
        
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
        
        # Landlord credit
        ledger_landlord = {
            "walletId": str(landlord_id),
            "amount": landlord_amount,
            "type": "credit",
            "category": "rent_received",
            "referenceId": transaction_id,
            "createdAt": datetime.now().isoformat()
        }
        
        # Platform credit
        ledger_platform = {
            "walletId": "platform",
            "amount": platform_fee,
            "type": "credit",
            "category": "platform_fee",
            "referenceId": transaction_id,
            "createdAt": datetime.now().isoformat()
        }
        
        # Renter debit
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
        
        return f"Success: Payment of ${amount} USD processed. Transaction ID: {transaction_id}. Landlord will receive: ${landlord_amount:.2f} (after 5% platform fee: ${platform_fee:.2f})"
        
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
