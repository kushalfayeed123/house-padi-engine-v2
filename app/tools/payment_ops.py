"""Payment tools exposed to the agent runtime.

These are thin adapters over :class:`app.services.payment_service.PaymentService`.
They intentionally **cannot** move money on their own:

* ``process_payment_worker`` either starts a real checkout (returning the
  gateway redirect for the frontend) or reconciles a payment that already has a
  gateway reference. It never marks a lease paid from a chat message.
* the amount is always derived server-side from the lease.
* ``split_payment_worker`` is admin-only and idempotent.
"""

import json
from typing import Optional

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from logging import getLogger

from app.services.payment_service import (
    PaymentError,
    build_default_payment_service,
)

logger = getLogger("uvicorn")


def _context(config: Optional[RunnableConfig]) -> tuple[Optional[str], str]:
    safe = config or {}
    configurable = safe.get("configurable", {}) or {}
    return configurable.get("user_id"), configurable.get("user_role", "renter")


@tool("process_payment_worker")
async def process_payment_worker(
    lease_id: str,
    config: RunnableConfig,
    gateway_ref: Optional[str] = None,
) -> str:
    """Handles rent payments for a lease.

    - With no ``gateway_ref``: starts (or resumes) checkout for the lease and
      returns the gateway redirect the user must complete. No money moves.
    - With a ``gateway_ref``: reconciles a payment that was already made
      through the gateway and finalises it if it succeeded.

    Use ``trigger_payment_ui_worker`` instead when the user simply wants to open
    the payments page.
    """
    user_id, role = _context(config)
    if not user_id:
        return "Security Guardrail: Execution halted due to absent tenant identity context."

    service = build_default_payment_service()
    try:
        if gateway_ref:
            status = await service.verify(gateway_ref, user_id, role)
            return json.dumps(status.model_dump(mode="json"))

        init = await service.initialize(lease_id, user_id)
        return json.dumps(
            {
                **init.model_dump(mode="json"),
                "action": "checkout_required",
                "message": "Please complete the payment in the checkout window.",
            }
        )
    except PaymentError as exc:
        logger.warning(f"[PAYMENT TOOL] {exc.code.value}: {exc.message}")
        return json.dumps(
            {"status": "error", "code": exc.code.value, "message": exc.message}
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(f"[PAYMENT TOOL] unexpected error: {exc}")
        return json.dumps(
            {"status": "error", "code": "internal_error", "message": "Payment could not be processed."}
        )


@tool("get_wallet_balance_worker")
async def get_wallet_balance_worker(config: RunnableConfig) -> str:
    """Retrieves the authenticated user's wallet balance and recent transactions."""
    user_id, _ = _context(config)
    if not user_id:
        return "Security Guardrail: User context missing."

    service = build_default_payment_service()
    try:
        wallet = await service.get_wallet(user_id)
        return wallet.model_dump_json()
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(f"[WALLET ERROR] {exc}")
        return json.dumps(
            {"status": "error", "code": "internal_error", "message": "Could not load wallet."}
        )


@tool("split_payment_worker")
async def split_payment_worker(
    transaction_id: str,
    config: RunnableConfig,
    landlord_percentage: float = 95,
) -> str:
    """STRICTLY FOR ADMINS: finalises fee distribution for a transaction."""
    user_id, role = _context(config)
    if role not in ("admin", "system"):
        return "Security Guardrail: Only administrators can finalize payment splits."
    if not user_id:
        return "Security Guardrail: User context missing."

    service = build_default_payment_service()
    try:
        result = await service.split(
            transaction_id=transaction_id,
            landlord_percentage=landlord_percentage,
            actor_id=user_id,
            role=role,
        )
        return json.dumps({"status": "success", **(result or {})})
    except PaymentError as exc:
        return json.dumps(
            {"status": "error", "code": exc.code.value, "message": exc.message}
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(f"[PAYMENT SPLIT ERROR] {exc}")
        return json.dumps(
            {"status": "error", "code": "internal_error", "message": "Payment split failed."}
        )


@tool("trigger_payment_ui_worker")
async def trigger_payment_ui_worker(config: RunnableConfig) -> str:
    """Triggered when the user wants to make a payment or go to the payment
    page — never for checking a balance, which get_wallet_balance_worker
    handles inline instead."""
    safe_config = config or {}
    user_id = (safe_config.get("configurable", {}) or {}).get("user_id")

    if not user_id:
        return "Security Guardrail: Execution halted due to absent tenant identity context."

    return json.dumps({"redirect_url": "/payments"})
