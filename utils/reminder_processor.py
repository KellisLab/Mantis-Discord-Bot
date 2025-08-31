import discord
import requests
import asyncio
import io
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
from PIL import Image, ImageDraw, ImageFont
import textwrap
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
    
    def create_item_summary_image(self, item: Dict, item_type: str) -> io.BytesIO:
        """Create a visual summary image for an issue or PR with its content and recent comments."""
        try:
            # Image dimensions and styling
            img_width = 800
            img_height = 600
            background_color = (255, 255, 255)  # White
            text_color = (33, 37, 41)  # Dark gray
            header_color = (0, 123, 255)  # Blue
            comment_bg_color = (248, 249, 250)  # Light gray
            
            # Create image and drawing context
            image = Image.new('RGB', (img_width, img_height), background_color)
            draw = ImageDraw.Draw(image)
            
            # Try to load a font, fall back to default if not available
            try:
                title_font = ImageFont.truetype("arial.ttf", 16)
                header_font = ImageFont.truetype("arial.ttf", 14)
                body_font = ImageFont.truetype("arial.ttf", 12)
                comment_font = ImageFont.truetype("arial.ttf", 11)
            except:
                title_font = ImageFont.load_default()
                header_font = ImageFont.load_default()
                body_font = ImageFont.load_default()
                comment_font = ImageFont.load_default()
            
            y_offset = 20
            padding = 20
            
            # Helper function to draw wrapped text
            def draw_wrapped_text(text, font, color, max_width, start_y):
                if not text:
                    return start_y
                
                # Clean and truncate text
                text = text.replace('\r\n', '\n').replace('\r', '\n')
                if len(text) > 2000:  # Limit text length
                    text = text[:2000] + "..."
                
                lines = []
                for paragraph in text.split('\n'):
                    if not paragraph.strip():
                        lines.append("") # Preserve empty lines
                        continue
                    
                    words = paragraph.split(' ')
                    current_line = ""
                    for word in words:
                        # Check if adding the next word exceeds the max width
                        if draw.textbbox((0,0), current_line + word + " ", font=font)[2] > max_width:
                            lines.append(current_line)
                            current_line = ""
                        current_line += word + " "
                    lines.append(current_line.strip())

                current_y = start_y
                for line in lines[:15]:  # Limit to 15 lines
                    if current_y > img_height - 50:  # Stop if we're running out of space
                        draw.text((padding, current_y), "...", font=font, fill=color)
                        break
                    draw.text((padding, current_y), line, font=font, fill=color)
                    current_y += 18
                
                return current_y + 10
            
            # Title and metadata
            repo_name = item.get("repository", "Unknown")
            number = item.get("number", "")
            title = item.get("title", "Untitled")
            author = item.get("author", {}).get("login", "Unknown")
            
            emoji = "📝" if item_type == "issue" else "🔄"
            header_text = f"{emoji} {repo_name}#{number}: {title}"
            
            # Draw header with background
            header_bbox = draw.textbbox((0, 0), header_text, font=title_font)
            header_height = header_bbox[3] - header_bbox[1] + 20
            draw.rectangle([0, 0, img_width, header_height], fill=header_color)
            draw.text((padding, 10), header_text, font=title_font, fill=(255, 255, 255))
            
            y_offset = header_height + 20
            
            # Author and date
            created_at = item.get("createdAt", "")
            if created_at:
                try:
                    date_obj = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    date_str = date_obj.strftime("%B %d, %Y")
                except:
                    date_str = created_at[:10]
            else:
                date_str = "Unknown date"
            
            meta_text = f"By @{author} • {date_str}"
            draw.text((padding, y_offset), meta_text, font=header_font, fill=text_color)
            y_offset += 35
            
            # Issue/PR body
            body = item.get("body", "").strip()
            if body:
                draw.text((padding, y_offset), "Description:", font=header_font, fill=header_color)
                y_offset += 25
                y_offset = draw_wrapped_text(body, body_font, text_color, img_width - 2*padding, y_offset)
            else:
                draw.text((padding, y_offset), "No description provided.", font=body_font, fill=(128, 128, 128))
                y_offset += 30
            
            # Recent comments
            comments = item.get("comments", {}).get("nodes", [])
            if comments and y_offset < img_height - 100:
                draw.text((padding, y_offset), "Recent Comments:", font=header_font, fill=header_color)
                y_offset += 25
                
                for comment in comments[-2:]:  # Show last 2 comments
                    if y_offset > img_height - 80:
                        break
                        
                    comment_author = comment.get("author", {}).get("login", "Unknown")
                    comment_body = comment.get("body", "").strip()
                    comment_date = comment.get("createdAt", "")
                    
                    if comment_date:
                        try:
                            date_obj = datetime.fromisoformat(comment_date.replace('Z', '+00:00'))
                            date_str = date_obj.strftime("%b %d")
                        except:
                            date_str = comment_date[:10]
                    else:
                        date_str = ""
                    
                    # Comment header
                    comment_header = f"💬 @{comment_author} • {date_str}"
                    draw.text((padding + 10, y_offset), comment_header, font=comment_font, fill=(100, 100, 100))
                    y_offset += 20
                    
                    # Comment body
                    if comment_body:
                        if len(comment_body) > 200:
                            comment_body = comment_body[:200] + "..."
                        y_offset = draw_wrapped_text(comment_body, comment_font, text_color, img_width - 3*padding, y_offset)
                    
                    y_offset += 10
            
            # Convert to BytesIO
            img_bytes = io.BytesIO()
            image.save(img_bytes, format='PNG', optimize=True)
            img_bytes.seek(0)
            
            return img_bytes
            
        except Exception as e:
            print(f"❌ Error creating summary image: {e}")
            return None
    
    async def send_dm_to_user(self, user, content, files=None):
        """Send DM with retry logic for rate limits."""
        async def dm_send():
            if files:
                await user.send(content, files=files)
            else:
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
        
        message_parts = [f"🔔 **Hello {discord_username}{name_display}! You have reminders from GitHub (@{github_username})**"]
        
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
        message_parts.append("\n**📝 Reply with your update message** and I'll post it directly to GitHub for you!")
        message_parts.append("📋 *Visual summaries with context are attached below for reference.*")
        message_parts.append("*Or you can write your updates manually in the corresponding issues and PRs.*")
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
    
    async def process_reminders(self, fallback_channel_id: Optional[int] = None, target_discord_user: Optional[discord.User] = None) -> Dict[str, Any]:
        """
        Process reminders for all users with stale GitHub issues and PRs.
        
        Args:
            fallback_channel_id: Optional channel ID to send fallback messages. 
                                Uses REMINDER_CHANNEL_ID if not provided.
            target_discord_user: Optional Discord user to send reminders to exclusively (for testing).
                                If provided, only this user will receive reminders.
        
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
                body
                author {
                  login
                }
                assignees(first: 10) {
                  nodes {
                    login
                  }
                }
                comments(last: 3) {
                  nodes {
                    body
                    author {
                      login
                    }
                    createdAt
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
                body
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
                comments(last: 3) {
                  nodes {
                    body
                    author {
                      login
                    }
                    createdAt
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
            
            # If target_discord_user is specified, only process that user
            if target_discord_user:
                if not discord_username:
                    continue  # Skip users without Discord mapping
                
                potential_discord_user = await self.find_discord_user(discord_username)
                if not potential_discord_user or potential_discord_user.id != target_discord_user.id:
                    continue  # Skip users that don't match the target
            
            dm_success = False
            dm_error = ""
            discord_user = None
            
            if discord_username:
                discord_user = await self.find_discord_user(discord_username)
                
                if discord_user:
                    dm_content = self.create_dm_message_content(github_username, discord_username, issues, prs)
                    dm_content = self.truncate_message_if_needed(dm_content)
                    
                    # Generate visual summaries for issues and PRs
                    summary_files = []
                    all_items = issues + prs
                    
                    print(f"📋 Creating visual summaries for {len(all_items)} items for {discord_username}...")
                    for item in all_items[:3]:  # Limit to 3 summaries to avoid Discord limits
                        repo = item.get("repository", "")
                        number = item.get("number", "")
                        item_type = "issue" if item in issues else "pr"
                        
                        summary_image = self.create_item_summary_image(item, item_type)
                        if summary_image:
                            filename = f"{repo}_{item_type}_{number}_summary.png"
                            summary_files.append(discord.File(summary_image, filename=filename))
                            print(f"✅ Visual summary created for {repo}#{number}")
                        else:
                            print(f"⚠️ Failed to create visual summary for {repo}#{number}")
                    
                    # Send DM with or without visual summaries
                    if summary_files:
                        print(f"📎 Attaching {len(summary_files)} visual summaries to DM")
                        dm_success, dm_error = await self.send_dm_to_user(discord_user, dm_content, files=summary_files)
                    else:
                        print("📝 No visual summaries available - sending DM with text only")
                        # Update message if no summaries available
                        dm_content_no_summaries = dm_content.replace("📋 *Visual summaries with context are attached below for reference.*\n", "")
                        dm_success, dm_error = await self.send_dm_to_user(discord_user, dm_content_no_summaries)
                    
                    if dm_success:
                        delivery_stats["dm_success"] += 1
                        
                        # Create update session for GitHub comment posting
                        update_manager = getattr(self.bot, 'github_update_manager', None)
                        if update_manager:
                            session_created = update_manager.create_update_session(
                                discord_user.id, github_username, issues, prs
                            )
                            if session_created:
                                print(f"✅ Created GitHub update session for {discord_username} ({github_username})")
                            else:
                                print(f"⚠️ Failed to create GitHub update session for {discord_username}")
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