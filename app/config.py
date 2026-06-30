from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr, Field


class Settings(BaseSettings):
    SUPABASE_URL: str = Field(..., env="SUPABASE_URL")
    SUPABASE_SERVICE_ROLE_KEY: SecretStr = Field(..., env="SUPABASE_SERVICE_ROLE_KEY")
    BACKEND_JWT_SECRET: str = Field(..., env="BACKEND_JWT_SECRET")  # Shared with Supabase Auth
    OPENAI_API_KEY: SecretStr = Field(..., env="OPENAI_API_KEY")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
