"""Revise team provisioning, orphan handling, and close attempts.

Revision ID: 20260812_0006
Revises: 20260812_0005
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260812_0006"
down_revision: str | None = "20260812_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

attempt_status = postgresql.ENUM(
    "open",
    "passed",
    "cancelled",
    name="team_close_attempt_status",
    create_type=False,
)


def upgrade() -> None:
    # PostgreSQL requires the enum value to be committed before it can be used.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE team_status ADD VALUE IF NOT EXISTS 'orphaned'")

    op.alter_column(
        "teams", "discord_channel_id", existing_type=sa.Text(), nullable=True
    )
    attempt_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "team_close_attempts",
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("discord_message_id", sa.Text(), nullable=True),
        sa.Column("status", attempt_status, server_default="open", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["team_uuid"], ["teams.uuid"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("uuid"),
    )
    op.create_index(
        "uq_team_close_attempts_open",
        "team_close_attempts",
        ["team_uuid"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )

    # Revision 0005 stored the message on Team and keyed votes by team. Build
    # one synthetic attempt per affected team before replacing the vote table.
    op.execute(
        """
        INSERT INTO team_close_attempts
            (uuid, team_uuid, discord_message_id, status, created_at)
        SELECT gen_random_uuid(), uuid, close_vote_message_id, 'open', now()
        FROM teams
        WHERE close_vote_message_id IS NOT NULL
        """
    )
    op.rename_table("team_close_votes", "legacy_team_close_votes")
    op.execute(
        """
        INSERT INTO team_close_attempts
            (uuid, team_uuid, discord_message_id, status, created_at)
        SELECT gen_random_uuid(), votes.team_uuid, NULL, 'cancelled', min(votes.created_at)
        FROM legacy_team_close_votes AS votes
        WHERE NOT EXISTS (
            SELECT 1 FROM team_close_attempts AS attempts
            WHERE attempts.team_uuid = votes.team_uuid
        )
        GROUP BY votes.team_uuid
        """
    )
    op.create_table(
        "team_close_votes",
        sa.Column("close_attempt_uuid", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["close_attempt_uuid"], ["team_close_attempts.uuid"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["member_uuid"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("close_attempt_uuid", "member_uuid"),
    )
    op.execute(
        """
        INSERT INTO team_close_votes
            (close_attempt_uuid, member_uuid, rank_at_vote, created_at)
        SELECT attempts.uuid, votes.member_uuid, votes.rank_at_vote, votes.created_at
        FROM legacy_team_close_votes AS votes
        JOIN team_close_attempts AS attempts ON attempts.team_uuid = votes.team_uuid
        """
    )
    op.drop_table("legacy_team_close_votes")
    op.drop_column("teams", "close_vote_message_id")


def downgrade() -> None:
    # A 0005 schema can retain only one attempt per team. Prefer each member's
    # vote from the newest attempt when collapsing the history below.
    op.add_column("teams", sa.Column("close_vote_message_id", sa.Text(), nullable=True))
    op.execute(
        """
        UPDATE teams
        SET close_vote_message_id = attempts.discord_message_id
        FROM team_close_attempts AS attempts
        WHERE attempts.team_uuid = teams.uuid AND attempts.status = 'open'
        """
    )
    op.rename_table("team_close_votes", "attempt_team_close_votes")
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
    op.execute(
        """
        INSERT INTO team_close_votes (team_uuid, member_uuid, rank_at_vote, created_at)
        SELECT DISTINCT ON (attempts.team_uuid, votes.member_uuid)
            attempts.team_uuid, votes.member_uuid, votes.rank_at_vote, votes.created_at
        FROM attempt_team_close_votes AS votes
        JOIN team_close_attempts AS attempts ON attempts.uuid = votes.close_attempt_uuid
        ORDER BY attempts.team_uuid, votes.member_uuid, attempts.created_at DESC
        """
    )
    op.drop_table("attempt_team_close_votes")
    op.drop_index("uq_team_close_attempts_open", table_name="team_close_attempts")
    op.drop_table("team_close_attempts")
    attempt_status.drop(op.get_bind(), checkfirst=True)
    op.execute("UPDATE teams SET status = 'closed' WHERE status = 'orphaned'")
    op.execute(
        "UPDATE teams SET discord_channel_id = 'unprovisioned-' || uuid::text "
        "WHERE discord_channel_id IS NULL"
    )
    op.alter_column(
        "teams", "discord_channel_id", existing_type=sa.Text(), nullable=False
    )
    # PostgreSQL cannot remove one enum value in place; retaining the unused
    # value is harmless and keeps downgrade safe for dependent objects.
