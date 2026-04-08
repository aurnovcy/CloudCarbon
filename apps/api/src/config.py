"""
Application configuration via pydantic-settings.
All values are read from environment variables or .env file.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -------------------------------------------------------------------------
    # Application
    # -------------------------------------------------------------------------
    environment: Literal["development", "staging", "production"] = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:5173"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v

    # -------------------------------------------------------------------------
    # Database
    # -------------------------------------------------------------------------
    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/cloudcarbon"
    database_pool_size: int = 10

    @field_validator("database_url")
    @classmethod
    def fix_database_url(cls, v: str) -> str:
        return v.replace("postgresql+asyncpg://", "postgresql+psycopg2://").replace(
            "postgresql://", "postgresql+psycopg2://"
        )

    # -------------------------------------------------------------------------
    # Redis
    # -------------------------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"

    # -------------------------------------------------------------------------
    # JWT
    # -------------------------------------------------------------------------
    jwt_secret_key: str = "changeme"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 7

    # -------------------------------------------------------------------------
    # OAuth providers
    # -------------------------------------------------------------------------
    google_client_id: str = ""
    google_client_secret: str = ""
    azure_ad_client_id: str = ""
    azure_ad_client_secret: str = ""
    azure_ad_tenant_id: str = ""
    okta_client_id: str = ""
    okta_client_secret: str = ""
    okta_domain: str = ""

    # -------------------------------------------------------------------------
    # Cloud provider credentials (ingestion service account only)
    # -------------------------------------------------------------------------
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    azure_subscription_id: str = ""
    azure_client_id: str = ""
    azure_client_secret: str = ""
    azure_tenant_id: str = ""
    gcp_project_id: str = ""
    gcp_service_account_json: str = ""
    alibaba_access_key_id: str = ""
    alibaba_access_key_secret: str = ""

    # -------------------------------------------------------------------------
    # External data APIs
    # -------------------------------------------------------------------------
    electricity_maps_api_key: str = ""
    wri_aqueduct_api_key: str = ""
    boavizta_api_url: AnyHttpUrl = "https://api.boavizta.org"  # type: ignore[assignment]

    # -------------------------------------------------------------------------
    # Anthropic
    # -------------------------------------------------------------------------
    anthropic_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
