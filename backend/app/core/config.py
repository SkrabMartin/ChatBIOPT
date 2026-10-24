from functools import lru_cache
from pathlib import Path
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

    # Model gateway (M04). Prompts go only to profiles in the registry; `external` profiles additionally need
    # ALLOW_EXTERNAL_MODELS=true, so nothing leaves the host by accident.
    model_registry_path: Path = Path(__file__).resolve().parents[2] / "model_registry.yaml"
    ollama_base_url: str = "http://localhost:11434"
    allow_external_models: bool = False
    model_connect_timeout_s: float = Field(default=5.0, gt=0, le=60)
    # Max silence between streamed chunks (covers model load time on first use).
    model_read_timeout_s: float = Field(default=120.0, gt=0, le=900)
    run_max_duration_s: float = Field(default=300.0, gt=0, le=3600)
    # Grounded answers (M06): passages placed in the model's context at most.
    rag_top_k: int = Field(default=8, ge=1, le=30)

    # Evidence ingestion (M05).
    europepmc_base_url: str = "https://www.ebi.ac.uk/europepmc/webservices/rest"
    europepmc_requests_per_second: float = Field(default=5.0, gt=0, le=20)
    object_store_path: Path = Path("var/objects")
    # Comma-separated canonical ids (see app/ingestion/licensing.py). Full text with any other license, or none,
    # is not stored; the abstract still is.
    fulltext_allowed_licenses: str = "CC0,CC-BY,CC-BY-SA,CC-BY-ND,CC-BY-NC,CC-BY-NC-SA,CC-BY-NC-ND"
    ingestion_poll_interval_s: float = Field(default=2.0, gt=0, le=60)
    ingestion_lease_s: float = Field(default=300.0, gt=0)
    ingestion_max_attempts: int = Field(default=3, ge=1, le=10)
    ingestion_retry_delay_s: float = Field(default=30.0, ge=0)
    worker_heartbeat_path: Path = Path("var/worker-heartbeat")

    @property
    def allowed_fulltext_licenses(self) -> frozenset[str]:
        return frozenset(part.strip() for part in self.fulltext_allowed_licenses.split(",") if part.strip())

    @property
    def ncbi_requests_per_second(self) -> float:
        return 10.0 if self.ncbi_api_key else 3.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
