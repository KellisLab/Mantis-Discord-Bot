"""Notify the bot when user role data changes.

Revision ID: 20260812_0003
Revises: 20260812_0002
Create Date: 2026-08-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260812_0003"
down_revision: str | None = "20260812_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION notify_user_role_sync()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            discord_ids json;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                discord_ids = json_build_array(OLD.discord_id);
            ELSIF TG_OP = 'UPDATE'
                AND OLD.discord_id IS DISTINCT FROM NEW.discord_id THEN
                discord_ids = json_build_array(OLD.discord_id, NEW.discord_id);
            ELSE
                discord_ids = json_build_array(NEW.discord_id);
            END IF;

            PERFORM pg_notify(
                'user_role_sync',
                json_build_object('discord_ids', discord_ids)::text
            );

            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$
        """,
    )
    op.execute(
        """
        CREATE TRIGGER users_notify_role_sync
        AFTER INSERT OR UPDATE OR DELETE ON users
        FOR EACH ROW
        EXECUTE FUNCTION notify_user_role_sync()
        """,
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER users_notify_role_sync ON users")
    op.execute("DROP FUNCTION notify_user_role_sync()")
