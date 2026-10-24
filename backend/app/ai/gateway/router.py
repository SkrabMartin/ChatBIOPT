import os

import httpx

from app.ai.gateway.base import ModelGateway
from app.ai.gateway.ollama import OllamaGateway
from app.ai.gateway.openai_compatible import OpenAICompatibleGateway
from app.ai.registry import ModelProfile, ModelRegistry
from app.core.errors import AppError, ForbiddenError


class UnknownModelProfileError(AppError):
    status_code = 422
    code = "unknown_model_profile"


class ModelRouter:
    """Resolves a profile id to a gateway, enforcing the data policy before any prompt can leave the host."""

    def __init__(
        self, registry: ModelRegistry, http: httpx.AsyncClient, *, ollama_base_url: str, allow_external: bool
    ) -> None:
        self.registry = registry
        self._http = http
        self._ollama_base_url = ollama_base_url
        self.allow_external = allow_external

    def profile(self, profile_id: str | None) -> ModelProfile:
        key = profile_id or self.registry.default_profile
        profile = self.registry.profiles.get(key)
        if profile is None:
            raise UnknownModelProfileError(f"Unknown model profile {key!r}")
        return profile

    def policy_block_reason(self, profile: ModelProfile) -> str | None:
        if profile.data_policy == "external" and not self.allow_external:
            return "External models are disabled; set ALLOW_EXTERNAL_MODELS=true to send prompts off-site"
        if profile.api_key_env and not os.environ.get(profile.api_key_env):
            return f"{profile.api_key_env} is not set"
        return None

    def gateway(self, profile: ModelProfile) -> ModelGateway:
        if (reason := self.policy_block_reason(profile)) is not None:
            raise ForbiddenError(reason)
        if profile.provider == "ollama":
            return OllamaGateway(self._http, profile.base_url or self._ollama_base_url, profile.model)
        api_key = os.environ.get(profile.api_key_env) if profile.api_key_env else None
        assert profile.base_url is not None  # guaranteed by ModelProfile validation
        return OpenAICompatibleGateway(self._http, profile.base_url, profile.model, api_key)
