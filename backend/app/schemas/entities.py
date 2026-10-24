import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.entities.resolver import InputKind, MatchType, Status, validate_input

TaxonId = Field(gt=0, lt=100_000_000, description="NCBI Taxonomy ID of the organism (e.g. 9606 human)")


class OrganismRead(BaseModel):
    taxon_id: int
    scientific_name: str
    common_name: str


class ResolveRequest(BaseModel):
    query: str = Field(min_length=1, max_length=64, description="Gene symbol/alias or identifier")
    taxon_id: int = TaxonId
    kind: Literal["auto"] | InputKind = Field(
        default="auto", description="Input type; `auto` classifies identifier shapes, anything else is a symbol"
    )

    @field_validator("query")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _shape(self) -> "ResolveRequest":
        validate_input(self.query, None if self.kind == "auto" else self.kind)
        return self


class ProviderVersionRead(BaseModel):
    provider: str
    release: str | None


class CandidateRead(BaseModel):
    gene_id: str
    symbol: str
    name: str
    taxon_id: int
    organism: str
    match_type: MatchType
    aliases: list[str]
    chromosome: str | None
    map_location: str | None
    official_nomenclature: bool


class ElsewhereRead(BaseModel):
    namespace: str
    identifier: str
    label: str
    taxon_id: int
    organism: str


class ResolutionRead(BaseModel):
    query: str
    taxon_id: int
    input_kind: InputKind
    status: Status
    candidates: list[CandidateRead]
    found_elsewhere: list[ElsewhereRead]
    warnings: list[str]
    providers: list[ProviderVersionRead]
    resolver_version: str
    retrieved_at: datetime


class AddEntityRequest(BaseModel):
    gene_id: str = Field(pattern=r"^[1-9]\d{0,11}$", description="NCBI Gene ID chosen from a resolution")
    taxon_id: int = TaxonId
    query: str | None = Field(default=None, max_length=64, description="The input it was resolved from")
    match_type: MatchType | None = None


class XrefRead(BaseModel):
    namespace: str
    id: str
    source: str
    release: str | None
    versions: list[str] = []
    reciprocal: bool | None = None
    checked_against: str | None = None


class EntitySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: Literal["gene", "protein", "disease"]
    namespace: str
    accession: str
    taxon_id: int | None
    organism: str | None
    label: str
    name: str | None
    provider: str
    provider_release: str | None
    retrieved_at: datetime


class EntityRead(EntitySummary):
    xrefs: list[XrefRead]
    attributes: dict[str, Any]
    raw_payload_sha256: str


class EdgeRead(BaseModel):
    id: uuid.UUID
    predicate: Literal["encodes", "associated_with_disease"]
    direction: Literal["out", "in"]
    neighbor: EntitySummary
    provider: str
    provider_release: str | None
    score: float | None
    evidence: dict[str, Any]
    retrieved_at: datetime


class EntityDetail(EntityRead):
    edges: list[EdgeRead]


class ProjectEntityRead(BaseModel):
    entity: EntityRead
    query: str | None
    match_type: MatchType | None
    warnings: list[str]
    added_at: datetime
