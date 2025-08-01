import discord
from discord.ext import commands
from config import DISCORD_TOKEN
from commands import project_commands, help_commands, ai_commands, issue_pr_commands, reminders, github_webhooks, transcript_commands
from utils.transcript_scheduler import TranscriptScheduler

# ─── Bot Setup ────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True  # Enable message content intent for reply detection
intents.members = True   # Enable guild members intent for finding users for DMs
bot = commands.Bot(command_prefix="!", intents=intents)  # Set a proper command prefix

# ─── Register Commands ───────────────────────────────────────────────────────

project_commands.setup(bot)
help_commands.setup(bot)
ai_commands.setup(bot)
issue_pr_commands.setup(bot)
reminders.setup(bot)
github_webhooks.setup(bot)
transcript_commands.setup(bot)

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
        
        # Initialize transcript scheduler
        print("Initializing transcript scheduler...")
        scheduler = TranscriptScheduler(bot)
        
        # Test configuration before starting
        config_test = await scheduler.test_configuration()
        if config_test["config_valid"] and config_test["channels_accessible"] > 0:
            scheduler.setup_daily_schedule()
            print(f"✅ Transcript scheduler started for {config_test['channels_accessible']} channels")
        else:
            print(f"⚠️ Transcript scheduler not started due to configuration issues:")
            for error in config_test.get("errors", []):
                print(f"   • {error}")
        
    except Exception as e:
        print(f"Failed to initialize bot features: {e}")

# ─── Run Bot ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
