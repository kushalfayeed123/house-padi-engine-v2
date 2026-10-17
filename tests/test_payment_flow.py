"""End-to-end behaviour tests for the payment flow.

These assert the security/integrity properties called out in
``PRODUCTION_READINESS_PLAN.md``: server-authoritative amounts, webhook
signature enforcement, idempotency, amount reconciliation, KYC/state gating and
the frontend response contract.
"""

import json

import pytest

from tests.conftest import (
    LANDLORD_ID,
    LEASE_ID,
    RENT,
    RENTER_ID,
    STRANGER_ID,
    WEBHOOK_SECRET,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def signed(gateway, payload: dict):
    body = json.dumps(payload).encode()
    return body, {"x-paystack-signature": gateway.sign(body)}


def success_payload(reference: str, amount: float = RENT, event_id: str = "evt_1"):
    return {
        "event": "charge.success",
        "data": {
            "id": event_id,
            "reference": reference,
            "amount": amount,
            "currency": "NGN",
            "channel": "card",
            "metadata": {"lease_id": LEASE_ID, "user_id": RENTER_ID},
        },
    }


def initialize(client, gateway):
    resp = client.post("/payments/initialize", json={"lease_id": LEASE_ID})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 1. initialize
# ---------------------------------------------------------------------------
def test_initialize_uses_server_side_amount(client, gateway):
    body = initialize(client, gateway)
    assert body["amount"] == RENT
    assert body["amount_minor"] == int(RENT * 100)
    assert body["currency"] == "NGN"
    assert body["gateway"] == "mock"
    assert body["reference"].startswith("HP-")
    assert body["poll_url"] == f"/payments/transactions/{body['transaction_id']}"
    assert body["checkout"]["authorization_url"].endswith(body["reference"])


def test_initialize_ignores_client_supplied_amount(client):
    # A client that tries to dictate the amount is ignored entirely.
    resp = client.post(
        "/payments/initialize",
        json={"lease_id": LEASE_ID, "amount": 1, "user_id": STRANGER_ID},
    )
    assert resp.status_code == 200
    assert resp.json()["amount"] == RENT


def test_initialize_creates_initialized_transaction(client, repo):
    body = initialize(client, None)
    txn = repo.transactions[body["transaction_id"]]
    assert txn["status"] == "initialized"
    assert txn["payment_gateway_ref"] == body["reference"]


@pytest.mark.asyncio
async def test_initialize_rejects_non_renter(service):
    from app.models.payment import PaymentErrorCode
    from app.services.payment_service import PaymentError

    with pytest.raises(PaymentError) as err:
        await service.initialize(LEASE_ID, STRANGER_ID)
    assert err.value.code == PaymentErrorCode.FORBIDDEN
    assert err.value.http_status == 403


@pytest.mark.asyncio
async def test_initialize_requires_kyc(service, repo):
    from app.models.payment import PaymentErrorCode
    from app.services.payment_service import PaymentError

    repo.kyc[RENTER_ID] = "pending"
    with pytest.raises(PaymentError) as err:
        await service.initialize(LEASE_ID, RENTER_ID)
    assert err.value.code == PaymentErrorCode.KYC_REQUIRED


@pytest.mark.asyncio
async def test_initialize_requires_approved_application(service, repo):
    from app.models.payment import PaymentErrorCode
    from app.services.payment_service import PaymentError

    repo.applications[LEASE_ID] = "pending_landlord_approval"
    with pytest.raises(PaymentError) as err:
        await service.initialize(LEASE_ID, RENTER_ID)
    assert err.value.code == PaymentErrorCode.LEASE_NOT_PAYABLE


@pytest.mark.asyncio
async def test_initialize_is_idempotent_while_open(service):
    from app.models.payment import PaymentErrorCode
    from app.services.payment_service import PaymentError

    first = await service.initialize(LEASE_ID, RENTER_ID)
    with pytest.raises(PaymentError) as err:
        await service.initialize(LEASE_ID, RENTER_ID)
    assert err.value.code == PaymentErrorCode.PAYMENT_ALREADY_IN_PROGRESS
    assert err.value.transaction_id == first.transaction_id


# ---------------------------------------------------------------------------
# 2. webhook security + idempotency
# ---------------------------------------------------------------------------
def test_webhook_without_signature_is_401_and_writes_nothing(client, repo):
    body = json.dumps(success_payload("HP-x")).encode()
    resp = client.post("/payments/webhook", content=body)
    assert resp.status_code == 401
    assert resp.json()["code"] == "invalid_signature"
    assert repo.transactions == {}


def test_webhook_with_bad_signature_is_401_and_writes_nothing(client, repo):
    body = json.dumps(success_payload("HP-x")).encode()
    resp = client.post(
        "/payments/webhook",
        content=body,
        headers={"x-paystack-signature": "deadbeef"},
    )
    assert resp.status_code == 401
    assert repo.transactions == {}


def test_webhook_settles_payment_and_is_idempotent(client, gateway, repo):
    init = initialize(client, gateway)
    body, headers = signed(gateway, success_payload(init["reference"], event_id="evt_100"))

    first = client.post("/payments/webhook", content=body, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "success"
    assert first.json()["replayed"] is False

    # Money moved exactly once, atomically.
    assert repo.leases[LEASE_ID]["is_active"] is True
    assert repo.wallets[LANDLORD_ID] == pytest.approx(RENT * 0.95)
    assert repo.applications[LEASE_ID] == "completed"
    ledger = repo.ledger
    assert len(ledger) == 3
    assert {row["type"] for row in ledger} == {"debit", "credit"}

    # Replay of the same webhook (gateways retry) must be a no-op.
    second = client.post("/payments/webhook", content=body, headers=headers)
    assert second.status_code == 200
    assert second.json()["replayed"] is True
    assert repo.wallets[LANDLORD_ID] == pytest.approx(RENT * 0.95)
    assert len(repo.transactions) == 1
    assert len(repo.ledger) == 3


def test_webhook_amount_mismatch_does_not_activate(client, gateway, repo):
    init = initialize(client, gateway)
    body, headers = signed(
        gateway, success_payload(init["reference"], amount=RENT / 2, event_id="evt_200")
    )
    resp = client.post("/payments/webhook", content=body, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["detail"] == "amount_mismatch"
    assert repo.leases[LEASE_ID]["is_active"] is False
    assert repo.wallets[LANDLORD_ID] == 0.0
    assert repo.transactions[init["transaction_id"]]["status"] == "failed"


def test_webhook_ignores_non_success_event(client, gateway, repo):
    payload = success_payload("HP-nope")
    payload["event"] = "charge.failed"
    body, headers = signed(gateway, payload)
    resp = client.post("/payments/webhook", content=body, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
    assert repo.transactions == {}


def test_webhook_ignores_unknown_reference(client, gateway, repo):
    body, headers = signed(gateway, success_payload("HP-unknown", event_id="evt_300"))
    resp = client.post("/payments/webhook", content=body, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
    assert resp.json()["detail"] == "unknown_reference"
    assert repo.transactions == {}


# ---------------------------------------------------------------------------
# 3. frontend callback verification + polling
# ---------------------------------------------------------------------------
def test_verify_endpoint_reconciles_and_returns_contract(client, gateway, repo):
    init = initialize(client, gateway)
    resp = client.get(f"/payments/verify/{init['reference']}")
    assert resp.status_code == 200, resp.text
    txn = resp.json()["transaction"]
    assert txn["status"] == "succeeded"
    assert txn["amount"] == RENT
    assert txn["currency"] == "NGN"
    assert txn["lease_activated"] is True
    assert repo.wallets[LANDLORD_ID] == pytest.approx(RENT * 0.95)

    # Verifying again is idempotent (frontend may poll/refresh).
    again = client.get(f"/payments/verify/{init['reference']}")
    assert again.status_code == 200
    assert repo.wallets[LANDLORD_ID] == pytest.approx(RENT * 0.95)
    assert len(repo.transactions) == 1


def test_verify_unknown_reference_is_404(client):
    resp = client.get("/payments/verify/HP-does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["code"] == "transaction_not_found"


def test_transaction_polling_returns_initialized_status(client, gateway):
    init = initialize(client, gateway)
    resp = client.get(f"/payments/transactions/{init['transaction_id']}")
    assert resp.status_code == 200
    assert resp.json()["transaction"]["status"] == "initialized"


def test_error_envelope_has_machine_code(client):
    resp = client.post("/payments/initialize", json={"lease_id": "not-a-real-lease"})
    assert resp.status_code == 404
    body = resp.json()
    assert body["status"] == "error"
    assert body["code"] == "lease_not_found"
    assert "request_id" in body


# ---------------------------------------------------------------------------
# 4. wallet
# ---------------------------------------------------------------------------
def test_wallet_returns_zero_when_no_row(client):
    resp = client.get("/payments/wallet")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["balance"] == 0
    assert body["currency"] == "NGN"
    assert body["recent_transactions"] == []


# ---------------------------------------------------------------------------
# 5. admin split
# ---------------------------------------------------------------------------
def _client_as(service, user_id, role):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.dependecies import require_user_context
    from app.routes import payment_routes

    app = FastAPI()
    app.include_router(payment_routes.router)
    app.dependency_overrides[payment_routes.get_payment_service] = lambda: service
    app.dependency_overrides[require_user_context] = lambda: {
        "id": user_id,
        "email": "x@example.com",
        "role": role,
        "profile": {},
    }
    return TestClient(app)


def test_split_is_admin_only(client, gateway, repo, service):
    init = initialize(client, gateway)
    body, headers = signed(gateway, success_payload(init["reference"], event_id="evt_400"))
    client.post("/payments/webhook", content=body, headers=headers)

    renter = _client_as(service, RENTER_ID, "renter")
    denied = renter.post(
        f"/payments/{init['transaction_id']}/split", json={"landlord_percentage": 95}
    )
    assert denied.status_code == 403

    admin = _client_as(
        service, "00000000-0000-0000-0000-000000000001", "admin"
    )
    ok = admin.post(
        f"/payments/{init['transaction_id']}/split", json={"landlord_percentage": 95}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "success"

    # Idempotent: a second split does not double-credit.
    balance_after_first = repo.wallets.get("platform", 0.0)
    again = admin.post(
        f"/payments/{init['transaction_id']}/split", json={"landlord_percentage": 95}
    )
    assert again.status_code == 200
    assert again.json().get("replayed") is True
    assert repo.wallets.get("platform", 0.0) == balance_after_first


# ---------------------------------------------------------------------------
# 6. agent-facing tool cannot move money on its own
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_payment_tool_has_no_client_amount_argument():
    from app.tools import payment_ops

    fields = payment_ops.process_payment_worker.args_schema.model_fields
    assert "amount" not in fields  # the LLM cannot dictate a price
    assert "gateway_ref" in fields


@pytest.mark.asyncio
async def test_payment_tool_only_starts_checkout_without_gateway_ref(
    monkeypatch, service, repo
):
    from app.tools import payment_ops

    monkeypatch.setattr(payment_ops, "build_default_payment_service", lambda: service)
    config = {"configurable": {"user_id": RENTER_ID, "user_role": "renter"}}

    raw = await payment_ops.process_payment_worker.ainvoke(
        {"lease_id": LEASE_ID}, config=config
    )
    out = json.loads(raw)
    assert out["action"] == "checkout_required"
    assert out["amount"] == RENT

    # Crucially: no lease activation and no wallet movement from a chat turn.
    assert repo.leases[LEASE_ID]["is_active"] is False
    assert repo.wallets[LANDLORD_ID] == 0.0
