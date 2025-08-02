import discord
import requests
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
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
from .member_mapping import MemberMappingCache
from .network import retry_with_exponential_backoff


class ReminderProcessor:
    """Handles the core reminder processing logic for stale GitHub issues and PRs."""
    
    def __init__(self, bot: discord.Client, member_cache: MemberMappingCache = None):
        """Initialize the ReminderProcessor.
        
        Args:
            bot: Discord bot/client instance
            member_cache: Optional shared MemberMappingCache instance
        """
        self.bot = bot
        self.member_cache = member_cache if member_cache is not None else MemberMappingCache(
            cache_duration=MEMBER_MAPPING_CACHE_DURATION
        )
    
    def truncate_message_if_needed(self, message: str, max_length: int = 1900) -> str:
        """Truncate message if it exceeds Discord's limits."""
        if len(message) <= max_length:
            return message
        return message[:max_length-3] + "..."
    
    async def make_github_api_request(self, query: str, variables: dict):
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
    
    async def find_discord_user(self, discord_username: str):
        """Find a Discord user by username across all guilds the bot can see."""
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
        
        # Search through bot's cached users
        for user in self.bot.users:
            if matches_username(user, discord_username):
                return user
                
        # Search through all guild members
        for guild in self.bot.guilds:
            for member in guild.members:
                if matches_username(member, discord_username):
                    return member
        
        return None
    
    def is_stale(self, updated_at_str: str, days_threshold: int) -> bool:
        """Check if an item is stale based on its last update date."""
        try:
            updated_at = datetime.fromisoformat(updated_at_str.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            threshold_date = now - timedelta(days=days_threshold)
            return updated_at < threshold_date
        except (ValueError, AttributeError):
            return True
    
    def get_reminder_reason_text(self, reason: str, item_type: str) -> str:
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
    
    def determine_issue_reminders(self, issue) -> List[Dict]:
        """Determine who should be reminded about a stale issue and why."""
        reminded_users = []
        
        updated_at = issue.get("updatedAt", "")
        if not self.is_stale(updated_at, STALE_ISSUE_DAYS):
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
    
    def determine_pr_reminders(self, pr) -> List[Dict]:
        """Determine who should be reminded about a stale PR and why."""
        reminded_users = []
        
        updated_at = pr.get("updatedAt", "")
        if not self.is_stale(updated_at, STALE_PR_DAYS):
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
            if author_login:
                reminded_users.append({
                    "username": author_login,
                    "reason": "draft_creator"
                })
        elif review_decision == "APPROVED":
            if author_login:
                reminded_users.append({
                    "username": author_login,
                    "reason": "approved_creator"
                })
        elif review_decision == "CHANGES_REQUESTED":
            if author_login:
                reminded_users.append({
                    "username": author_login,
                    "reason": "changes_requested_creator"
                })
        elif review_decision == "REVIEW_REQUIRED" or not review_decision:
            if reviewer_logins:
                for reviewer_login in reviewer_logins:
                    reminded_users.append({
                        "username": reviewer_login,
                        "reason": "reviewer"
                    })
            elif author_login:
                reminded_users.append({
                    "username": author_login,
                    "reason": "awaiting_review_creator"
                })
        
        return reminded_users
    
    async def send_dm_to_user(self, user, content):
        """Send DM with retry logic for rate limits."""
        async def dm_send():
            await user.send(content)
            return True
        
        success, result, error = await retry_with_exponential_backoff(dm_send, max_retries=3, base_delay=0.5)
        return success, error
    
    async def send_channel_message(self, channel, content):
        """Send channel message with retry logic."""
        async def channel_send():
            await channel.send(content)
            return True
        
        success, result, error = await retry_with_exponential_backoff(channel_send, max_retries=3, base_delay=0.5)
        return success, error
    
    def create_dm_message_content(self, github_username: str, discord_username: str, issues: List, prs: List) -> str:
        """Create personalized message content for DM."""
        real_name = self.member_cache.get_real_name(github_username)
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
                reason_text = self.get_reminder_reason_text(reason, "issue")
                
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
                reason_text = self.get_reminder_reason_text(reason, "pr")
                
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
    
    async def create_channel_message_content(self, github_username: str, discord_username: str, 
                                           issues: List, prs: List, should_mention: bool = True, 
                                           discord_user_obj=None) -> str:
        """Create message content for channel with appropriate mentioning logic."""
        real_name = self.member_cache.get_real_name(github_username)
        name_display = f" ({real_name})" if real_name else ""
        
        if discord_username and should_mention:
            if discord_user_obj:
                header = f"🔔 **{discord_user_obj.mention}**{name_display} (GitHub: @{github_username})"
            else:
                discord_user = await self.find_discord_user(discord_username)
                if discord_user:
                    header = f"🔔 **{discord_user.mention}**{name_display} (GitHub: @{github_username})"
                else:
                    header = f"🔔 **@{discord_username}**{name_display} (GitHub: @{github_username})"
        elif discord_username:
            header = f"🔔 **{discord_username}**{name_display} (GitHub: @{github_username})"
        else:
            header = f"🔔 **GitHub user @{github_username}**{name_display} (no Discord mapping)"
        
        message_parts = [header]
        
        if issues:
            message_parts.append(f"\n**📝 Stale Issues ({len(issues)}):**")
            for issue in issues[:5]:
                title = issue.get("title", "Untitled")
                number = issue.get("number", "")
                url = issue.get("url", "")
                repo = issue.get("repository", "")
                reason = issue.get("reminder_reason", "")
                reason_text = self.get_reminder_reason_text(reason, "issue")
                
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
            for pr in prs[:5]:
                title = pr.get("title", "Untitled")
                number = pr.get("number", "")
                url = pr.get("url", "")
                repo = pr.get("repository", "")
                is_draft = pr.get("isDraft", False)
                review_decision = pr.get("reviewDecision", "")
                reason = pr.get("reminder_reason", "")
                reason_text = self.get_reminder_reason_text(reason, "pr")
                
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
        
        message_parts.append("--------------------------------")
        return "\n".join(message_parts)
    
    async def process_reminders(self, fallback_channel_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Process reminders for all users with stale GitHub issues and PRs.
        
        Args:
            fallback_channel_id: Optional channel ID to send fallback messages. 
                                Uses REMINDER_CHANNEL_ID if not provided.
        
        Returns:
            Dictionary with processing statistics and results
        """
        # Get the fallback channel
        channel_id = fallback_channel_id if fallback_channel_id is not None else REMINDER_CHANNEL_ID
        fallback_channel = self.bot.get_channel(channel_id)
        if not fallback_channel:
            return {"error": f"Could not find fallback channel with ID {channel_id}"}
        
        # Fetch GitHub to Discord username mapping
        try:
            print("🔄 Fetching GitHub to Discord username mapping...")
            github_to_discord = await self.member_cache.get_mapping()
            cache_info = self.member_cache.get_cache_info()
            print(f"📊 Cache info: {cache_info['cache_size']} mappings, age: {cache_info['cache_age_seconds']}s")
            
            if not github_to_discord:
                print("⚠️ Warning: No GitHub to Discord mappings found")
        except Exception as e:
            print(f"❌ Error fetching member mapping: {e}")
            # Continue processing anyway, but users without mappings won't get DMs
        
        all_user_reminders = {}  # username -> {"issues": [items], "prs": [items]}
        
        # GraphQL queries (same as in original command)
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
        for repo_name in REMINDER_REPOS:
            current_cursor = None
            has_next_page = True
            
            while has_next_page:
                variables = {
                    "owner": GITHUB_ORG_NAME,
                    "name": repo_name,
                    "first": ITEMS_PER_PAGE,
                    "cursor": current_cursor,
                }
                
                try:
                    data = await self.make_github_api_request(issues_query_template, variables)
                except Exception as e:
                    print(f"❌ Failed to fetch issues from {repo_name}: {e}")
                    continue
                
                if data.get("errors"):
                    print(f"❌ GraphQL errors for {repo_name} issues")
                    continue
                
                repository_data = data.get("data", {}).get("repository")
                if not repository_data:
                    continue
                    
                issues_data = repository_data.get("issues", {})
                page_issues = issues_data.get("nodes", [])
                
                for issue in page_issues:
                    if not issue:
                        continue
                        
                    issue["repository"] = repo_name
                    users_to_remind = self.determine_issue_reminders(issue)
                    
                    for user_info in users_to_remind:
                        username = user_info["username"]
                        reason = user_info["reason"]
                        if username not in all_user_reminders:
                            all_user_reminders[username] = {"issues": [], "prs": []}
                        
                        issue_with_reason = issue.copy()
                        issue_with_reason["reminder_reason"] = reason
                        all_user_reminders[username]["issues"].append(issue_with_reason)
                
                page_info = issues_data.get("pageInfo", {})
                has_next_page = page_info.get("hasNextPage", False)
                current_cursor = page_info.get("endCursor")
                
                # Stop fetching if we've gone too far back
                if page_issues and len(page_issues) > 0:
                    oldest_updated = page_issues[-1].get("updatedAt", "")
                    if oldest_updated and not self.is_stale(oldest_updated, STALE_ISSUE_DAYS * 2):
                        break
        
        # Process PRs (similar logic)
        for repo_name in REMINDER_REPOS:
            current_cursor = None
            has_next_page = True
            
            while has_next_page:
                variables = {
                    "owner": GITHUB_ORG_NAME,
                    "name": repo_name,
                    "first": ITEMS_PER_PAGE,
                    "cursor": current_cursor,
                }
                
                try:
                    data = await self.make_github_api_request(prs_query_template, variables)
                except Exception as e:
                    print(f"❌ Failed to fetch PRs from {repo_name}: {e}")
                    continue
                
                if data.get("errors"):
                    print(f"❌ GraphQL errors for {repo_name} PRs")
                    continue
                
                repository_data = data.get("data", {}).get("repository")
                if not repository_data:
                    continue
                    
                prs_data = repository_data.get("pullRequests", {})
                page_prs = prs_data.get("nodes", [])
                
                for pr in page_prs:
                    if not pr:
                        continue
                        
                    pr["repository"] = repo_name
                    users_to_remind = self.determine_pr_reminders(pr)
                    
                    for user_info in users_to_remind:
                        username = user_info["username"]
                        reason = user_info["reason"]
                        if username not in all_user_reminders:
                            all_user_reminders[username] = {"issues": [], "prs": []}
                        
                        pr_with_reason = pr.copy()
                        pr_with_reason["reminder_reason"] = reason
                        all_user_reminders[username]["prs"].append(pr_with_reason)
                
                page_info = prs_data.get("pageInfo", {})
                has_next_page = page_info.get("hasNextPage", False)
                current_cursor = page_info.get("endCursor")
                
                # Stop fetching if we've gone too far back
                if page_prs and len(page_prs) > 0:
                    oldest_updated = page_prs[-1].get("updatedAt", "")
                    if oldest_updated and not self.is_stale(oldest_updated, STALE_PR_DAYS * 2):
                        break
        
        # Send reminders
        delivery_stats = {
            "dm_success": 0,
            "dm_failed": 0,
            "channel_sent": 0,
            "channel_failed": 0,
            "no_mapping": 0
        }
        
        for github_username, items in all_user_reminders.items():
            issues = items["issues"]
            prs = items["prs"]
            
            if not issues and not prs:
                continue
            
            discord_username = self.member_cache.get_discord_username(github_username)
            
            dm_success = False
            dm_error = ""
            discord_user = None
            
            if discord_username:
                discord_user = await self.find_discord_user(discord_username)
                
                if discord_user:
                    dm_content = self.create_dm_message_content(github_username, discord_username, issues, prs)
                    dm_content = self.truncate_message_if_needed(dm_content)
                    
                    dm_success, dm_error = await self.send_dm_to_user(discord_user, dm_content)
                    
                    if dm_success:
                        delivery_stats["dm_success"] += 1
                    else:
                        delivery_stats["dm_failed"] += 1
            else:
                delivery_stats["no_mapping"] += 1
            
            # Always send to channel
            should_mention = not dm_success
            
            channel_content = await self.create_channel_message_content(
                github_username, discord_username, issues, prs, should_mention, discord_user
            )
            channel_content = self.truncate_message_if_needed(channel_content)
            
            channel_success, channel_error = await self.send_channel_message(fallback_channel, channel_content)
            
            if channel_success:
                delivery_stats["channel_sent"] += 1
            else:
                delivery_stats["channel_failed"] += 1
            
            # Rate limiting
            if dm_success:
                await asyncio.sleep(DM_RATE_LIMIT_DELAY)
        
        # Return results
        total_users = len(all_user_reminders)
        return {
            "users_processed": total_users,
            "delivery_stats": delivery_stats
        }