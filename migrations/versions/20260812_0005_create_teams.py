"""Create team management tables.

Revision ID: 20260812_0005
Revises: 20260812_0004
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260812_0005"
down_revision: str | None = "20260812_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

team_status = postgresql.ENUM(
    "active", "closing", "closed", name="team_status", create_type=False
)
join_status = postgresql.ENUM(
    "pending",
    "approved",
    "rejected",
    name="team_join_request_status",
    create_type=False,
)


def upgrade() -> None:
    team_status.create(op.get_bind(), checkfirst=True)
    join_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "teams",
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("discord_channel_id", sa.Text(), nullable=False),
        sa.Column("info_message_id", sa.Text(), nullable=True),
        sa.Column("close_vote_message_id", sa.Text(), nullable=True),
        sa.Column("status", team_status, server_default="active", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("uuid"),
        sa.UniqueConstraint("discord_channel_id"),
    )
    op.create_index("ix_teams_discord_channel_id", "teams", ["discord_channel_id"])
    op.create_index(
        "uq_teams_active_name",
        "teams",
        [sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "team_memberships",
        sa.Column("team_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("rank BETWEEN 1 AND 4", name="ck_team_membership_rank"),
        sa.ForeignKeyConstraint(["member_uuid"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_uuid"], ["teams.uuid"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("team_uuid", "member_uuid"),
    )
    op.create_index(
        "uq_team_memberships_one_lead",
        "team_memberships",
        ["team_uuid"],
        unique=True,
        postgresql_where=sa.text("rank = 1"),
    )

    op.create_table(
        "team_join_requests",
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("discord_message_id", sa.Text(), nullable=True),
        sa.Column("status", join_status, server_default="pending", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["member_uuid"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["team_uuid"], ["teams.uuid"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("uuid"),
    )
    op.create_index(
        "uq_team_join_requests_pending_member",
        "team_join_requests",
        ["team_uuid", "member_uuid"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "team_close_votes",
        sa.Column("team_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rank_at_vote", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "rank_at_vote BETWEEN 1 AND 4", name="ck_team_close_vote_rank"
        ),
        sa.ForeignKeyConstraint(["member_uuid"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_uuid"], ["teams.uuid"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("team_uuid", "member_uuid"),
    )


def downgrade() -> None:
    op.drop_table("team_close_votes")
    op.drop_index(
        "uq_team_join_requests_pending_member", table_name="team_join_requests"
    )
    op.drop_table("team_join_requests")
    op.drop_index("uq_team_memberships_one_lead", table_name="team_memberships")
    op.drop_table("team_memberships")
    op.drop_index("uq_teams_active_name", table_name="teams")
    op.drop_index("ix_teams_discord_channel_id", table_name="teams")
    op.drop_table("teams")
    join_status.drop(op.get_bind(), checkfirst=True)
    team_status.drop(op.get_bind(), checkfirst=True)
