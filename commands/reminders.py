import discord
from config import (
    REMINDER_CHANNEL_ID,
    GITHUB_ORG_NAME,
    MEMBER_MAPPING_CACHE_DURATION,
)
from utils.member_mapping import MemberMappingCache
from utils.reminder_processor import ReminderProcessor

# Initialize member mapping cache with configuration (used by test commands)
member_mapping_cache = MemberMappingCache(
    cache_duration=MEMBER_MAPPING_CACHE_DURATION
)

def setup(bot):
    """Register reminder commands with the bot."""
    bot.tree.add_command(send_reminders)
    bot.tree.add_command(test_member_mapping)
    bot.tree.add_command(test_discord_lookup)

# Helper function for find_discord_user (used by test commands)
async def find_discord_user(bot, discord_username: str):
    """
    Find a Discord user by username across all guilds the bot can see.
    Returns the User object if found, None otherwise.
    """
    def matches_username(user, target_username):
        """Check if user matches target username in various ways."""
        target_lower = target_username.lower()
        
        # Check username (new system)
        if user.name and user.name.lower() == target_lower:
            return True
            
        # Check global name (display name)
        if hasattr(user, 'global_name') and user.global_name and user.global_name.lower() == target_lower:
            return True
            
        # Check display name (for guild members)
        if hasattr(user, 'display_name') and user.display_name and user.display_name.lower() == target_lower:
            return True
            
        # Check old format with discriminator (fallback)
        if hasattr(user, 'discriminator') and user.discriminator != '0':
            old_format = f"{user.name}#{user.discriminator}"
            if old_format.lower() == target_lower:
                return True
        
        return False
    
    # Method 1: Search through bot's cached users
    for user in bot.users:
        if matches_username(user, discord_username):
            print(f"🔍 Found user {discord_username} in bot.users cache: {user.name} (ID: {user.id})")
            return user
            
    # Method 2: Search through all guild members
    for guild in bot.guilds:
        for member in guild.members:
            if matches_username(member, discord_username):
                print(f"🔍 Found user {discord_username} in guild {guild.name}: {member.name} (ID: {member.id})")
                return member
    
    print(f"❌ Could not find Discord user: {discord_username}")
    print(f"🔍 Bot can see {len(bot.users)} cached users and {sum(len(g.members) for g in bot.guilds)} guild members across {len(bot.guilds)} guilds")
    return None

@discord.app_commands.command(
    name="test-discord-lookup",
    description="Test Discord user lookup by username.",
)
async def test_discord_lookup(interaction: discord.Interaction, username: str):
    """Test finding a Discord user by username."""
    await interaction.response.defer(ephemeral=True)
    
    try:
        discord_user = await find_discord_user(interaction.client, username)
        
        if discord_user:
            embed = discord.Embed(
                title="✅ Discord User Found",
                description=f"Successfully found user: **{username}**",
                color=discord.Color.green()
            )
            
            embed.add_field(
                name="User Details",
                value=f"• **Username:** {discord_user.name}\n"
                      f"• **Global Name:** {getattr(discord_user, 'global_name', 'None')}\n"
                      f"• **Display Name:** {getattr(discord_user, 'display_name', 'None')}\n"
                      f"• **User ID:** {discord_user.id}\n"
                      f"• **Bot:** {'Yes' if discord_user.bot else 'No'}",
                inline=False
            )
            
            # Test DM capability
            try:
                await discord_user.send("Test DM (this is a test, please ignore)")
                dm_status = "✅ DM sent successfully"
            except discord.Forbidden:
                dm_status = "❌ Cannot send DM (user has DMs disabled or doesn't share a server)"
            except discord.HTTPException as e:
                dm_status = f"❌ DM failed: {e}"
            except Exception as e:
                dm_status = f"❌ DM error: {e}"
            
            embed.add_field(
                name="DM Test",
                value=dm_status,
                inline=False
            )
            
        else:
            embed = discord.Embed(
                title="❌ Discord User Not Found",
                description=f"Could not find user: **{username}**",
                color=discord.Color.red()
            )
            
            embed.add_field(
                name="Bot Visibility",
                value=f"• **Cached Users:** {len(interaction.client.users)}\n"
                      f"• **Guilds:** {len(interaction.client.guilds)}\n"
                      f"• **Total Guild Members:** {sum(len(g.members) for g in interaction.client.guilds)}",
                inline=False
            )
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        embed = discord.Embed(
            title="❌ Test Failed",
            description=f"Error during Discord user lookup test: {str(e)}",
            color=discord.Color.red()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


@discord.app_commands.command(
    name="test-member-mapping",
    description="Test the GitHub to Discord username mapping API connection.",
)
async def test_member_mapping(interaction: discord.Interaction):
    """Test the member mapping API and show current mappings."""
    await interaction.response.defer(ephemeral=True)
    
    try:
        # Fetch mapping
        github_to_discord = await member_mapping_cache.get_mapping()
        cache_info = member_mapping_cache.get_cache_info()
        
        if github_to_discord:
            # Create embed with mapping info
            embed = discord.Embed(
                title="🔗 GitHub → Discord Mapping Test",
                description="Successfully connected to member mapping API!",
                color=discord.Color.green()
            )
            
            # Add cache info
            embed.add_field(
                name="📊 Cache Information",
                value=f"• **Mappings Found:** {cache_info['cache_size']}\n"
                      f"• **Cache Age:** {cache_info['cache_age_seconds']} seconds\n"
                      f"• **Last Updated:** {cache_info['last_fetch']}\n"
                      f"• **Cache Valid:** {'✅ Yes' if cache_info['cache_valid'] else '❌ No'}",
                inline=False
            )
            
            # Show sample mappings (first 10)
            if github_to_discord:
                sample_mappings = list(github_to_discord.items())[:10]
                mapping_lines = []
                for gh, user_info in sample_mappings:
                    if isinstance(user_info, dict):
                        discord_username = user_info.get("discord_username", "Unknown")
                        real_name = user_info.get("name", "Unknown")
                        mapping_lines.append(f"• `{gh}` → `{discord_username}` ({real_name})")
                    else:
                        # Fallback for old format
                        mapping_lines.append(f"• `{gh}` → `{user_info}`")
                
                mapping_text = "\n".join(mapping_lines)
                
                if len(github_to_discord) > 10:
                    mapping_text += f"\n• ... and {len(github_to_discord) - 10} more"
                
                embed.add_field(
                    name="👥 Sample Mappings",
                    value=mapping_text,
                    inline=False
                )
            
            embed.set_footer(text=f"API Endpoint: {member_mapping_cache.api_base_url}")
            
        else:
            embed = discord.Embed(
                title="❌ GitHub → Discord Mapping Test",
                description="No mappings found or API connection failed.",
                color=discord.Color.red()
            )
            
            embed.add_field(
                name="📊 Cache Information",
                value=f"• **Cache Size:** {cache_info['cache_size']}\n"
                      f"• **Last Fetch:** {cache_info['last_fetch']}\n"
                      f"• **API Endpoint:** {member_mapping_cache.api_base_url}",
                inline=False
            )
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        embed = discord.Embed(
            title="❌ Member Mapping Test Failed",
            description=f"Error connecting to member mapping API: {str(e)}",
            color=discord.Color.red()
        )
        embed.add_field(
            name="🔧 Troubleshooting",
            value="• Check that the Django API is running\n"
                  "• Verify the API endpoint URL\n" 
                  "• Check network connectivity",
            inline=False
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

@discord.app_commands.command(
    name="send-reminders",
    description=f"Send reminder DMs to users with stale issues/PRs in {GITHUB_ORG_NAME} repositories.",
)
async def send_reminders(interaction: discord.Interaction):
    """Send reminder messages for stale issues and PRs using the ReminderProcessor."""
    await interaction.response.defer()
    
    try:
        # Create a reminder processor with shared member cache
        processor = ReminderProcessor(interaction.client, member_mapping_cache)
        
        # Process reminders using the shared processor
        results = await processor.process_reminders(REMINDER_CHANNEL_ID)
        
        # Check for errors
        if "error" in results:
            await interaction.followup.send(f"❌ {results['error']}", ephemeral=True)
            return
        
        # Send summary
        total_users = results.get("users_processed", 0)
        if total_users > 0:
            delivery_stats = results.get("delivery_stats", {})
            summary_parts = [
                f"✅ **Processed {total_users} user(s) with stale items:**",
                f"📬 Direct Messages Sent: **{delivery_stats.get('dm_success', 0)}**",
                f"📬 Direct Messages Failed: **{delivery_stats.get('dm_failed', 0)}**",
                f"📢 Channel Messages Sent: **{delivery_stats.get('channel_sent', 0)}**",
                f"📢 Channel Messages Failed: **{delivery_stats.get('channel_failed', 0)}**",
                f"🔍 No Discord Mapping: **{delivery_stats.get('no_mapping', 0)}**"
            ]
            
            # Calculate how many users got mentioned vs not mentioned
            users_mentioned = delivery_stats.get("dm_failed", 0) + delivery_stats.get("no_mapping", 0)
            users_not_mentioned = delivery_stats.get("dm_success", 0)
            
            if users_mentioned > 0:
                summary_parts.append(f"\n📍 *{users_mentioned} users mentioned in <#{REMINDER_CHANNEL_ID}> (DM failed/no mapping)*")
            
            if users_not_mentioned > 0:
                summary_parts.append(f"\n💌 *{users_not_mentioned} users received both DM + channel message (not mentioned)*")
                
            summary_parts.append("\n🎯 *All reminders now sent to both DMs and the channel for better visibility*")
                
            await interaction.followup.send("\n".join(summary_parts), ephemeral=True)
        else:
            await interaction.followup.send("ℹ️ No stale items found that require reminders.", ephemeral=True)
            
    except Exception as e:
        await interaction.followup.send(f"❌ Error processing reminders: {str(e)}", ephemeral=True) 