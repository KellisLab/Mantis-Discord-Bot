"""Slash command for uploading Discord channel history."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ingestion import ingest_channel_history
from slash_commands.access import LEADERSHIP, allow_groups


def setup(bot: commands.Bot) -> None:
    bot.tree.add_command(upload_messages)


@app_commands.command(
    name="upload-messages",
    description="Upload messages from a channel to the Mantis backend.",
)
@app_commands.describe(
    channel="Channel to upload (defaults to the current channel)",
    limit="Maximum number of messages to upload",
)
@app_commands.guild_only()
@allow_groups(LEADERSHIP)
async def upload_messages(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None,
    limit: app_commands.Range[int, 1, 10000] = 100,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    selected_channel = channel or interaction.channel
    if selected_channel is None or not hasattr(selected_channel, "history"):
        await interaction.followup.send(
            "This command must be used with a text channel.", ephemeral=True
        )
        return

    result = await ingest_channel_history(
        interaction.client, selected_channel, int(limit)
    )
    channel_name = getattr(selected_channel, "name", "this channel")
    await interaction.followup.send(
        f"Uploaded {result['created']} messages from {channel_name}.", ephemeral=True
    )
