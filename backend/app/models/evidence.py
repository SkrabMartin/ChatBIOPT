import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

INGESTION_STATUSES = ("queued", "running", "succeeded", "failed")
ACTIVE_INGESTION_STATUSES = ("queued", "running")


class Document(Base):
    """One immutable text version of a source (`abstract` or `fulltext`).

    A new version is created only when the normalized text changes (`text_sha256`); old versions and their chunks
    are never deleted, so anything that cited them stays resolvable to the exact text that was seen.
    Text and raw upstream payload live in the object store; this row holds keys and checksums.
    """

    __tablename__ = "documents"
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012
    __table_args__ = (
        UniqueConstraint("source_id", "kind", "version"),
        UniqueConstraint("source_id", "kind", "text_sha256"),
        CheckConstraint("kind IN ('abstract', 'fulltext')", name="kind"),
        CheckConstraint("access IN ('metadata', 'open_access')", name="access"),
        CheckConstraint("version > 0", name="version_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    text_length: Mapped[int] = mapped_column(Integer, nullable=False)
    text_storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    raw_storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    raw_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    origin_url: Mapped[str] = mapped_column(Text, nullable=False)
    # `metadata`: bibliographic abstract; `open_access`: full text stored under `license`.
    access: Mapped[str] = mapped_column(String(16), nullable=False)
    license: Mapped[str | None] = mapped_column(String(32), nullable=True)
    license_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)
    sections: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Chunk(Base):
    """Addressable passage of a document version, produced by a specific chunker version."""

    __tablename__ = "chunks"
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012
    __table_args__ = (
        UniqueConstraint("document_id", "chunker_version", "ordinal"),
        CheckConstraint("end_offset > start_offset", name="offsets"),
        Index("ix_chunks_search_vector", "search_vector", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False)
    chunker_version: Mapped[str] = mapped_column(String(32), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str] = mapped_column(String(16), nullable=False)
    section_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_estimate: Mapped[int] = mapped_column(Integer, nullable=False)
    # Lexical index (English stemming). Exact identifiers such as "R175H" or "rs121913343" stay single tokens.
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(section_title, '') || ' ' || text)", persisted=True),
        nullable=False,
        deferred=True,  # only queried in SQL; never needed on loaded objects
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ProjectSource(Base):
    """A source in a project's evidence library. Sources/documents are shared public data; the link is private."""

    __tablename__ = "project_sources"
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="RESTRICT"), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class IngestionJob(Base):
    """Durable queue entry processed by the ingestion worker (`SELECT ... FOR UPDATE SKIP LOCKED`).

    `steps` is the per-step log; `locked_until` is the worker lease, so a crashed worker's job is picked up again.
    """

    __tablename__ = "ingestion_jobs"
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012
    __table_args__ = (
        CheckConstraint(f"status IN {INGESTION_STATUSES}", name="status"),
        # Idempotency: one active job per (project, source); repeated requests return the existing job.
        Index(
            "uq_ingestion_jobs_one_active",
            "project_id",
            "source_type",
            "external_id",
            unique=True,
            postgresql_where=text(f"status IN {ACTIVE_INGESTION_STATUSES}"),
        ),
        Index("ix_ingestion_jobs_status_run_after", "status", "run_after"),
        Index("ix_ingestion_jobs_project_id", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default="[]")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
