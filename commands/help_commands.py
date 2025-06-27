import discord


def setup(bot):
    """Register help commands with the bot."""
    bot.tree.add_command(help_command)


@discord.app_commands.command(name="help", description="Shows how to use the Mantis Bot.")
async def help_command(interaction: discord.Interaction):
    """Displays a help message for the bot."""
    await interaction.response.defer(ephemeral=True)

    embed = discord.Embed(
        title="Mantis Bot Help",
        description="Hello! I'm here to help you view tasks from our GitHub Projects and learn more about Mantis.",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="`/tasks` command",
        value=(
            "Use this command in a project-specific channel to see tasks.\n"
            "Example: `/tasks status:In Progress` in channel <#1376189017552457728>\n"
            "If no status is provided, it defaults to 'To Do'.\n"
            "This command automatically knows which project to fetch based on the channel it's used in."
        ),
        inline=False
    )

    embed.add_field(
        name="`/project_tasks` command",
        value=(
            "Use this command to view tasks for *any* project by specifying its number.\n"
            "Example: `/project_tasks number:2 status:Done`\n"
            "This is useful if you want to check tasks for a project not associated with the current channel, or if a channel isn't mapped."
        ),
        inline=False
    )
    
    embed.add_field(
        name="Status Options",
        value="When using `status`, you can choose from: `To Do`, `In Progress`, `In Review`, `Done`, `No Status`.",
        inline=False
    )

    embed.set_footer(text="Mantis AI Cognitive Cartography")
    await interaction.followup.send(embed=embed, ephemeral=True)