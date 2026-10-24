"""Read-only Open Targets Platform GraphQL client: target-disease associations for a human Ensembl gene."""

from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.errors import UpstreamInvalidResponseError
from app.integrations.http import RateLimiter, RetryPolicy, request_with_retries

PROVIDER = "Open Targets"
# Open Targets Platform covers human targets only.
HUMAN_TAXON = 9606

QUERY = """
query TargetAssociations($id: String!, $size: Int!) {
  meta { apiVersion { x y z } dataVersion { year month iteration } }
  target(ensemblId: $id) {
    id
    approvedSymbol
    approvedName
    proteinIds { id source }
    associatedDiseases(page: { index: 0, size: $size }) {
      count
      rows { score disease { id name } datatypeScores { id score } }
    }
  }
}
""".strip()


class _Version(BaseModel):
    x: str | None = None
    y: str | None = None
    z: str | None = None


class _DataVersion(BaseModel):
    year: str | None = None
    month: str | None = None
    iteration: str | None = None


class _Meta(BaseModel):
    apiVersion: _Version
    dataVersion: _DataVersion


class _Disease(BaseModel):
    id: str
    name: str


class _Score(BaseModel):
    id: str
    score: float


class _Row(BaseModel):
    score: float
    disease: _Disease
    datatypeScores: list[_Score] = []


class _Associations(BaseModel):
    count: int
    rows: list[_Row]


class _ProteinId(BaseModel):
    id: str
    source: str


class _Target(BaseModel):
    id: str
    approvedSymbol: str
    approvedName: str = ""
    proteinIds: list[_ProteinId] = []
    associatedDiseases: _Associations


class _Data(BaseModel):
    meta: _Meta
    target: _Target | None


class _Envelope(BaseModel):
    data: _Data | None = None
    errors: list[dict[str, Any]] | None = None


@dataclass(frozen=True)
class DiseaseAssociation:
    disease_id: str  # e.g. MONDO_0018875, EFO_0000519
    disease_name: str
    score: float
    datatype_scores: dict[str, float]


@dataclass(frozen=True)
class TargetAssociations:
    ensembl_id: str
    symbol: str
    name: str
    swissprot: list[str]
    total_associations: int
    associations: list[DiseaseAssociation]
    data_version: str
    api_version: str
    raw: dict[str, Any] = field(repr=False)


class OpenTargetsClient:
    def __init__(self, http: httpx.AsyncClient, *, url: str, limiter: RateLimiter, retry: RetryPolicy) -> None:
        self._http = http
        self._url = url
        self._limiter = limiter
        self._retry = retry

    @classmethod
    def from_settings(cls, http: httpx.AsyncClient, settings: Settings) -> "OpenTargetsClient":
        return cls(
            http,
            url=settings.opentargets_url,
            limiter=RateLimiter(settings.opentargets_requests_per_second),
            retry=RetryPolicy(max_attempts=settings.ncbi_max_attempts, backoff_base_s=settings.ncbi_backoff_base_s),
        )

    async def associations(self, ensembl_id: str, size: int) -> TargetAssociations | None:
        """Strongest disease associations of a target (by overall score); None if Open Targets lacks it."""
        response = await request_with_retries(
            self._http,
            "POST",
            self._url,
            json={"query": QUERY, "variables": {"id": ensembl_id, "size": size}},
            provider=PROVIDER,
            limiter=self._limiter,
            policy=self._retry,
        )
        try:
            envelope = _Envelope.model_validate_json(response.content)
        except ValidationError as exc:
            raise UpstreamInvalidResponseError("Open Targets returned an unexpected response") from exc
        if envelope.errors or envelope.data is None:
            raise UpstreamInvalidResponseError("Open Targets rejected the query")
        target, meta = envelope.data.target, envelope.data.meta
        if target is None:
            return None
        data = meta.dataVersion
        return TargetAssociations(
            ensembl_id=target.id,
            symbol=target.approvedSymbol,
            name=target.approvedName,
            swissprot=[p.id for p in target.proteinIds if p.source == "uniprot_swissprot"],
            total_associations=target.associatedDiseases.count,
            associations=[
                DiseaseAssociation(
                    disease_id=row.disease.id,
                    disease_name=row.disease.name,
                    score=row.score,
                    datatype_scores={s.id: s.score for s in row.datatypeScores},
                )
                for row in target.associatedDiseases.rows
            ],
            data_version=".".join(p for p in (data.year, data.month, data.iteration) if p),
            api_version=".".join(p for p in (meta.apiVersion.x, meta.apiVersion.y, meta.apiVersion.z) if p),
            raw=response.json(),
        )
