"""Persistence for the payment domain.

Two implementations:

* :class:`SupabasePaymentRepository` — production. All multi-write money
  operations go through Postgres functions (``process_payment_atomic``,
  ``finalize_payment_split``) so they are atomic and idempotent.
* :class:`InMemoryPaymentRepository` — used by tests and local dev so the
  service can be exercised without a database.

Keeping this behind an interface is what makes the payment flow testable:
``PaymentService`` never touches the Supabase client directly.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from logging import getLogger
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.core.database import db, supabase_client

logger = getLogger("uvicorn")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SupabasePaymentRepository:
    """Production repository backed by Supabase Postgres."""

    def __init__(self, client=supabase_client) -> None:
        self._client = client

    # -- reads ---------------------------------------------------------
    async def get_lease(self, lease_id: str) -> Optional[Dict[str, Any]]:
        res = await db.execute(
            self._client.table("leases")
            .select(
                "id, property_id, owner_id, renter_id, rent, start_date, is_active, contract_url"
            )
            .eq("id", lease_id)
            .maybe_single()
            .execute
        )
        return getattr(res, "data", None) if res else None

    async def get_application_status(self, lease_id: str) -> Optional[str]:
        res = await db.execute(
            self._client.table("applications")
            .select("status")
            .eq("lease_id", str(lease_id))
            .maybe_single()
            .execute
        )
        data = getattr(res, "data", None) if res else None
        return data.get("status") if data else None

    async def get_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        res = await db.execute(
            self._client.table("profiles")
            .select("id, first_name, last_name, email, kyc_status")
            .eq("id", user_id)
            .maybe_single()
            .execute
        )
        return getattr(res, "data", None) if res else None

    async def get_kyc_status(self, user_id: str) -> Optional[str]:
        res = await db.execute(
            self._client.table("kyc_verifications")
            .select("status")
            .eq("user_id", user_id)
            .maybe_single()
            .execute
        )
        data = getattr(res, "data", None) if res else None
        if data and data.get("status"):
            return data.get("status")
        profile = await self.get_profile(user_id)
        return profile.get("kyc_status") if profile else None

    async def get_open_transaction(
        self, lease_id: str, payer_id: str
    ) -> Optional[Dict[str, Any]]:
        res = await db.execute(
            self._client.table("transactions")
            .select("*")
            .eq("lease_id", lease_id)
            .eq("payer_id", payer_id)
            .in_("status", ["initialized", "pending", "processing"])
            .order("created_at", desc=True)
            .limit(1)
            .execute
        )
        rows = getattr(res, "data", None) if res else None
        return rows[0] if rows else None

    async def get_transaction_by_reference(
        self, reference: str
    ) -> Optional[Dict[str, Any]]:
        res = await db.execute(
            self._client.table("transactions")
            .select("*")
            .eq("payment_gateway_ref", reference)
            .maybe_single()
            .execute
        )
        return getattr(res, "data", None) if res else None

    async def get_transaction_by_id(self, txn_id: str) -> Optional[Dict[str, Any]]:
        res = await db.execute(
            self._client.table("transactions")
            .select("*")
            .eq("id", txn_id)
            .maybe_single()
            .execute
        )
        return getattr(res, "data", None) if res else None

    async def get_wallet(self, user_id: str) -> Optional[Dict[str, Any]]:
        res = await db.execute(
            self._client.table("wallets")
            .select("balance, userId")
            .eq("userId", user_id)
            .maybe_single()
            .execute
        )
        return getattr(res, "data", None) if res else None

    async def list_transactions(self, user_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        res = await db.execute(
            self._client.table("transactions")
            .select("id, amount, platform_fee, type, status, created_at")
            .eq("payer_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute
        )
        return getattr(res, "data", None) or []

    # -- writes --------------------------------------------------------
    async def create_transaction(
        self,
        *,
        lease_id: str,
        payer_id: str,
        amount: float,
        platform_fee: float,
        currency: str,
        reference: str,
        status: str,
    ) -> Dict[str, Any]:
        res = await db.execute(
            self._client.table("transactions")
            .insert(
                {
                    "lease_id": lease_id,
                    "payer_id": payer_id,
                    "amount": amount,
                    "platform_fee": platform_fee,
                    "type": "rent_payment",
                    "currency": currency,
                    "payment_gateway_ref": reference,
                    "status": status,
                }
            )
            .select()
            .execute
        )
        rows = getattr(res, "data", None) if res else None
        return rows[0] if rows else {}

    async def set_transaction_status(self, txn_id: str, status: str) -> None:
        await db.execute(
            self._client.table("transactions")
            .update({"status": status})
            .eq("id", txn_id)
            .execute
        )

    async def generate_lease_document(
        self, lease: Dict[str, Any], renter_name: str
    ) -> Optional[str]:
        """Compile + upload the executed lease PDF; returns the storage path."""
        from app.services.lease_generator import finalize_executed_lease
        from app.services.pdf_service import generate_and_upload_lease_pdf

        try:
            owner = await self.get_profile(lease.get("owner_id"))
            owner_name = (
                f"{owner.get('first_name', '')} {owner.get('last_name', '')}".strip()
                if owner
                else "Landlord"
            )
            prop_res = await db.execute(
                self._client.table("properties")
                .select("agreement_content")
                .eq("id", lease.get("property_id"))
                .maybe_single()
                .execute
            )
            prop = getattr(prop_res, "data", None) if prop_res else None
            base_agreement = (prop or {}).get("agreement_content") or (
                "Standard Residential Lease Agreement"
            )
            today = datetime.now().strftime("%Y-%m-%d")
            text = finalize_executed_lease(
                base_agreement=base_agreement,
                renter_name=renter_name,
                renter_signature=renter_name,
                landlord_signature=owner_name,
                start_date=str(lease.get("start_date")),
                landlord_signed_at=today,
                renter_signed_at=today,
            )
            return await generate_and_upload_lease_pdf(
                lease_id=str(lease.get("id")), contract_text=text
            )
        except Exception as exc:  # pragma: no cover - network/storage path
            logger.error(f"[PAYMENT] lease PDF generation failed: {exc}")
            return None

    async def finalize_payment(
        self,
        *,
        lease_id: str,
        payer_id: str,
        amount: float,
        platform_fee: float,
        landlord_amount: float,
        landlord_id: str,
        storage_path: Optional[str],
        renter_name: str,
        is_initial_activation: bool,
        currency: str,
        gateway_ref: str,
        event_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = {
            "p_lease_id": lease_id,
            "p_payer_id": payer_id,
            "p_amount": amount,
            "p_platform_fee": platform_fee,
            "p_landlord_amount": landlord_amount,
            "p_landlord_id": landlord_id,
            "p_storage_path": storage_path,
            "p_renter_name": renter_name,
            "p_is_initial_activation": is_initial_activation,
            "p_currency": currency,
            "p_gateway_ref": gateway_ref,
            "p_event_id": event_id,
        }
        res = await db.execute(
            self._client.rpc("process_payment_atomic", payload).execute
        )
        data = getattr(res, "data", None) if res else None
        if isinstance(data, list):
            data = data[0] if data else None
        if not data:
            raise RuntimeError("process_payment_atomic returned no data")
        return data

    async def finalize_split(
        self,
        *,
        transaction_id: str,
        landlord_percentage: float,
        actor_id: str,
    ) -> Dict[str, Any]:
        res = await db.execute(
            self._client.rpc(
                "finalize_payment_split",
                {
                    "p_transaction_id": transaction_id,
                    "p_landlord_percentage": landlord_percentage,
                    "p_actor_id": actor_id,
                },
            ).execute
        )
        data = getattr(res, "data", None) if res else None
        if isinstance(data, list):
            data = data[0] if data else None
        if not data:
            raise RuntimeError("finalize_payment_split returned no data")
        return data


class InMemoryPaymentRepository:
    """Test/dev repository. Emulates the idempotency + ledger semantics of the
    Postgres functions closely enough to exercise the whole service layer.
    """

    def __init__(
        self,
        leases: Optional[Dict[str, Dict[str, Any]]] = None,
        applications: Optional[Dict[str, str]] = None,
        profiles: Optional[Dict[str, Dict[str, Any]]] = None,
        kyc: Optional[Dict[str, str]] = None,
        wallets: Optional[Dict[str, float]] = None,
    ) -> None:
        self.leases = {k: dict(v) for k, v in (leases or {}).items()}
        self.applications = dict(applications or {})
        self.profiles = {k: dict(v) for k, v in (profiles or {}).items()}
        self.kyc = dict(kyc or {})
        self.wallets = dict(wallets or {})
        self.transactions: Dict[str, Dict[str, Any]] = {}
        self.ledger: List[Dict[str, Any]] = []
        self.splits: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _find_by_reference(self, reference: str) -> Optional[Dict[str, Any]]:
        for txn in self.transactions.values():
            if txn.get("payment_gateway_ref") == reference:
                return txn
        return None

    async def get_lease(self, lease_id: str) -> Optional[Dict[str, Any]]:
        return self.leases.get(str(lease_id))

    async def get_application_status(self, lease_id: str) -> Optional[str]:
        return self.applications.get(str(lease_id))

    async def get_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        return self.profiles.get(str(user_id))

    async def get_kyc_status(self, user_id: str) -> Optional[str]:
        if str(user_id) in self.kyc:
            return self.kyc[str(user_id)]
        profile = self.profiles.get(str(user_id))
        return profile.get("kyc_status") if profile else None

    async def get_open_transaction(
        self, lease_id: str, payer_id: str
    ) -> Optional[Dict[str, Any]]:
        for txn in self.transactions.values():
            if (
                str(txn.get("lease_id")) == str(lease_id)
                and str(txn.get("payer_id")) == str(payer_id)
                and txn.get("status") in {"initialized", "pending", "processing"}
            ):
                return txn
        return None

    async def get_transaction_by_reference(
        self, reference: str
    ) -> Optional[Dict[str, Any]]:
        return self._find_by_reference(reference)

    async def get_transaction_by_id(self, txn_id: str) -> Optional[Dict[str, Any]]:
        return self.transactions.get(str(txn_id))

    async def get_wallet(self, user_id: str) -> Optional[Dict[str, Any]]:
        if str(user_id) not in self.wallets:
            return None
        return {"balance": self.wallets[str(user_id)], "userId": str(user_id)}

    async def list_transactions(self, user_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        rows = [
            t
            for t in self.transactions.values()
            if str(t.get("payer_id")) == str(user_id)
        ]
        rows.sort(key=lambda t: t.get("created_at", ""), reverse=True)
        return rows[:limit]

    async def create_transaction(
        self,
        *,
        lease_id: str,
        payer_id: str,
        amount: float,
        platform_fee: float,
        currency: str,
        reference: str,
        status: str,
    ) -> Dict[str, Any]:
        txn = {
            "id": str(uuid4()),
            "lease_id": str(lease_id),
            "payer_id": str(payer_id),
            "amount": float(amount),
            "platform_fee": float(platform_fee),
            "type": "rent_payment",
            "currency": currency,
            "payment_gateway_ref": reference,
            "status": status,
            "created_at": _utcnow_iso(),
        }
        self.transactions[txn["id"]] = txn
        return txn

    async def set_transaction_status(self, txn_id: str, status: str) -> None:
        if str(txn_id) in self.transactions:
            self.transactions[str(txn_id)]["status"] = status

    async def generate_lease_document(
        self, lease: Dict[str, Any], renter_name: str
    ) -> Optional[str]:
        return f"leases/{lease.get('id')}/agreement.pdf"

    async def finalize_payment(
        self,
        *,
        lease_id: str,
        payer_id: str,
        amount: float,
        platform_fee: float,
        landlord_amount: float,
        landlord_id: str,
        storage_path: Optional[str],
        renter_name: str,
        is_initial_activation: bool,
        currency: str,
        gateway_ref: str,
        event_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            existing = self._find_by_reference(gateway_ref)
            if existing is not None and existing.get("status") in ("succeeded", "completed"):
                return {
                    "transaction_id": existing["id"],
                    "replayed": True,
                    "status": existing.get("status"),
                }

            if existing is not None:
                # A row created by /payments/initialize: settle it in place.
                txn = existing
                txn["status"] = "succeeded"
                txn["amount"] = float(amount)
                txn["platform_fee"] = float(platform_fee)
                txn["currency"] = currency
            else:
                txn = await self.create_transaction(
                    lease_id=lease_id,
                    payer_id=payer_id,
                    amount=amount,
                    platform_fee=platform_fee,
                    currency=currency,
                    reference=gateway_ref,
                    status="succeeded",
                )

            self.ledger.append(
                {
                    "transaction_id": txn["id"],
                    "walletId": str(payer_id),
                    "amount": amount,
                    "type": "debit",
                    "category": "rent",
                }
            )
            self.ledger.append(
                {
                    "transaction_id": txn["id"],
                    "walletId": str(landlord_id),
                    "amount": landlord_amount,
                    "type": "credit",
                    "category": "rent",
                }
            )
            self.ledger.append(
                {
                    "transaction_id": txn["id"],
                    "walletId": "platform",
                    "amount": platform_fee,
                    "type": "credit",
                    "category": "fee",
                }
            )
            self.wallets[str(landlord_id)] = (
                float(self.wallets.get(str(landlord_id), 0.0)) + float(landlord_amount)
            )

            if is_initial_activation and str(lease_id) in self.leases:
                self.leases[str(lease_id)]["is_active"] = True
                if storage_path:
                    self.leases[str(lease_id)]["contract_url"] = storage_path
                self.applications[str(lease_id)] = "completed"

            return {
                "transaction_id": txn["id"],
                "replayed": False,
                "status": "succeeded",
            }

    async def finalize_split(
        self,
        *,
        transaction_id: str,
        landlord_percentage: float,
        actor_id: str,
    ) -> Dict[str, Any]:
        with self._lock:
            txn = self.transactions.get(str(transaction_id))
            if txn is None:
                raise KeyError("transaction not found")
            if str(transaction_id) in self.splits:
                existing = self.splits[str(transaction_id)]
                return {**existing, "replayed": True}

            total = float(txn.get("amount", 0))
            platform_fee = total * (1 - landlord_percentage / 100.0)
            landlord_amount = total - platform_fee
            landlord_id = str(self.leases.get(str(txn.get("lease_id")), {}).get("owner_id"))

            self.wallets[landlord_id] = (
                float(self.wallets.get(landlord_id, 0.0)) + landlord_amount
            )
            self.wallets["platform"] = (
                float(self.wallets.get("platform", 0.0)) + platform_fee
            )
            txn["status"] = "completed"
            result = {
                "transaction_id": str(transaction_id),
                "landlord_amount": landlord_amount,
                "platform_fee": platform_fee,
                "replayed": False,
            }
            self.splits[str(transaction_id)] = result
            return result
