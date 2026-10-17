"""Payment orchestration.

``PaymentService`` is the only place that knows the payment rules:

* the server (never the client) decides how much is owed, by reading the lease;
* a payment can only start from an approved application and, if configured,
  only for a KYC-verified renter;
* every finalisation is idempotent, keyed on the gateway reference, so webhook
  retries and double-clicks cannot double-charge or double-activate a lease;
* the amount the gateway reports is reconciled against the lease before any
  money moves.

The service is transport-agnostic: routes and agent tools both call it, and it
only ever talks to a ``PaymentRepository`` + ``PaymentGateway`` (both injectable).
"""

from __future__ import annotations

from logging import getLogger
from typing import Any, Dict, Mapping, Optional

from app.core.config import settings
from app.models.payment import (
    NON_TERMINAL_STATUSES,
    CheckoutPayload,
    PaymentErrorCode,
    PaymentInitResponse,
    PaymentStatusResponse,
    TransactionStatus,
    TransactionView,
    WalletResponse,
    WalletTransactionView,
    WebhookAck,
)
from app.services.payment_gateway import (
    PaymentGateway,
    WebhookVerificationError,
    build_reference,
    get_gateway,
)
from app.services.payment_repository import SupabasePaymentRepository

logger = getLogger("uvicorn")

#: Application status that permits payment.
PAYABLE_APPLICATION_STATUS = "approved_pending_payment"

_LEGACY_STATUS_MAP = {
    "completed": TransactionStatus.SUCCEEDED,
    "success": TransactionStatus.SUCCEEDED,
    "successful": TransactionStatus.SUCCEEDED,
    "pending_payment": TransactionStatus.PENDING,
    "failed": TransactionStatus.FAILED,
    "cancelled": TransactionStatus.FAILED,
}


class PaymentError(Exception):
    """Domain error carrying a machine code and the HTTP status to return."""

    def __init__(
        self,
        code: PaymentErrorCode,
        message: str,
        http_status: int = 400,
        transaction_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.transaction_id = transaction_id


class PaymentService:
    def __init__(
        self,
        repository: Any,
        gateway: PaymentGateway,
        *,
        platform_fee_percent: Optional[float] = None,
        currency: Optional[str] = None,
        require_kyc: Optional[bool] = None,
        callback_url: Optional[str] = None,
    ) -> None:
        self.repo = repository
        self.gateway = gateway
        self.platform_fee_percent = (
            settings.PLATFORM_FEE_PERCENT
            if platform_fee_percent is None
            else platform_fee_percent
        )
        self.currency = currency or settings.DEFAULT_CURRENCY
        self.require_kyc = (
            settings.REQUIRE_KYC_FOR_PAYMENT if require_kyc is None else require_kyc
        )
        self.callback_url = callback_url or settings.PAYMENT_CALLBACK_URL

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _split_amount(self, amount: float) -> tuple[float, float]:
        fee = round(amount * (self.platform_fee_percent / 100.0), 2)
        return fee, round(amount - fee, 2)

    @staticmethod
    def _coerce_status(raw: Optional[str]) -> TransactionStatus:
        if raw in _LEGACY_STATUS_MAP:
            return _LEGACY_STATUS_MAP[raw]
        try:
            return TransactionStatus(raw)
        except (ValueError, TypeError):
            return TransactionStatus.PENDING

    async def _renter_name(self, renter_id: str) -> str:
        profile = await self.repo.get_profile(renter_id)
        if not profile:
            return "Tenant"
        name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
        return name or "Tenant"

    async def _finalize_from_lease(
        self,
        lease: Dict[str, Any],
        txn: Dict[str, Any],
        amount: float,
        event_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Shared, idempotent finalisation used by both webhook and verify."""
        fee, landlord_amount = self._split_amount(amount)
        is_initial = not bool(lease.get("is_active"))
        renter_id = str(lease.get("renter_id"))
        renter_name = await self._renter_name(renter_id)
        storage_path = None
        if is_initial:
            storage_path = await self.repo.generate_lease_document(lease, renter_name)
        try:
            return await self.repo.finalize_payment(
                lease_id=str(lease.get("id")),
                payer_id=str(txn.get("payer_id") or renter_id),
                amount=amount,
                platform_fee=fee,
                landlord_amount=landlord_amount,
                landlord_id=str(lease.get("owner_id")),
                storage_path=storage_path,
                renter_name=renter_name,
                is_initial_activation=is_initial,
                currency=(txn.get("currency") or self.currency),
                gateway_ref=str(txn.get("payment_gateway_ref")),
                event_id=event_id,
            )
        except Exception as exc:
            logger.error(f"[PAYMENT] atomic finalisation failed: {exc}")
            raise PaymentError(
                PaymentErrorCode.INTERNAL_ERROR,
                "Could not finalise the payment; the gateway will retry.",
                http_status=500,
                transaction_id=txn.get("id"),
            ) from exc

    async def _view(self, txn: Dict[str, Any]) -> TransactionView:
        lease = None
        if txn.get("lease_id"):
            lease = await self.repo.get_lease(str(txn["lease_id"]))
        return TransactionView(
            id=str(txn.get("id")),
            reference=str(txn.get("payment_gateway_ref") or ""),
            status=self._coerce_status(txn.get("status")),
            amount=float(txn.get("amount") or 0),
            currency=txn.get("currency") or self.currency,
            lease_id=(str(txn["lease_id"]) if txn.get("lease_id") else None),
            lease_activated=bool(lease.get("is_active")) if lease else False,
            created_at=txn.get("created_at"),
        )

    def _authorize_transaction(
        self, txn: Dict[str, Any], user_id: str, role: str
    ) -> None:
        if role in ("admin", "system"):
            return
        if str(txn.get("payer_id")) == str(user_id):
            return
        raise PaymentError(
            PaymentErrorCode.FORBIDDEN,
            "You are not allowed to view this transaction.",
            http_status=403,
        )

    # ------------------------------------------------------------------
    # 1. initialize
    # ------------------------------------------------------------------
    async def initialize(
        self, lease_id: str, user_id: str, email: Optional[str] = None
    ) -> PaymentInitResponse:
        lease = await self.repo.get_lease(lease_id)
        if not lease:
            raise PaymentError(
                PaymentErrorCode.LEASE_NOT_FOUND, "Lease not found.", http_status=404
            )
        if str(lease.get("renter_id")) != str(user_id):
            raise PaymentError(
                PaymentErrorCode.FORBIDDEN,
                "Only the renter on this lease can pay for it.",
                http_status=403,
            )
        if lease.get("is_active"):
            raise PaymentError(
                PaymentErrorCode.LEASE_NOT_PAYABLE,
                "This lease is already active.",
                http_status=409,
            )

        amount = float(lease.get("rent") or 0)
        if amount <= 0:
            raise PaymentError(
                PaymentErrorCode.LEASE_NOT_PAYABLE,
                "The lease has no payable rent amount.",
                http_status=409,
            )

        app_status = await self.repo.get_application_status(str(lease.get("id")))
        if app_status != PAYABLE_APPLICATION_STATUS:
            raise PaymentError(
                PaymentErrorCode.LEASE_NOT_PAYABLE,
                "The application for this lease has not been approved for payment yet.",
                http_status=409,
            )

        if self.require_kyc:
            kyc_status = await self.repo.get_kyc_status(str(user_id))
            if kyc_status != "verified":
                raise PaymentError(
                    PaymentErrorCode.KYC_REQUIRED,
                    "Identity verification (KYC) is required before making a payment.",
                    http_status=403,
                )

        existing = await self.repo.get_open_transaction(
            str(lease.get("id")), str(user_id)
        )
        if existing:
            raise PaymentError(
                PaymentErrorCode.PAYMENT_ALREADY_IN_PROGRESS,
                "A payment for this lease is already in progress.",
                http_status=409,
                transaction_id=str(existing.get("id")),
            )

        fee, _ = self._split_amount(amount)
        reference = build_reference(str(lease.get("id")))
        txn = await self.repo.create_transaction(
            lease_id=str(lease.get("id")),
            payer_id=str(user_id),
            amount=amount,
            platform_fee=fee,
            currency=self.currency,
            reference=reference,
            status=TransactionStatus.INITIALIZED.value,
        )

        try:
            session = await self.gateway.initialize(
                reference=reference,
                amount=amount,
                currency=self.currency,
                email=email,
                metadata={
                    "lease_id": str(lease.get("id")),
                    "user_id": str(user_id),
                    "transaction_id": str(txn.get("id")),
                    "reference": reference,
                },
                callback_url=self.callback_url,
            )
        except Exception as exc:
            logger.error(f"[PAYMENT] gateway initialise failed: {exc}")
            await self.repo.set_transaction_status(
                str(txn.get("id")), TransactionStatus.FAILED.value
            )
            raise PaymentError(
                PaymentErrorCode.GATEWAY_UNAVAILABLE,
                "The payment gateway is unavailable; please try again.",
                http_status=502,
                transaction_id=str(txn.get("id")),
            ) from exc

        return PaymentInitResponse(
            transaction_id=str(txn.get("id")),
            reference=reference,
            amount=amount,
            amount_minor=int(round(amount * 100)),
            currency=self.currency,
            gateway=self.gateway.name,
            checkout=CheckoutPayload(
                authorization_url=session.authorization_url,
                access_code=session.access_code,
                provider=session.provider,
            ),
            poll_url=f"/payments/transactions/{txn.get('id')}",
        )

    # ------------------------------------------------------------------
    # 2. webhook
    # ------------------------------------------------------------------
    async def handle_webhook(
        self, raw_body: bytes, headers: Mapping[str, str]
    ) -> WebhookAck:
        # Raises WebhookVerificationError -> route maps to 401. Nothing is
        # parsed or acted on before this succeeds.
        event = self.gateway.verify_webhook(raw_body, headers)

        if not event.is_success:
            return WebhookAck(status="ignored", event_type=event.event_type)

        metadata = event.metadata or {}
        reference = event.reference or metadata.get("reference")
        txn = await self.repo.get_transaction_by_reference(reference) if reference else None
        if txn is None and metadata.get("lease_id") and metadata.get("user_id"):
            txn = await self.repo.get_open_transaction(
                str(metadata["lease_id"]), str(metadata["user_id"])
            )
        if txn is None:
            logger.warning(f"[PAYMENT] webhook for unknown reference {reference!r}")
            return WebhookAck(
                status="ignored",
                event_type=event.event_type,
                detail="unknown_reference",
            )

        if self._coerce_status(txn.get("status")) == TransactionStatus.SUCCEEDED:
            return WebhookAck(
                status="success",
                replayed=True,
                event_type=event.event_type,
                transaction_id=str(txn.get("id")),
            )

        lease = await self.repo.get_lease(str(txn.get("lease_id")))
        if not lease:
            return WebhookAck(
                status="ignored", event_type=event.event_type, detail="lease_missing"
            )
        if str(txn.get("payer_id")) != str(lease.get("renter_id")):
            return WebhookAck(
                status="ignored", event_type=event.event_type, detail="payer_mismatch"
            )

        expected = float(lease.get("rent") or 0)
        if event.amount is not None and expected > 0 and event.amount + 0.01 < expected:
            await self.repo.set_transaction_status(
                str(txn.get("id")), TransactionStatus.FAILED.value
            )
            logger.warning(
                f"[PAYMENT] amount mismatch on {reference}: paid {event.amount}, owed {expected}"
            )
            return WebhookAck(
                status="ignored",
                event_type=event.event_type,
                detail="amount_mismatch",
                transaction_id=str(txn.get("id")),
            )

        amount = expected if expected > 0 else float(txn.get("amount") or 0)
        result = await self._finalize_from_lease(
            lease, txn, amount, event_id=event.event_id
        )
        logger.info(
            f"[PAYMENT] webhook finalised txn={result.get('transaction_id')} "
            f"replayed={result.get('replayed')}"
        )
        return WebhookAck(
            status="success",
            replayed=bool(result.get("replayed")),
            event_type=event.event_type,
            transaction_id=str(result.get("transaction_id")),
        )

    # ------------------------------------------------------------------
    # 3. verify (frontend callback) + polling
    # ------------------------------------------------------------------
    async def verify(
        self, reference: str, user_id: str, role: str = "renter"
    ) -> PaymentStatusResponse:
        txn = await self.repo.get_transaction_by_reference(reference)
        if not txn:
            raise PaymentError(
                PaymentErrorCode.TRANSACTION_NOT_FOUND,
                "No transaction found for that reference.",
                http_status=404,
            )
        self._authorize_transaction(txn, user_id, role)

        if self._coerce_status(txn.get("status")) in (
            TransactionStatus.INITIALIZED,
            TransactionStatus.PENDING,
            TransactionStatus.PROCESSING,
        ):
            await self._reconcile_with_gateway(txn)
            txn = await self.repo.get_transaction_by_id(str(txn.get("id"))) or txn

        return PaymentStatusResponse(transaction=await self._view(txn))

    async def get_transaction(
        self, transaction_id: str, user_id: str, role: str = "renter"
    ) -> PaymentStatusResponse:
        txn = await self.repo.get_transaction_by_id(transaction_id)
        if not txn:
            raise PaymentError(
                PaymentErrorCode.TRANSACTION_NOT_FOUND,
                "Transaction not found.",
                http_status=404,
            )
        self._authorize_transaction(txn, user_id, role)
        return PaymentStatusResponse(transaction=await self._view(txn))

    async def _reconcile_with_gateway(self, txn: Dict[str, Any]) -> None:
        """Ask the gateway for the authoritative state and settle if paid."""
        reference = str(txn.get("payment_gateway_ref"))
        try:
            remote = await self.gateway.verify_transaction(reference)
        except Exception as exc:
            logger.warning(f"[PAYMENT] gateway verify failed for {reference}: {exc}")
            return

        if remote.status == "failed":
            await self.repo.set_transaction_status(
                str(txn.get("id")), TransactionStatus.FAILED.value
            )
            return
        if remote.status != "succeeded":
            return

        lease = await self.repo.get_lease(str(txn.get("lease_id")))
        if not lease:
            return
        expected = float(lease.get("rent") or 0)
        amount = remote.amount if remote.amount is not None else expected
        if expected > 0 and amount + 0.01 < expected:
            await self.repo.set_transaction_status(
                str(txn.get("id")), TransactionStatus.FAILED.value
            )
            return
        await self._finalize_from_lease(lease, txn, amount or expected)

    # ------------------------------------------------------------------
    # 4. wallet
    # ------------------------------------------------------------------
    async def get_wallet(self, user_id: str) -> WalletResponse:
        wallet = await self.repo.get_wallet(user_id)
        balance = float((wallet or {}).get("balance") or 0)
        rows = await self.repo.list_transactions(user_id, limit=5)
        return WalletResponse(
            balance=balance,
            currency=self.currency,
            recent_transactions=[
                WalletTransactionView(
                    id=str(r.get("id")),
                    amount=float(r.get("amount") or 0),
                    platform_fee=(float(r["platform_fee"]) if r.get("platform_fee") is not None else None),
                    type=r.get("type"),
                    status=r.get("status"),
                    created_at=r.get("created_at"),
                )
                for r in rows
            ],
        )

    # ------------------------------------------------------------------
    # 5. admin split
    # ------------------------------------------------------------------
    async def split(
        self,
        transaction_id: str,
        landlord_percentage: float,
        actor_id: str,
        role: str,
    ) -> Dict[str, Any]:
        if role not in ("admin", "system"):
            raise PaymentError(
                PaymentErrorCode.FORBIDDEN,
                "Only administrators can finalize payment splits.",
                http_status=403,
            )
        if not 0 <= landlord_percentage <= 100:
            raise PaymentError(
                PaymentErrorCode.INTERNAL_ERROR,
                "landlord_percentage must be between 0 and 100.",
                http_status=422,
            )
        txn = await self.repo.get_transaction_by_id(transaction_id)
        if not txn:
            raise PaymentError(
                PaymentErrorCode.TRANSACTION_NOT_FOUND,
                "Transaction not found.",
                http_status=404,
            )
        try:
            return await self.repo.finalize_split(
                transaction_id=transaction_id,
                landlord_percentage=landlord_percentage,
                actor_id=actor_id,
            )
        except KeyError as exc:
            raise PaymentError(
                PaymentErrorCode.TRANSACTION_NOT_FOUND,
                "Transaction not found.",
                http_status=404,
            ) from exc
        except Exception as exc:
            logger.error(f"[PAYMENT] split failed for {transaction_id}: {exc}")
            raise PaymentError(
                PaymentErrorCode.INTERNAL_ERROR,
                "Could not finalize the payment split.",
                http_status=500,
            ) from exc


def build_default_payment_service() -> PaymentService:
    """Production wiring: Supabase repository + configured gateway."""
    return PaymentService(SupabasePaymentRepository(), get_gateway())
