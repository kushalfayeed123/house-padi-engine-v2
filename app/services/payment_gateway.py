"""Payment gateway abstraction.

The rest of the codebase talks to a :class:`PaymentGateway`, never to a
specific provider's HTTP API. Each provider is responsible for:

* verifying the authenticity of an inbound webhook **before** anything is
  parsed or acted upon, and
* normalising provider-specific quirks (minor units, header names, event
  names, payload shape) into the small dataclasses below.

Amounts crossing this boundary are always in **major units** (naira, dollars).
Providers that speak in minor units (Paystack kobo, Stripe cents) convert here,
which removes the old ``/100 if "paystack" in url`` heuristic.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from logging import getLogger
from typing import Any, Dict, Mapping, Optional

import httpx

from app.core.config import settings

logger = getLogger("uvicorn")


class WebhookVerificationError(Exception):
    """Raised when a webhook signature is missing or invalid."""


@dataclass
class NormalizedEvent:
    """Provider-agnostic view of an inbound webhook."""

    event_type: str
    is_success: bool
    reference: Optional[str]
    event_id: Optional[str]
    amount: Optional[float]  # major units
    currency: Optional[str]
    channel: Optional[str]
    metadata: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GatewayTransaction:
    """Provider-agnostic view of a transaction looked up from the gateway."""

    reference: str
    status: str  # succeeded | failed | pending
    amount: Optional[float]  # major units
    currency: Optional[str]
    channel: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CheckoutSession:
    authorization_url: Optional[str]
    access_code: Optional[str]
    provider: str


class PaymentGateway(ABC):
    name: str = "base"

    @abstractmethod
    def verify_webhook(
        self, raw_body: bytes, headers: Mapping[str, str]
    ) -> NormalizedEvent:
        """Validate the signature and return a normalised event.

        Must raise :class:`WebhookVerificationError` if the signature does not
        match. Must never trust the body before the signature is verified.
        """

    @abstractmethod
    async def initialize(
        self,
        *,
        reference: str,
        amount: float,
        currency: str,
        email: Optional[str],
        metadata: Dict[str, Any],
        callback_url: Optional[str],
    ) -> CheckoutSession:
        """Create a checkout session with the provider."""

    @abstractmethod
    async def verify_transaction(self, reference: str) -> GatewayTransaction:
        """Fetch the authoritative state of a transaction from the provider."""


def _lower(headers: Mapping[str, str]) -> Dict[str, str]:
    return {str(k).lower(): v for k, v in headers.items()}


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Mock gateway — used for local development and tests. No network calls.
# ---------------------------------------------------------------------------
class MockGateway(PaymentGateway):
    name = "mock"

    def __init__(self, secret: str, callback_url: Optional[str] = None) -> None:
        self._secret = secret
        self._callback_url = callback_url
        # In-memory registry so a dev flow can initialize then verify.
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def sign(self, raw_body: bytes) -> str:
        return hmac.new(self._secret.encode(), raw_body, hashlib.sha512).hexdigest()

    def verify_webhook(
        self, raw_body: bytes, headers: Mapping[str, str]
    ) -> NormalizedEvent:
        lowered = _lower(headers)
        provided = lowered.get("x-paystack-signature") or lowered.get("x-webhook-signature")
        if not self._secret or not provided:
            raise WebhookVerificationError("Missing webhook signature.")
        expected = self.sign(raw_body)
        if not hmac.compare_digest(expected, provided):
            raise WebhookVerificationError("Invalid webhook signature.")

        payload = json.loads(raw_body.decode("utf-8"))
        data = payload.get("data", payload)
        event_type = payload.get("event") or payload.get("type") or "unknown"
        is_success = event_type in {
            "charge.success",
            "payment_intent.succeeded",
            "successful_payment",
        }
        return NormalizedEvent(
            event_type=event_type,
            is_success=is_success,
            reference=data.get("reference") or data.get("tx_ref") or data.get("id"),
            event_id=str(data.get("id") or data.get("reference") or ""),
            amount=_to_float(data.get("amount")),
            currency=data.get("currency"),
            channel=data.get("channel"),
            metadata=data.get("metadata") or {},
            raw=payload,
        )

    async def initialize(
        self,
        *,
        reference: str,
        amount: float,
        currency: str,
        email: Optional[str],
        metadata: Dict[str, Any],
        callback_url: Optional[str],
    ) -> CheckoutSession:
        self._sessions[reference] = {
            "amount": amount,
            "currency": currency,
            "metadata": metadata,
            "channel": "card",
        }
        base = callback_url or self._callback_url or ""
        return CheckoutSession(
            authorization_url=(
                f"{base}?reference={reference}" if base else f"mock://checkout/{reference}"
            ),
            access_code=f"mock_{reference}",
            provider=self.name,
        )

    async def verify_transaction(self, reference: str) -> GatewayTransaction:
        session = self._sessions.get(reference)
        if not session:
            return GatewayTransaction(
                reference=reference, status="failed", amount=None, currency=None
            )
        return GatewayTransaction(
            reference=reference,
            status="succeeded",
            amount=session["amount"],
            currency=session["currency"],
            channel=session.get("channel"),
            metadata=session.get("metadata", {}),
        )


# ---------------------------------------------------------------------------
# Paystack
# ---------------------------------------------------------------------------
class PaystackGateway(PaymentGateway):
    name = "paystack"
    BASE_URL = "https://api.paystack.co"

    def __init__(self, secret_key: str, callback_url: Optional[str] = None) -> None:
        self._secret_key = secret_key
        self._callback_url = callback_url

    def verify_webhook(
        self, raw_body: bytes, headers: Mapping[str, str]
    ) -> NormalizedEvent:
        lowered = _lower(headers)
        provided = lowered.get("x-paystack-signature")
        if not self._secret_key or not provided:
            raise WebhookVerificationError("Missing Paystack signature.")
        expected = hmac.new(
            self._secret_key.encode(), raw_body, hashlib.sha512
        ).hexdigest()
        if not hmac.compare_digest(expected, provided):
            raise WebhookVerificationError("Invalid Paystack signature.")

        payload = json.loads(raw_body.decode("utf-8"))
        data = payload.get("data", {})
        event_type = payload.get("event", "unknown")
        return NormalizedEvent(
            event_type=event_type,
            is_success=event_type == "charge.success",
            reference=data.get("reference"),
            event_id=str(data.get("id") or data.get("reference") or ""),
            # Paystack reports kobo -> convert to major units.
            amount=(_to_float(data.get("amount")) or 0) / 100.0,
            currency=data.get("currency"),
            channel=data.get("channel"),
            metadata=data.get("metadata") or {},
            raw=payload,
        )

    async def initialize(
        self,
        *,
        reference: str,
        amount: float,
        currency: str,
        email: Optional[str],
        metadata: Dict[str, Any],
        callback_url: Optional[str],
    ) -> CheckoutSession:
        body: Dict[str, Any] = {
            "email": email or "no-reply@housepadi.app",
            "amount": int(round(amount * 100)),  # kobo
            "currency": currency,
            "reference": reference,
            "metadata": metadata,
        }
        if callback_url or self._callback_url:
            body["callback_url"] = callback_url or self._callback_url
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{self.BASE_URL}/transaction/initialize",
                json=body,
                headers={"Authorization": f"Bearer {self._secret_key}"},
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
        return CheckoutSession(
            authorization_url=data.get("authorization_url"),
            access_code=data.get("access_code"),
            provider=self.name,
        )

    async def verify_transaction(self, reference: str) -> GatewayTransaction:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.BASE_URL}/transaction/verify/{reference}",
                headers={"Authorization": f"Bearer {self._secret_key}"},
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
        status_map = {"success": "succeeded", "failed": "failed", "abandoned": "failed"}
        return GatewayTransaction(
            reference=reference,
            status=status_map.get(data.get("status"), "pending"),
            amount=(_to_float(data.get("amount")) or 0) / 100.0,
            currency=data.get("currency"),
            channel=data.get("channel"),
            metadata=data.get("metadata") or {},
            raw=data,
        )


# ---------------------------------------------------------------------------
# Flutterwave
# ---------------------------------------------------------------------------
class FlutterwaveGateway(PaymentGateway):
    name = "flutterwave"
    BASE_URL = "https://api.flutterwave.com/v3"

    def __init__(
        self,
        secret_key: str,
        webhook_hash: str,
        callback_url: Optional[str] = None,
    ) -> None:
        self._secret_key = secret_key
        self._webhook_hash = webhook_hash
        self._callback_url = callback_url

    def verify_webhook(
        self, raw_body: bytes, headers: Mapping[str, str]
    ) -> NormalizedEvent:
        lowered = _lower(headers)
        provided = lowered.get("verif-hash")
        if not self._webhook_hash or not provided:
            raise WebhookVerificationError("Missing Flutterwave verif-hash.")
        if not hmac.compare_digest(self._webhook_hash, provided):
            raise WebhookVerificationError("Invalid Flutterwave verif-hash.")

        payload = json.loads(raw_body.decode("utf-8"))
        data = payload.get("data", {})
        status = (data.get("status") or "").lower()
        return NormalizedEvent(
            event_type=payload.get("event") or "unknown",
            is_success=status == "successful",
            reference=data.get("tx_ref"),
            event_id=str(data.get("id") or data.get("tx_ref") or ""),
            amount=_to_float(data.get("amount")),  # Flutterwave uses major units
            currency=data.get("currency"),
            channel=data.get("payment_type"),
            metadata=payload.get("meta_data") or data.get("meta_data") or {},
            raw=payload,
        )

    async def initialize(
        self,
        *,
        reference: str,
        amount: float,
        currency: str,
        email: Optional[str],
        metadata: Dict[str, Any],
        callback_url: Optional[str],
    ) -> CheckoutSession:
        body = {
            "tx_ref": reference,
            "amount": amount,
            "currency": currency,
            "redirect_url": callback_url or self._callback_url,
            "customer": {"email": email or "no-reply@housepadi.app"},
            "meta": metadata,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{self.BASE_URL}/payments",
                json=body,
                headers={"Authorization": f"Bearer {self._secret_key}"},
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
        return CheckoutSession(
            authorization_url=data.get("link"),
            access_code=data.get("tx_ref"),
            provider=self.name,
        )

    async def verify_transaction(self, reference: str) -> GatewayTransaction:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.BASE_URL}/transactions/verify_by_reference",
                params={"tx_ref": reference},
                headers={"Authorization": f"Bearer {self._secret_key}"},
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
        status = (data.get("status") or "").lower()
        return GatewayTransaction(
            reference=reference,
            status=(
                "succeeded"
                if status == "successful"
                else ("failed" if status else "pending")
            ),
            amount=_to_float(data.get("amount")),
            currency=data.get("currency"),
            channel=data.get("payment_type"),
            raw=data,
        )


def get_gateway() -> PaymentGateway:
    """Build the gateway configured by ``PAYMENT_PROVIDER``."""
    provider = settings.PAYMENT_PROVIDER
    secret = settings.payment_secret_key
    callback = settings.PAYMENT_CALLBACK_URL

    if provider == "paystack":
        return PaystackGateway(secret, callback_url=callback)
    if provider == "flutterwave":
        return FlutterwaveGateway(secret, settings.webhook_secret, callback_url=callback)
    if provider == "stripe":
        raise NotImplementedError(
            "Stripe gateway not wired yet; set PAYMENT_PROVIDER to paystack/flutterwave/mock."
        )
    return MockGateway(settings.webhook_secret, callback_url=callback)


def build_reference(lease_id: str) -> str:
    """Unique, human-readable, provider-safe transaction reference."""
    suffix = hashlib.sha1(f"{lease_id}:{time.time_ns()}".encode()).hexdigest()[:10]
    return f"HP-{str(lease_id)[:8]}-{suffix}"
