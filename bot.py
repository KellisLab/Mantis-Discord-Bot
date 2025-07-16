import discord
from discord.ext import commands
from config import DISCORD_TOKEN
from commands import project_commands, help_commands, ai_commands, issue_pr_commands

# ─── Bot Setup ────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True  # Enable message content intent for reply detection
bot = commands.Bot(command_prefix="!", intents=intents)  # Set a proper command prefix

# ─── Register Commands ───────────────────────────────────────────────────────

project_commands.setup(bot)
help_commands.setup(bot)
ai_commands.setup(bot)
issue_pr_commands.setup(bot)

# ─── Bot Events ──────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    try:
        # Set the bot's activity
        activity = discord.Activity(name="/help", type=discord.ActivityType.listening)
        await bot.change_presence(activity=activity)
        print("Set bot activity.")

        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

# ─── Run Bot ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
