"""Create durable provider-based access synchronization.

Revision ID: 20260831_0001
Revises: 20260821_0001
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260831_0001"
down_revision: str | None = "20260821_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    status = postgresql.ENUM(
        "pending", "running", "failed", name="access_sync_job_status"
    )
    status.create(op.get_bind(), checkfirst=True)
    status_column = postgresql.ENUM(
        "pending",
        "running",
        "failed",
        name="access_sync_job_status",
        create_type=False,
    )

    op.create_table(
        "access_sync_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("member_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["member_uuid"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_access_sync_events_member_uuid", "access_sync_events", ["member_uuid"]
    )
    op.create_table(
        "access_sync_jobs",
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("status", status_column, server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["member_uuid"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("uuid"),
    )
    op.create_index("ix_access_sync_jobs_member_uuid", "access_sync_jobs", ["member_uuid"])
    op.create_index(
        "uq_access_sync_jobs_pending_member_provider",
        "access_sync_jobs",
        ["member_uuid", "provider"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "access_sync_identities",
        sa.Column("member_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("external_login", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["member_uuid"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("member_uuid", "provider"),
    )

    # Refuse ambiguous ownership instead of silently picking a GitHub account.
    op.create_index(
        "uq_users_github_username_ci",
        "users",
        [sa.text("lower(github_username)")],
        unique=True,
        postgresql_where=sa.text(
            "github_username IS NOT NULL AND btrim(github_username) <> ''"
        ),
    )

    op.execute("DROP TRIGGER IF EXISTS users_notify_role_sync ON users")
    op.execute("DROP FUNCTION IF EXISTS notify_user_role_sync()")
    op.execute(
        """
        CREATE FUNCTION queue_user_access_sync()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            INSERT INTO access_sync_events (member_uuid) VALUES (NEW.id);
            PERFORM pg_notify(
                'access_sync', json_build_object('member_uuid', NEW.id)::text
            );
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER users_queue_access_sync
        AFTER INSERT OR UPDATE ON users
        FOR EACH ROW EXECUTE FUNCTION queue_user_access_sync()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER users_queue_access_sync ON users")
    op.execute("DROP FUNCTION queue_user_access_sync()")
    op.drop_index("uq_users_github_username_ci", table_name="users")
    op.drop_table("access_sync_identities")
    op.drop_index("uq_access_sync_jobs_pending_member_provider", table_name="access_sync_jobs")
    op.drop_index("ix_access_sync_jobs_member_uuid", table_name="access_sync_jobs")
    op.drop_table("access_sync_jobs")
    op.drop_index("ix_access_sync_events_member_uuid", table_name="access_sync_events")
    op.drop_table("access_sync_events")
    postgresql.ENUM(name="access_sync_job_status").drop(op.get_bind(), checkfirst=True)

    # Restore the legacy listener for rollback compatibility.
    op.execute(
        """
        CREATE FUNCTION notify_user_role_sync()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE discord_ids json;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                discord_ids = json_build_array(OLD.discord_id);
            ELSIF TG_OP = 'UPDATE' AND OLD.discord_id IS DISTINCT FROM NEW.discord_id THEN
                discord_ids = json_build_array(OLD.discord_id, NEW.discord_id);
            ELSE
                discord_ids = json_build_array(NEW.discord_id);
            END IF;
            PERFORM pg_notify('user_role_sync', json_build_object('discord_ids', discord_ids)::text);
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER users_notify_role_sync
        AFTER INSERT OR UPDATE OR DELETE ON users
        FOR EACH ROW EXECUTE FUNCTION notify_user_role_sync()
        """
    )
