"""Keep database-backed user roles synchronized with Discord."""

import asyncio
import json
import logging
from contextlib import suppress

import discord
import psycopg
from discord.ext import commands
from sqlmodel import select

from database import DATABASE_URL, get_session
from users import Stage, User

logger = logging.getLogger(__name__)

ROLE_SYNC_CHANNEL = "user_role_sync"
JOURNEY_MENTOR_ROLE = "M • Journey Mentor"
LEADERSHIP_ROLE = "M • Leadership"
TEAM_ROLE = "M • Team"
REMOVED_PREBOARDING_ROLE = "M • Preboarding"
STAGE_ROLES = {
    Stage.ONBOARDING: "M • Onboarding",
    Stage.CARTOGRAPHER: "M • Cartographer",
    Stage.NAVIGATOR: "M • Navigator",
    Stage.SAVANT: "M • Savant",
    Stage.ADMIRAL: "M • Admiral",
    Stage.DEVELOPER: "M • Developer",
    Stage.ENGINEER: "M • Engineer",
    Stage.ARCHITECT: "M • Architect",
}
SYNCED_ROLE_NAMES = frozenset(
    {
        *STAGE_ROLES.values(),
        JOURNEY_MENTOR_ROLE,
        LEADERSHIP_ROLE,
        TEAM_ROLE,
        REMOVED_PREBOARDING_ROLE,
    },
)


def _psycopg_url() -> str:
    return DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)


class UserRoleSync:
    """Queue and reconcile managed Discord roles without blocking DB writers."""

    def __init__(self, bot: commands.Bot, worker_count: int = 2) -> None:
        self.bot = bot
        self.worker_count = worker_count
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._queued_ids: set[str] = set()
        self._tasks: list[asyncio.Task[None]] = []
        self._started = False

    def start(self) -> None:
        """Start background workers and the PostgreSQL notification listener."""

        if self._started:
            return

        self._started = True
        for worker_number in range(self.worker_count):
            self._tasks.append(
                asyncio.create_task(
                    self._worker(),
                    name=f"user-role-sync-worker-{worker_number}",
                ),
            )
        self._tasks.append(
            asyncio.create_task(
                self._listen_for_database_changes(),
                name="user-role-sync-database-listener",
            ),
        )

    async def stop(self) -> None:
        """Stop all role-sync background tasks."""

        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        self._started = False

    def enqueue(self, discord_id: str | int) -> None:
        """Schedule one Discord user for reconciliation, deduplicating queued work."""

        normalized_id = str(discord_id)
        if normalized_id in self._queued_ids:
            return

        self._queued_ids.add(normalized_id)
        self._queue.put_nowait(normalized_id)

    async def enqueue_all(self) -> None:
        """Reconcile DB users and members currently holding a managed role."""

        database_ids = await asyncio.to_thread(self._load_all_discord_ids)
        discord_ids = {
            str(member.id)
            for guild in self.bot.guilds
            for member in guild.members
            if any(role.name in SYNCED_ROLE_NAMES for role in member.roles)
        }

        for discord_id in database_ids | discord_ids:
            self.enqueue(discord_id)

    async def on_member_update(
        self,
        before: discord.Member,
        after: discord.Member,
    ) -> None:
        """Repair a manual Discord change to any managed role."""

        before_roles = {
            role.name for role in before.roles if role.name in SYNCED_ROLE_NAMES
        }
        after_roles = {
            role.name for role in after.roles if role.name in SYNCED_ROLE_NAMES
        }
        if before_roles != after_roles or before.nick != after.nick:
            self.enqueue(after.id)

    async def _worker(self) -> None:
        while True:
            discord_id = await self._queue.get()
            self._queued_ids.discard(discord_id)
            try:
                await self._sync_user(discord_id)
            except Exception:
                logger.exception("Failed to sync Discord roles for user %s", discord_id)
            finally:
                self._queue.task_done()

    async def _sync_user(self, discord_id: str) -> None:
        user = await asyncio.to_thread(self._load_user, discord_id)
        desired_names = self._desired_role_names(user)

        try:
            member_id = int(discord_id)
        except ValueError:
            logger.warning("Cannot sync invalid Discord ID %r", discord_id)
            return

        for guild in self.bot.guilds:
            member = guild.get_member(member_id)
            if member is None:
                try:
                    member = await guild.fetch_member(member_id)
                except discord.NotFound:
                    continue
                except discord.HTTPException:
                    logger.exception(
                        "Failed to fetch Discord member %s from guild %s",
                        discord_id,
                        guild.id,
                    )
                    continue

            await self._sync_member_roles(member, desired_names)
            await self._sync_member_nickname(member, user)

    async def _sync_member_roles(
        self,
        member: discord.Member,
        desired_names: set[str],
    ) -> None:
        guild_roles = {
            role.name: role
            for role in member.guild.roles
            if role.name in SYNCED_ROLE_NAMES
        }
        missing_roles = desired_names - guild_roles.keys()
        if missing_roles:
            logger.error(
                "Guild %s is missing managed role(s): %s",
                member.guild.id,
                ", ".join(sorted(missing_roles)),
            )

        current_roles = {
            role.name: role for role in member.roles if role.name in SYNCED_ROLE_NAMES
        }
        roles_to_add = [
            guild_roles[name]
            for name in desired_names - current_roles.keys()
            if name in guild_roles
        ]
        roles_to_remove = [
            role for name, role in current_roles.items() if name not in desired_names
        ]

        if roles_to_add:
            await member.add_roles(
                *roles_to_add,
                reason="Mantis database role synchronization",
            )
        if roles_to_remove:
            await member.remove_roles(
                *roles_to_remove,
                reason="Mantis database role synchronization",
            )

    @staticmethod
    async def _sync_member_nickname(
        member: discord.Member,
        user: User | None,
    ) -> None:
        if user is None or not user.full_name:
            return

        # Discord limits server nicknames to 32 characters. Keep the canonical
        # full name in the database and use its displayable prefix in Discord.
        desired_nickname = user.full_name[:32]
        if member.nick != desired_nickname:
            await member.edit(
                nick=desired_nickname,
                reason="Mantis member profile synchronization",
            )

    async def _listen_for_database_changes(self) -> None:
        while True:
            try:
                connection = await psycopg.AsyncConnection.connect(
                    _psycopg_url(),
                    autocommit=True,
                )
                async with connection:
                    await connection.execute(f"LISTEN {ROLE_SYNC_CHANNEL}")
                    await self.enqueue_all()

                    async for notification in connection.notifies():
                        payload = json.loads(notification.payload)
                        for discord_id in payload["discord_ids"]:
                            if discord_id:
                                self.enqueue(discord_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "User role sync lost its database connection; retrying",
                )
                await asyncio.sleep(5)

    @staticmethod
    def _load_user(discord_id: str) -> User | None:
        with get_session() as session:
            statement = select(User).where(User.discord_id == discord_id)
            return session.exec(statement).one_or_none()

    @staticmethod
    def _load_all_discord_ids() -> set[str]:
        with get_session() as session:
            return {
                discord_id
                for discord_id in session.exec(select(User.discord_id)).all()
                if discord_id is not None
            }

    @staticmethod
    def _desired_role_names(user: User | None) -> set[str]:
        if user is None:
            return set()

        roles: set[str] = set()
        stage_role = STAGE_ROLES.get(user.stage)
        if stage_role is not None:
            roles.add(stage_role)
        if user.stage not in {Stage.PREBOARDING, Stage.ONBOARDING}:
            roles.add(TEAM_ROLE)
        if user.is_journey_mentor:
            roles.add(JOURNEY_MENTOR_ROLE)
        if user.is_leadership:
            roles.add(LEADERSHIP_ROLE)
        return roles
