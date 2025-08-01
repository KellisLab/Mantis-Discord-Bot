import discord
from discord.ext import tasks
from datetime import datetime, time, timezone
from typing import Dict, Any


class ReminderScheduler:
    """Automated scheduler for weekly reminder generation to users with stale GitHub issues/PRs."""
    
    def __init__(self, bot: discord.Client, processor=None):
        """Initialize the ReminderScheduler with a Discord bot instance and optional shared processor.
        
        Args:
            bot: Discord bot/client instance
            processor: Optional shared ReminderProcessor instance. If None, creates a new instance.
        """
        self.bot = bot
        self.processor = processor
        self.is_running = False
        self.job_stats = {
            "last_run": None,
            "total_runs": 0,
            "total_users_reminded": 0,
            "dm_success_count": 0,
            "dm_failed_count": 0,
            "channel_messages_sent": 0,
        }
    
    def setup_weekly_schedule(self):
        """Set up the weekly reminder schedule.
        
        Configures the scheduled task to run every Saturday at 12:00 AM UTC.
        """
        print("🕐 Setting up weekly reminder schedule for Saturdays at 00:00 UTC")
        
        # Configure the task to run weekly on Saturday at midnight UTC
        schedule_time = time(hour=0, minute=0, second=0, tzinfo=timezone.utc)
        
        # Set up the weekly task (runs every 7 days starting from the next Saturday)
        self.weekly_reminder_task.change_interval(time=schedule_time)
        
        if not self.weekly_reminder_task.is_running():
            self.weekly_reminder_task.start()
            self.is_running = True
            print("✅ Weekly reminder scheduler started successfully")
        else:
            print("⚠️ Weekly reminder scheduler was already running")
    
    @tasks.loop(hours=24)  # Run every 24 hours to check if it's Saturday
    async def weekly_reminder_task(self):
        """Weekly task that processes reminders for all users with stale items."""
        # Only run on Saturday (weekday 5, where Monday is 0)
        current_time = datetime.now(timezone.utc)
        if current_time.weekday() == 5:  # Saturday
            await self.run_weekly_reminder_job()
        else:
            print(f"⏭️ Skipping reminder job - today is {current_time.strftime('%A')}, not Saturday")
    
    @weekly_reminder_task.before_loop
    async def before_weekly_task(self):
        """Wait for the bot to be ready before starting the scheduled task."""
        await self.bot.wait_until_ready()
        print("🤖 Bot is ready, reminder scheduler can now start")
    
    async def run_weekly_reminder_job(self) -> Dict[str, Any]:
        """
        Execute the weekly reminder job for all users with stale GitHub items.
        
        Returns:
            Dictionary with job execution statistics and results
        """
        job_start_time = datetime.utcnow()
        print(f"🚀 Starting weekly reminder job at {job_start_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        
        # Initialize job tracking
        results = {
            "start_time": job_start_time,
            "users_processed": 0,
            "dm_success": 0,
            "dm_failed": 0,
            "channel_sent": 0,
            "channel_failed": 0,
            "no_mapping": 0,
            "errors": []
        }
        
        # Execute the reminder processing
        if self.processor:
            try:
                # Use the shared processor to run reminders
                reminder_results = await self.processor.process_reminders()
                
                # Update results with processor output
                results.update(reminder_results)
                
                # Update global statistics
                self.job_stats["last_run"] = datetime.utcnow()
                self.job_stats["total_runs"] += 1
                self.job_stats["total_users_reminded"] += results.get("users_processed", 0)
                self.job_stats["dm_success_count"] += results.get("dm_success", 0)
                self.job_stats["dm_failed_count"] += results.get("dm_failed", 0)
                self.job_stats["channel_messages_sent"] += results.get("channel_sent", 0)
                
                print("✅ Weekly reminder job completed successfully")
                print(f"📊 Processed {results.get('users_processed', 0)} users")
                print(f"📬 DMs: {results.get('dm_success', 0)} success, {results.get('dm_failed', 0)} failed")
                print(f"📢 Channel: {results.get('channel_sent', 0)} sent, {results.get('channel_failed', 0)} failed")
                
            except Exception as e:
                error_msg = f"Error during weekly reminder job: {str(e)}"
                print(f"❌ {error_msg}")
                results["errors"].append(error_msg)
        else:
            error_msg = "No reminder processor configured"
            print(f"❌ {error_msg}")
            results["errors"].append(error_msg)
        
        # Calculate job completion statistics
        job_end_time = datetime.utcnow()
        total_processing_time = (job_end_time - job_start_time).total_seconds()
        
        results["end_time"] = job_end_time
        results["total_processing_time"] = total_processing_time
        
        print(f"🎯 Weekly reminder job completed at {job_end_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"⏱️ Total processing time: {total_processing_time:.1f} seconds")
        
        return results
    
    async def run_manual_job(self) -> Dict[str, Any]:
        """
        Manually trigger the weekly reminder job (for testing or on-demand execution).
        
        Returns:
            Dictionary with job execution statistics and results
        """
        print("🔧 Running manual reminder job...")
        return await self.run_weekly_reminder_job()
    
    def get_scheduler_status(self) -> Dict[str, Any]:
        """Get current scheduler status and statistics.
        
        Returns:
            Dictionary with scheduler status and job statistics
        """
        return {
            "is_running": self.is_running,
            "task_running": self.weekly_reminder_task.is_running() if hasattr(self, 'weekly_reminder_task') else False,
            "next_iteration": self.weekly_reminder_task.next_iteration if hasattr(self, 'weekly_reminder_task') and self.weekly_reminder_task.is_running() else None,
            "schedule_info": "Every Saturday at 00:00 UTC",
            "job_stats": self.job_stats.copy()
        }
    
    def stop_scheduler(self):
        """Stop the weekly reminder scheduler."""
        if hasattr(self, 'weekly_reminder_task') and self.weekly_reminder_task.is_running():
            self.weekly_reminder_task.cancel()
            self.is_running = False
            print("🛑 Weekly reminder scheduler stopped")
        else:
            print("⚠️ Weekly reminder scheduler was not running")