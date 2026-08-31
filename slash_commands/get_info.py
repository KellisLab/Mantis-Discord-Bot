"""Look up complete member profiles with ``/get-info``."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from members.models import User
from members.permissions import has_leadership
from members.service import MemberServiceError, resolve_member
from slash_commands.access import TEAM, allow_groups
from teams.service import RANK_NAMES, list_memberships_for_member
from utils.member_identifier import IDENTIFIER_DESCRIPTION, discord_id_from_tag

LOGGER = logging.getLogger(__name__)


def setup(bot: commands.Bot) -> None:
    bot.tree.add_command(get_info)


def _display(value: object | None) -> str:
    if value is None or value == "":
        return "Not set"
    return str(value)


def _teams_value(memberships: tuple[tuple[object, int], ...]) -> str:
    if not memberships:
        return "None"
    return "\n".join(
        f"**{team.name}** — {RANK_NAMES[rank]}" for team, rank in memberships
    )


def _profile_embed(
    member: User,
    discord_member: discord.Member | None,
    memberships: tuple[tuple[object, int], ...],
) -> discord.Embed:
    embed = discord.Embed(
        title=f"👤 {member.full_name or member.email}",
        description="Mantis member profile",
        color=discord.Color.teal(),
    )
    discord_value = "Not linked"
    if member.discord_id is not None:
        tag = (
            str(discord_member) if discord_member is not None else "Not in this server"
        )
        discord_value = f"{tag} (`{member.discord_id}`)"

    embed.add_field(name="📧 Email", value=member.email, inline=True)
    embed.add_field(name="🎓 Stage", value=member.stage.value, inline=True)
    embed.add_field(name="​", value="​", inline=True)

    embed.add_field(
        name="⭐ Leadership", value="Yes" if has_leadership(member) else "No", inline=True
    )
    embed.add_field(
        name="🧭 Journey Mentor",
        value="Yes" if member.is_journey_mentor else "No",
        inline=True,
    )
    embed.add_field(name="​", value="​", inline=True)

    embed.add_field(name="💬 Discord", value=discord_value, inline=False)
    embed.add_field(
        name="🐙 GitHub", value=_display(member.github_username), inline=True
    )
    embed.add_field(
        name="📱 WhatsApp", value=_display(member.whatsapp_number), inline=True
    )
    embed.add_field(name="​", value="​", inline=True)

    embed.add_field(name="🧑‍🤝‍🧑 Teams", value=_teams_value(memberships), inline=False)

    embed.add_field(name="🆔 UUID", value=f"`{member.id}`", inline=False)
    embed.set_footer(
        text=(
            f"Created {member.created_at.isoformat()} • "
            f"Updated {member.updated_at.isoformat()}"
        )
    )
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
    await interaction.response.defer(ephemeral=False, thinking=True)

    guild = interaction.guild
    discord_id = discord_id_from_tag(guild, identifier)
    try:
        member = await asyncio.to_thread(
            resolve_member,
            identifier,
            discord_id=discord_id,
        )
    except MemberServiceError as error:
        await interaction.followup.send(str(error), ephemeral=False)
        return
    except Exception:
        LOGGER.exception("Unexpected /get-info failure")
        await interaction.followup.send(
            "The member profile could not be retrieved due to an unexpected error.",
            ephemeral=False,
        )
        return

    discord_member = None
    if member.discord_id is not None and guild is not None:
        try:
            discord_member = guild.get_member(int(member.discord_id))
        except ValueError:
            pass

    memberships = await asyncio.to_thread(list_memberships_for_member, member.id)

    await interaction.followup.send(
        embed=_profile_embed(member, discord_member, memberships),
        ephemeral=False,
    )
