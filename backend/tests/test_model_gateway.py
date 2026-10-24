import json
import math
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.ai.context import CHARS_PER_TOKEN, ContextTooLongError, build_context
from app.ai.gateway.base import ChatMessage, GatewayError, GatewayEvent, GenerationRequest, StreamEnd, TextDelta
from app.ai.gateway.openai_compatible import OpenAICompatibleGateway
from app.ai.gateway.router import ModelRouter
from app.ai.prompts.research_assistant import SYSTEM_PROMPT
from app.ai.registry import ModelProfile, ModelRegistry, load_registry
from app.core.errors import ForbiddenError
from app.models import Message

REQUEST = GenerationRequest(
    messages=[ChatMessage("user", "hi")], temperature=0, max_output_tokens=16, context_window=1024
)


def sse(*chunks: dict[str, Any] | str) -> bytes:
    lines = [f"data: {c if isinstance(c, str) else json.dumps(c)}\n\n" for c in chunks]
    return "".join(lines).encode()


async def collect(gateway: OpenAICompatibleGateway) -> list[GatewayEvent]:
    return [event async for event in gateway.stream(REQUEST)]


def openai_gateway(handler: Any, api_key: str | None = "k") -> OpenAICompatibleGateway:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatibleGateway(http, "http://vllm.test/v1", "Qwen/Qwen2.5-7B-Instruct", api_key)


async def test_openai_compatible_stream_yields_text_and_usage() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = sse(
            {"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "Hel"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 2}},
            "[DONE]",
        )
        return httpx.Response(200, content=body)

    events = await collect(openai_gateway(handler))

    assert events == [TextDelta("Hel"), TextDelta("lo"), StreamEnd("stop", 7, 2)]
    sent = json.loads(seen[0].content)
    assert (sent["max_tokens"], sent["stream"], sent["stream_options"]) == (16, True, {"include_usage": True})
    assert seen[0].headers["Authorization"] == "Bearer k"


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(401), "model_unavailable"),
        (httpx.Response(404), "model_unavailable"),
        (httpx.Response(500), "model_error"),
        (httpx.Response(200, content=sse({"choices": [{"delta": {"content": "x"}}]})), "model_error"),
        (httpx.Response(200, content=b"data: {not json}\n\n"), "model_error"),
    ],
    ids=["unauthorized", "unknown-model", "server-error", "no-done-marker", "malformed"],
)
async def test_openai_compatible_failures_map_to_gateway_errors(response: httpx.Response, code: str) -> None:
    with pytest.raises(GatewayError) as caught:
        await collect(openai_gateway(lambda _r: response))

    assert caught.value.code == code


async def test_openai_compatible_timeout_is_model_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(GatewayError) as caught:
        await collect(openai_gateway(handler))

    assert caught.value.code == "model_timeout"


def profile(**overrides: Any) -> ModelProfile:
    values: dict[str, Any] = {
        "id": "p",
        "label": "P",
        "provider": "openai_compatible",
        "model": "m",
        "base_url": "https://api.example.test/v1",
        "deployment": "cloud",
        "data_policy": "external",
        "context_window": 1000,
        "max_output_tokens": 100,
        "license": "proprietary",
        "price_input_per_mtok": 2.0,
        "price_output_per_mtok": 8.0,
    }
    return ModelProfile.model_validate({**values, **overrides})


def router_for(p: ModelProfile, *, allow_external: bool) -> ModelRouter:
    registry = ModelRegistry(default_profile=p.id, profiles={p.id: p})
    return ModelRouter(registry, httpx.AsyncClient(), ollama_base_url="http://x", allow_external=allow_external)


def test_external_models_are_blocked_unless_explicitly_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    external = profile(api_key_env="TEST_PROVIDER_KEY")
    monkeypatch.setenv("TEST_PROVIDER_KEY", "secret")

    with pytest.raises(ForbiddenError, match="ALLOW_EXTERNAL_MODELS"):
        router_for(external, allow_external=False).gateway(external)
    assert router_for(external, allow_external=True).policy_block_reason(external) is None


def test_profile_with_missing_api_key_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    external = profile(api_key_env="TEST_PROVIDER_KEY")
    monkeypatch.delenv("TEST_PROVIDER_KEY", raising=False)

    assert router_for(external, allow_external=True).policy_block_reason(external) == "TEST_PROVIDER_KEY is not set"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"data_policy": "local"}, "must declare data_policy: external"),
        ({"max_output_tokens": 1000}, "max_output_tokens must be smaller"),
        ({"base_url": None}, "need base_url"),
    ],
)
def test_registry_rejects_inconsistent_profiles(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        profile(**overrides)


def test_cost_estimate_uses_per_million_prices() -> None:
    assert profile().estimate_cost_usd(1_000_000, 500_000) == pytest.approx(6.0)
    assert profile().estimate_cost_usd(None, None) == 0


def test_shipped_registry_is_valid() -> None:
    registry = load_registry(Path(__file__).resolve().parents[1] / "model_registry.yaml")

    assert registry.default_profile in registry.profiles
    assert all(p.data_policy == "local" for p in registry.profiles.values()), "no off-site model ships enabled"


def msg(role: str, content: str, sequence: int) -> Message:
    return Message(id=uuid.uuid4(), conversation_id=uuid.uuid4(), sequence=sequence, role=role, content=content)


def test_context_keeps_newest_messages_within_budget() -> None:
    history = [msg("user", "a" * 900, 1), msg("assistant", "b" * 900, 2), msg("user", "c" * 900, 3)]

    # Window sized so the character budget (3 chars/token, minus the system prompt) holds ~2000 characters:
    # room for the two newest 900-character messages but not the third.
    window = 100 + math.ceil((2000 + len(SYSTEM_PROMPT)) / CHARS_PER_TOKEN)
    built = build_context(history, context_window=window, max_output_tokens=100)

    assert built.messages[0].role == "system"
    assert [m.content[0] for m in built.messages[1:]] == ["b", "c"]
    assert built.history_message_count == 2


def test_context_never_truncates_the_latest_question() -> None:
    with pytest.raises(ContextTooLongError):
        build_context([msg("user", "x" * 10_000, 1)], context_window=1000, max_output_tokens=100)


def test_context_skips_system_messages_from_history() -> None:
    built = build_context([msg("system", "s", 1), msg("user", "q", 2)], context_window=8192, max_output_tokens=100)

    assert [m.role for m in built.messages] == ["system", "user"]
    assert built.messages[1].content == "q"
