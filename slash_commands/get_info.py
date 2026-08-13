"""Look up complete member profiles with ``/get-info``."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from members.models import User
from members.service import MemberServiceError, resolve_member
from slash_commands.access import TEAM, allow_groups
from utils.member_identifier import IDENTIFIER_DESCRIPTION, discord_id_from_tag

LOGGER = logging.getLogger(__name__)


def setup(bot: commands.Bot) -> None:
    bot.tree.add_command(get_info)


def _display(value: object | None) -> str:
    if value is None or value == "":
        return "Not set"
    return str(value)


def _profile_embed(
    member: User, discord_member: discord.Member | None
) -> discord.Embed:
    embed = discord.Embed(
        title=member.full_name or member.email,
        description="Mantis member profile",
        color=discord.Color.blurple(),
    )
    discord_value = "Not linked"
    if member.discord_id is not None:
        tag = (
            str(discord_member) if discord_member is not None else "Not in this server"
        )
        discord_value = f"{tag} (`{member.discord_id}`)"

    fields = (
        ("UUID", str(member.id)),
        ("Email", member.email),
        ("Full Name", _display(member.full_name)),
        ("Discord", discord_value),
        ("GitHub Username", _display(member.github_username)),
        ("WhatsApp Number", _display(member.whatsapp_number)),
        ("Stage", member.stage.value),
        ("Leadership", "Yes" if member.is_leadership else "No"),
        ("Journey Mentor", "Yes" if member.is_journey_mentor else "No"),
        ("Created", member.created_at.isoformat()),
        ("Last Updated", member.updated_at.isoformat()),
    )
    for name, value in fields:
        embed.add_field(name=name, value=value, inline=False)
    return embed


@app_commands.command(
    name="get-info",
    description="Get a complete member profile from any known identifier.",
)
@app_commands.describe(
    identifier=IDENTIFIER_DESCRIPTION,
)
@app_commands.guild_only()
@allow_groups(TEAM)
async def get_info(
    interaction: discord.Interaction,
    identifier: str,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    guild = interaction.guild
    discord_id = discord_id_from_tag(guild, identifier)
    try:
        member = await asyncio.to_thread(
            resolve_member,
            identifier,
            discord_id=discord_id,
        )
    except MemberServiceError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return
    except Exception:
        LOGGER.exception("Unexpected /get-info failure")
        await interaction.followup.send(
            "The member profile could not be retrieved due to an unexpected error.",
            ephemeral=True,
        )
        return

    discord_member = None
    if member.discord_id is not None and guild is not None:
        try:
            discord_member = guild.get_member(int(member.discord_id))
        except ValueError:
            pass

    await interaction.followup.send(
        embed=_profile_embed(member, discord_member),
        ephemeral=True,
    )
