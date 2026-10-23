from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration, read from environment variables (and `.env` for local dev)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    database_url: str = "postgresql+psycopg://bioresearch:bioresearch@localhost:5432/bioresearch"
    # Upper bound for establishing a DB connection and for the readiness probe query.
    database_connect_timeout_s: int = Field(default=3, ge=1, le=60)
    health_check_timeout_s: float = Field(default=2.0, gt=0, le=30)

    # NCBI E-utilities (PubMed). NCBI asks every client to identify itself with `tool` and a contact `email`.
    # Without an API key the limit is 3 requests/s, with one 10/s; the limiter enforces it per process.
    ncbi_api_key: str | None = None
    ncbi_tool: str = "bioresearch-ai"
    ncbi_email: str | None = None
    ncbi_base_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    ncbi_timeout_s: float = Field(default=10.0, gt=0, le=60)
    ncbi_max_attempts: int = Field(default=3, ge=1, le=6)
    ncbi_backoff_base_s: float = Field(default=0.5, ge=0, le=10)

    @property
    def ncbi_requests_per_second(self) -> float:
        return 10.0 if self.ncbi_api_key else 3.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
