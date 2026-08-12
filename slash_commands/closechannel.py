"""Placeholder for the /closechannel command."""

import discord
from discord.ext import commands


def setup(bot: commands.Bot) -> None:
    bot.tree.add_command(closechannel)


@discord.app_commands.command(
    name="closechannel",
    description="Close the current channel.",
)
async def closechannel(interaction: discord.Interaction) -> None:
    """Placeholder command with no channel-closing behavior yet."""
    await interaction.response.send_message(
        "`/closechannel` is not implemented yet.",
        ephemeral=True,
    )
