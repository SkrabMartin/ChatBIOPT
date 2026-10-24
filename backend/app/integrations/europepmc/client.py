"""Read-only Europe PMC REST client: open-access status/license for a PMID and JATS full text by PMCID."""

from dataclasses import dataclass

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.errors import UpstreamInvalidResponseError
from app.integrations.http import RateLimiter, RetryPolicy, request_with_retries
from app.schemas.literature import PMCID_PATTERN, PMID_PATTERN

PROVIDER = "Europe PMC"


class _Result(BaseModel):
    pmid: str | None = None
    pmcid: str | None = None
    isOpenAccess: str | None = None
    license: str | None = None


class _ResultList(BaseModel):
    result: list[_Result] = []


class _SearchEnvelope(BaseModel):
    resultList: _ResultList


@dataclass(frozen=True)
class OpenAccessInfo:
    pmcid: str | None
    is_open_access: bool
    license: str | None


class EuropePMCClient:
    def __init__(self, http: httpx.AsyncClient, *, base_url: str, limiter: RateLimiter, retry: RetryPolicy) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._limiter = limiter
        self._retry = retry

    @classmethod
    def from_settings(cls, http: httpx.AsyncClient, settings: Settings) -> "EuropePMCClient":
        return cls(
            http,
            base_url=settings.europepmc_base_url,
            limiter=RateLimiter(settings.europepmc_requests_per_second),
            retry=RetryPolicy(max_attempts=settings.ncbi_max_attempts, backoff_base_s=settings.ncbi_backoff_base_s),
        )

    async def open_access_info(self, pmid: str) -> OpenAccessInfo:
        if not PMID_PATTERN.fullmatch(pmid):
            raise ValueError(f"invalid PMID {pmid!r}")
        response = await request_with_retries(
            self._http,
            "GET",
            f"{self._base_url}/search",
            params={"query": f"EXT_ID:{pmid} AND SRC:MED", "resultType": "core", "format": "json", "pageSize": 1},
            provider=PROVIDER,
            limiter=self._limiter,
            policy=self._retry,
        )
        try:
            results = _SearchEnvelope.model_validate_json(response.content).resultList.result
        except ValidationError as exc:
            raise UpstreamInvalidResponseError("Europe PMC returned an unexpected search response") from exc
        match = next((r for r in results if r.pmid == pmid), None)
        if match is None:
            return OpenAccessInfo(pmcid=None, is_open_access=False, license=None)
        pmcid = match.pmcid if match.pmcid and PMCID_PATTERN.fullmatch(match.pmcid) else None
        return OpenAccessInfo(pmcid=pmcid, is_open_access=match.isOpenAccess == "Y", license=match.license)

    def full_text_url(self, pmcid: str) -> str:
        if not PMCID_PATTERN.fullmatch(pmcid):
            raise ValueError(f"invalid PMCID {pmcid!r}")
        return f"{self._base_url}/{pmcid}/fullTextXML"

    async def full_text_xml(self, pmcid: str) -> bytes:
        # Europe PMC answers 500 (not 404) for articles outside the OA subset; callers only ask for eligible ones,
        # so after retries any failure is reported as unavailable rather than guessed at.
        response = await request_with_retries(
            self._http,
            "GET",
            self.full_text_url(pmcid),
            params={},
            provider=PROVIDER,
            limiter=self._limiter,
            policy=self._retry,
        )
        return response.content
