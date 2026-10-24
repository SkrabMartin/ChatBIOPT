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


class EmbeddingGateway(Protocol):
    async def availability(self) -> Availability: ...

    async def embed(self, texts: list[str], dimensions: int) -> list[list[float]]:
        """One vector per input, in order; raises GatewayError on provider failure or malformed output."""
        ...


def check_vectors(vectors: object, expected_count: int, dimensions: int) -> list[list[float]]:
    """Validate provider output: right count, right dimension, finite numbers."""
    if not isinstance(vectors, list) or len(vectors) != expected_count:
        raise GatewayError("model_error", "Embedding response has the wrong number of vectors")
    checked: list[list[float]] = []
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != dimensions:
            raise GatewayError("model_error", f"Embedding dimension mismatch (expected {dimensions})")
        try:
            values = [float(v) for v in vector]
        except (TypeError, ValueError) as exc:
            raise GatewayError("model_error", "Embedding contains non-numeric values") from exc
        if any(v != v or v in (float("inf"), float("-inf")) for v in values):
            raise GatewayError("model_error", "Embedding contains non-finite values")
        checked.append(values)
    return checked
