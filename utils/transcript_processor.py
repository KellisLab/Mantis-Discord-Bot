import discord
from datetime import datetime
from typing import List, Dict, Any, Tuple
from config import TRANSCRIPT_CHANNELS, TRANSCRIPT_HOURS_BACK, TRANSCRIPT_MIN_MESSAGES
from .transcript_api import TranscriptAPI
from .message_analyzer import MessageAnalyzer
from .ai_summarizer import ConversationSummarizer


class TranscriptProcessor:
    """Main orchestrator for Discord conversation transcript generation and API submission."""
    
    def __init__(self, bot: discord.Client, member_cache=None, message_analyzer=None, ai_summarizer=None, transcript_api=None):
        """Initialize the TranscriptProcessor with a Discord bot instance and optional shared components.
        
        Args:
            bot: Discord bot/client instance
            member_cache: Optional shared MemberMappingCache instance
            message_analyzer: Optional shared MessageAnalyzer instance
            ai_summarizer: Optional shared ConversationSummarizer instance
            transcript_api: Optional shared TranscriptAPI instance
        """
        self.bot = bot
        self.transcript_api = transcript_api if transcript_api is not None else TranscriptAPI()
        self.message_analyzer = message_analyzer if message_analyzer is not None else MessageAnalyzer(member_cache)
        self.ai_summarizer = ai_summarizer if ai_summarizer is not None else ConversationSummarizer()
    
    async def process_channel_transcript(
        self, 
        channel_id: int,
        hours_back: int = None,
        force_process: bool = False
    ) -> Dict[str, Any]:
        """
        Process a complete transcript workflow for a Discord channel.
        
        Args:
            channel_id: Discord channel ID to process
            hours_back: Hours of message history to analyze (defaults to config)
            force_process: Skip channel validation checks if True
        
        Returns:
            Dictionary with processing results:
            {
                "success": bool,
                "message": str,
                "participants": List[str] (optional),
                "summary": str (optional),
                "message_count": int (optional),
                "transcript_id": str (optional)
            }
        """
        if hours_back is None:
            hours_back = TRANSCRIPT_HOURS_BACK
        
        channel_id_str = str(channel_id)
        
        try:
            print(f"🚀 Starting transcript processing for channel {channel_id}")
            
            # Step 1: Validate channel configuration (unless forced)
            if not force_process and int(channel_id) not in TRANSCRIPT_CHANNELS:
                message = f"Channel {channel_id} not in configured transcript channels"
                print(f"❌ {message}")
                return {
                    "success": False,
                    "message": message,
                    "error": "Channel not configured"
                }
            
            # Step 2: Fetch channel object from bot
            channel = self.bot.get_channel(channel_id)
            if not channel:
                try:
                    channel = await self.bot.fetch_channel(channel_id)
                except discord.NotFound:
                    message = f"Channel {channel_id} not found"
                    print(f"❌ {message}")
                    return {
                        "success": False,
                        "message": message,
                        "error": "Channel not found"
                    }
                except discord.Forbidden:
                    message = f"No permission to access channel {channel_id}"
                    print(f"❌ {message}")
                    return {
                        "success": False,
                        "message": message,
                        "error": "Permission denied"
                    }
            
            # Ensure it's a text channel
            if not isinstance(channel, discord.TextChannel):
                message = f"Channel {channel_id} is not a text channel"
                print(f"❌ {message}")
                return {
                    "success": False,
                    "message": message,
                    "error": "Invalid channel type"
                }
            
            print(f"📄 Processing channel #{channel.name}")
            
            # Step 3: Fetch and analyze messages
            raw_messages = await self.message_analyzer.fetch_channel_messages(
                channel, hours_back
            )
            
            if not raw_messages:
                message = f"No messages found in #{channel.name} for the last {hours_back} hours"
                print(f"⚠️ {message}")
                return {
                    "success": False,
                    "message": message,
                    "error": "No messages found",
                    "message_count": 0
                }
            
            # Step 4: Filter valid messages
            valid_messages = self.message_analyzer.filter_valid_messages(raw_messages)
            
            # Step 5: Check minimum message threshold
            if not self.message_analyzer.check_minimum_threshold(valid_messages):
                message = f"Not enough messages ({len(valid_messages)}) in #{channel.name} to generate transcript"
                print(f"⚠️ {message}")
                return {
                    "success": False,
                    "message": message,
                    "error": "Insufficient messages",
                    "message_count": len(valid_messages)
                }
            
            # Step 6: Extract participants and map to real names
            participants = self.message_analyzer.extract_participants(valid_messages)
            real_names = await self.message_analyzer.map_users_to_real_names(participants)
            
            # Step 7: Format messages for AI analysis
            formatted_conversation = self.message_analyzer.format_messages_for_analysis(valid_messages)
            
            # Step 8: Generate AI summary
            raw_summary = await self.ai_summarizer.generate_conversation_summary(
                formatted_conversation, 
                channel.name,
                real_names
            )
            
            if not raw_summary:
                message = f"Failed to generate AI summary for #{channel.name}"
                print(f"❌ {message}")
                return {
                    "success": False,
                    "message": message,
                    "error": "AI summary generation failed",
                    "participants": real_names,
                    "message_count": len(valid_messages)
                }
            
            # Step 9: Format summary for API submission
            formatted_summary = self.ai_summarizer.format_summary_for_api(
                raw_summary, 
                channel.name, 
                len(valid_messages)
            )
            
            # Step 10: Submit to Django API
            timestamp = datetime.utcnow()
            success, api_response = await self.transcript_api.create_discord_transcript(
                channel_name=channel.name,
                channel_type="text",
                channel_id=channel_id_str,
                description=formatted_summary,
                timestamp=timestamp,
                people_involved_names=real_names if real_names else None
            )
            
            if success:
                transcript_id = api_response.get('data', {}).get('id', 'unknown')
                message = f"✅ Successfully created transcript for #{channel.name} (ID: {transcript_id})"
                print(f"✅ {message}")
                return {
                    "success": True,
                    "message": message,
                    "participants": real_names,
                    "summary": formatted_summary,
                    "message_count": len(valid_messages),
                    "transcript_id": transcript_id
                }
            else:
                error_detail = api_response.get('error', 'Unknown API error')
                message = f"Failed to submit transcript for #{channel.name}: {error_detail}"
                print(f"❌ {message}")
                return {
                    "success": False,
                    "message": message,
                    "error": f"API submission failed: {error_detail}",
                    "participants": real_names,
                    "summary": formatted_summary,
                    "message_count": len(valid_messages)
                }
                
        except discord.Forbidden:
            message = f"No permission to read messages in channel {channel_id}"
            print(f"❌ {message}")
            return {
                "success": False,
                "message": message,
                "error": "Permission denied"
            }
        
        except discord.HTTPException as e:
            message = f"Discord API error for channel {channel_id}: {e}"
            print(f"❌ {message}")
            return {
                "success": False,
                "message": message,
                "error": f"Discord API error: {e}"
            }
        
        except Exception as e:
            message = f"Unexpected error processing channel {channel_id}: {e}"
            print(f"❌ {message}")
            return {
                "success": False,
                "message": message,
                "error": f"Unexpected error: {e}"
            }
    
    async def process_all_configured_channels(self) -> Dict[str, Tuple[bool, str]]:
        """
        Process transcripts for all configured channels.
        
        Returns:
            Dict mapping channel_id -> (success, message) for each channel
        """
        results = {}
        
        if not TRANSCRIPT_CHANNELS:
            print("⚠️ No channels configured for transcript processing")
            return results
        
        print(f"🚀 Processing transcripts for {len(TRANSCRIPT_CHANNELS)} configured channels")
        
        for channel_id in TRANSCRIPT_CHANNELS:
            result = await self.process_channel_transcript(channel_id)
            results[str(channel_id)] = (result["success"], result["message"])
        
        # Summary statistics
        successful = sum(1 for success, _ in results.values() if success)
        total = len(results)
        print(f"📊 Transcript processing complete: {successful}/{total} channels successful")
        
        return results
    
    async def validate_configuration(self) -> Tuple[bool, List[str]]:
        """
        Validate the current transcript configuration.
        
        Returns:
            Tuple of (all_valid: bool, error_messages: List[str])
        """
        errors = []
        
        # Check if any channels are configured
        if not TRANSCRIPT_CHANNELS:
            errors.append("No channels configured in TRANSCRIPT_CHANNELS")
        
        # Check if bot can access configured channels
        for channel_id in TRANSCRIPT_CHANNELS:
            try:
                channel = self.bot.get_channel(channel_id)
                if not channel:
                    channel = await self.bot.fetch_channel(channel_id)
                
                if not isinstance(channel, discord.TextChannel):
                    errors.append(f"Channel {channel_id} is not a text channel")
                    continue
                
                # Check if bot has necessary permissions
                if not channel.permissions_for(channel.guild.me).read_message_history:
                    errors.append(f"Bot lacks 'Read Message History' permission in #{channel.name}")
                
                if not channel.permissions_for(channel.guild.me).view_channel:
                    errors.append(f"Bot lacks 'View Channel' permission in #{channel.name}")
                    
            except discord.NotFound:
                errors.append(f"Channel {channel_id} not found")
            except discord.Forbidden:
                errors.append(f"No permission to access channel {channel_id}")
            except Exception as e:
                errors.append(f"Error checking channel {channel_id}: {e}")
        
        # Check API configuration
        if not self.transcript_api.api_key:
            errors.append("M4M_DISCORD_API_KEY not configured")
        
        # Check AI configuration
        if not self.ai_summarizer.client:
            errors.append("OpenAI API not configured properly")
        
        return len(errors) == 0, errors
    
    def get_processing_stats(self) -> Dict[str, Any]:
        """
        Get current processing configuration and statistics.
        
        Returns:
            Dict with configuration and status information
        """
        return {
            "configured_channels": len(TRANSCRIPT_CHANNELS),
            "channel_ids": TRANSCRIPT_CHANNELS,
            "hours_back": TRANSCRIPT_HOURS_BACK,
            "min_messages": TRANSCRIPT_MIN_MESSAGES,
            "api_configured": bool(self.transcript_api.api_key),
            "ai_configured": bool(self.ai_summarizer.client),
            "member_mapping_cache_info": self.message_analyzer.member_cache.get_cache_info()
        }