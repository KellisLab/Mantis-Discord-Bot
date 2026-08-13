"""Update users to the canonical member profile model.

Revision ID: 20260812_0004
Revises: 20260812_0003
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_0004"
down_revision: str | None = "20260812_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A missing email cannot be repaired safely because email is the canonical
    # profile-linking key. This statement deliberately stops the migration if
    # legacy rows still need an email assigned.
    op.alter_column("users", "email", existing_type=sa.Text(), nullable=False)
    op.alter_column("users", "full_name", existing_type=sa.Text(), nullable=True)
    op.alter_column("users", "discord_id", existing_type=sa.Text(), nullable=True)
    op.alter_column(
        "users",
        "stage",
        server_default="preboarding",
        existing_nullable=False,
    )
    op.drop_index(op.f("ix_users_github_username"), table_name="users")


def downgrade() -> None:
    op.create_index(
        op.f("ix_users_github_username"),
        "users",
        ["github_username"],
        unique=True,
    )
    op.alter_column(
        "users",
        "stage",
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column("users", "discord_id", existing_type=sa.Text(), nullable=False)
    op.alter_column("users", "full_name", existing_type=sa.Text(), nullable=False)
    op.alter_column("users", "email", existing_type=sa.Text(), nullable=True)
