"""Typed request/response contracts for the payment API.

These models are the single source of truth for the JSON the frontend sees.
They are intentionally explicit (no bare dicts) so that the OpenAPI schema at
``/api/v1/docs`` can be used to generate frontend types and contract tests.

Money convention
----------------
* ``amount`` and every other money field the UI renders is a **number in major
  units** (naira, dollars), e.g. ``1500000`` for ₦1,500,000.
* ``amount_minor`` is an **integer in minor units** (kobo/cents) for gateway
  SDKs, e.g. ``150000000``. The frontend must never divide by 100 itself.
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class TransactionStatus(str, Enum):
    """Canonical transaction lifecycle. Mirrored by a DB CHECK constraint."""

    INITIALIZED = "initialized"
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUNDED = "refunded"


#: Statuses that mean "keep polling, not decided yet".
NON_TERMINAL_STATUSES = frozenset(
    {
        TransactionStatus.INITIALIZED.value,
        TransactionStatus.PENDING.value,
        TransactionStatus.PROCESSING.value,
    }
)


class PaymentErrorCode(str, Enum):
    """Machine-readable error codes so the frontend never parses prose."""

    AUTH_REQUIRED = "auth_required"
    FORBIDDEN = "forbidden"
    LEASE_NOT_FOUND = "lease_not_found"
    LEASE_NOT_PAYABLE = "lease_not_payable"
    KYC_REQUIRED = "kyc_required"
    PAYMENT_ALREADY_IN_PROGRESS = "payment_already_in_progress"
    AMOUNT_MISMATCH = "amount_mismatch"
    INVALID_SIGNATURE = "invalid_signature"
    GATEWAY_UNAVAILABLE = "gateway_unavailable"
    TRANSACTION_NOT_FOUND = "transaction_not_found"
    INTERNAL_ERROR = "internal_error"


class PaymentInitRequest(BaseModel):
    lease_id: str = Field(..., description="Lease UUID the renter is paying for")


class CheckoutPayload(BaseModel):
    """Everything the frontend needs to hand off to the gateway SDK/redirect."""

    authorization_url: Optional[str] = Field(
        None, description="URL to redirect the browser to for hosted checkout"
    )
    access_code: Optional[str] = Field(
        None, description="Provider access/reference code for inline SDKs"
    )
    provider: str = Field(..., description="Gateway identifier, e.g. 'paystack'")


class PaymentInitResponse(BaseModel):
    status: str = "success"
    transaction_id: str
    reference: str
    amount: float = Field(..., description="Amount in major units")
    amount_minor: int = Field(..., description="Amount in minor units (kobo/cents)")
    currency: str
    gateway: str
    checkout: CheckoutPayload
    poll_url: str = Field(..., description="Relative URL to poll for status")


class TransactionView(BaseModel):
    """Canonical, pollable view of a transaction for the frontend."""

    id: str
    reference: str
    status: TransactionStatus
    amount: float
    currency: str
    lease_id: Optional[str] = None
    lease_activated: bool = False
    receipt_url: Optional[str] = None
    created_at: Optional[datetime] = None


class PaymentStatusResponse(BaseModel):
    status: str = "success"
    transaction: TransactionView


class WebhookAck(BaseModel):
    """Response returned to the gateway. Always 200 for verified events."""

    status: str
    replayed: bool = False
    event_type: Optional[str] = None
    transaction_id: Optional[str] = None
    detail: Optional[str] = None


class WalletTransactionView(BaseModel):
    id: str
    amount: float
    platform_fee: Optional[float] = None
    type: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[datetime] = None


class WalletResponse(BaseModel):
    status: str = "success"
    balance: float = 0
    currency: str
    recent_transactions: List[WalletTransactionView] = Field(default_factory=list)


class PaymentError(BaseModel):
    """Uniform error envelope returned by every payment endpoint."""

    status: str = "error"
    code: PaymentErrorCode
    message: str
    transaction_id: Optional[str] = None
    request_id: Optional[str] = None
