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


class OpenAICompatibleGateway:
    """`/v1/chat/completions` with SSE streaming: vLLM and hosted OpenAI-compatible APIs.

    Servers differ in what they support; this adapter relies only on streamed `delta.content`,
    `finish_reason`, and (when offered) `stream_options.include_usage`.
    """

    def __init__(self, http: httpx.AsyncClient, base_url: str, model: str, api_key: str | None) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def availability(self) -> Availability:
        try:
            response = await self._http.get(f"{self._base_url}/models", headers=self._headers, timeout=5)
            response.raise_for_status()
            served = [m.get("id") for m in response.json().get("data", []) if isinstance(m, dict)]
        except (httpx.HTTPError, ValueError, AttributeError):
            return Availability(False, reason=f"Model server is not reachable at {self._base_url}")
        if self._model in served:
            return Availability(True, revision=self._model)
        return Availability(False, reason=f"Model server does not serve {self._model}")

    async def embed(self, texts: list[str], dimensions: int) -> list[list[float]]:
        payload = {"model": self._model, "input": texts}
        try:
            response = await self._http.post(f"{self._base_url}/embeddings", json=payload, headers=self._headers)
        except httpx.TimeoutException as exc:
            raise GatewayError("model_timeout", "Model server did not return embeddings in time") from exc
        except httpx.TransportError as exc:
            raise GatewayError("model_unavailable", f"Model server is not reachable at {self._base_url}") from exc
        if response.status_code in (401, 403, 404):
            raise GatewayError("model_unavailable", f"Model server refused embeddings for {self._model}")
        if not response.is_success:
            raise GatewayError("model_error", f"Model server returned HTTP {response.status_code} for embeddings")
        try:
            data = sorted(response.json()["data"], key=lambda item: item["index"])
            vectors = [item["embedding"] for item in data]
        except (ValueError, KeyError, TypeError) as exc:
            raise GatewayError("model_error", "Model server sent a malformed embedding response") from exc
        return check_vectors(vectors, len(texts), dimensions)

    async def stream(self, request: GenerationRequest) -> AsyncIterator[GatewayEvent]:
        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
            **({"response_format": {"type": "json_object"}} if request.json_output else {}),
        }
        finish_reason: str | None = None
        usage: dict[str, Any] = {}
        try:
            async with self._http.stream(
                "POST", f"{self._base_url}/chat/completions", json=payload, headers=self._headers
            ) as response:
                if response.status_code in (401, 403):
                    raise GatewayError("model_unavailable", "Model server rejected the credentials")
                if response.status_code == 404:
                    raise GatewayError("model_unavailable", f"Model server does not serve {self._model}")
                if not response.is_success:
                    await response.aread()
                    raise GatewayError("model_error", f"Model server returned HTTP {response.status_code}")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        yield StreamEnd(finish_reason, usage.get("prompt_tokens"), usage.get("completion_tokens"))
                        return
                    chunk = _parse(data)
                    if "error" in chunk:
                        raise GatewayError("model_error", "Model server reported an error mid-stream")
                    if isinstance(chunk.get("usage"), dict):
                        usage = chunk["usage"]
                    for choice in chunk.get("choices") or []:
                        text = (choice.get("delta") or {}).get("content") or ""
                        if text:
                            yield TextDelta(text)
                        finish_reason = choice.get("finish_reason") or finish_reason
        except httpx.TimeoutException as exc:
            raise GatewayError("model_timeout", "Model server did not respond in time") from exc
        except httpx.TransportError as exc:
            raise GatewayError("model_unavailable", f"Model server is not reachable at {self._base_url}") from exc
        raise GatewayError("model_error", "Model server closed the stream before finishing")


def _parse(data: str) -> dict[str, Any]:
    try:
        chunk = json.loads(data)
    except json.JSONDecodeError as exc:
        raise GatewayError("model_error", "Model server sent malformed stream data") from exc
    if not isinstance(chunk, dict):
        raise GatewayError("model_error", "Model server sent malformed stream data")
    return chunk
