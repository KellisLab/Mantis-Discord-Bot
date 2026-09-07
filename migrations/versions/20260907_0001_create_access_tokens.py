"""Create the access_tokens table.

Revision ID: 20260907_0001
Revises: 20260831_0001
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260907_0001"
down_revision: str | None = "20260831_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "access_tokens",
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_uuid"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("uuid"),
    )
    op.create_index(
        "ix_access_tokens_token_hash",
        "access_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "uq_access_tokens_active_user",
        "access_tokens",
        ["user_uuid"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_access_tokens_active_user", table_name="access_tokens")
    op.drop_index("ix_access_tokens_token_hash", table_name="access_tokens")
    op.drop_table("access_tokens")
