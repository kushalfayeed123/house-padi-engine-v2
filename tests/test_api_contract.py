"""Application-level contract tests.

These import the *real* ``app.main`` application (not a stripped-down test app)
to prove the payment routes are wired, the OpenAPI schema exposes the frontend
contract, and CORS is not configured with the invalid wildcard+credentials
combination.
"""


def test_app_imports_and_exposes_payment_routes():
    from app.main import app

    paths = set(app.openapi()["paths"])
    assert "/payments/initialize" in paths
    assert "/payments/webhook" in paths
    assert "/payments/verify/{reference}" in paths
    assert "/payments/transactions/{transaction_id}" in paths
    assert "/payments/wallet" in paths
    assert "/healthz" in paths
    assert "/readyz" in paths


def test_openapi_documents_the_payment_contract():
    from app.main import app

    schema = app.openapi()
    assert "/payments/initialize" in schema["paths"]
    init_schema = schema["paths"]["/payments/initialize"]["post"]["responses"]["200"]
    ref = init_schema["content"]["application/json"]["schema"]["$ref"]
    model_name = ref.rsplit("/", 1)[-1]
    model = schema["components"]["schemas"][model_name]
    assert {"transaction_id", "reference", "amount", "amount_minor", "currency", "checkout", "poll_url"} <= set(
        model["properties"]
    )


def test_cors_never_uses_wildcard_with_credentials():
    from app.core.config import settings

    assert "*" not in settings.cors_origins


def test_production_config_validation_flags_unsafe_defaults():
    from app.core.config import Settings

    prod = Settings(
        SUPABASE_URL="https://x.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY="k",
        ENVIRONMENT="production",
        PAYMENT_PROVIDER="mock",
        CORS_ORIGINS="",
    )
    problems = prod.validate_for_production()
    assert any("CORS_ORIGINS" in p for p in problems)
    assert any("PAYMENT_PROVIDER" in p for p in problems)

    # A wildcard origin is always rejected in production.
    wildcard = Settings(
        SUPABASE_URL="https://x.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY="k",
        ENVIRONMENT="production",
        PAYMENT_PROVIDER="paystack",
        CORS_ORIGINS="*",
        PAYMENT_SECRET_KEY="sk_test",
        PAYMENT_CALLBACK_URL="https://app.housepadi.test/payments/callback",
        INTERNAL_CRON_SECRET="cron-secret",
    )
    assert any("must not contain '*'" in p for p in wildcard.validate_for_production())
