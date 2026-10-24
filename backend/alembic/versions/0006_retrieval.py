"""retrieval: chunk full-text index, pgvector embeddings, run evidence, claims

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The pgvector extension ships with the pgvector/pgvector image; creating it needs no superuser there.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "chunk_embeddings",
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("embedding_space", sa.String(length=160), nullable=False),
        sa.Column("profile_id", sa.String(length=64), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("vector_dims(embedding) = dimensions", name=op.f("ck_chunk_embeddings_dimensions")),
        sa.ForeignKeyConstraint(
            ["chunk_id"], ["chunks.id"], name=op.f("fk_chunk_embeddings_chunk_id_chunks"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("chunk_id", "embedding_space", name=op.f("pk_chunk_embeddings")),
    )
    op.create_index("ix_chunk_embeddings_embedding_space", "chunk_embeddings", ["embedding_space"], unique=False)
    op.create_table(
        "run_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=8), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("keyword_rank", sa.Integer(), nullable=True),
        sa.Column("semantic_rank", sa.Integer(), nullable=True),
        sa.Column("similarity", sa.Float(), nullable=True),
        sa.Column("fused_score", sa.Float(), nullable=False),
        sa.Column("cited", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(
            ["chunk_id"], ["chunks.id"], name=op.f("fk_run_evidence_chunk_id_chunks"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name=op.f("fk_run_evidence_run_id_runs"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_evidence")),
        sa.UniqueConstraint("run_id", "chunk_id", name=op.f("uq_run_evidence_run_id_chunk_id")),
        sa.UniqueConstraint("run_id", "label", name=op.f("uq_run_evidence_run_id_label")),
    )
    op.create_table(
        "claims",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("support_state", sa.String(length=16), nullable=False),
        sa.Column("review_status", sa.String(length=16), server_default="requires_review", nullable=False),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("extraction_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "review_status IN ('requires_review', 'verified', 'disputed', 'rejected')",
            name=op.f("ck_claims_review_status"),
        ),
        sa.CheckConstraint("support_state IN ('cited', 'uncited')", name=op.f("ck_claims_support_state")),
        sa.ForeignKeyConstraint(
            ["message_id"], ["messages.id"], name=op.f("fk_claims_message_id_messages"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_claims_project_id_projects"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name=op.f("fk_claims_run_id_runs"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_claims")),
        sa.UniqueConstraint("run_id", "ordinal", name=op.f("uq_claims_run_id_ordinal")),
    )
    op.create_index("ix_claims_project_id_created_at", "claims", ["project_id", "created_at"], unique=False)
    op.create_table(
        "claim_evidence",
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=8), nullable=False),
        sa.Column("stance", sa.String(length=16), server_default="cited", nullable=False),
        sa.ForeignKeyConstraint(
            ["chunk_id"], ["chunks.id"], name=op.f("fk_claim_evidence_chunk_id_chunks"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claims.id"], name=op.f("fk_claim_evidence_claim_id_claims"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("claim_id", "chunk_id", name=op.f("pk_claim_evidence")),
    )
    op.add_column(
        "chunks",
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', coalesce(section_title, '') || ' ' || text)", persisted=True),
            nullable=False,
        ),
    )
    op.create_index("ix_chunks_search_vector", "chunks", ["search_vector"], unique=False, postgresql_using="gin")
    op.add_column("runs", sa.Column("grounding", sa.String(length=16), server_default="none", nullable=False))
    op.add_column("runs", sa.Column("retrieval_version", sa.String(length=32), nullable=True))
    op.add_column("runs", sa.Column("embedding_space", sa.String(length=160), nullable=True))
    op.add_column("runs", sa.Column("answer_class", sa.String(length=32), nullable=True))
    op.add_column("runs", sa.Column("citation_stats", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column("runs", "citation_stats")
    op.drop_column("runs", "answer_class")
    op.drop_column("runs", "embedding_space")
    op.drop_column("runs", "retrieval_version")
    op.drop_column("runs", "grounding")
    op.drop_index("ix_chunks_search_vector", table_name="chunks", postgresql_using="gin")
    op.drop_column("chunks", "search_vector")
    op.drop_table("claim_evidence")
    op.drop_index("ix_claims_project_id_created_at", table_name="claims")
    op.drop_table("claims")
    op.drop_table("run_evidence")
    op.drop_index("ix_chunk_embeddings_embedding_space", table_name="chunk_embeddings")
    op.drop_table("chunk_embeddings")
    # The extension is left installed: other database objects may depend on it.
