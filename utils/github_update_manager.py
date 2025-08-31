import discord
import requests
import asyncio
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple
from config import (
    GITHUB_TOKEN, 
    GITHUB_ORG_NAME,
    REMINDER_REPOS,
)


class GitHubUpdateManager:
    """Manages GitHub comment posting and DM update sessions for reminder responses."""
    
    def __init__(self, bot: discord.Client, member_cache=None):
        """Initialize the GitHub Update Manager.
        
        Args:
            bot: Discord bot/client instance
            member_cache: Optional shared MemberMappingCache instance
        """
        self.bot = bot
        self.member_cache = member_cache
        self.active_sessions = {}  # user_id -> session_data
        self.session_timeout_hours = 48  # Sessions expire after 48 hours
        
        # GitHub API headers for REST API calls
        self.github_headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
        }
    
    def create_update_session(self, discord_user_id: int, github_username: str, 
                            issues: List[Dict], prs: List[Dict]) -> bool:
        """Create a new update session for a user after they receive reminder DMs.
        
        Args:
            discord_user_id: Discord user ID
            github_username: User's GitHub username
            issues: List of stale issues from reminder
            prs: List of stale PRs from reminder
            
        Returns:
            bool: True if session created successfully
        """
        try:
            # Clean up expired sessions first
            self._cleanup_expired_sessions()
            
            # Prepare update items (combine issues and PRs)
            update_items = []
            
            for issue in issues:
                update_items.append({
                    "type": "issue",
                    "repository": issue.get("repository", ""),
                    "number": issue.get("number", ""),
                    "title": issue.get("title", "Untitled"),
                    "url": issue.get("url", ""),
                })
            
            for pr in prs:
                update_items.append({
                    "type": "pr",
                    "repository": pr.get("repository", ""),
                    "number": pr.get("number", ""),
                    "title": pr.get("title", "Untitled"),
                    "url": pr.get("url", ""),
                })
            
            # Create session data
            session_data = {
                "github_username": github_username,
                "update_items": update_items,
                "created_at": datetime.now(timezone.utc),
                "stage": "awaiting_initial_response",  # Track conversation stage
                "updated_items": [],  # Track which items have been updated
            }
            
            self.active_sessions[discord_user_id] = session_data
            print(f"✅ Created update session for user {discord_user_id} with {len(update_items)} items")
            return True
            
        except Exception as e:
            print(f"❌ Error creating update session for user {discord_user_id}: {e}")
            return False
    
    def get_session(self, discord_user_id: int) -> Optional[Dict]:
        """Get active session for a user if it exists and hasn't expired.
        
        Args:
            discord_user_id: Discord user ID
            
        Returns:
            Session data dict or None if no active session
        """
        if discord_user_id not in self.active_sessions:
            return None
            
        session = self.active_sessions[discord_user_id]
        
        # Check if session has expired
        created_at = session.get("created_at")
        if created_at:
            hours_elapsed = (datetime.now(timezone.utc) - created_at).total_seconds() / 3600
            if hours_elapsed > self.session_timeout_hours:
                # Session expired, remove it
                del self.active_sessions[discord_user_id]
                return None
        
        return session
    
    def update_session(self, discord_user_id: int, updates: Dict) -> bool:
        """Update session data for a user.
        
        Args:
            discord_user_id: Discord user ID
            updates: Dictionary of updates to apply to session
            
        Returns:
            bool: True if update successful
        """
        if discord_user_id not in self.active_sessions:
            return False
            
        self.active_sessions[discord_user_id].update(updates)
        return True
    
    def end_session(self, discord_user_id: int) -> bool:
        """End and remove a user's session.
        
        Args:
            discord_user_id: Discord user ID
            
        Returns:
            bool: True if session was removed
        """
        if discord_user_id in self.active_sessions:
            del self.active_sessions[discord_user_id]
            return True
        return False
    
    def _cleanup_expired_sessions(self):
        """Remove expired sessions to prevent memory leaks."""
        current_time = datetime.now(timezone.utc)
        expired_users = []
        
        for user_id, session in self.active_sessions.items():
            created_at = session.get("created_at")
            if created_at:
                hours_elapsed = (current_time - created_at).total_seconds() / 3600
                if hours_elapsed > self.session_timeout_hours:
                    expired_users.append(user_id)
        
        for user_id in expired_users:
            del self.active_sessions[user_id]
        
        if expired_users:
            print(f"🧹 Cleaned up {len(expired_users)} expired update sessions")
    
    def parse_github_url(self, url: str) -> Optional[Tuple[str, str, int, str]]:
        """Parse GitHub URL to extract owner, repo, number, and type.
        
        Args:
            url: GitHub issue or PR URL
            
        Returns:
            Tuple of (owner, repo, number, type) or None if invalid
        """
        # Pattern for GitHub issue/PR URLs
        pattern = r"https://github\.com/([^/]+)/([^/]+)/(issues|pull)/(\d+)"
        match = re.match(pattern, url)
        
        if match:
            owner, repo, url_type, number = match.groups()
            item_type = "issue" if url_type in ["issues", "pull"] else url_type
            return owner, repo, int(number), item_type
        
        return None
    
    async def post_github_comment(self, repository: str, item_number: int, 
                                comment_body: str, item_type: str = "issue") -> Tuple[bool, str]:
        """Post a comment to a GitHub issue or PR.
        
        Args:
            repository: Repository name (e.g., "Mantis", "MantisAPI")
            item_number: Issue or PR number
            comment_body: Comment text to post
            item_type: "issue" or "pr" (both use same API endpoint)
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            # Construct API URL - PRs use the same endpoint as issues
            api_url = f"https://api.github.com/repos/{GITHUB_ORG_NAME}/{repository}/issues/{item_number}/comments"
            
            # Prepare payload
            payload = {
                "body": comment_body
            }
            
            # Make the API request
            response = requests.post(
                api_url, 
                headers=self.github_headers, 
                json=payload,
                timeout=30
            )
            
            if response.status_code == 201:
                # Success
                comment_data = response.json()
                comment_url = comment_data.get("html_url", "")
                return True, f"✅ Comment posted successfully! [View comment]({comment_url})"
            
            elif response.status_code == 403:
                return False, "❌ Permission denied. The bot may not have write access to this repository."
            
            elif response.status_code == 404:
                return False, f"❌ {item_type.title()} #{item_number} not found in {repository} repository."
            
            else:
                error_msg = f"❌ GitHub API error (status {response.status_code})"
                try:
                    error_detail = response.json().get("message", "Unknown error")
                    error_msg += f": {error_detail}"
                except:
                    pass
                return False, error_msg
                
        except requests.exceptions.Timeout:
            return False, "❌ Request timed out. Please try again."
        
        except requests.exceptions.RequestException as e:
            return False, f"❌ Network error: {str(e)}"
        
        except Exception as e:
            return False, f"❌ Unexpected error: {str(e)}"
    
    def format_item_list(self, items: List[Dict], updated_items: List[int] = None) -> str:
        """Format a list of items for display to user.
        
        Args:
            items: List of update items
            updated_items: List of indices of items that have been updated
            
        Returns:
            Formatted string for Discord message
        """
        if not items:
            return "No items available."
        
        updated_items = updated_items or []
        lines = []
        
        for i, item in enumerate(items):
            status_emoji = "✅" if i in updated_items else "📝"
            item_type_emoji = "🔄" if item["type"] == "pr" else "📝"
            
            title = item["title"]
            if len(title) > 50:
                title = title[:47] + "..."
            
            line = f"{i+1}. {status_emoji} {item_type_emoji} **{item['repository']}#{item['number']}**: {title}"
            lines.append(line)
        
        return "\n".join(lines)
    
    def get_session_stats(self) -> Dict[str, int]:
        """Get statistics about active sessions.
        
        Returns:
            Dictionary with session statistics
        """
        return {
            "active_sessions": len(self.active_sessions),
            "total_items": sum(len(session.get("update_items", [])) for session in self.active_sessions.values()),
        }
