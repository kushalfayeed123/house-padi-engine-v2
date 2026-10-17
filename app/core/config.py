"""Central application configuration.

Design rules
------------
* Only configuration that the process genuinely cannot start without is
  marked required. Everything else has a safe default so a missing optional
  key degrades a feature instead of crashing the whole service.
* Production-safety is enforced explicitly via ``validate_for_production``,
  which is called from the FastAPI lifespan so misconfiguration surfaces as
  one clear startup error rather than a mid-request failure.
"""

from functools import lru_cache
from typing import List, Optional

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ------------------------------------------------------------------
    # Required: without these the service cannot talk to its datastore.
    # ------------------------------------------------------------------
    SUPABASE_URL: str
    SUPABASE_SERVICE_ROLE_KEY: SecretStr

    # ------------------------------------------------------------------
    # Environment
    # ------------------------------------------------------------------
    ENVIRONMENT: str = "development"  # development | staging | production
    LOG_LEVEL: str = "INFO"

    # Comma-separated allow-list. Empty means "no cross-origin access", which
    # is the safe default; development adds localhost explicitly.
    CORS_ORIGINS: str = ""

    # ------------------------------------------------------------------
    # Optional infrastructure / third-party keys
    # ------------------------------------------------------------------
    BACKEND_JWT_SECRET: Optional[str] = None
    OPENAI_API_KEY: Optional[SecretStr] = None
    GROQ_API_KEY: Optional[SecretStr] = None
    OPENROUTER_API_KEY: Optional[SecretStr] = None

    # Internal cron endpoint. No default: if unset, the endpoint refuses to run.
    INTERNAL_CRON_SECRET: Optional[str] = None

    # ------------------------------------------------------------------
    # Payments
    # ------------------------------------------------------------------
    PAYMENT_PROVIDER: str = "mock"  # mock | paystack | flutterwave | stripe
    PAYMENT_SECRET_KEY: Optional[SecretStr] = None
    PAYMENT_WEBHOOK_SECRET: Optional[SecretStr] = None
    # Percentage of a rent payment retained by the platform (rest goes to landlord).
    PLATFORM_FEE_PERCENT: float = Field(default=5.0, ge=0.0, le=100.0)
    DEFAULT_CURRENCY: str = "NGN"
    # Where the gateway should send the browser after checkout (frontend route).
    PAYMENT_CALLBACK_URL: Optional[str] = None
    # Public base URL of this API, used to build absolute links.
    PUBLIC_BASE_URL: Optional[str] = None
    # Require KYC verification before a rent payment can be completed.
    REQUIRE_KYC_FOR_PAYMENT: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------
    @field_validator("PAYMENT_PROVIDER")
    @classmethod
    def _normalise_provider(cls, value: str) -> str:
        return (value or "mock").strip().lower()


    @property
    def cors_origins(self) -> List[str]:
        raw = [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]
        if raw:
            return raw
        if self.is_production:
            return []
        # Development convenience only — never used in production.
        return ["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:3000"]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def environment(self) -> str:
        return (self.ENVIRONMENT or "development").strip().lower()

    @property
    def webhook_secret(self) -> str:
        """Secret used to verify inbound webhook signatures.

        Paystack signs webhooks with the account secret key, so we fall back to
        it when a dedicated webhook secret is not configured.
        """
        if self.PAYMENT_WEBHOOK_SECRET is not None:
            return self.PAYMENT_WEBHOOK_SECRET.get_secret_value()
        if self.PAYMENT_SECRET_KEY is not None:
            return self.PAYMENT_SECRET_KEY.get_secret_value()
        return ""

    @property
    def payment_secret_key(self) -> str:
        if self.PAYMENT_SECRET_KEY is not None:
            return self.PAYMENT_SECRET_KEY.get_secret_value()
        return ""

    def validate_for_production(self) -> List[str]:
        """Return a list of human-readable production configuration problems.

        An empty list means the deployment looks production-safe. The caller
        decides whether to hard-fail (production) or just warn (dev).
        """
        problems: List[str] = []

        if self.is_production:
            if not self.cors_origins:
                problems.append(
                    "CORS_ORIGINS must be set to an explicit allow-list in production."
                )
            if "*" in self.cors_origins:
                problems.append("CORS_ORIGINS must not contain '*' in production.")
            if not self.INTERNAL_CRON_SECRET:
                problems.append("INTERNAL_CRON_SECRET must be set in production.")
            if self.PAYMENT_PROVIDER == "mock":
                problems.append(
                    "PAYMENT_PROVIDER is 'mock'; set a real gateway before taking live payments."
                )
            if not self.webhook_secret:
                problems.append(
                    "PAYMENT_WEBHOOK_SECRET (or PAYMENT_SECRET_KEY) must be set in production."
                )
            if not self.PAYMENT_CALLBACK_URL:
                problems.append("PAYMENT_CALLBACK_URL must be set in production.")

        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
