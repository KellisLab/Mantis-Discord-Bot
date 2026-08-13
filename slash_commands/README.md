# Adding slash commands

Add each new slash command as a Python module in this directory. Modules are
discovered automatically when the bot starts, so `bot.py` does not need to be
edited for each command.

Each command module must expose a synchronous `setup(bot)` function that
registers its commands. The shared `access.py` helper is excluded from command
discovery:

```python
import discord


def setup(bot):
    bot.tree.add_command(example)


@discord.app_commands.command(name="example", description="Example command.")
async def example(interaction: discord.Interaction):
    await interaction.response.send_message("Hello!", ephemeral=True)
```

Run `docker compose watch` while developing. Saving a Python file restarts the
bot, which discovers the module and syncs its slash commands with Discord.

See [ACCESS.md](ACCESS.md) for command access-control documentation.

The larger `/team` feature is organized as its own package rather than a
standalone module here. See [teams/README.md](../teams/README.md) for its command
and architecture notes.
