import discord
from discord.ext import tasks
from datetime import datetime, time, timezone
from typing import Dict, Any
from config import TRANSCRIPT_CHANNELS, TRANSCRIPT_SCHEDULE_HOUR
from .transcript_processor import TranscriptProcessor


class TranscriptScheduler:
    """Automated scheduler for daily Discord conversation transcript generation."""
    
    def __init__(self, bot: discord.Client):
        """Initialize the TranscriptScheduler with a Discord bot instance.
        
        Args:
            bot: Discord bot/client instance
        """
        self.bot = bot
        self.processor = TranscriptProcessor(bot)
        self.is_running = False
        self.job_stats = {
            "last_run": None,
            "total_runs": 0,
            "successful_channels": 0,
            "failed_channels": 0,
            "total_transcripts": 0
        }
    
    def setup_daily_schedule(self):
        """Set up the daily transcript generation schedule.
        
        Configures the scheduled task to run daily at the specified hour (UTC).
        """
        print(f"🕐 Setting up daily transcript schedule for {TRANSCRIPT_SCHEDULE_HOUR:02d}:00 UTC")
        
        # Configure the task to run daily at the specified hour
        schedule_time = time(hour=TRANSCRIPT_SCHEDULE_HOUR, minute=0, second=0, tzinfo=timezone.utc)
        
        # Set up the daily task
        self.daily_transcript_task.change_interval(time=schedule_time)
        
        if not self.daily_transcript_task.is_running():
            self.daily_transcript_task.start()
            self.is_running = True
            print("✅ Daily transcript scheduler started successfully")
        else:
            print("⚠️ Daily transcript scheduler was already running")
    
    @tasks.loop(hours=24)
    async def daily_transcript_task(self):
        """Daily task that processes transcripts for all configured channels."""
        await self.run_daily_transcript_job()
    
    @daily_transcript_task.before_loop
    async def before_daily_task(self):
        """Wait for the bot to be ready before starting the scheduled task."""
        await self.bot.wait_until_ready()
        print("🤖 Bot is ready, transcript scheduler can now start")
    
    async def run_daily_transcript_job(self) -> Dict[str, Any]:
        """
        Execute the daily transcript generation job for all configured channels.
        
        Returns:
            Dictionary with job execution statistics and results
        """
        job_start_time = datetime.utcnow()
        print(f"🚀 Starting daily transcript job at {job_start_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        
        # Initialize job tracking
        results = {
            "start_time": job_start_time,
            "channels_processed": 0,
            "successful_channels": 0,
            "failed_channels": 0,
            "channel_results": {},
            "errors": []
        }
        
        # Validate configuration
        if not TRANSCRIPT_CHANNELS:
            error_msg = "No channels configured in TRANSCRIPT_CHANNELS"
            print(f"⚠️ {error_msg}")
            results["errors"].append(error_msg)
            return results
        
        print(f"📋 Processing transcripts for {len(TRANSCRIPT_CHANNELS)} configured channels")
        
        # Process each configured channel
        for channel_id in TRANSCRIPT_CHANNELS:
            channel_start_time = datetime.utcnow()
            print(f"\n📄 Processing channel {channel_id}...")
            
            try:
                # Process the channel transcript
                result = await self.processor.process_channel_transcript(
                    channel_id=channel_id,
                    force_process=False  # Respect configuration validation
                )
                
                # Track results
                results["channels_processed"] += 1
                results["channel_results"][str(channel_id)] = {
                    "success": result["success"],
                    "message": result["message"],
                    "participants": result.get("participants", []),
                    "message_count": result.get("message_count", 0),
                    "transcript_id": result.get("transcript_id"),
                    "processing_time": (datetime.utcnow() - channel_start_time).total_seconds()
                }
                
                if result["success"]:
                    results["successful_channels"] += 1
                    participant_count = len(result.get("participants", []))
                    message_count = result.get("message_count", 0)
                    transcript_id = result.get("transcript_id", "unknown")
                    print(f"✅ Channel {channel_id}: {message_count} messages, {participant_count} participants (ID: {transcript_id})")
                else:
                    results["failed_channels"] += 1
                    error_msg = result.get("error", result["message"])
                    print(f"❌ Channel {channel_id}: {error_msg}")
                    results["errors"].append(f"Channel {channel_id}: {error_msg}")
                
            except Exception as e:
                # Handle unexpected errors during channel processing
                results["channels_processed"] += 1
                results["failed_channels"] += 1
                error_msg = f"Unexpected error processing channel {channel_id}: {str(e)}"
                print(f"❌ {error_msg}")
                results["errors"].append(error_msg)
                results["channel_results"][str(channel_id)] = {
                    "success": False,
                    "message": error_msg,
                    "participants": [],
                    "message_count": 0,
                    "transcript_id": None,
                    "processing_time": (datetime.utcnow() - channel_start_time).total_seconds()
                }
        
        # Calculate job completion statistics
        job_end_time = datetime.utcnow()
        total_processing_time = (job_end_time - job_start_time).total_seconds()
        
        results["end_time"] = job_end_time
        results["total_processing_time"] = total_processing_time
        
        # Update global statistics
        self.job_stats["last_run"] = job_end_time
        self.job_stats["total_runs"] += 1
        self.job_stats["successful_channels"] += results["successful_channels"]
        self.job_stats["failed_channels"] += results["failed_channels"]
        self.job_stats["total_transcripts"] += results["successful_channels"]
        
        # Log job completion summary
        print(f"\n📊 Daily transcript job completed in {total_processing_time:.1f} seconds")
        print(f"📈 Results: {results['successful_channels']} successful, {results['failed_channels']} failed out of {results['channels_processed']} channels")
        
        if results["errors"]:
            print(f"⚠️ Errors encountered: {len(results['errors'])}")
            for error in results["errors"]:
                print(f"   • {error}")
        
        print(f"🎯 Job completed at {job_end_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        
        return results
    
    async def run_manual_job(self) -> Dict[str, Any]:
        """
        Manually trigger the daily transcript job (for testing or on-demand execution).
        
        Returns:
            Dictionary with job execution statistics and results
        """
        print("🔧 Running manual transcript job...")
        return await self.run_daily_transcript_job()
    
    def get_scheduler_status(self) -> Dict[str, Any]:
        """Get current scheduler status and statistics.
        
        Returns:
            Dictionary with scheduler status and job statistics
        """
        return {
            "is_running": self.is_running,
            "task_running": self.daily_transcript_task.is_running() if hasattr(self, 'daily_transcript_task') else False,
            "next_iteration": self.daily_transcript_task.next_iteration if hasattr(self, 'daily_transcript_task') and self.daily_transcript_task.is_running() else None,
            "configured_channels": len(TRANSCRIPT_CHANNELS),
            "schedule_hour": TRANSCRIPT_SCHEDULE_HOUR,
            "job_stats": self.job_stats.copy()
        }
    
    def stop_scheduler(self):
        """Stop the daily transcript scheduler."""
        if hasattr(self, 'daily_transcript_task') and self.daily_transcript_task.is_running():
            self.daily_transcript_task.cancel()
            self.is_running = False
            print("🛑 Daily transcript scheduler stopped")
        else:
            print("⚠️ Daily transcript scheduler was not running")
    
    async def test_configuration(self) -> Dict[str, Any]:
        """
        Test the scheduler configuration and channel accessibility.
        
        Returns:
            Dictionary with configuration test results
        """
        print("🧪 Testing transcript scheduler configuration...")
        
        test_results = {
            "config_valid": True,
            "channels_accessible": 0,
            "channels_inaccessible": 0,
            "errors": [],
            "channel_details": {}
        }
        
        # Test basic configuration
        if not TRANSCRIPT_CHANNELS:
            test_results["config_valid"] = False
            test_results["errors"].append("No channels configured in TRANSCRIPT_CHANNELS")
            return test_results
        
        # Test each configured channel
        for channel_id in TRANSCRIPT_CHANNELS:
            try:
                channel = self.bot.get_channel(channel_id)
                if not channel:
                    channel = await self.bot.fetch_channel(channel_id)
                
                if isinstance(channel, discord.TextChannel):
                    test_results["channels_accessible"] += 1
                    test_results["channel_details"][str(channel_id)] = {
                        "name": channel.name,
                        "accessible": True,
                        "type": "text",
                        "guild": channel.guild.name
                    }
                    print(f"✅ Channel {channel_id} (#{channel.name}) is accessible")
                else:
                    test_results["channels_inaccessible"] += 1
                    test_results["errors"].append(f"Channel {channel_id} is not a text channel")
                    test_results["channel_details"][str(channel_id)] = {
                        "name": getattr(channel, 'name', 'unknown'),
                        "accessible": False,
                        "type": type(channel).__name__,
                        "guild": getattr(channel, 'guild', {}).get('name', 'unknown') if hasattr(channel, 'guild') else 'unknown'
                    }
                    
            except discord.NotFound:
                test_results["channels_inaccessible"] += 1
                test_results["errors"].append(f"Channel {channel_id} not found")
                test_results["channel_details"][str(channel_id)] = {
                    "name": "not_found",
                    "accessible": False,
                    "type": "unknown",
                    "guild": "unknown"
                }
                print(f"❌ Channel {channel_id} not found")
                
            except discord.Forbidden:
                test_results["channels_inaccessible"] += 1
                test_results["errors"].append(f"No permission to access channel {channel_id}")
                test_results["channel_details"][str(channel_id)] = {
                    "name": "permission_denied",
                    "accessible": False,
                    "type": "unknown",
                    "guild": "unknown"
                }
                print(f"❌ No permission for channel {channel_id}")
        
        # Summary
        total_channels = len(TRANSCRIPT_CHANNELS)
        print(f"📋 Configuration test complete: {test_results['channels_accessible']}/{total_channels} channels accessible")
        
        if test_results["errors"]:
            print(f"⚠️ Issues found: {len(test_results['errors'])}")
            for error in test_results["errors"]:
                print(f"   • {error}")
        
        return test_results