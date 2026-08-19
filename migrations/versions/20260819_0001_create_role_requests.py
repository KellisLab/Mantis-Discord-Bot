"""Create the role_requests table.

Revision ID: 20260819_0001
Revises: 20260812_0006
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260819_0001"
down_revision: str | None = "20260812_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

role_request_type = postgresql.ENUM(
    "stage",
    "journey_mentor",
    "leadership",
    name="role_request_type",
    create_type=False,
)
role_request_status = postgresql.ENUM(
    "pending",
    "approved",
    "rejected",
    name="role_request_status",
    create_type=False,
)
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
    role_request_type.create(op.get_bind(), checkfirst=True)
    role_request_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "role_requests",
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requester_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_type", role_request_type, nullable=False),
        sa.Column("requested_stage", user_stage, nullable=True),
        sa.Column("justification", sa.Text(), nullable=True),
        sa.Column(
            "evidence_urls",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("discord_message_id", sa.Text(), nullable=True),
        sa.Column(
            "status",
            role_request_status,
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["requester_uuid"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("uuid"),
    )
    op.create_index(
        "uq_role_requests_pending_requester_type",
        "role_requests",
        ["requester_uuid", "request_type"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_role_requests_pending_requester_type", table_name="role_requests"
    )
    op.drop_table("role_requests")
    role_request_status.drop(op.get_bind(), checkfirst=True)
    role_request_type.drop(op.get_bind(), checkfirst=True)
