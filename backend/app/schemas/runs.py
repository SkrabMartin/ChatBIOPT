import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

RunStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Registry profile id; the registry's default when omitted.
    model_profile: str | None = None
    # "auto": ground in the project's evidence library when it has passages, otherwise answer from the model alone.
    grounding: Literal["auto", "project", "none"] = "auto"


class RunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    kind: str
    status: RunStatus
    model_profile: str
    provider: str
    model_id: str
    model_revision: str | None
    prompt_version: str
    config_hash: str
    context_until_sequence: int
    context_message_count: int | None
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost_usd: float | None
    time_to_first_token_ms: int | None
    latency_ms: int | None
    finish_reason: str | None
    error_code: str | None
    error_message: str | None
    grounding: Literal["none", "project"]
    retrieval_version: str | None
    embedding_space: str | None
    answer_class: str | None
    citation_stats: dict[str, int] | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RunEvidenceRead(BaseModel):
    """A passage given to a grounded run as `[label]`, with retrieval ranks and whether the answer cited it."""

    label: str
    rank: int
    cited: bool
    keyword_rank: int | None
    semantic_rank: int | None
    similarity: float | None
    fused_score: float
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


class ModelProfileRead(BaseModel):
    id: str
    label: str
    provider: str
    model: str
    deployment: str
    data_policy: str
    context_window: int
    max_output_tokens: int
    license: str
    price_input_per_mtok: float
    price_output_per_mtok: float
    notes: str | None
    is_default: bool
    # Policy gate (external data / missing key): when set, the profile cannot be used at all.
    blocked_reason: str | None
    available: bool
    unavailable_reason: str | None
    revision: str | None


class ModelList(BaseModel):
    default_profile: str
    allow_external_models: bool
    items: list[ModelProfileRead]
