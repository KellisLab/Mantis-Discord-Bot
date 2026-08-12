"""Create the users table.

Revision ID: 20260812_0002
Revises: 20260812_0001
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260812_0002"
down_revision: str | None = "20260812_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


user_stage = postgresql.ENUM(
    "preboarding",
    "onboarding",
    "cartographer",
    "navigator",
    "savant",
    "admiral",
    "developer",
    "engineer",
    "architect",
    name="user_stage",
    create_type=False,
)


def upgrade() -> None:
    user_stage.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("discord_id", sa.Text(), nullable=False),
        sa.Column("github_username", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("whatsapp_number", sa.Text(), nullable=True),
        sa.Column("stage", user_stage, nullable=False),
        sa.Column(
            "is_journey_mentor",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "is_leadership",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
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
    )
    op.create_index(
        op.f("ix_users_discord_id"),
        "users",
        ["discord_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_users_email"),
        "users",
        ["email"],
        unique=True,
    )
    op.create_index(
        op.f("ix_users_github_username"),
        "users",
        ["github_username"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_users_github_username"), table_name="users")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_index(op.f("ix_users_discord_id"), table_name="users")
    op.drop_table("users")
    user_stage.drop(op.get_bind(), checkfirst=True)
