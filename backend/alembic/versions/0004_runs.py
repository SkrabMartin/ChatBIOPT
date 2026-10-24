"""runs: AI execution records; messages.run_id links assistant messages to the run that produced them

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("model_profile", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("model_revision", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("context_until_sequence", sa.Integer(), nullable=False),
        sa.Column("context_message_count", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
        sa.Column("time_to_first_token_ms", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("finish_reason", sa.String(length=32), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')", name=op.f("ck_runs_status")
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_runs_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runs")),
    )
    op.create_index(
        "uq_runs_one_active_per_conversation",
        "runs",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index("ix_runs_conversation_id_created_at", "runs", ["conversation_id", "created_at"], unique=False)
    op.add_column("messages", sa.Column("run_id", sa.Uuid(), nullable=True))
    op.create_unique_constraint(op.f("uq_messages_run_id"), "messages", ["run_id"])
    op.create_foreign_key(op.f("fk_messages_run_id_runs"), "messages", "runs", ["run_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint(op.f("fk_messages_run_id_runs"), "messages", type_="foreignkey")
    op.drop_constraint(op.f("uq_messages_run_id"), "messages", type_="unique")
    op.drop_column("messages", "run_id")
    op.drop_index("ix_runs_conversation_id_created_at", table_name="runs")
    op.drop_index(
        "uq_runs_one_active_per_conversation",
        table_name="runs",
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.drop_table("runs")
