# Adding slash commands

Add each new slash command as a Python module in this directory. Modules are
discovered automatically when the bot starts, so `bot.py` does not need to be
edited for each command.

Each module must expose a synchronous `setup(bot)` function that registers its
commands:

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
