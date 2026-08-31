"""Discord projection adapter for the generic access-sync engine."""

from __future__ import annotations

import asyncio
from uuid import UUID

import discord
from discord.ext import commands
from sqlmodel import select

from access_sync.models import AccessSyncIdentity
from access_sync.types import SyncResult
from database import get_session
from members.models import User
from members.permissions import PERMANENT_LEADERSHIP_DISCORD_IDS
from utils.user_role_sync import MANTIS_AGENT_APP_ID, SYNCED_ROLE_NAMES, UserRoleSync


class DiscordAccessProvider:
    name = "discord"

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.projector = UserRoleSync(bot)

    async def reconcile(
        self, member_uuid: UUID, *, dry_run: bool = False
    ) -> SyncResult:
        # Reload at execution time. Jobs deliberately carry no member snapshot.
        user, identity = await asyncio.to_thread(self._load_state, member_uuid)
        current_id = user.discord_id if user is not None else None
        if dry_run:
            return SyncResult()
        if identity is not None and identity.external_id != current_id:
            await self.projector._sync_user(identity.external_id)
        if current_id is not None:
            await self.projector._sync_user(current_id)
            await asyncio.to_thread(self._save_identity, member_uuid, current_id)
        else:
            await asyncio.to_thread(self._delete_identity, member_uuid)
        return SyncResult()

    async def on_member_update(
        self, before: discord.Member, after: discord.Member
    ) -> None:
        before_roles = {
            role.name for role in before.roles if role.name in SYNCED_ROLE_NAMES
        }
        after_roles = {
            role.name for role in after.roles if role.name in SYNCED_ROLE_NAMES
        }
        if before_roles != after_roles or before.nick != after.nick:
            await self.projector._sync_user(str(after.id))

    async def on_member_join(self, member: discord.Member) -> None:
        await self.projector._sync_user(str(member.id))

    async def reconcile_startup(self) -> None:
        users = await asyncio.to_thread(self._load_all_users)
        database_ids = {
            user.discord_id for user in users if user.discord_id is not None
        }
        projected_ids = {
            str(member.id)
            for guild in self.bot.guilds
            for member in guild.members
            if any(role.name in SYNCED_ROLE_NAMES for role in member.roles)
            or (member.bot and member.id == MANTIS_AGENT_APP_ID)
        }
        for user in users:
            await self.reconcile(user.id)
        orphan_ids = (projected_ids | PERMANENT_LEADERSHIP_DISCORD_IDS) - database_ids
        for discord_id in orphan_ids:
            await self.projector._sync_user(discord_id)

    @staticmethod
    def _load_state(
        member_uuid: UUID,
    ) -> tuple[User | None, AccessSyncIdentity | None]:
        with get_session() as session:
            user = session.get(User, member_uuid)
            identity = session.get(AccessSyncIdentity, (member_uuid, "discord"))
            for value in (user, identity):
                if value is not None:
                    session.expunge(value)
            return user, identity

    @staticmethod
    def _load_all_users() -> list[User]:
        with get_session() as session:
            users = list(session.exec(select(User)).all())
            for user in users:
                session.expunge(user)
            return users

    @staticmethod
    def _save_identity(member_uuid: UUID, discord_id: str) -> None:
        with get_session() as session:
            identity = session.get(AccessSyncIdentity, (member_uuid, "discord"))
            if identity is None:
                identity = AccessSyncIdentity(
                    member_uuid=member_uuid,
                    provider="discord",
                    external_id=discord_id,
                )
            identity.external_id = discord_id
            identity.external_login = None
            session.add(identity)
            session.commit()

    @staticmethod
    def _delete_identity(member_uuid: UUID) -> None:
        with get_session() as session:
            identity = session.get(AccessSyncIdentity, (member_uuid, "discord"))
            if identity is not None:
                session.delete(identity)
                session.commit()
