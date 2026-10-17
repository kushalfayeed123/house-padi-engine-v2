"""Test configuration.

Environment variables must be set *before* any ``app.*`` import because
``app.core.config.settings`` is instantiated (and cached) at import time.
"""

import os

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("PAYMENT_PROVIDER", "mock")
os.environ.setdefault("PAYMENT_WEBHOOK_SECRET", "test-webhook-secret")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DEFAULT_CURRENCY", "NGN")

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


LEASE_ID = "11111111-1111-1111-1111-111111111111"
RENTER_ID = "22222222-2222-2222-2222-222222222222"
LANDLORD_ID = "33333333-3333-3333-3333-333333333333"
STRANGER_ID = "44444444-4444-4444-4444-444444444444"
WEBHOOK_SECRET = "test-webhook-secret"
RENT = 1500000.0


@pytest.fixture
def repo():
    from app.services.payment_repository import InMemoryPaymentRepository

    return InMemoryPaymentRepository(
        leases={
            LEASE_ID: {
                "id": LEASE_ID,
                "property_id": "99999999-9999-9999-9999-999999999999",
                "owner_id": LANDLORD_ID,
                "renter_id": RENTER_ID,
                "rent": RENT,
                "start_date": "2026-11-01",
                "is_active": False,
                "contract_url": None,
            }
        },
        applications={LEASE_ID: "approved_pending_payment"},
        profiles={
            RENTER_ID: {
                "id": RENTER_ID,
                "first_name": "Ada",
                "last_name": "Obi",
                "email": "ada@example.com",
                "kyc_status": "verified",
            },
            LANDLORD_ID: {
                "id": LANDLORD_ID,
                "first_name": "John",
                "last_name": "Adeyemi",
                "kyc_status": "verified",
            },
        },
        kyc={RENTER_ID: "verified"},
        wallets={LANDLORD_ID: 0.0},
    )


@pytest.fixture
def gateway():
    from app.services.payment_gateway import MockGateway

    return MockGateway(WEBHOOK_SECRET, callback_url="https://app.housepadi.test/payments/callback")


@pytest.fixture
def service(repo, gateway):
    from app.services.payment_service import PaymentService

    return PaymentService(
        repo,
        gateway,
        platform_fee_percent=5.0,
        currency="NGN",
        require_kyc=True,
        callback_url="https://app.housepadi.test/payments/callback",
    )


@pytest.fixture
def client(service):
    """TestClient over a minimal app exposing only the payment router,
    with auth overridden to the renter by default."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.dependecies import require_user_context
    from app.routes import payment_routes

    app = FastAPI()
    app.include_router(payment_routes.router)
    app.dependency_overrides[payment_routes.get_payment_service] = lambda: service
    app.dependency_overrides[require_user_context] = lambda: {
        "id": RENTER_ID,
        "email": "ada@example.com",
        "role": "renter",
        "profile": {},
    }
    return TestClient(app)
