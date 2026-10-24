import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
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

ENTITY_KINDS = ("gene", "protein", "disease")
# Typed relations. Each predicate has one meaning, one direction and one provider-defined evidence shape:
#   encodes                  gene -> protein   UniProtKB entry cross-references the NCBI Gene ID (same organism)
#   associated_with_disease  gene -> disease   Open Targets association score: aggregated evidence, not causality
EDGE_PREDICATES = ("encodes", "associated_with_disease")


class Entity(Base):
    """A normalized biological object keyed by its authoritative `(namespace, accession)`.

    Genes and proteins always carry the organism (`taxon_id`): a symbol alone is never an identity. `raw_payload`
    is the provider record the row was derived from; `provider_release` the provider's data version.
    """

    __tablename__ = "entities"
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012
    __table_args__ = (
        UniqueConstraint("namespace", "accession"),
        CheckConstraint(f"kind IN {ENTITY_KINDS}", name="kind"),
        CheckConstraint("kind = 'disease' OR taxon_id IS NOT NULL", name="organism_required"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    namespace: Mapped[str] = mapped_column(String(32), nullable=False)
    accession: Mapped[str] = mapped_column(String(64), nullable=False)
    taxon_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    organism: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Display label: gene symbol, UniProtKB entry name or disease name.
    label: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # [{"namespace", "id", "source", "release", "reciprocal"}]: every cross-reference names who asserted it.
    xrefs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_release: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_payload: Mapped[str] = mapped_column(Text, nullable=False)
    raw_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class EntityEdge(Base):
    """A typed relation asserted by one provider in one data release, with its evidence."""

    __tablename__ = "entity_edges"
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012
    __table_args__ = (
        CheckConstraint(f"predicate IN {EDGE_PREDICATES}", name="predicate"),
        # Explicit name: the naming convention would exceed PostgreSQL's 63-character identifier limit.
        Index("ux_entity_edges_identity", "source_entity_id", "predicate", "target_entity_id", "provider", unique=True),
        CheckConstraint("score IS NULL OR (score >= 0 AND score <= 1)", name="score"),
        Index("ix_entity_edges_target_entity_id", "target_entity_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    predicate: Mapped[str] = mapped_column(String(32), nullable=False)
    target_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_release: Mapped[str | None] = mapped_column(String(64), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProjectEntity(Base):
    """A gene the user resolved and pinned to a project, with the input it was resolved from."""

    __tablename__ = "project_entities"
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012
    __table_args__ = (Index("ix_project_entities_entity_id", "entity_id"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="RESTRICT"), primary_key=True)
    query: Mapped[str | None] = mapped_column(Text, nullable=True)
    match_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    warnings: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
