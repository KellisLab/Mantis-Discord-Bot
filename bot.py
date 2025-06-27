import discord
from discord.ext import commands
from config import DISCORD_TOKEN
from commands import project_commands, help_commands

# ─── Bot Setup ────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
bot = commands.Bot(command_prefix=None, intents=intents)

# ─── Register Commands ───────────────────────────────────────────────────────

project_commands.setup(bot)
help_commands.setup(bot)

# ─── Bot Events ──────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    try:
        # Set the bot's activity
        activity = discord.Activity(name="/tasks", type=discord.ActivityType.listening)
        await bot.change_presence(activity=activity)
        print("Set bot activity.")

        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

# ─── Run Bot ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
