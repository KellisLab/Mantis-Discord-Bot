import discord
import requests
import asyncio
import random
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
)
from utils.member_mapping import MemberMappingCache

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

async def retry_with_exponential_backoff(func, max_retries: int = 3, base_delay: float = 1.0):
    """
    Retry a function with exponential backoff for transient failures.
    Returns (success: bool, result: any, error: str)
    """
    for attempt in range(max_retries):
        try:
            result = await func()
            return True, result, ""
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                return False, None, str(e)
            
            # Check if it's a rate limit error (status 403 or 429)
            if hasattr(e, 'response') and e.response is not None:
                if e.response.status_code in [403, 429]:
                    # For rate limits, wait longer
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                    await asyncio.sleep(delay)
                    continue
            
            # For other errors, shorter delay
            delay = base_delay * (1.5 ** attempt) + random.uniform(0, 0.5)
            await asyncio.sleep(delay)
        except Exception as e:
            if attempt == max_retries - 1:
                return False, None, str(e)
            delay = base_delay * (1.5 ** attempt)
            await asyncio.sleep(delay)
    
    return False, None, "Max retries exceeded"

async def make_github_api_request(query: str, variables: dict):
    """Make a GitHub API request with retry logic."""
    async def api_call():
        loop = asyncio.get_event_loop()
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
    
    return await retry_with_exponential_backoff(api_call, max_retries=3, base_delay=1.0)

async def find_discord_user(bot, discord_username: str):
    """
    Find a Discord user by username across all guilds the bot can see.
    Returns the User object if found, None otherwise.
    """
    # Method 1: Search through bot's cached users
    for user in bot.users:
        if user.name.lower() == discord_username.lower():
            return user
            
    # Method 2: Search through all guild members
    for guild in bot.guilds:
        for member in guild.members:
            if member.name.lower() == discord_username.lower():
                return member
                
    return None

async def send_dm_or_fallback(bot, github_username: str, discord_username: str, message_content: str, fallback_channel):
    """
    Attempt to send a DM to a user, fallback to channel mention if it fails.
    Includes retry logic and message length validation.
    Returns (success: bool, method: str, error: str)
    """
    # Validate and truncate message length
    message_content = truncate_message_if_needed(message_content)
    
    async def send_dm_with_retry(user, content):
        """Send DM with retry logic for rate limits."""
        async def dm_send():
            await user.send(content)
            return True
        
        success, result, error = await retry_with_exponential_backoff(dm_send, max_retries=3, base_delay=0.5)
        return success, error
    
    async def send_channel_with_retry(channel, content):
        """Send channel message with retry logic."""
        async def channel_send():
            await channel.send(content)
            return True
        
        success, result, error = await retry_with_exponential_backoff(channel_send, max_retries=3, base_delay=0.5)
        return success, error
    
    try:
        discord_user = await find_discord_user(bot, discord_username)
        
        if discord_user:
            # Try to send DM with retry
            dm_success, dm_error = await send_dm_with_retry(discord_user, message_content)
            
            if dm_success:
                return True, "DM", ""
            elif "403" in str(dm_error) or "Forbidden" in str(dm_error):
                # User has DMs disabled, fallback to channel mention
                fallback_message = f"**{discord_user.mention}** (DMs disabled)\n{message_content}"
                fallback_message = truncate_message_if_needed(fallback_message)
                
                channel_success, channel_error = await send_channel_with_retry(fallback_channel, fallback_message)
                if channel_success:
                    return True, "Channel (DMs disabled)", ""
                else:
                    return False, "Failed", f"DM disabled, channel fallback failed: {channel_error}"
            else:
                # Other Discord API error, fallback to channel
                fallback_message = f"**@{discord_username}** (failed to DM: {str(dm_error)})\n{message_content}"
                fallback_message = truncate_message_if_needed(fallback_message)
                
                channel_success, channel_error = await send_channel_with_retry(fallback_channel, fallback_message)
                if channel_success:
                    return True, "Channel (DM failed)", str(dm_error)
                else:
                    return False, "Failed", f"DM failed: {dm_error}, channel fallback failed: {channel_error}"
        else:
            # User not found in any guild, fallback to channel mention
            fallback_message = f"**@{discord_username}** (not found in server)\n{message_content}"
            fallback_message = truncate_message_if_needed(fallback_message)
            
            channel_success, channel_error = await send_channel_with_retry(fallback_channel, fallback_message)
            if channel_success:
                return True, "Channel (user not found)", "User not found in server"
            else:
                return False, "Failed", f"User not found, channel fallback failed: {channel_error}"
            
    except Exception as e:
        # Unexpected error, try basic channel fallback
        fallback_message = f"**@{discord_username}** (error occurred)\n{message_content}"
        fallback_message = truncate_message_if_needed(fallback_message)
        
        try:
            channel_success, channel_error = await send_channel_with_retry(fallback_channel, fallback_message)
            if channel_success:
                return True, "Channel (error)", str(e)
            else:
                return False, "Failed", f"Unexpected error: {str(e)}, channel fallback failed: {channel_error}"
        except Exception as fallback_error:
            return False, "Failed", f"Unexpected error: {str(e)}, Fallback error: {str(fallback_error)}"


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
                mapping_text = "\n".join([f"• `{gh}` → `{dc}`" for gh, dc in sample_mappings])
                
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
    
    repos_to_query = ["Mantis", "MantisAPI"]
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
            success, data, error = await make_github_api_request(issues_query_template, variables)
            if not success:
                await interaction.followup.send(f"❌ Failed to fetch issues from {repo_name}: {error}", ephemeral=True)
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
            success, data, error = await make_github_api_request(prs_query_template, variables)
            if not success:
                await interaction.followup.send(f"❌ Failed to fetch PRs from {repo_name}: {error}", ephemeral=True)
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
    
    # Send individual reminder messages (DMs with fallback to channel)
    delivery_stats = {
        "dm_success": 0,
        "channel_fallback": 0,
        "failed": 0,
        "no_mapping": 0
    }
    
    for github_username, items in all_user_reminders.items():
        issues = items["issues"]
        prs = items["prs"]
        
        if not issues and not prs:
            continue
        
        # Get Discord username mapping
        discord_username = github_to_discord.get(github_username)
        
        if not discord_username:
            # No Discord mapping found, send to channel with GitHub username
            delivery_stats["no_mapping"] += 1
            
            # Create message content for unmapped users
            message_parts = [f"🔔 **Reminder for GitHub user @{github_username}** (no Discord mapping)"]
            
            # Build message content (same logic as below)
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
            message_content = "\n".join(message_parts)
            message_content = truncate_message_if_needed(message_content)
            
            try:
                await fallback_channel.send(message_content)
            except discord.errors.HTTPException as e:
                print(f"❌ Failed to send fallback reminder for {github_username}: {e}")
                delivery_stats["failed"] += 1
                continue
        else:
            # Create message content for DM (personalized)
            message_parts = [f"🔔 **Hello {discord_username}! You have reminders from GitHub (@{github_username})**"]
            
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
            message_content = "\n".join(message_parts)
            # Note: message_content will be truncated in send_dm_or_fallback function
            
            # Attempt to send DM or fallback to channel
            success, method, error = await send_dm_or_fallback(
                interaction.client, github_username, discord_username, message_content, fallback_channel
            )
            
            if success:
                if "DM" in method:
                    delivery_stats["dm_success"] += 1
                    print(f"✅ Sent DM to {discord_username} (GitHub: {github_username})")
                else:
                    delivery_stats["channel_fallback"] += 1
                    print(f"📢 Sent channel fallback for {discord_username} (GitHub: {github_username}): {method}")
            else:
                delivery_stats["failed"] += 1
                print(f"❌ Failed to send reminder for {github_username} -> {discord_username}: {error}")
        
        # Rate limiting to avoid Discord limits
        if delivery_stats["dm_success"] > 0:
            await asyncio.sleep(DM_RATE_LIMIT_DELAY)
    
    # Send summary
    total_reminders = sum(delivery_stats.values())
    if total_reminders > 0:
        summary_parts = [
            f"✅ **Sent {total_reminders} reminder(s):**",
            f"📬 Direct Messages: **{delivery_stats['dm_success']}**",
            f"📢 Channel Fallbacks: **{delivery_stats['channel_fallback']}**",
            f"❌ Failed: **{delivery_stats['failed']}**",
            f"🔍 No Discord Mapping: **{delivery_stats['no_mapping']}**"
        ]
        
        if delivery_stats["channel_fallback"] > 0 or delivery_stats["no_mapping"] > 0:
            summary_parts.append(f"\n📍 *Fallback messages sent to <#{REMINDER_CHANNEL_ID}>*")
        
        if delivery_stats["dm_success"] > 0:
            summary_parts.append(f"\n💌 *{delivery_stats['dm_success']} users received direct messages*")
            
        await interaction.followup.send("\n".join(summary_parts), ephemeral=True)
    else:
        await interaction.followup.send("ℹ️ No stale items found that require reminders.", ephemeral=True) 