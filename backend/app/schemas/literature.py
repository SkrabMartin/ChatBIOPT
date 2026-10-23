import re
import uuid
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

LiteratureSource = Literal["pubmed"]

# Identifier syntax accepted from upstream. Anything else is dropped rather than turned into a link.
PMID_PATTERN = re.compile(r"^[1-9]\d{0,9}$")
PMCID_PATTERN = re.compile(r"^PMC\d{1,10}$")
DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$")

# PubMed ESearch cannot page beyond the first 10,000 hits.
PUBMED_MAX_WINDOW = 10_000


class AbstractSection(BaseModel):
    label: str | None
    text: str


class LiteratureLinks(BaseModel):
    """Outbound links, each built only from an identifier that passed syntax validation."""

    pubmed: str
    doi: str | None
    pmc: str | None


class LiteratureRecord(BaseModel):
    """Normalized bibliographic record. Upstream field names never leak past this contract."""

    source: LiteratureSource
    source_id: str
    pmid: str
    pmcid: str | None
    doi: str | None
    title: str
    abstract: str | None
    abstract_sections: list[AbstractSection]
    authors: list[str]
    journal: str | None
    published_year: int | None
    # ISO 8601 with the precision PubMed provides: "2008", "2008-10" or "2008-10-23".
    published_date: str | None
    publication_types: list[str]
    source_url: str
    links: LiteratureLinks
    retrieved_at: datetime
    raw_payload_sha256: str
    normalizer_version: str


class LiteratureSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    limit: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _within_pubmed_window(self) -> Self:
        if self.offset + self.limit > PUBMED_MAX_WINDOW:
            raise ValueError(f"offset + limit must not exceed {PUBMED_MAX_WINDOW} (PubMed paging limit)")
        return self


class LiteratureSearchResponse(BaseModel):
    search_id: uuid.UUID
    source: LiteratureSource
    query: str
    # PubMed's interpretation of the query (MeSH expansion etc.), shown so users can audit the search strategy.
    query_translation: str | None
    total_count: int
    limit: int
    offset: int
    retrieved_at: datetime
    items: list[LiteratureRecord]
