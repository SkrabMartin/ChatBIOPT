import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Source(Base):
    """Latest verified snapshot of an upstream bibliographic record, keyed by `(source_type, external_id)`.

    `record` holds the normalized `LiteratureRecord`; `raw_payload` the canonical upstream payload it was derived
    from, so every displayed field can be audited against what the provider actually returned.
    """

    __tablename__ = "sources"
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012
    __table_args__ = (UniqueConstraint("source_type", "external_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    record: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    raw_payload: Mapped[str] = mapped_column(Text, nullable=False)
    raw_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    normalizer_version: Mapped[str] = mapped_column(String(32), nullable=False)
    first_retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceIdentifier(Base):
    """All identifiers of a source (pmid, doi, pmcid). M:N by design: one DOI may map to records from several
    providers, so publications are never merged on a single overloaded ID (or on title)."""

    __tablename__ = "source_identifiers"
    __table_args__ = (Index("ix_source_identifiers_scheme_value", "scheme", "value"),)

    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True)
    scheme: Mapped[str] = mapped_column(String(16), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class LiteratureSearch(Base):
    """Audit log of a provider search: what was asked, how the provider interpreted it, what came back, when."""

    __tablename__ = "literature_searches"
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    query_translation: Mapped[str | None] = mapped_column(Text, nullable=True)
    limit: Mapped[int] = mapped_column(Integer, nullable=False)
    offset: Mapped[int] = mapped_column(Integer, nullable=False)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # Ordered as ranked by the provider.
    result_external_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
