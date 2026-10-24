"""Read-only Ensembl REST client: maps an Ensembl gene stable ID to NCBI Gene IDs, with the Ensembl release."""

import re
import time
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.core.config import Settings
from app.core.errors import UpstreamInvalidResponseError
from app.integrations.http import RateLimiter, RetryPolicy, request_with_retries

PROVIDER = "Ensembl"
# Gene stable IDs: ENSG (human), ENSMUSG (mouse), ENSDARG (zebrafish)…, optionally versioned.
GENE_ID_PATTERN = re.compile(r"(ENS[A-Z]{0,5}G\d{11})(?:\.(\d+))?")
_RELEASE_TTL_S = 3600.0
_JSON = {"Content-Type": "application/json", "Accept": "application/json"}


class _Xref(BaseModel):
    primary_id: str
    dbname: str


_XREFS = TypeAdapter(list[_Xref])


class EnsemblClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        base_url: str,
        limiter: RateLimiter,
        retry: RetryPolicy,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._limiter = limiter
        self._retry = retry
        self._clock = clock
        self._release: tuple[float, str] | None = None

    @classmethod
    def from_settings(cls, http: httpx.AsyncClient, settings: Settings) -> "EnsemblClient":
        return cls(
            http,
            base_url=settings.ensembl_base_url,
            limiter=RateLimiter(settings.ensembl_requests_per_second),
            retry=RetryPolicy(max_attempts=settings.ncbi_max_attempts, backoff_base_s=settings.ncbi_backoff_base_s),
        )

    async def _get(
        self, path: str, params: dict[str, str | int], operation: str, passthrough: frozenset[int] = frozenset()
    ) -> Any:
        return await request_with_retries(
            self._http,
            "GET",
            f"{self._base_url}{path}",
            params=params,
            headers=_JSON,
            passthrough_statuses=passthrough,
            provider=PROVIDER,
            limiter=self._limiter,
            policy=self._retry,
            operation=operation,
        )

    async def ncbi_gene_ids(self, stable_id: str) -> list[str] | None:
        """NCBI Gene IDs cross-referenced by Ensembl; None if Ensembl does not know the ID."""
        match = GENE_ID_PATTERN.fullmatch(stable_id)
        if not match:
            raise ValueError(f"invalid Ensembl gene ID {stable_id!r}")
        # Ensembl answers 400 {"error": "ID '…' not found"} for unknown IDs.
        response = await self._get(
            f"/xrefs/id/{match.group(1)}", {"external_db": "EntrezGene"}, "xrefs/id", passthrough=frozenset({400})
        )
        if response.status_code == 400:
            if "not found" in response.text:
                return None
            raise UpstreamInvalidResponseError("Ensembl rejected the request")
        try:
            xrefs = _XREFS.validate_json(response.content)
        except ValidationError as exc:
            raise UpstreamInvalidResponseError("Ensembl returned an unexpected xref response") from exc
        return sorted({x.primary_id for x in xrefs if x.dbname == "EntrezGene"}, key=int)

    async def release(self) -> str:
        now = self._clock()
        if self._release and now - self._release[0] < _RELEASE_TTL_S:
            return self._release[1]
        response = await self._get("/info/data", {}, "info/data")
        try:
            release = str(response.json()["releases"][0])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise UpstreamInvalidResponseError("Ensembl returned an unexpected release response") from exc
        self._release = (now, release)
        return release
