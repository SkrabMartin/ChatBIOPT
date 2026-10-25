"""Per-key sliding-window request limits (per user, per client IP, per login email).

Process-local, like the provider rate limiters: the API runs as one uvicorn worker. Several workers or replicas
would each enforce the limit separately (a shared store such as Redis would then be needed).
"""

import math
import time
from collections import OrderedDict, deque
from collections.abc import Callable

from app.core.errors import RateLimitedError

WINDOW_S = 60.0
MAX_KEYS = 50_000  # oldest keys are evicted first, so memory stays bounded under key-spraying


class SlidingWindowLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    def check(self, key: str, limit: int, what: str) -> None:
        """Record one request for `key`; RateLimitedError (429 + Retry-After) if `limit` per minute is exceeded."""
        now = self._clock()
        hits = self._hits.get(key)
        if hits is None:
            hits = self._hits[key] = deque()
            if len(self._hits) > MAX_KEYS:
                self._hits.popitem(last=False)
        else:
            self._hits.move_to_end(key)
        while hits and hits[0] <= now - WINDOW_S:
            hits.popleft()
        if len(hits) >= limit:
            retry_after = max(1, math.ceil(hits[0] + WINDOW_S - now))
            raise RateLimitedError(
                f"Too many {what} requests: limit is {limit} per minute. Retry in {retry_after} s.",
                headers={"Retry-After": str(retry_after)},
            )
        hits.append(now)
