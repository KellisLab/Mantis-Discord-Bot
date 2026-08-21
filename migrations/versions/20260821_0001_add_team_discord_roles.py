"""Add managed Discord roles to teams.

Revision ID: 20260821_0001
Revises: 20260819_0001
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260821_0001"
down_revision: str | None = "20260819_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("teams", sa.Column("discord_role_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("teams", "discord_role_id")
