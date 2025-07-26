import requests
import time
import asyncio
from typing import Dict, Optional
from datetime import datetime
from config import DJANGO_API_BASE_URL, M4M_DISCORD_API_KEY
from .network import retry_with_exponential_backoff

class MemberMappingCache:
    """Cache for GitHub to Discord username mapping."""
    
    def __init__(self, cache_duration: int = 7200):
        self.api_base_url = DJANGO_API_BASE_URL.rstrip('/')
        self.cache_duration = cache_duration  # 2 hours default
        self._cache = {}
        self._last_fetch = 0
    

        
    async def get_mapping(self) -> Dict[str, Dict[str, str]]:
        """Get GitHub to Discord username mapping with caching and retry logic.
        
        Returns:
            Dict mapping GitHub usernames to user info objects:
            {
                "github_username": {
                    "discord_username": "discord_username",
                    "name": "Real Name"
                }
            }
        """
        current_time = time.time()
        
        # Check if cache is still valid
        if (current_time - self._last_fetch) < self.cache_duration and self._cache:
            return self._cache
            
        # Fetch fresh data with retry logic
        async def api_call():
            url = f"{self.api_base_url}/api/mantis4mantis/github-discord-mapping/"
            
            # Prepare headers with API key authentication
            headers = {}
            if M4M_DISCORD_API_KEY:
                headers['Authorization'] = f'Api-Key {M4M_DISCORD_API_KEY}'
            else:
                print("⚠️ Warning: M4M_DISCORD_API_KEY not set, request may fail")
            
            # Run the blocking request in a thread pool
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None, 
                lambda: requests.get(url, headers=headers, timeout=10)
            )
            response.raise_for_status()
            return response.json()
        
        success, data, error = await retry_with_exponential_backoff(api_call, max_retries=3, base_delay=1.0)
        
        if success and data:
            if data.get('success'):
                # Store the new object-based mapping format
                self._cache = data.get('mapping', {})
                self._last_fetch = current_time
                count = data.get('count', len(self._cache))
                print(f"✅ [{datetime.now().strftime('%H:%M:%S')}] Fetched {count} GitHub-Discord mappings from API")
            else:
                api_error = data.get('error', 'Unknown error')
                print(f"❌ [{datetime.now().strftime('%H:%M:%S')}] API returned error: {api_error}")
                # Don't clear cache on logical errors - return existing stale data
        else:
            print(f"❌ [{datetime.now().strftime('%H:%M:%S')}] Failed to fetch member mapping after retries: {error}")
            # Return cached data if available, empty dict otherwise
            
        return self._cache
    
    def get_discord_username(self, github_username: str) -> Optional[str]:
        """Get Discord username for a given GitHub username from cache.
        
        Args:
            github_username: The GitHub username to look up
            
        Returns:
            Discord username if found, None otherwise
        """
        user_info = self._cache.get(github_username)
        if user_info and isinstance(user_info, dict):
            return user_info.get("discord_username")
        return None
    
    def get_user_info(self, github_username: str) -> Optional[Dict[str, str]]:
        """Get full user info for a given GitHub username from cache.
        
        Args:
            github_username: The GitHub username to look up
            
        Returns:
            Dict with 'discord_username' and 'name' if found, None otherwise
        """
        user_info = self._cache.get(github_username)
        if user_info and isinstance(user_info, dict):
            return user_info
        return None
    
    def get_real_name(self, github_username: str) -> Optional[str]:
        """Get real name for a given GitHub username from cache.
        
        Args:
            github_username: The GitHub username to look up
            
        Returns:
            Real name if found, None otherwise
        """
        user_info = self._cache.get(github_username)
        if user_info and isinstance(user_info, dict):
            return user_info.get("name")
        return None
    
    def get_cache_age(self) -> int:
        """Get cache age in seconds."""
        return int(time.time() - self._last_fetch) if self._last_fetch > 0 else -1
    
    def get_cache_info(self) -> Dict[str, any]:
        """Get cache information for debugging."""
        return {
            "cache_size": len(self._cache),
            "cache_age_seconds": self.get_cache_age(),
            "last_fetch": datetime.fromtimestamp(self._last_fetch).strftime('%Y-%m-%d %H:%M:%S') if self._last_fetch > 0 else "Never",
            "cache_valid": (time.time() - self._last_fetch) < self.cache_duration if self._last_fetch > 0 else False
        }