import discord
import time
from collections import deque
from typing import Dict
from discord.ext import commands


class MentionReminder(commands.Cog):
    """Reminds users to include mentions in their messages to ensure proper notifications."""
    
    # Class-level constant for common short responses that shouldn't trigger reminders
    _SHORT_RESPONSES = {
        'ok', 'k', 'ty', 'thx', 'np', 'yes', 'no', 'lol', 'lmao', 'xd',
        'wow', 'nice', 'good', 'bad', 'cool', 'hmm', 'sure', 'maybe',
        '+1', '-1', '^', '^^', '^^^', 'same', 'this', 'true', 'false',
    }
    
    def __init__(self, bot):
        self.bot = bot
        # Rate limiting: track recent reminders per user to avoid spam
        self.recent_reminders: Dict[int, float] = {}  # user_id -> timestamp
        self.reminder_cooldown = 300  # 5 minutes cooldown per user
        
        # Track processed messages to avoid duplicate processing (FIFO with automatic cleanup)
        self.processed_messages = deque(maxlen=1000)
        
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Check messages for missing mentions and send friendly reminders.
        
        This follows Discord best practices by only reminding users when
        appropriate and avoiding spam through comprehensive edge case handling.
        """
        try:
            # Comprehensive filtering to avoid false positives and spam
            if not self._should_process_message(message):
                return
            
            # Check if message already has mentions (any type)
            if self._has_mentions(message):
                return
            
            # Check rate limiting
            if not self._check_rate_limit(message.author.id):
                return
            
            # Send the reminder
            await self._send_mention_reminder(message)
            
        except Exception as e:
            # Silently log errors to avoid disrupting normal bot operation
            print(f"❌ Error in mention reminder: {e}")
    
    def _should_process_message(self, message: discord.Message) -> bool:
        """
        Determine if a message should be processed for mention reminders.
        
        Returns False for messages that shouldn't trigger reminders based on
        comprehensive edge case analysis and best practices.
        """
        # Skip if message was already processed (prevents duplicate processing)
        if message.id in self.processed_messages:
            return False
        
        # Add to processed deque (automatically removes oldest when maxlen is reached)
        self.processed_messages.append(message.id)
        
        # 1. Skip bot messages (prevents bot loops and unnecessary processing)
        if message.author.bot:
            return False
        
        # 2. Skip DM messages (only process guild/server messages)
        if not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
            return False
        
        # 3. Skip reply messages (original author already gets notification)
        if message.reference and message.reference.message_id:
            return False
        
        # 4. Skip system messages (joins, leaves, pins, boosts, etc.)
        if message.type != discord.MessageType.default:
            return False
        
        # 5. Skip command messages (messages starting with command prefixes)
        content = message.content.strip()
        if content.startswith(('!', '/', '$', '?', '>', '<', '.')):
            return False
        
        # 6. Skip empty messages or messages with only whitespace
        if not content:
            return False
        
        # 7. Skip very short messages (likely reactions, acknowledgments, etc.)
        if len(content) < 3:
            return False
        
        # 8. Skip messages that are just single characters or common short responses
        if content.lower() in self._SHORT_RESPONSES:
            return False
        
        # 9. Skip messages that are primarily URLs (link sharing)
        words = content.split()
        url_count = sum(1 for word in words if word.startswith(('http://', 'https://', 'www.')))
        if url_count > 0 and len(words) <= 3:  # Mostly just links
            return False
        
        # 10. Skip messages that are just emoji reactions or contain mostly emoji
        if self._is_mostly_emoji(content):
            return False
        
        return True
    
    def _has_mentions(self, message: discord.Message) -> bool:
        """
        Check if message contains any type of mention that would notify users.
        
        Includes user mentions and role mentions as they
        all serve the notification purpose.
        """
        # User mentions (specific users)
        if message.mentions:
            return True
        
        # Role mentions (including @everyone and @here)
        if message.role_mentions or message.mention_everyone:
            return True
        
        return False
    
    def _check_rate_limit(self, user_id: int) -> bool:
        """
        Check if user is within rate limit for mention reminders.
        
        Prevents spam by limiting reminders to once per cooldown period per user.
        """
        current_time = time.time()
        last_reminder = self.recent_reminders.get(user_id, 0)
        
        if current_time - last_reminder < self.reminder_cooldown:
            return False
        
        # Update the timestamp
        self.recent_reminders[user_id] = current_time
        
        # Clean up old entries to prevent memory bloat
        if len(self.recent_reminders) > 500:
            cutoff_time = current_time - self.reminder_cooldown * 2
            self.recent_reminders = {
                uid: timestamp for uid, timestamp in self.recent_reminders.items()
                if timestamp > cutoff_time
            }
        
        return True
    
    def _is_mostly_emoji(self, content: str) -> bool:
        """
        Check if message content is mostly emoji or emoji-like characters.
        
        This helps avoid reminding users for purely expressive messages.
        """
        # Remove whitespace for analysis
        clean_content = content.replace(' ', '').replace('\n', '')
        
        if len(clean_content) == 0:
            return True
        
        # Count emoji-like characters (this is a simple heuristic)
        emoji_chars = 0
        for char in clean_content:
            # Unicode emoji ranges (simplified detection)
            if ord(char) > 127:  # Non-ASCII characters (includes many emoji)
                emoji_chars += 1
        
        # If more than 70% of non-whitespace characters are likely emoji
        return (emoji_chars / len(clean_content)) > 0.7
    
    async def _send_mention_reminder(self, message: discord.Message):
        """
        Send a friendly, helpful reminder about including mentions.
        
        The message is designed to be educational and non-intrusive while
        explaining the benefit of using mentions.
        """
        try:
            reminder_text = (
                f"💡 **Hey {message.author.display_name}!** Just a friendly reminder: "
                "consider including mentions (@username, @team-name, @everyone) in your message to ensure "
                "the relevant people get notifications! This helps keep everyone in the loop. 😊"
            )
            
            # Reply to the original message to maintain context
            await message.reply(reminder_text, mention_author=False)
            
            print(f"📬 Sent mention reminder to {message.author.name} in #{message.channel.name}")
            
        except discord.Forbidden:
            # Bot doesn't have permission to send messages in this channel
            print(f"⚠️ No permission to send mention reminder in #{message.channel.name}")
        except discord.HTTPException as e:
            # Other Discord API errors
            print(f"❌ Failed to send mention reminder: {e}")


async def setup(bot):
    """Register the Mention Reminder Cog with the bot."""
    await bot.add_cog(MentionReminder(bot))
    print("✅ Mention reminder system registered successfully!")
