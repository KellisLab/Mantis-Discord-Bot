import discord
import requests
import asyncio
from datetime import datetime, timezone, timedelta
from config import (
    GRAPHQL_URL, 
    HEADERS, 
    GITHUB_ORG_NAME,
    ITEMS_PER_PAGE,
    REMINDER_CHANNEL_ID,
    STALE_ISSUE_DAYS,
    STALE_PR_DAYS,
    MEMBER_MAPPING_CACHE_DURATION,
    DM_RATE_LIMIT_DELAY,
    REMINDER_REPOS
)
from utils.member_mapping import MemberMappingCache
from utils.network import retry_with_exponential_backoff

# Initialize member mapping cache with configuration
member_mapping_cache = MemberMappingCache(
    cache_duration=MEMBER_MAPPING_CACHE_DURATION
)

def truncate_message_if_needed(message: str, max_length: int = 1900) -> str:
    """
    Truncate message if it exceeds Discord's limits.
    Uses 1900 as default to leave room for embeds and other content.
    """
    if len(message) <= max_length:
        return message
    return message[:max_length-3] + "..."

async def make_github_api_request(query: str, variables: dict):
    """Make a GitHub API request with retry logic."""
    async def api_call():
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None, 
            lambda: requests.post(
                GRAPHQL_URL, 
                headers=HEADERS, 
                json={"query": query, "variables": variables},
                timeout=30
            )
        )
        response.raise_for_status()
        return response.json()
    
    success, result, error = await retry_with_exponential_backoff(api_call, max_retries=3, base_delay=1.0)
    if success:
        return result
        raise RuntimeError(f"GitHub API request failed: {error}")

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


def setup(bot):
    """Register reminder commands with the bot."""
    bot.tree.add_command(send_reminders)
    bot.tree.add_command(test_member_mapping)
    bot.tree.add_command(test_discord_lookup)


def is_stale(updated_at_str: str, days_threshold: int) -> bool:
    """Check if an item is stale based on its last update date."""
    try:
        updated_at = datetime.fromisoformat(updated_at_str.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        threshold_date = now - timedelta(days=days_threshold)
        return updated_at < threshold_date
    except (ValueError, AttributeError):
        # If we can't parse the date, consider it stale to be safe
        return True


def get_reminder_reason_text(reason: str, item_type: str) -> str:
    """Convert reminder reason codes to human-readable text."""
    if item_type == "issue":
        reasons = {
            "assigned": "You are assigned to this issue",
            "created": "You created this issue (no assignees)"
        }
    else:  # PR
        reasons = {
            "draft_creator": "You created this draft PR",
            "approved_creator": "You created this approved PR that needs merging",
            "changes_requested_creator": "You created this PR with requested changes",
            "reviewer": "You are requested to review this PR",
            "awaiting_review_creator": "You created this PR awaiting review"
        }
    
    return reasons.get(reason, "Unknown reason")


def determine_issue_reminders(issue) -> list[dict]:
    """Determine who should be reminded about a stale issue and why."""
    reminded_users = []
    
    # Check if issue is stale
    updated_at = issue.get("updatedAt", "")
    if not is_stale(updated_at, STALE_ISSUE_DAYS):
        return reminded_users
    
    # Priority 1: Assignees
    assignees = issue.get("assignees", {}).get("nodes", [])
    if assignees:
        for assignee in assignees:
            if assignee and assignee.get("login"):
                reminded_users.append({
                    "username": assignee["login"],
                    "reason": "assigned"
                })
    else:
        # Priority 2: Creator (if no assignees)
        author = issue.get("author")
        if author and author.get("login"):
            reminded_users.append({
                "username": author["login"],
                "reason": "created"
            })
    
    return reminded_users


def determine_pr_reminders(pr) -> list[dict]:
    """Determine who should be reminded about a stale PR and why."""
    reminded_users = []
    
    # Check if PR is stale
    updated_at = pr.get("updatedAt", "")
    if not is_stale(updated_at, STALE_PR_DAYS):
        return reminded_users
    
    is_draft = pr.get("isDraft", False)
    review_decision = pr.get("reviewDecision", "")
    author = pr.get("author")
    author_login = author.get("login") if author else None
    
    # Get reviewers
    review_requests = pr.get("reviewRequests", {}).get("nodes", [])
    reviewer_logins = []
    for req in review_requests:
        if req and req.get("requestedReviewer"):
            reviewer = req["requestedReviewer"]
            if reviewer.get("login"):  # User reviewer
                reviewer_logins.append(reviewer["login"])
    
    # Apply reminder logic based on PR state
    if is_draft:
        # Remind creator of draft PRs
        if author_login:
            reminded_users.append({
                "username": author_login,
                "reason": "draft_creator"
            })
    elif review_decision == "APPROVED":
        # Remind creator of approved but not merged PRs
        if author_login:
            reminded_users.append({
                "username": author_login,
                "reason": "approved_creator"
            })
    elif review_decision == "CHANGES_REQUESTED":
        # Remind creator when changes are requested
        if author_login:
            reminded_users.append({
                "username": author_login,
                "reason": "changes_requested_creator"
            })
    elif review_decision == "REVIEW_REQUIRED" or not review_decision:
        # Waiting for review
        if reviewer_logins:
            # Remind reviewers if there are specific reviewers
            for reviewer_login in reviewer_logins:
                reminded_users.append({
                    "username": reviewer_login,
                    "reason": "reviewer"
                })
        elif author_login:
            # No specific reviewers, remind creator
            reminded_users.append({
                "username": author_login,
                "reason": "awaiting_review_creator"
            })
    
    return reminded_users


@discord.app_commands.command(
    name="send-reminders",
    description=f"Send reminder DMs to users with stale issues/PRs in {GITHUB_ORG_NAME} repositories.",
)
async def send_reminders(interaction: discord.Interaction):
    """Send reminder messages for stale issues and PRs."""
    await interaction.response.defer()
    
    # Get the fallback channel for users who can't receive DMs
    fallback_channel = interaction.client.get_channel(REMINDER_CHANNEL_ID)
    if not fallback_channel:
        await interaction.followup.send(f"❌ Could not find fallback channel with ID {REMINDER_CHANNEL_ID}", ephemeral=True)
        return
    
    # Fetch GitHub to Discord username mapping
    try:
        github_to_discord = await member_mapping_cache.get_mapping()
        cache_info = member_mapping_cache.get_cache_info()
        print(f"📊 Cache info: {cache_info['cache_size']} mappings, age: {cache_info['cache_age_seconds']}s")
        
        if not github_to_discord:
            await interaction.followup.send("⚠️ Could not fetch member mapping. Falling back to channel-only reminders.", ephemeral=True)
    except Exception as e:
        print(f"❌ Error fetching member mapping: {e}")
        github_to_discord = {}
        await interaction.followup.send("⚠️ Error fetching member mapping. Falling back to channel-only reminders.", ephemeral=True)
    
    repos_to_query = REMINDER_REPOS
    all_user_reminders = {}  # username -> {"issues": [items], "prs": [items]}
    
    # Enhanced GraphQL query for issues with assignees and updatedAt
    issues_query_template = """
    query GetRepoIssues($owner: String!, $name: String!, $first: Int!, $cursor: String) {
      repository(owner: $owner, name: $name) {
        issues(first: $first, after: $cursor, states: OPEN, orderBy: {field: UPDATED_AT, direction: DESC}) {
          pageInfo {
            endCursor
            hasNextPage
          }
          nodes {
            title
            url
            number
            createdAt
            updatedAt
            author {
              login
            }
            assignees(first: 10) {
              nodes {
                login
              }
            }
          }
        }
      }
    }
    """
    
    # Enhanced GraphQL query for PRs with reviewers and updatedAt
    prs_query_template = """
    query GetRepoPRs($owner: String!, $name: String!, $first: Int!, $cursor: String) {
      repository(owner: $owner, name: $name) {
        pullRequests(first: $first, after: $cursor, states: OPEN, orderBy: {field: UPDATED_AT, direction: DESC}) {
          pageInfo {
            endCursor
            hasNextPage
          }
          nodes {
            title
            url
            number
            createdAt
            updatedAt
            isDraft
            author {
              login
            }
            reviewDecision
            reviewRequests(first: 10) {
              nodes {
                requestedReviewer {
                  ... on User {
                    login
                  }
                }
              }
            }
          }
        }
      }
    }
    """
    
    # Process issues
    for repo_name in repos_to_query:
        current_cursor = None
        has_next_page = True
        
        while has_next_page:
            variables = {
                "owner": GITHUB_ORG_NAME,
                "name": repo_name,
                "first": ITEMS_PER_PAGE,
                "cursor": current_cursor,
            }
            
            # Use retry logic for GitHub API request
            try:
                data = await make_github_api_request(issues_query_template, variables)
            except Exception as e:
                await interaction.followup.send(f"❌ Failed to fetch issues from {repo_name}: {e}", ephemeral=True)
                return
            
            if data.get("errors"):
                error_msg = "\n".join([err.get("message", "Unknown error") for err in data["errors"]])
                await interaction.followup.send(f"❌ GraphQL errors for {repo_name} issues: {error_msg}", ephemeral=True)
                return
            
            repository_data = data.get("data", {}).get("repository")
            if not repository_data:
                continue
                
            issues_data = repository_data.get("issues", {})
            page_issues = issues_data.get("nodes", [])
            
            for issue in page_issues:
                if not issue:
                    continue
                    
                issue["repository"] = repo_name
                users_to_remind = determine_issue_reminders(issue)
                
                for user_info in users_to_remind:
                    username = user_info["username"]
                    reason = user_info["reason"]
                    if username not in all_user_reminders:
                        all_user_reminders[username] = {"issues": [], "prs": []}
                    
                    # Add reason to issue data
                    issue_with_reason = issue.copy()
                    issue_with_reason["reminder_reason"] = reason
                    all_user_reminders[username]["issues"].append(issue_with_reason)
            
            page_info = issues_data.get("pageInfo", {})
            has_next_page = page_info.get("hasNextPage", False)
            current_cursor = page_info.get("endCursor")
            
            # Stop fetching if we've gone too far back (optimization)
            if page_issues and len(page_issues) > 0:
                oldest_updated = page_issues[-1].get("updatedAt", "")
                if oldest_updated and not is_stale(oldest_updated, STALE_ISSUE_DAYS * 2):
                    # If the oldest item on this page is newer than 2x our threshold, 
                    # we can stop fetching as subsequent pages will be even newer
                    break
    
    # Process PRs
    for repo_name in repos_to_query:
        current_cursor = None
        has_next_page = True
        
        while has_next_page:
            variables = {
                "owner": GITHUB_ORG_NAME,
                "name": repo_name,
                "first": ITEMS_PER_PAGE,
                "cursor": current_cursor,
            }
            
            # Use retry logic for GitHub API request
            try:
                data = await make_github_api_request(prs_query_template, variables)
            except Exception as e:
                await interaction.followup.send(f"❌ Failed to fetch PRs from {repo_name}: {e}", ephemeral=True)
                return
            
            if data.get("errors"):
                error_msg = "\n".join([err.get("message", "Unknown error") for err in data["errors"]])
                await interaction.followup.send(f"❌ GraphQL errors for {repo_name} PRs: {error_msg}", ephemeral=True)
                return
            
            repository_data = data.get("data", {}).get("repository")
            if not repository_data:
                continue
                
            prs_data = repository_data.get("pullRequests", {})
            page_prs = prs_data.get("nodes", [])
            
            for pr in page_prs:
                if not pr:
                    continue
                    
                pr["repository"] = repo_name
                users_to_remind = determine_pr_reminders(pr)
                
                for user_info in users_to_remind:
                    username = user_info["username"]
                    reason = user_info["reason"]
                    if username not in all_user_reminders:
                        all_user_reminders[username] = {"issues": [], "prs": []}
                    
                    # Add reason to PR data
                    pr_with_reason = pr.copy()
                    pr_with_reason["reminder_reason"] = reason
                    all_user_reminders[username]["prs"].append(pr_with_reason)
            
            page_info = prs_data.get("pageInfo", {})
            has_next_page = page_info.get("hasNextPage", False)
            current_cursor = page_info.get("endCursor")
            
            # Stop fetching if we've gone too far back (optimization)
            if page_prs and len(page_prs) > 0:
                oldest_updated = page_prs[-1].get("updatedAt", "")
                if oldest_updated and not is_stale(oldest_updated, STALE_PR_DAYS * 2):
                    break
    
    # Send individual reminder messages (DMs + channel messages)
    delivery_stats = {
        "dm_success": 0,
        "dm_failed": 0,
        "channel_sent": 0,
        "channel_failed": 0,
        "no_mapping": 0
    }
    
    async def send_dm_to_user(user, content):
        """Send DM with retry logic for rate limits."""
        async def dm_send():
            await user.send(content)
            return True
        
        success, result, error = await retry_with_exponential_backoff(dm_send, max_retries=3, base_delay=0.5)
        return success, error

    async def send_channel_message(channel, content):
        """Send channel message with retry logic."""
        async def channel_send():
            await channel.send(content)
            return True
        
        success, result, error = await retry_with_exponential_backoff(channel_send, max_retries=3, base_delay=0.5)
        return success, error

    async def create_channel_message_content(github_username, discord_username, issues, prs, should_mention=True, discord_user_obj=None):
        """Create message content for channel with appropriate mentioning logic."""
        # Get real name from member mapping
        real_name = member_mapping_cache.get_real_name(github_username)
        name_display = f" ({real_name})" if real_name else ""
        
        if discord_username and should_mention:
            # Use the already-found Discord user object if provided, otherwise find it
            if discord_user_obj:
                print(f"🎯 Using provided Discord user object for mention: {discord_user_obj.name} (ID: {discord_user_obj.id})")
                header = f"🔔 **{discord_user_obj.mention}**{name_display} (GitHub: @{github_username})"
            else:
                print(f"🔍 No Discord user object provided, searching for: {discord_username}")
                # Find the actual Discord user to mention
                discord_user = await find_discord_user(interaction.client, discord_username)
                
                if discord_user:
                    print(f"✅ Found Discord user for mention: {discord_user.name} (ID: {discord_user.id})")
                    header = f"🔔 **{discord_user.mention}**{name_display} (GitHub: @{github_username})"
                else:
                    print(f"❌ Could not find Discord user {discord_username} for mention, using plain text")
                    header = f"🔔 **@{discord_username}**{name_display} (GitHub: @{github_username})"
        elif discord_username:
            header = f"🔔 **{discord_username}**{name_display} (GitHub: @{github_username})"
        else:
            header = f"🔔 **GitHub user @{github_username}**{name_display} (no Discord mapping)"
        
        message_parts = [header]
        
        if issues:
            message_parts.append(f"\n**📝 Stale Issues ({len(issues)}):**")
            for issue in issues[:5]:  # Limit to 5 issues per user
                title = issue.get("title", "Untitled")
                number = issue.get("number", "")
                url = issue.get("url", "")
                repo = issue.get("repository", "")
                reason = issue.get("reminder_reason", "")
                reason_text = get_reminder_reason_text(reason, "issue")
                
                if len(title) > 50:
                    title = title[:47] + "..."
                
                if url and number:
                    message_parts.append(f"• [{repo}#{number}]({url}) {title}")
                    message_parts.append(f"  *{reason_text}*")
                else:
                    message_parts.append(f"• {repo}#{number} {title}")
                    message_parts.append(f"  *{reason_text}*")
            
            if len(issues) > 5:
                message_parts.append(f"• ... and {len(issues) - 5} more issues")
        
        if prs:
            message_parts.append(f"\n**🔄 Stale Pull Requests ({len(prs)}):**")
            for pr in prs[:5]:  # Limit to 5 PRs per user
                title = pr.get("title", "Untitled")
                number = pr.get("number", "")
                url = pr.get("url", "")
                repo = pr.get("repository", "")
                is_draft = pr.get("isDraft", False)
                review_decision = pr.get("reviewDecision", "")
                reason = pr.get("reminder_reason", "")
                reason_text = get_reminder_reason_text(reason, "pr")
                
                if len(title) > 50:
                    title = title[:47] + "..."
                
                status_emoji = ""
                if is_draft:
                    status_emoji = "🚧"
                elif review_decision == "APPROVED":
                    status_emoji = "✅"
                elif review_decision == "CHANGES_REQUESTED":
                    status_emoji = "🔄"
                else:
                    status_emoji = "👀"
                
                if url and number:
                    message_parts.append(f"• {status_emoji} [{repo}#{number}]({url}) {title}")
                    message_parts.append(f"  *{reason_text}*")
                else:
                    message_parts.append(f"• {status_emoji} {repo}#{number} {title}")
                    message_parts.append(f"  *{reason_text}*")
            
            if len(prs) > 5:
                message_parts.append(f"• ... and {len(prs) - 5} more PRs")
        
        # message_parts.append(f"\n*Issues stale after {STALE_ISSUE_DAYS} days, PRs after {STALE_PR_DAYS} days of inactivity.*")
        message_parts.append("--------------------------------")
        return "\n".join(message_parts)

    def create_dm_message_content(github_username, discord_username, issues, prs):
        """Create personalized message content for DM."""
        # Get real name from member mapping
        real_name = member_mapping_cache.get_real_name(github_username)
        name_display = f" ({real_name})" if real_name else ""
        
        message_parts = [f"🔔 **Hello {discord_username}!{name_display} You have reminders from GitHub (@{github_username})**"]
        
        if issues:
            message_parts.append(f"\n**📝 Stale Issues ({len(issues)}):**")
            for issue in issues[:5]:  # Limit to 5 issues per user
                title = issue.get("title", "Untitled")
                number = issue.get("number", "")
                url = issue.get("url", "")
                repo = issue.get("repository", "")
                reason = issue.get("reminder_reason", "")
                reason_text = get_reminder_reason_text(reason, "issue")
                
                if len(title) > 50:
                    title = title[:47] + "..."
                
                if url and number:
                    message_parts.append(f"• [{repo}#{number}]({url}) {title}")
                    message_parts.append(f"  *{reason_text}*")
                else:
                    message_parts.append(f"• {repo}#{number} {title}")
                    message_parts.append(f"  *{reason_text}*")
            
            if len(issues) > 5:
                message_parts.append(f"• ... and {len(issues) - 5} more issues")
        
        if prs:
            message_parts.append(f"\n**🔄 Stale Pull Requests ({len(prs)}):**")
            for pr in prs[:5]:  # Limit to 5 PRs per user
                title = pr.get("title", "Untitled")
                number = pr.get("number", "")
                url = pr.get("url", "")
                repo = pr.get("repository", "")
                is_draft = pr.get("isDraft", False)
                review_decision = pr.get("reviewDecision", "")
                reason = pr.get("reminder_reason", "")
                reason_text = get_reminder_reason_text(reason, "pr")
                
                if len(title) > 50:
                    title = title[:47] + "..."
                
                status_emoji = ""
                if is_draft:
                    status_emoji = "🚧"
                elif review_decision == "APPROVED":
                    status_emoji = "✅"
                elif review_decision == "CHANGES_REQUESTED":
                    status_emoji = "🔄"
                else:
                    status_emoji = "👀"
                
                if url and number:
                    message_parts.append(f"• {status_emoji} [{repo}#{number}]({url}) {title}")
                    message_parts.append(f"  *{reason_text}*")
                else:
                    message_parts.append(f"• {status_emoji} {repo}#{number} {title}")
                    message_parts.append(f"  *{reason_text}*")
            
            if len(prs) > 5:
                message_parts.append(f"• ... and {len(prs) - 5} more PRs")
        
        message_parts.append(f"\n*Issues stale after {STALE_ISSUE_DAYS} days, PRs after {STALE_PR_DAYS} days of inactivity.*")
        return "\n".join(message_parts)
    
    for github_username, items in all_user_reminders.items():
        issues = items["issues"]
        prs = items["prs"]
        
        if not issues and not prs:
            continue
        
        # Get Discord username mapping using the new format
        discord_username = member_mapping_cache.get_discord_username(github_username)
        
        dm_success = False
        dm_error = ""
        discord_user = None  # Store the Discord user object to reuse for channel mentions
        
        if discord_username:
            # Try to send DM first
            discord_user = await find_discord_user(interaction.client, discord_username)
            
            if discord_user:
                dm_content = create_dm_message_content(github_username, discord_username, issues, prs)
                dm_content = truncate_message_if_needed(dm_content)
                
                dm_success, dm_error = await send_dm_to_user(discord_user, dm_content)
                
                if dm_success:
                    delivery_stats["dm_success"] += 1
                    print(f"✅ Sent DM to {discord_username} (GitHub: {github_username})")
                else:
                    delivery_stats["dm_failed"] += 1
                    print(f"❌ Failed to send DM to {discord_username} (GitHub: {github_username}): {dm_error}")
        else:
            delivery_stats["no_mapping"] += 1
        
        # Always send to channel
        # If DM succeeded, don't mention the user in channel
        # If DM failed or no mapping, mention the user in channel (fallback behavior)
        should_mention = not dm_success
        
        channel_content = await create_channel_message_content(
            github_username, discord_username, issues, prs, should_mention, discord_user
        )
        channel_content = truncate_message_if_needed(channel_content)
        
        channel_success, channel_error = await send_channel_message(fallback_channel, channel_content)
        
        if channel_success:
            delivery_stats["channel_sent"] += 1
            if should_mention:
                print(f"📢 Sent channel reminder (mentioned) for {discord_username or github_username} (GitHub: {github_username})")
            else:
                print(f"📢 Sent channel reminder (no mention) for {discord_username} (GitHub: {github_username})")
        else:
            delivery_stats["channel_failed"] += 1
            print(f"❌ Failed to send channel reminder for {github_username}: {channel_error}")
        
        # Rate limiting to avoid Discord limits
        if dm_success:
            await asyncio.sleep(DM_RATE_LIMIT_DELAY)
    
    # Send summary
    total_users = len(all_user_reminders)
    if total_users > 0:
        summary_parts = [
            f"✅ **Processed {total_users} user(s) with stale items:**",
            f"📬 Direct Messages Sent: **{delivery_stats['dm_success']}**",
            f"📬 Direct Messages Failed: **{delivery_stats['dm_failed']}**",
            f"📢 Channel Messages Sent: **{delivery_stats['channel_sent']}**",
            f"📢 Channel Messages Failed: **{delivery_stats['channel_failed']}**",
            f"🔍 No Discord Mapping: **{delivery_stats['no_mapping']}**"
        ]
        
        # Calculate how many users got mentioned vs not mentioned
        users_mentioned = delivery_stats["dm_failed"] + delivery_stats["no_mapping"]
        users_not_mentioned = delivery_stats["dm_success"]
        
        if users_mentioned > 0:
            summary_parts.append(f"\n📍 *{users_mentioned} users mentioned in <#{REMINDER_CHANNEL_ID}> (DM failed/no mapping)*")
        
        if users_not_mentioned > 0:
            summary_parts.append(f"\n💌 *{users_not_mentioned} users received both DM + channel message (not mentioned)*")
            
        summary_parts.append("\n🎯 *All reminders now sent to both DMs and the channel for better visibility*")
            
        await interaction.followup.send("\n".join(summary_parts), ephemeral=True)
    else:
        await interaction.followup.send("ℹ️ No stale items found that require reminders.", ephemeral=True) 