"""Application settings, read from environment variables / .env."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://billbook:billbook@localhost:5432/billbook"
    secret_key: str = "dev-only-secret-change-me-in-production-0123456789"
    access_token_expire_minutes: int = 60

    # Our own company. company_state_code is what GST place-of-supply logic compares against.
    company_name: str = "BillBook Demo Traders Pvt Ltd"
    company_gstin: str = "27AAPFU0939F1ZV"
    company_state_code: str = "27"
    company_address: str = "12 MG Road, Pune, Maharashtra 411001"


@lru_cache
def get_settings() -> Settings:
    return Settings()
