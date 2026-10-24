"""Read-only UniProtKB REST client: protein entries by accession, NCBI Gene ID or HGNC ID, with release provenance."""

import re
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.config import Settings
from app.core.errors import UpstreamInvalidResponseError
from app.integrations.http import RateLimiter, RetryPolicy, request_with_retries

PROVIDER = "UniProt"
# UniProt's documented accession format, optionally with an isoform suffix (P04637-2).
ACCESSION_PATTERN = re.compile(
    r"(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})(?:-\d{1,3})?"
)
FIELDS = (
    "accession,id,gene_primary,gene_synonym,organism_id,organism_name,protein_name,reviewed,"
    "xref_geneid,xref_hgnc,xref_ensembl,length,sequence_version,annotation_score"
)


class _Value(BaseModel):
    value: str


class _Gene(BaseModel):
    geneName: _Value | None = None
    synonyms: list[_Value] = []


class _Property(BaseModel):
    key: str
    value: str


class _Xref(BaseModel):
    database: str
    id: str
    properties: list[_Property] = []


class _Organism(BaseModel):
    taxonId: int
    scientificName: str = ""


class _Entry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    primaryAccession: str
    uniProtkbId: str = ""
    entryType: str
    organism: _Organism
    genes: list[_Gene] = []
    proteinDescription: dict[str, Any] = {}
    uniProtKBCrossReferences: list[_Xref] = []
    sequence: dict[str, Any] = {}
    entryAudit: dict[str, Any] = {}
    annotationScore: float | None = None


class _InactiveReason(BaseModel):
    inactiveReasonType: str
    deletedReason: str | None = None
    mergeDemergeTos: list[str] = []


class _InactiveEntry(BaseModel):
    primaryAccession: str
    inactiveReason: _InactiveReason


class _Results(BaseModel):
    results: list[dict[str, Any]]


@dataclass(frozen=True)
class ProteinEntry:
    accession: str
    entry_name: str
    reviewed: bool
    taxon_id: int
    organism: str
    protein_name: str | None
    gene_symbols: list[str]
    gene_synonyms: list[str]
    gene_ids: list[str]
    hgnc_ids: list[str]
    ensembl_genes: list[str]  # versioned stable IDs as cross-referenced by this UniProt release
    length: int | None
    sequence_version: int | None
    raw: dict[str, Any] = field(repr=False)


@dataclass(frozen=True)
class InactiveEntry:
    """An accession UniProtKB no longer serves: deleted, or merged/demerged into `replaced_by`."""

    accession: str
    reason: str  # DELETED | MERGED | DEMERGED
    deleted_reason: str | None
    replaced_by: list[str]


@dataclass(frozen=True)
class ProteinSearch:
    entries: list[ProteinEntry]
    release: str | None
    release_date: str | None
    inactive: list[InactiveEntry] = field(default_factory=list)


class UniProtClient:
    def __init__(self, http: httpx.AsyncClient, *, base_url: str, limiter: RateLimiter, retry: RetryPolicy) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._limiter = limiter
        self._retry = retry

    @classmethod
    def from_settings(cls, http: httpx.AsyncClient, settings: Settings) -> "UniProtClient":
        return cls(
            http,
            base_url=settings.uniprot_base_url,
            limiter=RateLimiter(settings.uniprot_requests_per_second),
            retry=RetryPolicy(max_attempts=settings.ncbi_max_attempts, backoff_base_s=settings.ncbi_backoff_base_s),
        )

    async def search(self, query: str, size: int = 25) -> ProteinSearch:
        response = await request_with_retries(
            self._http,
            "GET",
            f"{self._base_url}/uniprotkb/search",
            params={"query": query, "fields": FIELDS, "format": "json", "size": size},
            provider=PROVIDER,
            limiter=self._limiter,
            policy=self._retry,
        )
        entries: list[ProteinEntry] = []
        inactive: list[InactiveEntry] = []
        try:
            for item in _Results.model_validate_json(response.content).results:
                if item.get("entryType") == "Inactive":
                    gone = _InactiveEntry.model_validate(item)
                    reason = gone.inactiveReason
                    inactive.append(
                        InactiveEntry(
                            accession=gone.primaryAccession,
                            reason=reason.inactiveReasonType,
                            deleted_reason=reason.deletedReason or None,
                            replaced_by=reason.mergeDemergeTos,
                        )
                    )
                else:
                    entries.append(_to_entry(_Entry.model_validate(item), item))
        except ValidationError as exc:
            raise UpstreamInvalidResponseError("UniProt returned an unexpected search response") from exc
        return ProteinSearch(
            entries=entries,
            release=response.headers.get("X-UniProt-Release"),
            release_date=response.headers.get("X-UniProt-Release-Date"),
            inactive=inactive,
        )

    async def by_accession(self, accession: str) -> ProteinSearch:
        if not ACCESSION_PATTERN.fullmatch(accession):
            raise ValueError(f"invalid UniProt accession {accession!r}")
        return await self.search(f"accession:{accession}", size=1)

    async def reviewed_for_gene(self, gene_id: str, taxon_id: int) -> ProteinSearch:
        """Swiss-Prot entries cross-referencing this NCBI Gene ID in this organism."""
        return await self.search(f"(xref:geneid-{int(gene_id)}) AND (organism_id:{taxon_id}) AND (reviewed:true)")

    async def by_hgnc(self, hgnc_number: int) -> ProteinSearch:
        return await self.search(f"(xref:hgnc-{hgnc_number}) AND (reviewed:true)")


def _to_entry(entry: _Entry, raw: dict[str, Any]) -> ProteinEntry:
    xrefs = entry.uniProtKBCrossReferences
    ensembl = sorted({p.value for x in xrefs if x.database == "Ensembl" for p in x.properties if p.key == "GeneId"})
    recommended = entry.proteinDescription.get("recommendedName") or {}
    submitted = (entry.proteinDescription.get("submissionNames") or [{}])[0]
    full_name = (recommended.get("fullName") or submitted.get("fullName") or {}).get("value")
    return ProteinEntry(
        accession=entry.primaryAccession,
        entry_name=entry.uniProtkbId,
        reviewed="reviewed" in entry.entryType.lower() and "unreviewed" not in entry.entryType.lower(),
        taxon_id=entry.organism.taxonId,
        organism=entry.organism.scientificName,
        protein_name=full_name,
        gene_symbols=[g.geneName.value for g in entry.genes if g.geneName],
        gene_synonyms=[s.value for g in entry.genes for s in g.synonyms],
        gene_ids=[x.id for x in xrefs if x.database == "GeneID"],
        hgnc_ids=[x.id for x in xrefs if x.database == "HGNC"],
        ensembl_genes=ensembl,
        length=entry.sequence.get("length"),
        sequence_version=entry.entryAudit.get("sequenceVersion"),
        raw=raw,
    )
