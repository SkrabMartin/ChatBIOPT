import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.literature import PMID_PATTERN

IngestionStatus = Literal["queued", "running", "succeeded", "failed"]


class AddSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["pubmed"] = "pubmed"
    identifier: str = Field(pattern=PMID_PATTERN.pattern, description="PubMed ID")


class IngestionJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    source_type: str
    external_id: str
    source_id: uuid.UUID | None
    status: IngestionStatus
    attempts: int
    max_attempts: int
    run_after: datetime
    steps: list[dict[str, Any]]
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class DocumentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: uuid.UUID
    kind: Literal["abstract", "fulltext"]
    version: int
    access: Literal["metadata", "open_access"]
    license: str | None
    license_url: str | None
    origin: str
    origin_url: str
    text_sha256: str
    text_length: int
    raw_sha256: str
    parser_version: str
    retrieved_at: datetime
    created_at: datetime


class SourceSummary(BaseModel):
    id: uuid.UUID
    source_type: str
    external_id: str
    title: str
    url: str
    doi: str | None
    pmcid: str | None
    journal: str | None
    published_year: int | None


class LibraryItem(BaseModel):
    source: SourceSummary
    added_at: datetime
    # Latest version of each document kind, with how many chunks the current chunker produced for it.
    documents: list[DocumentSummary]
    chunk_counts: dict[str, int]
    latest_job: IngestionJobRead | None


class Library(BaseModel):
    items: list[LibraryItem]
    # Jobs whose source is not in the library yet (metadata not fetched, or the PMID turned out to be unknown).
    pending_jobs: list[IngestionJobRead]


class AddSourceResponse(BaseModel):
    job: IngestionJobRead
    # False when an identical job was already queued or running; that job is returned instead.
    created: bool


class SectionRead(BaseModel):
    kind: str
    title: str | None
    start_offset: int
    end_offset: int


class ChunkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    chunker_version: str
    ordinal: int
    section: str
    section_title: str | None
    start_offset: int
    end_offset: int
    text: str
    token_estimate: int


class DocumentRead(DocumentSummary):
    text: str
    sections: list[SectionRead]
    chunker_version: str
    chunks: list[ChunkRead]
    is_latest: bool
    source: SourceSummary


class ChunkWithProvenance(ChunkRead):
    document: DocumentSummary
    source: SourceSummary
