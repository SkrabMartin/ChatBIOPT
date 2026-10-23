"""Read-only NCBI E-utilities client for PubMed: ESearch for IDs, EFetch for full records."""

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.errors import UpstreamInvalidResponseError
from app.integrations.http import RateLimiter, RetryPolicy, get_with_retries
from app.integrations.pubmed.parser import parse_efetch
from app.schemas.literature import PMID_PATTERN, LiteratureRecord

PROVIDER = "PubMed"


class _ESearchResult(BaseModel):
    count: int
    idlist: list[str]
    querytranslation: str | None = None


class _ESearchEnvelope(BaseModel):
    esearchresult: _ESearchResult


@dataclass(frozen=True)
class SearchHits:
    total_count: int
    pmids: list[str]
    query_translation: str | None


@dataclass(frozen=True)
class FetchedRecord:
    record: LiteratureRecord
    raw_payload: str


class PubMedClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        base_url: str,
        tool: str,
        email: str | None,
        api_key: str | None,
        limiter: RateLimiter,
        retry: RetryPolicy,
    ) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._identity: dict[str, str] = {"tool": tool}
        if email:
            self._identity["email"] = email
        if api_key:
            self._identity["api_key"] = api_key
        self._limiter = limiter
        self._retry = retry

    @classmethod
    def from_settings(cls, http: httpx.AsyncClient, settings: Settings) -> "PubMedClient":
        return cls(
            http,
            base_url=settings.ncbi_base_url,
            tool=settings.ncbi_tool,
            email=settings.ncbi_email,
            api_key=settings.ncbi_api_key,
            limiter=RateLimiter(settings.ncbi_requests_per_second),
            retry=RetryPolicy(max_attempts=settings.ncbi_max_attempts, backoff_base_s=settings.ncbi_backoff_base_s),
        )

    async def _get(self, endpoint: str, params: dict[str, str | int]) -> httpx.Response:
        return await get_with_retries(
            self._http,
            f"{self._base_url}/{endpoint}",
            params={**params, **self._identity},
            provider=PROVIDER,
            limiter=self._limiter,
            policy=self._retry,
        )

    async def search(self, query: str, *, limit: int, offset: int) -> SearchHits:
        response = await self._get(
            "esearch.fcgi",
            {
                "db": "pubmed",
                "term": query,
                "retmode": "json",
                "retmax": limit,
                "retstart": offset,
                "sort": "relevance",
            },
        )
        try:
            result = _ESearchEnvelope.model_validate_json(response.content).esearchresult
        except ValidationError as exc:
            raise UpstreamInvalidResponseError("PubMed returned an unexpected search response") from exc
        pmids = [pmid for pmid in result.idlist if PMID_PATTERN.fullmatch(pmid)]
        if len(pmids) != len(result.idlist):
            raise UpstreamInvalidResponseError("PubMed returned malformed identifiers")
        return SearchHits(total_count=result.count, pmids=pmids, query_translation=result.querytranslation)

    async def fetch(self, pmids: list[str]) -> list[FetchedRecord]:
        """Full records for `pmids`, in the requested order. IDs PubMed does not return are omitted."""
        if not pmids:
            return []
        response = await self._get("efetch.fcgi", {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"})
        retrieved_at = datetime.now(UTC)
        by_pmid = {
            record.pmid: FetchedRecord(record, raw) for record, raw in parse_efetch(response.content, retrieved_at)
        }
        return [by_pmid[pmid] for pmid in pmids if pmid in by_pmid]
