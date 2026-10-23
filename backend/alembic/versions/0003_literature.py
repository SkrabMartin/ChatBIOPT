"""literature: sources, source_identifiers, literature_searches

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("record", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("raw_payload", sa.Text(), nullable=False),
        sa.Column("raw_payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("normalizer_version", sa.String(length=32), nullable=False),
        sa.Column("first_retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sources")),
        sa.UniqueConstraint("source_type", "external_id", name=op.f("uq_sources_source_type_external_id")),
    )
    op.create_table(
        "source_identifiers",
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("scheme", sa.String(length=16), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], name=op.f("fk_source_identifiers_source_id_sources"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("source_id", "scheme", name=op.f("pk_source_identifiers")),
    )
    op.create_index("ix_source_identifiers_scheme_value", "source_identifiers", ["scheme", "value"], unique=False)
    op.create_table(
        "literature_searches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("query_translation", sa.Text(), nullable=True),
        sa.Column("limit", sa.Integer(), nullable=False),
        sa.Column("offset", sa.Integer(), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=False),
        sa.Column("result_external_ids", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_literature_searches")),
    )


def downgrade() -> None:
    op.drop_table("literature_searches")
    op.drop_index("ix_source_identifiers_scheme_value", table_name="source_identifiers")
    op.drop_table("source_identifiers")
    op.drop_table("sources")
