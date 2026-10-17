"""Payment Router.

Endpoints for checkout initialisation, gateway webhooks, frontend callback
verification, wallet balance, transaction polling and admin fee splitting.

Design notes
------------
* Amounts are always computed server-side from the lease (see
  ``app.services.payment_service``). The client only ever sends a ``lease_id``.
* The webhook verifies the gateway signature *before* parsing, dedupes on the
  gateway reference and reconciles the amount before any money moves.
* Errors use one machine-readable envelope so the frontend branches on ``code``.
"""

from typing import Optional

from fastapi import APIRouter, Body, Depends, Header, Request, status
from fastapi.responses import JSONResponse

from app.core.dependecies import require_user_context
from app.models.payment import (
    PaymentError,
    PaymentInitRequest,
    PaymentInitResponse,
    PaymentStatusResponse,
    WebhookAck,
    WalletResponse,
)
from app.services.payment_gateway import WebhookVerificationError
from app.services.payment_service import (
    PaymentError as DomainPaymentError,
    build_default_payment_service,
)

router = APIRouter(prefix="/payments", tags=["Payments"])


def get_payment_service():
    """FastAPI dependency. Tests override this via ``app.dependency_overrides``."""
    return build_default_payment_service()


def _request_id(request: Request) -> Optional[str]:
    return getattr(request.state, "request_id", None)


def _error_response(exc: DomainPaymentError, request: Request) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http_status,
        content=PaymentError(
            code=exc.code,
            message=exc.message,
            transaction_id=exc.transaction_id,
            request_id=_request_id(request),
        ).model_dump(mode="json"),
    )


# ============================================================================
# 1. CHECKOUT INITIALISATION
# ============================================================================
@router.post("/initialize", response_model=PaymentInitResponse)
async def initialize_lease_payment(
    request: Request,
    body: PaymentInitRequest,
    context: dict = Depends(require_user_context),
    service=Depends(get_payment_service),
):
    """Start a payment for a lease.

    The server reads the amount from the lease; the returned ``checkout`` payload
    is handed to the gateway SDK/redirect. Safe to retry: while a transaction is
    open the endpoint returns 409 with the existing ``transaction_id``.
    """
    try:
        return await service.initialize(
            lease_id=body.lease_id,
            user_id=context["id"],
            email=context.get("email"),
        )
    except DomainPaymentError as exc:
        return _error_response(exc, request)


# ============================================================================
# 2. WEBHOOK
# ============================================================================
@router.post("/webhook", response_model=WebhookAck)
async def payment_gateway_webhook(
    request: Request,
    service=Depends(get_payment_service),
):
    """Gateway webhook listener (Paystack / Flutterwave / mock).

    Returns 401 for a bad signature, 200 for every verified event (including
    duplicates and ignored event types), and 500 only when our own datastore is
    unavailable so the gateway retries.
    """
    raw_body = await request.body()
    headers = dict(request.headers)

    try:
        return await service.handle_webhook(raw_body, headers)
    except WebhookVerificationError as exc:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=PaymentError(
                code="invalid_signature",
                message=str(exc),
                request_id=_request_id(request),
            ).model_dump(mode="json"),
        )
    except DomainPaymentError as exc:
        return _error_response(exc, request)
    except Exception as exc:  # datastore failure -> let the gateway retry
        logger_error = f"[PAYMENT WEBHOOK] unexpected failure: {exc}"
        import logging

        logging.getLogger("uvicorn").error(logger_error)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=PaymentError(
                code="internal_error",
                message="Webhook processing failed; please retry.",
                request_id=_request_id(request),
            ).model_dump(mode="json"),
        )


# ============================================================================
# 3. FRONTEND CALLBACK VERIFICATION + POLLING
# ============================================================================
@router.get("/verify/{reference}", response_model=PaymentStatusResponse)
async def verify_payment(
    reference: str,
    request: Request,
    context: dict = Depends(require_user_context),
    service=Depends(get_payment_service),
):
    """Reconcile a payment after the gateway redirects the browser back.

    Idempotent: safe for the frontend to call on every page load/poll.
    """
    try:
        return await service.verify(
            reference=reference,
            user_id=context["id"],
            role=context.get("role", "renter"),
        )
    except DomainPaymentError as exc:
        return _error_response(exc, request)


@router.get("/transactions/{transaction_id}", response_model=PaymentStatusResponse)
async def get_transaction_status(
    transaction_id: str,
    request: Request,
    context: dict = Depends(require_user_context),
    service=Depends(get_payment_service),
):
    """Poll a transaction's canonical status (used by the frontend spinner)."""
    try:
        return await service.get_transaction(
            transaction_id=transaction_id,
            user_id=context["id"],
            role=context.get("role", "renter"),
        )
    except DomainPaymentError as exc:
        return _error_response(exc, request)


# ============================================================================
# 4. WALLET
# ============================================================================
@router.get("/wallet", response_model=WalletResponse)
async def get_wallet(
    context: dict = Depends(require_user_context),
    service=Depends(get_payment_service),
):
    """Authenticated user's wallet balance and recent transactions."""
    return await service.get_wallet(context["id"])


# ============================================================================
# 5. ADMIN SPLIT FEE
# ============================================================================
@router.post("/{transaction_id}/split")
async def execute_payment_split(
    transaction_id: str,
    request: Request,
    landlord_percentage: float = Body(95.0, embed=True),
    context: dict = Depends(require_user_context),
    service=Depends(get_payment_service),
):
    """Admin endpoint to finalize fee distribution for a transaction."""
    try:
        result = await service.split(
            transaction_id=transaction_id,
            landlord_percentage=landlord_percentage,
            actor_id=context["id"],
            role=context.get("role", "renter"),
        )
    except DomainPaymentError as exc:
        return _error_response(exc, request)
    return {"status": "success", **(result or {})}
