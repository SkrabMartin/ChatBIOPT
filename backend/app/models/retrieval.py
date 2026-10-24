import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

REVIEW_STATUSES = ("requires_review", "verified", "disputed", "rejected")


class ChunkEmbedding(Base):
    """A chunk's vector in one embedding space (`<profile>@<model revision>`).

    Spaces are never mixed: retrieval compares a query only with vectors of the same space, and a model change
    produces a new space that chunks are re-embedded into.
    """

    __tablename__ = "chunk_embeddings"
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012
    __table_args__ = (
        CheckConstraint("vector_dims(embedding) = dimensions", name="dimensions"),
        Index("ix_chunk_embeddings_embedding_space", "embedding_space"),
    )

    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True)
    embedding_space: Mapped[str] = mapped_column(String(160), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(64), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    # Untyped vector column: dimension depends on the space and is enforced by the check constraint.
    embedding: Mapped[list[float]] = mapped_column(Vector(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RunEvidence(Base):
    """A passage retrieved for a grounded run, with its ranks per retriever and whether the answer cited it."""

    __tablename__ = "run_evidence"
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012
    __table_args__ = (UniqueConstraint("run_id", "label"), UniqueConstraint("run_id", "chunk_id"))

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    label: Mapped[str] = mapped_column(String(8), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chunks.id", ondelete="RESTRICT"), nullable=False)
    keyword_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    semantic_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    fused_score: Mapped[float] = mapped_column(Float, nullable=False)
    rerank_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cited: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


class Claim(Base):
    """One sentence of a grounded answer, with the evidence it cites.

    `support_state` is mechanical (did the sentence cite valid evidence?); it is NOT a judgement that the
    evidence entails the claim. `review_status` records the human judgement on the evidence board.
    """

    __tablename__ = "claims"
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012
    __table_args__ = (
        UniqueConstraint("run_id", "ordinal"),
        CheckConstraint("support_state IN ('cited', 'uncited')", name="support_state"),
        CheckConstraint(f"review_status IN {REVIEW_STATUSES}", name="review_status"),
        Index("ix_claims_project_id_created_at", "project_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    support_state: Mapped[str] = mapped_column(String(16), nullable=False)
    review_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="requires_review")
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ClaimEvidence(Base):
    __tablename__ = "claim_evidence"

    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), primary_key=True)
    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chunks.id", ondelete="RESTRICT"), primary_key=True)
    label: Mapped[str] = mapped_column(String(8), nullable=False)
    # What the model asserted by citing; whether the passage really supports the claim is a review question.
    stance: Mapped[str] = mapped_column(String(16), nullable=False, server_default="cited")


class Evaluation(Base):
    """One evaluation run (retrieval or answer quality) with its full report, for comparison over time."""

    __tablename__ = "evaluations"
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012
    __table_args__ = (
        CheckConstraint("kind IN ('retrieval', 'answers', 'entities')", name="kind"),
        Index("ix_evaluations_kind_created_at", "kind", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    dataset: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(32), nullable=False)
    dataset_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # dev: used for tuning; test: held out, used only to report.
    split: Mapped[str] = mapped_column(String(16), nullable=False)
    git_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Headline numbers per configuration, e.g. {"hybrid": {"ndcg@10": 0.83, ...}}.
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
