"""Create the generic stored data table.

Revision ID: 20260812_0001
Revises:
Create Date: 2026-08-12
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260812_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stored_data",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("namespace", sa.String(length=100), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "namespace",
            "key",
            name="uq_stored_data_namespace_key",
        ),
    )
    op.create_index(
        op.f("ix_stored_data_namespace"),
        "stored_data",
        ["namespace"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_stored_data_namespace"), table_name="stored_data")
    op.drop_table("stored_data")
