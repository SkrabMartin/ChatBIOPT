import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class RetrievalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: uuid.UUID
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    mode: Literal["keyword", "semantic", "hybrid"] = "hybrid"
    top_k: int = Field(default=10, ge=1, le=50)


class RetrievalHitRead(BaseModel):
    rank: int
    chunk_id: uuid.UUID
    text: str
    section: str
    section_title: str | None
    start_offset: int
    end_offset: int
    document_id: uuid.UUID
    document_kind: str
    document_version: int
    source_id: uuid.UUID
    external_id: str
    source_title: str
    source_url: str
    published_year: int | None
    journal: str | None
    keyword_rank: int | None
    keyword_score: float | None
    keyword_coverage: float | None
    semantic_rank: int | None
    similarity: float | None
    fused_score: float
    # Would this hit count as evidence for a grounded answer (lexical match or similarity above threshold)?
    counts_as_evidence: bool


class RetrievalResponse(BaseModel):
    query: str
    mode: Literal["keyword", "semantic", "hybrid"]
    retrieval_version: str
    embedding_space: str | None
    min_similarity: float | None
    latency_ms: int
    warnings: list[str]
    hits: list[RetrievalHitRead]


ReviewStatus = Literal["requires_review", "verified", "disputed", "rejected"]


class ClaimEvidenceRead(BaseModel):
    label: str
    stance: str
    chunk_id: uuid.UUID
    text: str
    section: str
    section_title: str | None
    document_id: uuid.UUID
    document_kind: str
    document_version: int
    external_id: str
    source_title: str
    published_year: int | None


class ClaimRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    run_id: uuid.UUID
    message_id: uuid.UUID
    conversation_id: uuid.UUID
    conversation_title: str
    question: str | None
    ordinal: int
    claim_text: str
    support_state: Literal["cited", "uncited"]
    review_status: ReviewStatus
    review_note: str | None
    answer_class: str | None
    model_id: str
    extraction_version: str
    created_at: datetime
    reviewed_at: datetime | None
    evidence: list[ClaimEvidenceRead]


class ClaimReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_status: ReviewStatus
    review_note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None
