"""Slash commands for persistent, database-backed Mantis teams."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from members.service import MemberServiceError
from slash_commands.access import LEADERSHIP, allow_groups
from teams.discord import (
    CloseVoteView,
    channel_slug,
    create_team_channel,
    on_directory_reaction,
    on_team_messages_deleted,
    refresh_team_artifacts,
    restore_team_views,
    resync_all_team_artifacts,
)
from teams.service import (
    TeamServiceError,
    add_team_member,
    begin_close_vote,
    cancel_close_attempt,
    create_team,
    edit_team,
    get_team_by_channel,
    leave_team,
    mark_team_orphaned,
    remove_team_member,
    set_close_vote_message_id,
    set_team_channel_id,
    set_team_rank,
    transfer_team_lead,
)
from utils.member_identifier import IDENTIFIER_DESCRIPTION, discord_id_from_tag

LOGGER = logging.getLogger(__name__)

team_commands = app_commands.Group(
    name="team", description="Create and manage Mantis teams.", guild_only=True
)


def setup(bot: commands.Bot) -> None:
    bot.tree.add_command(team_commands)

    async def raw_reaction_listener(payload: discord.RawReactionActionEvent) -> None:
        await on_directory_reaction(bot, payload)

    async def ready_listener() -> None:
        await restore_team_views(bot)
        await resync_all_team_artifacts(bot)

    async def raw_message_delete_listener(
        payload: discord.RawMessageDeleteEvent,
    ) -> None:
        await on_team_messages_deleted((payload.message_id,))

    async def raw_bulk_message_delete_listener(
        payload: discord.RawBulkMessageDeleteEvent,
    ) -> None:
        await on_team_messages_deleted(payload.message_ids)

    bot.add_listener(raw_reaction_listener, "on_raw_reaction_add")
    bot.add_listener(raw_message_delete_listener, "on_raw_message_delete")
    bot.add_listener(raw_bulk_message_delete_listener, "on_raw_bulk_message_delete")
    bot.add_listener(ready_listener, "on_ready")


async def _team_for_channel(interaction: discord.Interaction):
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        raise TeamServiceError("Run this command inside a team text channel.")
    team = await asyncio.to_thread(get_team_by_channel, channel.id)
    if team is None:
        raise TeamServiceError("This is not an active Mantis team channel.")
    return team


async def _failure(interaction: discord.Interaction, error: Exception) -> None:
    await interaction.followup.send(str(error), ephemeral=True)


async def _refresh(interaction: discord.Interaction, team_uuid) -> None:
    if interaction.guild is not None:
        await refresh_team_artifacts(interaction.client, interaction.guild, team_uuid)


@team_commands.command(
    name="create", description="Create a team and its Discord channel."
)
@app_commands.describe(name="Team name", description="What this team works on")
@allow_groups(LEADERSHIP)
async def team_create(
    interaction: discord.Interaction, name: str, description: str
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    guild = interaction.guild
    if guild is None:
        await interaction.followup.send(
            "This command can only be used in a server.", ephemeral=True
        )
        return
    try:
        # Phase 1 commits the canonical row and Lead. The nullable channel ID
        # makes a crash or Discord failure observable instead of losing history.
        team = await asyncio.to_thread(
            create_team, name, description, None, interaction.user.id
        )
    except (TeamServiceError, MemberServiceError) as error:
        await _failure(interaction, error)
        return
    channel = None
    try:
        # Phase 2 creates the projection and atomically attaches its snowflake.
        channel = await create_team_channel(guild, name)
        team = await asyncio.to_thread(set_team_channel_id, team.uuid, channel.id)
    except (discord.Forbidden, discord.HTTPException):
        await asyncio.to_thread(mark_team_orphaned, team.uuid)
        LOGGER.exception("Discord rejected team creation")
        await interaction.followup.send(
            "The team record was preserved as orphaned because Discord could not "
            "create its channel. Check my Manage Channels permission.",
            ephemeral=True,
        )
        return
    except TeamServiceError as error:
        await asyncio.to_thread(mark_team_orphaned, team.uuid)
        if channel is not None:
            try:
                await channel.delete(
                    reason="Cleaning up failed Mantis team provisioning"
                )
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.exception("Could not clean up failed team channel provisioning")
        await _failure(interaction, error)
        return
    await refresh_team_artifacts(interaction.client, guild, team.uuid)
    await interaction.followup.send(
        f"Created **{team.name}** in {channel.mention}.", ephemeral=True
    )


@team_commands.command(name="edit", description="Edit this team's name or description.")
@app_commands.describe(name="New team name", description="New team description")
async def team_edit(
    interaction: discord.Interaction,
    name: str | None = None,
    description: str | None = None,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        current = await _team_for_channel(interaction)
        team = await asyncio.to_thread(
            edit_team,
            current.uuid,
            interaction.user.id,
            name=name,
            description=description,
        )
    except (TeamServiceError, MemberServiceError) as error:
        await _failure(interaction, error)
        return
    channel = interaction.channel
    if name is not None and isinstance(channel, discord.TextChannel):
        try:
            await channel.edit(
                name=channel_slug(team.name), reason="Mantis team renamed"
            )
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.exception("Could not rename Discord channel for team %s", team.uuid)
    await _refresh(interaction, team.uuid)
    await interaction.followup.send("Team updated.", ephemeral=True)


@team_commands.command(name="add", description="Add a member to this team.")
@app_commands.describe(
    identifier=IDENTIFIER_DESCRIPTION, rank="2 Co-Lead, 3 Engineer, or 4 Developer"
)
async def team_add(
    interaction: discord.Interaction,
    identifier: str,
    rank: app_commands.Range[int, 2, 4] = 4,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        team = await _team_for_channel(interaction)
        discord_id = discord_id_from_tag(interaction.guild, identifier)
        await asyncio.to_thread(
            add_team_member,
            team.uuid,
            interaction.user.id,
            identifier,
            rank,
            discord_id=discord_id,
        )
    except (TeamServiceError, MemberServiceError) as error:
        await _failure(interaction, error)
        return
    await _refresh(interaction, team.uuid)
    await interaction.followup.send("Member added.", ephemeral=True)


@team_commands.command(name="remove", description="Remove a member from this team.")
@app_commands.describe(identifier=IDENTIFIER_DESCRIPTION)
async def team_remove(interaction: discord.Interaction, identifier: str) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        team = await _team_for_channel(interaction)
        discord_id = discord_id_from_tag(interaction.guild, identifier)
        await asyncio.to_thread(
            remove_team_member,
            team.uuid,
            interaction.user.id,
            identifier,
            discord_id=discord_id,
        )
    except (TeamServiceError, MemberServiceError) as error:
        await _failure(interaction, error)
        return
    await _refresh(interaction, team.uuid)
    await interaction.followup.send("Member removed.", ephemeral=True)


@team_commands.command(
    name="set-rank", description="Change a current team member's rank."
)
@app_commands.describe(
    identifier=IDENTIFIER_DESCRIPTION, rank="2 Co-Lead, 3 Engineer, or 4 Developer"
)
async def team_set_rank(
    interaction: discord.Interaction,
    identifier: str,
    rank: app_commands.Range[int, 2, 4],
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        team = await _team_for_channel(interaction)
        discord_id = discord_id_from_tag(interaction.guild, identifier)
        await asyncio.to_thread(
            set_team_rank,
            team.uuid,
            interaction.user.id,
            identifier,
            rank,
            discord_id=discord_id,
        )
    except (TeamServiceError, MemberServiceError) as error:
        await _failure(interaction, error)
        return
    await _refresh(interaction, team.uuid)
    await interaction.followup.send("Rank updated.", ephemeral=True)


@team_commands.command(
    name="transfer-lead", description="Transfer this team's Lead role."
)
@app_commands.describe(identifier=IDENTIFIER_DESCRIPTION)
async def team_transfer_lead(interaction: discord.Interaction, identifier: str) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        team = await _team_for_channel(interaction)
        discord_id = discord_id_from_tag(interaction.guild, identifier)
        await asyncio.to_thread(
            transfer_team_lead,
            team.uuid,
            interaction.user.id,
            identifier,
            discord_id=discord_id,
        )
    except (TeamServiceError, MemberServiceError) as error:
        await _failure(interaction, error)
        return
    await _refresh(interaction, team.uuid)
    await interaction.followup.send("Lead transferred.", ephemeral=True)


@team_commands.command(name="leave", description="Leave the team for this channel.")
async def team_leave(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        team = await _team_for_channel(interaction)
        await asyncio.to_thread(leave_team, team.uuid, interaction.user.id)
    except TeamServiceError as error:
        await _failure(interaction, error)
        return
    await _refresh(interaction, team.uuid)
    await interaction.followup.send("You left the team.", ephemeral=True)


@team_commands.command(
    name="close", description="Start a vote to close this team channel."
)
async def team_close(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        team = await _team_for_channel(interaction)
        # The attempt commits before Discord I/O so duplicate /team close calls
        # are rejected by the database partial unique index.
        attempt = await asyncio.to_thread(
            begin_close_vote, team.uuid, interaction.user.id
        )
    except TeamServiceError as error:
        await _failure(interaction, error)
        return
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await asyncio.to_thread(cancel_close_attempt, attempt.uuid)
        await interaction.followup.send(
            "This command requires a team text channel.", ephemeral=True
        )
        return
    try:
        message = await channel.send(
            "**Close team vote**\nVote to archive and close this team. Quorum is the Lead/Leadership, or a Co-Lead plus an Engineer.",
            view=CloseVoteView(attempt.uuid),
        )
        await asyncio.to_thread(set_close_vote_message_id, attempt.uuid, message.id)
        # Close the send→persist race: if a moderator deleted the message before
        # its ID committed, the earlier raw event could not yet find the attempt.
        await channel.fetch_message(message.id)
    except discord.NotFound:
        await asyncio.to_thread(cancel_close_attempt, attempt.uuid)
        await interaction.followup.send(
            "The close-vote message was deleted, so the attempt was cancelled.",
            ephemeral=True,
        )
        return
    except (discord.Forbidden, discord.HTTPException):
        # A vote without a usable Discord control must not block a later attempt.
        await asyncio.to_thread(cancel_close_attempt, attempt.uuid)
        LOGGER.exception("Could not create close vote for team %s", team.uuid)
        await interaction.followup.send(
            "Discord could not create the close-vote message.", ephemeral=True
        )
        return
    except TeamServiceError as error:
        await asyncio.to_thread(cancel_close_attempt, attempt.uuid)
        if message is not None:
            try:
                await message.edit(view=None)
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.exception("Could not disable an invalid close-vote message")
        await _failure(interaction, error)
        return
    await interaction.followup.send("Close vote started.", ephemeral=True)
