"""Provider-neutral model interface. Adapters map these types onto a concrete inference protocol."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol

Role = Literal["system", "user", "assistant"]
GatewayErrorCode = Literal["model_unavailable", "model_timeout", "model_error"]


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(frozen=True)
class GenerationRequest:
    messages: list[ChatMessage]
    temperature: float
    max_output_tokens: int
    context_window: int


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class StreamEnd:
    finish_reason: str | None
    input_tokens: int | None
    output_tokens: int | None


GatewayEvent = TextDelta | StreamEnd


@dataclass(frozen=True)
class Availability:
    available: bool
    # Exact served artifact (Ollama digest, vLLM served model id); recorded on runs for reproducibility.
    revision: str | None = None
    reason: str | None = None


class GatewayError(Exception):
    def __init__(self, code: GatewayErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: GatewayErrorCode = code
        self.message = message


class ModelGateway(Protocol):
    async def availability(self) -> Availability: ...

    def stream(self, request: GenerationRequest) -> AsyncIterator[GatewayEvent]:
        """Yields TextDelta chunks then exactly one StreamEnd; raises GatewayError on any provider failure."""
        ...
