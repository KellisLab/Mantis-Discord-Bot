import logging

import discord
from discord.ext import commands

from commands import (
    ai_commands,
    github_webhooks,
    help_commands,
    issue_pr_commands,
    project_commands,
    reminders,
    transcript_commands,
)
from config import DISCORD_TOKEN
from members.commands import setup as setup_member_commands
from slash_commands import setup as setup_slash_commands
from teams.commands import setup as setup_team_commands
from utils.ai_summarizer import ConversationSummarizer
from utils.github_update_manager import GitHubUpdateManager
from utils.member_mapping import MemberMappingCache
from utils.message_analyzer import MessageAnalyzer
from utils.reminder_processor import ReminderProcessor
from utils.reminder_scheduler import ReminderScheduler
from utils.transcript_api import TranscriptAPI
from utils.transcript_processor import TranscriptProcessor
from utils.transcript_scheduler import TranscriptScheduler
from utils.user_role_sync import UserRoleSync

# ─── Bot Setup ────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True  # Enable message content intent for reply detection
intents.members = True  # Enable guild members intent for finding users for DMs
bot = commands.Bot(command_prefix="!", intents=intents)  # Set a proper command prefix
bot.user_role_sync = UserRoleSync(bot)

logger = logging.getLogger(__name__)

# ─── Shared Component Instances ─────────────────────────────────────────────
# Create shared instances to avoid cache duplication and improve performance

# Shared cache for member mapping (prevents repeated API calls)
bot.member_cache = MemberMappingCache()

# Shared transcript components
bot.transcript_api = TranscriptAPI()
bot.ai_summarizer = ConversationSummarizer()
bot.message_analyzer = MessageAnalyzer(bot.member_cache)

# Shared transcript processor (used by both commands and scheduler)
bot.transcript_processor = TranscriptProcessor(
    bot=bot,
    member_cache=bot.member_cache,
    message_analyzer=bot.message_analyzer,
    ai_summarizer=bot.ai_summarizer,
    transcript_api=bot.transcript_api,
)

# Shared reminder processor (used by both commands and scheduler)
bot.reminder_processor = ReminderProcessor(bot=bot, member_cache=bot.member_cache)

# Shared GitHub update manager (handles DM-based GitHub commenting)
bot.github_update_manager = GitHubUpdateManager(bot=bot, member_cache=bot.member_cache)

# ─── Register Commands ───────────────────────────────────────────────────────
# Keep synchronous setup calls here
project_commands.setup(bot)
help_commands.setup(bot)
ai_commands.setup(bot)
issue_pr_commands.setup(bot)
reminders.setup(bot)
github_webhooks.setup(bot)
transcript_commands.setup(bot)
setup_slash_commands(bot)
# Member commands live with their models and service, mirroring the teams
# feature package instead of being split across top-level modules.
setup_member_commands(bot)
# Team commands live with their models, service, and Discord projection instead
# of being split across top-level and slash-command modules.
setup_team_commands(bot)

# ─── Bot Events ──────────────────────────────────────────────────────────────


@bot.event
async def on_ready():
    print(f"{bot.user} has connected to Discord!")

    # Set the bot's activity
    activity = discord.Activity(name="/help", type=discord.ActivityType.listening)
    await bot.change_presence(activity=activity)
    print("Set bot activity.")

    # Role synchronization is critical: start it first so Discord roles
    # converge with the database as early as possible.
    try:
        bot.user_role_sync.start()
        await bot.user_role_sync.enqueue_all()
        print("User role synchronization started.")
    except Exception:  # noqa: BLE001
        logger.exception("Failed to start user role synchronization")

    # Load cogs
    try:
        await bot.load_extension("commands.m4m_task_mentor_agent")
        await bot.load_extension("commands.m4m_task_assignee_finder")
        print("M4M Cog loaded successfully.")

        await bot.load_extension("commands.dm_update_handler")
        await bot.load_extension("utils.mention_reminder")
        await bot.load_extension("commands.oracle")
    except Exception:  # noqa: BLE001
        logger.exception("Failed to load one or more cogs")

    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception:  # noqa: BLE001
        logger.exception("Failed to sync slash commands")

    # Initialize transcript scheduler
    try:
        print("Initializing transcript scheduler...")
        bot.transcript_scheduler = TranscriptScheduler(bot, bot.transcript_processor)
        config_test = await bot.transcript_scheduler.test_configuration()
        if config_test["config_valid"] and config_test["channels_accessible"] > 0:
            bot.transcript_scheduler.setup_daily_schedule()
            print(
                f"✅ Transcript scheduler started for {config_test['channels_accessible']} channels"
            )
        else:
            print("⚠️ Transcript scheduler not started due to configuration issues:")
            for error in config_test.get("errors", []):
                print(f"   • {error}")
    except Exception:  # noqa: BLE001
        logger.exception("Failed to initialize transcript scheduler")

    # Initialize reminder scheduler
    try:
        print("Initializing reminder scheduler...")
        bot.reminder_scheduler = ReminderScheduler(bot, bot.reminder_processor)
        bot.reminder_scheduler.setup_weekly_schedule()
        print(
            "✅ Reminder scheduler started for weekly reminders (Saturdays at 00:00 UTC)"
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to initialize reminder scheduler")


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    await bot.user_role_sync.on_member_update(before, after)


@bot.event
async def on_member_join(member: discord.Member):
    bot.user_role_sync.enqueue(member.id)


# ─── Run Bot ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
