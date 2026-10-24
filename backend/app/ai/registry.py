from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

Provider = Literal["ollama", "openai_compatible"]


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    label: str
    provider: Provider
    model: str = Field(min_length=1)
    base_url: str | None = None
    api_key_env: str | None = None
    deployment: Literal["local", "self_hosted", "cloud"]
    data_policy: Literal["local", "external"]
    context_window: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    temperature: float = Field(default=0.2, ge=0, le=2)
    license: str
    price_input_per_mtok: float = Field(default=0, ge=0)
    price_output_per_mtok: float = Field(default=0, ge=0)
    notes: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.max_output_tokens >= self.context_window:
            raise ValueError(f"{self.id}: max_output_tokens must be smaller than context_window")
        if self.provider == "openai_compatible" and not self.base_url:
            raise ValueError(f"{self.id}: openai_compatible profiles need base_url")
        if self.deployment == "cloud" and self.data_policy != "external":
            raise ValueError(f"{self.id}: cloud deployments send data off-site and must declare data_policy: external")
        return self

    def estimate_cost_usd(self, input_tokens: int | None, output_tokens: int | None) -> float:
        return (
            (input_tokens or 0) * self.price_input_per_mtok + (output_tokens or 0) * self.price_output_per_mtok
        ) / 1e6


class ModelRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    default_profile: str
    profiles: dict[str, ModelProfile]

    @model_validator(mode="before")
    @classmethod
    def _inject_ids(cls, data: object) -> object:
        if isinstance(data, dict) and isinstance(data.get("profiles"), dict):
            profiles = {k: {**v, "id": k} if isinstance(v, dict) else v for k, v in data["profiles"].items()}
            data = {**data, "profiles": profiles}
        return data

    @model_validator(mode="after")
    def _default_exists(self) -> Self:
        if self.default_profile not in self.profiles:
            raise ValueError(f"default_profile {self.default_profile!r} is not defined")
        return self


def load_registry(path: Path) -> ModelRegistry:
    """Fail fast at startup on a malformed registry rather than at the first chat request."""
    with path.open(encoding="utf-8") as handle:
        return ModelRegistry.model_validate(yaml.safe_load(handle))
