"""App configuration, loaded from environment variables (.env locally,
Cloud Run's --set-env-vars / Secret Manager in production). See
.env.example for the full list."""
from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/sensor_analysis"
    session_secret: str = "change-me-in-production"
    google_oauth_client_id: str = ""
    anthropic_api_key: str = ""
    frontend_origin: str = "http://localhost:5173"

    @field_validator("database_url")
    @classmethod
    def _use_psycopg3_driver(cls, v: str) -> str:
        # Neon (and most managed Postgres providers) hand out a bare
        # "postgresql://...?sslmode=require" URL, but we're on psycopg3 —
        # normalize the scheme rather than requiring every deploy target
        # to know this detail. psycopg3 honors the ?sslmode=require query
        # param as-is, so it passes through untouched.
        if v.startswith("postgresql://"):
            return "postgresql+psycopg://" + v[len("postgresql://"):]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
