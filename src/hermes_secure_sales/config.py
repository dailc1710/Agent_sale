from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "sqlite:///./hermes.db"
    jwt_secret: str = ""
    webhook_secrets_json: str = "{}"
    google_forms_webhook_configs_json: str = "{}"
    hermes_api_url: str = "http://localhost:8001"
    hermes_api_key: str = ""
    hermes_model: str = "hermes-sales"
    hermes_timeout_seconds: float = 20.0
    admin_username: str = "admin"
    admin_password: str = ""
    admin_secret: str = ""
    staff_username: str = ""
    staff_password: str = ""
    pii_encryption_key: str = ""
    public_base_url: str = ""
    public_session_hours: int = 8
    public_rate_limit_per_minute: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
