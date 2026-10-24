"""Read-only NCBI Gene client (E-utilities `db=gene`): symbol/alias search within one organism and gene summaries."""

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.config import Settings
from app.core.errors import UpstreamInvalidResponseError
from app.integrations.http import RateLimiter, RetryPolicy, request_with_retries

PROVIDER = "NCBI Gene"
GENE_ID_PATTERN = r"[1-9]\d{0,11}"
# NCBI's `status` in gene summaries: "" (or 0) live, 1 replaced by `currentid`, 2 discontinued.
_STATUS = {"": "live", "0": "live", "1": "replaced", "2": "discontinued"}
_BUILD_TTL_S = 3600.0


class _Organism(BaseModel):
    scientificname: str
    taxid: int


class _Summary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    uid: str
    name: str
    description: str = ""
    status: str | int = ""
    currentid: str | int = ""
    organism: _Organism
    otheraliases: str = ""
    otherdesignations: str = ""
    chromosome: str = ""
    maplocation: str = ""
    mim: list[str] = []
    nomenclaturestatus: str = ""


@dataclass(frozen=True)
class GeneSummary:
    gene_id: str
    symbol: str
    name: str
    taxon_id: int
    organism: str
    status: str  # live | replaced | discontinued
    current_id: str | None
    aliases: list[str]
    chromosome: str | None
    map_location: str | None
    mim: list[str]
    official_nomenclature: bool
    raw: dict[str, Any] = field(repr=False)


class NCBIGeneClient:
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
        clock: Callable[[], float] = time.monotonic,
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
        self._clock = clock
        self._build: tuple[float, str] | None = None

    @classmethod
    def from_settings(cls, http: httpx.AsyncClient, settings: Settings, limiter: RateLimiter) -> "NCBIGeneClient":
        """`limiter` must be the one PubMed uses: NCBI's request budget is per client, not per database."""
        return cls(
            http,
            base_url=settings.ncbi_base_url,
            tool=settings.ncbi_tool,
            email=settings.ncbi_email,
            api_key=settings.ncbi_api_key,
            limiter=limiter,
            retry=RetryPolicy(max_attempts=settings.ncbi_max_attempts, backoff_base_s=settings.ncbi_backoff_base_s),
        )

    async def _get(self, endpoint: str, params: dict[str, str | int]) -> Any:
        response = await request_with_retries(
            self._http,
            "GET",
            f"{self._base_url}/{endpoint}",
            params={**params, "db": "gene", "retmode": "json", **self._identity},
            provider=PROVIDER,
            limiter=self._limiter,
            policy=self._retry,
        )
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise UpstreamInvalidResponseError("NCBI Gene returned an unexpected response") from exc

    async def search_symbol(self, symbol: str, taxon_ids: Iterable[int], limit: int = 20) -> list[str]:
        """Live genes of these organisms whose symbol field (official symbol or alias) matches `symbol`.

        `symbol` must already be validated (no quotes or field tags): it is quoted into the Entrez query.
        """
        taxa = " OR ".join(f"{int(t)}[taxid]" for t in taxon_ids)
        term = f'"{symbol}"[sym] AND ({taxa}) AND alive[prop]'
        payload = await self._get("esearch.fcgi", {"term": term, "retmax": limit})
        try:
            ids = payload["esearchresult"]["idlist"]
        except (KeyError, TypeError) as exc:
            raise UpstreamInvalidResponseError("NCBI Gene returned an unexpected search response") from exc
        return [str(i) for i in ids]

    async def summaries(self, gene_ids: list[str]) -> list[GeneSummary]:
        """Summaries in the requested order; IDs unknown to NCBI are omitted."""
        if not gene_ids:
            return []
        payload = await self._get("esummary.fcgi", {"id": ",".join(gene_ids)})
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            raise UpstreamInvalidResponseError("NCBI Gene returned an unexpected summary response")
        summaries: list[GeneSummary] = []
        for gene_id in gene_ids:
            item = result.get(gene_id)
            if not isinstance(item, dict) or "error" in item:
                continue
            try:
                parsed = _Summary.model_validate(item)
            except ValidationError as exc:
                raise UpstreamInvalidResponseError(f"NCBI Gene returned an invalid summary for {gene_id}") from exc
            summaries.append(_to_summary(parsed, item))
        return summaries

    async def database_build(self) -> str:
        """NCBI Gene database build (e.g. "Build261023-2230m.1"), cached for an hour."""
        now = self._clock()
        if self._build and now - self._build[0] < _BUILD_TTL_S:
            return self._build[1]
        payload = await self._get("einfo.fcgi", {})
        try:
            build = str(payload["einforesult"]["dbinfo"][0]["dbbuild"])
        except (KeyError, IndexError, TypeError) as exc:
            raise UpstreamInvalidResponseError("NCBI Gene returned an unexpected einfo response") from exc
        self._build = (now, build)
        return build


def _to_summary(item: _Summary, raw: dict[str, Any]) -> GeneSummary:
    current = str(item.currentid) if item.currentid not in ("", 0, "0") else None
    return GeneSummary(
        gene_id=item.uid,
        symbol=item.name,
        name=item.description,
        taxon_id=item.organism.taxid,
        organism=item.organism.scientificname,
        status=_STATUS.get(str(item.status), "unknown"),
        current_id=current,
        aliases=[a.strip() for a in item.otheraliases.split(",") if a.strip()],
        chromosome=item.chromosome or None,
        map_location=item.maplocation or None,
        mim=item.mim,
        official_nomenclature=item.nomenclaturestatus == "Official",
        raw=raw,
    )
