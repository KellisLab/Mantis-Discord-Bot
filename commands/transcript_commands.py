import discord
from typing import Optional
from config import TRANSCRIPT_CHANNELS
from utils.transcript_processor import TranscriptProcessor


def setup(bot):
    """Register transcript commands with the bot."""
    bot.tree.add_command(summarize_channel)

@discord.app_commands.command(
    name="summarize_channel", 
    description="Generate an AI summary of recent conversation in a channel and upload to database."
)
@discord.app_commands.describe(
    channel="The channel to summarize (defaults to current channel)"
)
async def summarize_channel(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    """Generate an AI summary of recent conversation in a channel and upload to database.
    
    Args:
        interaction: Discord interaction object
        channel: Optional target channel (defaults to current channel)
    """
    # Defer response since this operation can take time
    await interaction.response.defer()
    
    try:
        # Determine target channel
        target_channel = channel if channel else interaction.channel
        
        # Validate that the target channel is a text channel
        if not isinstance(target_channel, discord.TextChannel):
            await interaction.followup.send(
                "❌ This command can only be used on text channels.",
                ephemeral=True
            )
            return
        
        # Validate that channel is in allowed transcript channels
        if target_channel.id not in TRANSCRIPT_CHANNELS:
            await interaction.followup.send(
                f"❌ Channel {target_channel.mention} is not configured for transcript generation.\n"
                f"**Allowed channels:** {', '.join([f'<#{channel_id}>' for channel_id in TRANSCRIPT_CHANNELS]) if TRANSCRIPT_CHANNELS else 'None configured'}",
                ephemeral=True
            )
            return
        
        # Create transcript processor
        processor = TranscriptProcessor(interaction.client)
        
        # Process the channel transcript
        result = await processor.process_channel_transcript(target_channel.id)
        
        if result["success"]:
            # Create success embed with summary preview
            embed = discord.Embed(
                title="✅ Transcript Generated Successfully",
                description=f"**Channel:** {target_channel.mention}\n**Participants:** {len(result['participants'])} people",
                color=discord.Color.green()
            )
            
            # Add participant list if available
            if result["participants"]:
                participant_list = ", ".join(result["participants"][:10])  # Limit to first 10
                if len(result["participants"]) > 10:
                    participant_list += f" and {len(result['participants']) - 10} others"
                embed.add_field(
                    name="👥 Participants",
                    value=participant_list,
                    inline=False
                )
            
            # Add summary preview (truncated)
            if result.get("summary"):
                summary_preview = result["summary"][:800]  # Discord embed field limit
                if len(result["summary"]) > 800:
                    summary_preview += "..."
                embed.add_field(
                    name="📝 Summary Preview",
                    value=summary_preview,
                    inline=False
                )
            
            # Add processing stats
            embed.add_field(
                name="📊 Processing Stats",
                value=f"**Messages analyzed:** {result.get('message_count', 'N/A')}\n"
                      f"**Time period:** Last 24 hours\n"
                      f"**API submission:** Success",
                inline=False
            )
            
            embed.set_footer(text="Transcript uploaded to Mantis database")
            await interaction.followup.send(embed=embed)
            
        else:
            # Create error embed
            embed = discord.Embed(
                title="❌ Transcript Generation Failed",
                description=f"**Channel:** {target_channel.mention}",
                color=discord.Color.red()
            )
            
            # Add error details
            embed.add_field(
                name="🔍 Error Details",
                value=result.get("error", "Unknown error occurred"),
                inline=False
            )
            
            # Add helpful information based on error type
            error_msg = result.get("error", "").lower()
            if "insufficient messages" in error_msg or "minimum" in error_msg:
                embed.add_field(
                    name="💡 Suggestion",
                    value="Try again when there's more conversation activity in the channel.",
                    inline=False
                )
            elif "permission" in error_msg:
                embed.add_field(
                    name="💡 Suggestion", 
                    value="Make sure the bot has 'Read Message History' permission in this channel.",
                    inline=False
                )
            elif "api" in error_msg or "network" in error_msg:
                embed.add_field(
                    name="💡 Suggestion",
                    value="This might be a temporary issue. Please try again in a few minutes.",
                    inline=False
                )
            
            embed.set_footer(text="Check logs for detailed error information")
            await interaction.followup.send(embed=embed, ephemeral=True)
    
    except Exception as e:
        # Handle unexpected errors
        error_embed = discord.Embed(
            title="❌ Unexpected Error",
            description="An unexpected error occurred while processing the transcript.",
            color=discord.Color.red()
        )
        error_embed.add_field(
            name="🔍 Error Details",
            value=f"```python\n{str(e)[:1000]}```",  # Truncate very long errors
            inline=False
        )
        error_embed.set_footer(text="This error has been logged for investigation")
        
        await interaction.followup.send(embed=error_embed, ephemeral=True)
        
        # Log the error for debugging
        print(f"❌ Transcript command error in channel {interaction.channel}: {e}")