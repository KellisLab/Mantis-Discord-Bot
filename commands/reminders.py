import discord

from config import (
    GITHUB_ORG_NAME,
    REMINDER_CHANNEL_ID,
)

# Note: Using shared member cache from bot instance instead of creating separate instance
# This ensures test commands use the same cache as the reminder processor


def setup(bot):
    """Register reminder commands with the bot."""
    bot.tree.add_command(send_reminders)
    bot.tree.add_command(test_member_mapping)
    bot.tree.add_command(test_discord_lookup)


@discord.app_commands.command(
    name="test-discord-lookup",
    description="Test Discord user lookup by username.",
)
async def test_discord_lookup(interaction: discord.Interaction, username: str):
    """Test finding a Discord user by username."""
    await interaction.response.defer(ephemeral=True)

    try:
        discord_user = await interaction.client.reminder_processor.find_discord_user(
            username
        )

        if discord_user:
            embed = discord.Embed(
                title="✅ Discord User Found",
                description=f"Successfully found user: **{username}**",
                color=discord.Color.green(),
            )

            embed.add_field(
                name="User Details",
                value=f"• **Username:** {discord_user.name}\n"
                f"• **Global Name:** {getattr(discord_user, 'global_name', 'None')}\n"
                f"• **Display Name:** {getattr(discord_user, 'display_name', 'None')}\n"
                f"• **User ID:** {discord_user.id}\n"
                f"• **Bot:** {'Yes' if discord_user.bot else 'No'}",
                inline=False,
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

            embed.add_field(name="DM Test", value=dm_status, inline=False)

        else:
            embed = discord.Embed(
                title="❌ Discord User Not Found",
                description=f"Could not find user: **{username}**",
                color=discord.Color.red(),
            )

            embed.add_field(
                name="Bot Visibility",
                value=f"• **Cached Users:** {len(interaction.client.users)}\n"
                f"• **Guilds:** {len(interaction.client.guilds)}\n"
                f"• **Total Guild Members:** {sum(len(g.members) for g in interaction.client.guilds)}",
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        embed = discord.Embed(
            title="❌ Test Failed",
            description=f"Error during Discord user lookup test: {e!s}",
            color=discord.Color.red(),
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
        # Fetch mapping using the shared bot cache
        member_cache = interaction.client.member_cache
        github_to_discord = await member_cache.get_mapping()
        cache_info = member_cache.get_cache_info()

        if github_to_discord:
            # Create embed with mapping info
            embed = discord.Embed(
                title="🔗 GitHub → Discord Mapping Test",
                description="Successfully connected to member mapping API!",
                color=discord.Color.green(),
            )

            # Add cache info
            embed.add_field(
                name="📊 Cache Information",
                value=f"• **Mappings Found:** {cache_info['cache_size']}\n"
                f"• **Cache Age:** {cache_info['cache_age_seconds']} seconds\n"
                f"• **Last Updated:** {cache_info['last_fetch']}\n"
                f"• **Cache Valid:** {'✅ Yes' if cache_info['cache_valid'] else '❌ No'}",
                inline=False,
            )

            # Show sample mappings (first 10)
            if github_to_discord:
                sample_mappings = list(github_to_discord.items())[:10]
                mapping_lines = []
                for gh, user_info in sample_mappings:
                    if isinstance(user_info, dict):
                        discord_username = user_info.get("discord_username", "Unknown")
                        real_name = user_info.get("name", "Unknown")
                        mapping_lines.append(
                            f"• `{gh}` → `{discord_username}` ({real_name})"
                        )
                    else:
                        # Fallback for old format
                        mapping_lines.append(f"• `{gh}` → `{user_info}`")

                mapping_text = "\n".join(mapping_lines)

                if len(github_to_discord) > 10:
                    mapping_text += f"\n• ... and {len(github_to_discord) - 10} more"

                embed.add_field(
                    name="👥 Sample Mappings", value=mapping_text, inline=False
                )

            embed.set_footer(text=f"API Endpoint: {member_cache.api_base_url}")

        else:
            embed = discord.Embed(
                title="❌ GitHub → Discord Mapping Test",
                description="No mappings found or API connection failed.",
                color=discord.Color.red(),
            )

            embed.add_field(
                name="📊 Cache Information",
                value=f"• **Cache Size:** {cache_info['cache_size']}\n"
                f"• **Last Fetch:** {cache_info['last_fetch']}\n"
                f"• **API Endpoint:** {member_cache.api_base_url}",
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        embed = discord.Embed(
            title="❌ Member Mapping Test Failed",
            description=f"Error connecting to member mapping API: {e!s}",
            color=discord.Color.red(),
        )
        embed.add_field(
            name="🔧 Troubleshooting",
            value="• Check that the Django API is running\n"
            "• Verify the API endpoint URL\n"
            "• Check network connectivity",
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


@discord.app_commands.command(
    name="send-reminders",
    description=f"Send reminder DMs to users with stale issues/PRs in {GITHUB_ORG_NAME} repositories.",
)
@discord.app_commands.describe(
    target_user="Optional: Send reminders only to this specific Discord user (for testing purposes)."
)
async def send_reminders(
    interaction: discord.Interaction, target_user: discord.User = None
):
    """Send reminder messages for stale issues and PRs using the ReminderProcessor."""
    await interaction.response.defer()

    try:
        # Use the shared reminder processor from the bot instance
        results = await interaction.client.reminder_processor.process_reminders(
            REMINDER_CHANNEL_ID, target_user
        )

        # Check for errors
        if "error" in results:
            await interaction.followup.send(f"❌ {results['error']}", ephemeral=True)
            return

        # Send summary
        total_users = results.get("users_processed", 0)
        target_info = f" (targeting {target_user.mention})" if target_user else ""
        if total_users > 0:
            delivery_stats = results.get("delivery_stats", {})
            summary_parts = [
                f"✅ **Processed {total_users} user(s) with stale items{target_info}:**",
                f"📬 Direct Messages Sent: **{delivery_stats.get('dm_success', 0)}**",
                f"📬 Direct Messages Failed: **{delivery_stats.get('dm_failed', 0)}**",
                f"📢 Channel Messages Sent: **{delivery_stats.get('channel_sent', 0)}**",
                f"📢 Channel Messages Failed: **{delivery_stats.get('channel_failed', 0)}**",
                f"🔍 No Discord Mapping: **{delivery_stats.get('no_mapping', 0)}**",
            ]

            # Calculate how many users got mentioned vs not mentioned
            users_mentioned = delivery_stats.get("dm_failed", 0) + delivery_stats.get(
                "no_mapping", 0
            )
            users_not_mentioned = delivery_stats.get("dm_success", 0)

            if users_mentioned > 0:
                summary_parts.append(
                    f"\n📍 *{users_mentioned} users mentioned in <#{REMINDER_CHANNEL_ID}> (DM failed/no mapping)*"
                )

            if users_not_mentioned > 0:
                summary_parts.append(
                    f"\n💌 *{users_not_mentioned} users received both DM + channel message (not mentioned)*"
                )

            summary_parts.append(
                "\n🎯 *All reminders now sent to both DMs and the channel for better visibility*"
            )

            await interaction.followup.send("\n".join(summary_parts), ephemeral=True)
        else:
            if target_user:
                await interaction.followup.send(
                    f"ℹ️ No stale items found for {target_user.mention}.", ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "ℹ️ No stale items found that require reminders.", ephemeral=True
                )

    except Exception as e:
        await interaction.followup.send(
            f"❌ Error processing reminders: {e!s}", ephemeral=True
        )
