"""Issue a personal Mantis access token with ``/access-token``."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands
from sqlmodel import select

from database import get_session
from members.models import User
from members.token_service import (
    AccessTokenServiceError,
    issue_token,
    revoke_token,
)
from slash_commands.access import (
    JOURNEY_MENTOR,
    LEADERSHIP,
    ONBOARDING,
    PREBOARDING,
    TEAM,
    allow_groups,
)

LOGGER = logging.getLogger(__name__)


def setup(bot: commands.Bot) -> None:
    bot.tree.add_command(access_token)


def _user_uuid_for_discord_id(discord_id: int):
    with get_session() as session:
        user = session.exec(
            select(User).where(User.discord_id == str(discord_id))
        ).one_or_none()
        return user.id if user is not None else None


@app_commands.command(
    name="access-token",
    description="Issue a personal access token for Mantis services.",
)
@allow_groups(LEADERSHIP, JOURNEY_MENTOR, TEAM, ONBOARDING, PREBOARDING)
async def access_token(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    user_uuid = await asyncio.to_thread(_user_uuid_for_discord_id, interaction.user.id)
    if user_uuid is None:
        await interaction.followup.send(
            "Please run /create-profile first.", ephemeral=True
        )
        return

    try:
        issued = await asyncio.to_thread(issue_token, user_uuid)
    except AccessTokenServiceError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return
    except Exception:
        LOGGER.exception("Unexpected /access-token failure while issuing token")
        await interaction.followup.send(
            "Your access token could not be issued due to an unexpected error.",
            ephemeral=True,
        )
        return

    dm_message = (
        f"🔑 **Mantis access token**\n"
        f"```{issued.token}```\n"
        f"Expires: {issued.expires_at.isoformat()}\n\n"
        "Supply this token as a Bearer token when calling Mantis services, "
        "e.g. `Authorization: Bearer <token>`.\n\n"
        "Running /access-token again will reset your token."
    )

    try:
        await interaction.user.send(dm_message)
    except discord.Forbidden:
        await asyncio.to_thread(revoke_token, user_uuid)
        await interaction.followup.send(
            "I couldn't DM you your access token. Please enable DMs from server "
            "members and try again.",
            ephemeral=True,
        )
        return
    except Exception:
        LOGGER.exception("Unexpected /access-token failure while sending DM")
        await asyncio.to_thread(revoke_token, user_uuid)
        await interaction.followup.send(
            "Your access token could not be delivered due to an unexpected error.",
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        "Your access token has been sent to you via DM.", ephemeral=True
    )
