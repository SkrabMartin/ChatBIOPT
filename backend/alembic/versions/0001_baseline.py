"""baseline: empty schema anchor for all later migrations

Domain tables arrive with the milestones that need them (M02: projects, conversations, messages).

Revision ID: 0001
Revises:
Create Date: 2026-10-23
"""

from collections.abc import Sequence

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
