"""Shared plumbing for external scientific APIs: rate limiting and bounded retries of transient failures."""

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.core.errors import UpstreamError, UpstreamTimeoutError
from app.core.tracing import span

logger = logging.getLogger(__name__)

# 429 and these 5xx are worth retrying; any other non-2xx is a caller or provider bug that will not heal.
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER_S = 5.0


class RateLimiter:
    """Spaces calls at least `1 / rate` seconds apart, across all coroutines sharing this instance.

    Process-local by design: the app runs as a single uvicorn worker. Running several workers or replicas
    requires a shared limiter (e.g. Redis) to keep the provider-wide limit.
    """

    def __init__(
        self,
        rate_per_second: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._interval = 1.0 / rate_per_second
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._next_slot = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = self._clock()
            wait = self._next_slot - now
            if wait > 0:
                await self._sleep(wait)
                now += wait
            self._next_slot = now + self._interval


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    backoff_base_s: float = 0.5

    def delay(self, attempt: int, retry_after: str | None) -> float:
        if retry_after is not None:
            try:
                return min(float(retry_after), MAX_RETRY_AFTER_S)
            except ValueError:
                pass  # HTTP-date form: fall back to exponential backoff
        # Exponential backoff with full jitter on the top half avoids synchronized retry bursts.
        base = self.backoff_base_s * (1 << (attempt - 1))
        return base / 2 + random.uniform(0, base / 2)  # noqa: S311 - jitter, not cryptography


async def request_with_retries(
    client: httpx.AsyncClient,
    method: Literal["GET", "POST"],
    url: str,
    *,
    params: Mapping[str, str | int] | None = None,
    json: Any = None,
    headers: Mapping[str, str] | None = None,
    passthrough_statuses: frozenset[int] = frozenset(),
    provider: str,
    limiter: RateLimiter,
    policy: RetryPolicy,
    operation: str | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> httpx.Response:
    """Rate-limited request; retries timeouts, connection errors and transient statuses only.

    Only idempotent reads go through here (POST is used for read-only GraphQL queries).
    `passthrough_statuses` are returned to the caller instead of raising (e.g. a provider's "unknown ID" status).
    `operation` names the span; default is the last URL segment (pass it when that segment is an identifier).
    """
    for attempt in range(1, policy.max_attempts + 1):
        await limiter.acquire()
        start = time.perf_counter()
        retry_after: str | None = None
        try:
            name = f"{provider} {operation or url.rsplit('/', 1)[-1]}"
            with span(name, provider=provider, attempt=attempt) as current:
                response = await client.request(method, url, params=params, json=json, headers=headers)
                current.set_attribute("http.status_code", response.status_code)
        except httpx.TimeoutException as exc:
            failure: UpstreamError = UpstreamTimeoutError(f"{provider} did not respond in time")
            outcome = type(exc).__name__
        except httpx.TransportError as exc:
            failure = UpstreamError(f"{provider} is unreachable")
            outcome = type(exc).__name__
        else:
            outcome = str(response.status_code)
            if response.is_success or response.status_code in passthrough_statuses:
                logger.info(
                    "upstream request",
                    extra=_log_fields(provider, url, attempt, outcome, start),
                )
                return response
            if response.status_code not in TRANSIENT_STATUSES:
                logger.warning("upstream request rejected", extra=_log_fields(provider, url, attempt, outcome, start))
                raise UpstreamError(f"{provider} rejected the request (HTTP {response.status_code})")
            failure = UpstreamError(f"{provider} is temporarily unavailable (HTTP {response.status_code})")
            retry_after = response.headers.get("Retry-After")

        logger.warning("upstream request failed", extra=_log_fields(provider, url, attempt, outcome, start))
        if attempt == policy.max_attempts:
            raise failure
        await sleep(policy.delay(attempt, retry_after))
    raise AssertionError("unreachable")  # pragma: no cover - loop always returns or raises


def _log_fields(provider: str, url: str, attempt: int, outcome: str, start: float) -> dict[str, object]:
    # Query parameters are deliberately not logged: they contain the user's search terms and the API key.
    return {
        "provider": provider,
        "endpoint": url.rsplit("/", 1)[-1],
        "attempt": attempt,
        "outcome": outcome,
        "latency_ms": round((time.perf_counter() - start) * 1000, 2),
    }
