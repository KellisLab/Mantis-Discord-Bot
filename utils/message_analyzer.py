import discord
from datetime import datetime, timedelta
from typing import List
from config import TRANSCRIPT_HOURS_BACK, TRANSCRIPT_MIN_MESSAGES
from .member_mapping import MemberMappingCache


class MessageAnalyzer:
    """Analyzes Discord messages to extract conversation data for transcript generation."""
    
    def __init__(self):
        """Initialize the MessageAnalyzer with member mapping cache."""
        self.member_cache = MemberMappingCache()
    
    async def fetch_channel_messages(
        self, 
        channel: discord.TextChannel, 
        hours_back: int = None
    ) -> List[discord.Message]:
        """
        Fetch messages from a Discord channel within the specified time window.
        
        Args:
            channel: Discord text channel object
            hours_back: Hours of history to fetch (defaults to TRANSCRIPT_HOURS_BACK)
        
        Returns:
            List of Discord message objects
        """
        if hours_back is None:
            hours_back = TRANSCRIPT_HOURS_BACK
        
        # Calculate timestamp cutoff (current time - hours_back)
        cutoff_time = datetime.utcnow() - timedelta(hours=hours_back)
        
        print(f"📥 Fetching messages from #{channel.name} since {cutoff_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        
        try:
            # Fetch messages using Discord.py's history method
            messages = []
            async for message in channel.history(after=cutoff_time, limit=None):
                messages.append(message)
            
            # Sort messages by timestamp (oldest first) for better conversation flow
            messages.sort(key=lambda m: m.created_at)
            
            print(f"📥 Fetched {len(messages)} messages from #{channel.name}")
            return messages
            
        except discord.Forbidden:
            print(f"❌ No permission to read message history in #{channel.name}")
            return []
        except discord.HTTPException as e:
            print(f"❌ HTTP error fetching messages from #{channel.name}: {e}")
            return []
        except Exception as e:
            print(f"❌ Unexpected error fetching messages from #{channel.name}: {e}")
            return []
    
    def extract_participants(self, messages: List[discord.Message]) -> List[discord.Member]:
        """
        Extract unique Discord users who participated in the conversation.
        
        Args:
            messages: List of Discord message objects
        
        Returns:
            List of unique Discord member/user objects (excluding bots)
        """
        participants = set()
        
        for message in messages:
            author = message.author
            
            # Skip bot users
            if author.bot:
                continue
                
            # Skip system messages (these don't have meaningful authors)
            if message.type != discord.MessageType.default:
                continue
            
            participants.add(author)
        
        participant_list = list(participants)
        print(f"👥 Found {len(participant_list)} participants: {[p.display_name for p in participant_list]}")
        
        return participant_list
    
    async def map_users_to_real_names(self, discord_users: List[discord.Member]) -> List[str]:
        """
        Map Discord users to their real names using the existing member mapping system.
        
        Args:
            discord_users: List of Discord member/user objects
        
        Returns:
            List of real names (only users found in mapping database)
        """
        # Get the current mapping data (GitHub username -> user info)
        mapping_data = await self.member_cache.get_mapping()
        
        # Create reverse lookup: Discord username -> real name
        discord_to_real_name = {}
        for github_user, user_info in mapping_data.items():
            if isinstance(user_info, dict):
                discord_username = user_info.get("discord_username")
                real_name = user_info.get("name")
                if discord_username and real_name:
                    discord_to_real_name[discord_username] = real_name
        
        # Map the Discord users to real names
        real_names = []
        unmapped_users = []
        
        for user in discord_users:
            # Try multiple Discord username formats
            possible_usernames = [
                user.name,  # Current username
                user.display_name,  # Server nickname or global display name
            ]
            
            # If it's a Member object, also try the nickname
            if hasattr(user, 'nick') and user.nick:
                possible_usernames.append(user.nick)
            
            mapped = False
            for username in possible_usernames:
                if username in discord_to_real_name:
                    real_name = discord_to_real_name[username]
                    if real_name not in real_names:  # Avoid duplicates
                        real_names.append(real_name)
                        print(f"✅ Mapped {user.display_name} ({username}) → {real_name}")
                        mapped = True
                        break
            
            if not mapped:
                unmapped_users.append(user.display_name)
        
        if unmapped_users:
            print(f"⚠️ Could not map {len(unmapped_users)} users to real names: {unmapped_users}")
        
        print(f"👥 Mapped {len(real_names)} users to real names: {real_names}")
        return real_names
    
    def filter_valid_messages(self, messages: List[discord.Message]) -> List[discord.Message]:
        """
        Filter out invalid messages (bot messages, system messages, empty messages).
        
        Args:
            messages: List of Discord message objects
        
        Returns:
            List of filtered Discord message objects
        """
        valid_messages = []
        
        for message in messages:
            # Skip bot messages
            if message.author.bot:
                continue
            
            # Skip system messages (joins, leaves, pins, etc.)
            if message.type != discord.MessageType.default:
                continue
            
            # Skip empty messages (or messages with only whitespace)
            if not message.content or not message.content.strip():
                continue
            
            # Skip messages that are just mentions or very short
            if len(message.content.strip()) < 3:
                continue
            
            valid_messages.append(message)
        
        print(f"🔍 Filtered {len(messages)} messages → {len(valid_messages)} valid messages")
        return valid_messages
    
    def check_minimum_threshold(
        self, 
        messages: List[discord.Message], 
        min_count: int = None
    ) -> bool:
        """
        Check if the message count meets the minimum threshold for generating a transcript.
        
        Args:
            messages: List of Discord message objects
            min_count: Minimum message count (defaults to TRANSCRIPT_MIN_MESSAGES)
        
        Returns:
            Boolean indicating whether threshold is met
        """
        if min_count is None:
            min_count = TRANSCRIPT_MIN_MESSAGES
        
        meets_threshold = len(messages) >= min_count
        
        if meets_threshold:
            print(f"✅ Message count ({len(messages)}) meets minimum threshold ({min_count})")
        else:
            print(f"❌ Message count ({len(messages)}) below minimum threshold ({min_count}) - skipping transcript")
        
        return meets_threshold
    
    def format_messages_for_analysis(self, messages: List[discord.Message]) -> str:
        """
        Format Discord messages into a readable text format for AI analysis.
        
        Args:
            messages: List of Discord message objects
        
        Returns:
            Formatted conversation string
        """
        if not messages:
            return ""
        
        conversation_lines = []
        
        for message in messages:
            # Format timestamp
            timestamp = message.created_at.strftime('%H:%M')
            
            # Use display name (nickname or username)
            author_name = message.author.display_name
            
            # Clean up the message content
            content = message.content.strip()
            
            # Handle different message types
            if message.attachments:
                attachment_info = f" [+{len(message.attachments)} attachment(s)]"
                content += attachment_info
            
            if message.embeds:
                embed_info = f" [+{len(message.embeds)} embed(s)]"
                content += embed_info
            
            # Format: [HH:MM] Username: message content
            line = f"[{timestamp}] {author_name}: {content}"
            conversation_lines.append(line)
        
        conversation_text = "\n".join(conversation_lines)
        print(f"📝 Formatted {len(messages)} messages for AI analysis ({len(conversation_text)} characters)")
        
        return conversation_text