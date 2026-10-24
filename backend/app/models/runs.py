import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

RUN_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")
ACTIVE_RUN_STATUSES = ("queued", "running")


class Run(Base):
    """One AI execution with everything needed to audit and reproduce it: model + revision, prompt version,
    config hash, the exact context window it saw, usage, latency, cost and outcome.

    The assistant message it produced (if any) points back here via `messages.run_id`.
    """

    __tablename__ = "runs"
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012
    __table_args__ = (
        CheckConstraint(f"status IN {RUN_STATUSES}", name="status"),
        # At most one queued/running run per conversation, enforced by the database rather than a race-prone check.
        Index(
            "uq_runs_one_active_per_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text(f"status IN {ACTIVE_RUN_STATUSES}"),
        ),
        Index("ix_runs_conversation_id_created_at", "conversation_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    model_profile: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    model_revision: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Messages with sequence <= this existed when the run was created; the context is drawn from them.
    context_until_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    context_message_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_to_first_token_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
