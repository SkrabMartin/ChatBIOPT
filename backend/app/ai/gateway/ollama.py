import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.ai.gateway.base import (
    Availability,
    GatewayError,
    GatewayEvent,
    GenerationRequest,
    StreamEnd,
    TextDelta,
    check_vectors,
)


class OllamaGateway:
    """Ollama HTTP API: `/api/tags` for availability, streaming NDJSON from `/api/chat`."""

    def __init__(self, http: httpx.AsyncClient, base_url: str, model: str) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._model = model

    async def availability(self) -> Availability:
        try:
            response = await self._http.get(f"{self._base_url}/api/tags", timeout=5)
            response.raise_for_status()
            models = response.json().get("models", [])
        except (httpx.HTTPError, ValueError, AttributeError):
            return Availability(False, reason=f"Ollama is not reachable at {self._base_url}")
        for entry in models:
            if isinstance(entry, dict) and entry.get("name") in (self._model, f"{self._model}:latest"):
                digest = entry.get("digest")
                return Availability(True, revision=f"sha256:{digest}" if isinstance(digest, str) else None)
        return Availability(False, reason=f"Model {self._model} is not pulled; run `ollama pull {self._model}`")

    async def embed(self, texts: list[str], dimensions: int) -> list[list[float]]:
        payload = {"model": self._model, "input": texts, "truncate": True}
        try:
            response = await self._http.post(f"{self._base_url}/api/embed", json=payload)
        except httpx.TimeoutException as exc:
            raise GatewayError("model_timeout", "Ollama did not return embeddings in time") from exc
        except httpx.TransportError as exc:
            raise GatewayError("model_unavailable", f"Ollama is not reachable at {self._base_url}") from exc
        if response.status_code == 404:
            raise GatewayError("model_unavailable", f"Model {self._model} is not available in Ollama")
        if not response.is_success:
            raise GatewayError("model_error", f"Ollama returned HTTP {response.status_code} for embeddings")
        try:
            vectors = response.json().get("embeddings")
        except (ValueError, AttributeError) as exc:
            raise GatewayError("model_error", "Ollama sent a malformed embedding response") from exc
        return check_vectors(vectors, len(texts), dimensions)

    async def stream(self, request: GenerationRequest) -> AsyncIterator[GatewayEvent]:
        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "stream": True,
            **({"format": "json"} if request.json_output else {}),
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_output_tokens,
                # Ollama's default window is small and it truncates silently; always size it explicitly.
                "num_ctx": request.context_window,
            },
        }
        try:
            async with self._http.stream("POST", f"{self._base_url}/api/chat", json=payload) as response:
                if response.status_code == 404:
                    raise GatewayError("model_unavailable", f"Model {self._model} is not available in Ollama")
                if not response.is_success:
                    await response.aread()
                    raise GatewayError("model_error", f"Ollama returned HTTP {response.status_code}")
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = _parse(line)
                    if "error" in chunk:
                        raise GatewayError("model_error", f"Ollama error: {chunk['error']}")
                    text = (chunk.get("message") or {}).get("content") or ""
                    if text:
                        yield TextDelta(text)
                    if chunk.get("done"):
                        yield StreamEnd(
                            finish_reason=chunk.get("done_reason"),
                            input_tokens=chunk.get("prompt_eval_count"),
                            output_tokens=chunk.get("eval_count"),
                        )
                        return
        except httpx.TimeoutException as exc:
            raise GatewayError("model_timeout", "Ollama did not respond in time") from exc
        except httpx.TransportError as exc:
            raise GatewayError("model_unavailable", f"Ollama is not reachable at {self._base_url}") from exc
        raise GatewayError("model_error", "Ollama closed the stream before finishing")


def _parse(line: str) -> dict[str, Any]:
    try:
        chunk = json.loads(line)
    except json.JSONDecodeError as exc:
        raise GatewayError("model_error", "Ollama sent malformed stream data") from exc
    if not isinstance(chunk, dict):
        raise GatewayError("model_error", "Ollama sent malformed stream data")
    return chunk
