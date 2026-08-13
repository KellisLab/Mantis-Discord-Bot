"""Reusable database-backed access control for slash commands."""

from __future__ import annotations

import asyncio
from enum import StrEnum

import discord
from discord import app_commands
from sqlmodel import select

from database import get_session
from members.models import Stage, User
from members.permissions import has_leadership


class AccessGroup(StrEnum):
    """Groups that can be granted access to a slash command."""

    LEADERSHIP = "leadership"
    JOURNEY_MENTOR = "journey mentor"
    TEAM = "team"
    ONBOARDING = "onboarding"
    PREBOARDING = "preboarding"


LEADERSHIP = AccessGroup.LEADERSHIP
JOURNEY_MENTOR = AccessGroup.JOURNEY_MENTOR
TEAM = AccessGroup.TEAM
ONBOARDING = AccessGroup.ONBOARDING
PREBOARDING = AccessGroup.PREBOARDING


class AccessDenied(app_commands.CheckFailure):
    """Raised when a member is not in any group allowed for a command."""

    def __init__(self, allowed_groups: tuple[AccessGroup, ...]) -> None:
        self.allowed_groups = allowed_groups
        allowed = ", ".join(group.value for group in allowed_groups)
        super().__init__(f"This command requires one of these groups: {allowed}")


def _get_user(discord_id: int) -> User | None:
    with get_session() as session:
        statement = select(User).where(User.discord_id == str(discord_id))
        user = session.exec(statement).one_or_none()
        if user is not None:
            session.expunge(user)
        return user


def _belongs_to(user: User, group: AccessGroup) -> bool:
    if group is AccessGroup.LEADERSHIP:
        return has_leadership(user)
    if group is AccessGroup.JOURNEY_MENTOR:
        return user.is_journey_mentor
    if group is AccessGroup.TEAM:
        return user.stage not in {Stage.PREBOARDING, Stage.ONBOARDING}
    if group is AccessGroup.ONBOARDING:
        return user.stage is Stage.ONBOARDING
    if group is AccessGroup.PREBOARDING:
        return user.stage is Stage.PREBOARDING
    return False


def allow_groups(
    *groups: AccessGroup,
) -> app_commands.Check:
    """Allow members in any listed group to use a slash command."""
    if not groups:
        raise ValueError("allow_groups requires at least one access group")

    allowed_groups = tuple(AccessGroup(group) for group in groups)

    async def predicate(interaction: discord.Interaction) -> bool:
        if AccessGroup.LEADERSHIP in allowed_groups and has_leadership(
            discord_id=interaction.user.id
        ):
            return True
        user = await asyncio.to_thread(_get_user, interaction.user.id)
        if user is not None and any(
            _belongs_to(user, group) for group in allowed_groups
        ):
            return True
        raise AccessDenied(allowed_groups)

    return app_commands.check(predicate)


async def handle_access_denied(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> bool:
    """Send an access-denied response and report whether the error was handled."""
    if not isinstance(error, AccessDenied):
        return False

    groups = ", ".join(group.value for group in error.allowed_groups)
    message = f"You need one of these access groups to use this command: {groups}."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)
    return True
