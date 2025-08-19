import discord
from discord.ext import commands
from config import DISCORD_TOKEN
from commands import project_commands, help_commands, ai_commands, issue_pr_commands, reminders, github_webhooks, transcript_commands
from utils.transcript_scheduler import TranscriptScheduler
from utils.transcript_processor import TranscriptProcessor
from utils.reminder_scheduler import ReminderScheduler
from utils.reminder_processor import ReminderProcessor
from utils.member_mapping import MemberMappingCache
from utils.message_analyzer import MessageAnalyzer
from utils.ai_summarizer import ConversationSummarizer
from utils.transcript_api import TranscriptAPI

# ─── Bot Setup ────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True  # Enable message content intent for reply detection
intents.members = True   # Enable guild members intent for finding users for DMs
bot = commands.Bot(command_prefix="!", intents=intents)  # Set a proper command prefix

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
    transcript_api=bot.transcript_api
)

# Shared reminder processor (used by both commands and scheduler)
bot.reminder_processor = ReminderProcessor(
    bot=bot,
    member_cache=bot.member_cache
)

# ─── Register Commands ───────────────────────────────────────────────────────
# Keep synchronous setup calls here
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

        # Load M4M as a cog
        await bot.load_extension('commands.m4m_task_mentor_agent')
        await bot.load_extension('commands.m4m_task_assignee_finder')
        print("M4M Cog loaded successfully.")

        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
        
        # Initialize transcript scheduler with shared processor
        print("Initializing transcript scheduler...")
        bot.transcript_scheduler = TranscriptScheduler(bot, bot.transcript_processor)
        
        # Test configuration before starting
        config_test = await bot.transcript_scheduler.test_configuration()
        if config_test["config_valid"] and config_test["channels_accessible"] > 0:
            bot.transcript_scheduler.setup_daily_schedule()
            print(f"✅ Transcript scheduler started for {config_test['channels_accessible']} channels")
        else:
            print("⚠️ Transcript scheduler not started due to configuration issues:")
            for error in config_test.get("errors", []):
                print(f"   • {error}")
        
        # Initialize reminder scheduler with shared processor
        print("Initializing reminder scheduler...")
        bot.reminder_scheduler = ReminderScheduler(bot, bot.reminder_processor)
        bot.reminder_scheduler.setup_weekly_schedule()
        print("✅ Reminder scheduler started for weekly reminders (Saturdays at 00:00 UTC)")
        
    except Exception as e:
        print(f"Failed to initialize bot features: {e}")

# ─── Run Bot ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)