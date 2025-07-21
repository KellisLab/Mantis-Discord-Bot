import requests
import time
import asyncio
import random
from typing import Dict, Optional
from datetime import datetime
from config import DJANGO_API_BASE_URL

class MemberMappingCache:
    """Cache for GitHub to Discord username mapping."""
    
    def __init__(self, cache_duration: int = 7200):
        self.api_base_url = DJANGO_API_BASE_URL.rstrip('/')
        self.cache_duration = cache_duration  # 2 hours default
        self._cache = {}
        self._last_fetch = 0
    
    async def _retry_with_exponential_backoff(self, func, max_retries: int = 3, base_delay: float = 1.0):
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
                
                # Check if it's a rate limit error (status 403, 429, or 502/503 for server issues)
                if hasattr(e, 'response') and e.response is not None:
                    if e.response.status_code in [403, 429, 502, 503]:
                        # For rate limits and server errors, wait longer
                        delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                        print(f"⏳ API request failed (status {e.response.status_code}), retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})")
                        await asyncio.sleep(delay)
                        continue
                
                # For other errors, shorter delay
                delay = base_delay * (1.5 ** attempt) + random.uniform(0, 0.5)
                print(f"⏳ API request failed, retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries}): {str(e)}")
                await asyncio.sleep(delay)
            except Exception as e:
                if attempt == max_retries - 1:
                    return False, None, str(e)
                delay = base_delay * (1.5 ** attempt)
                print(f"⏳ Unexpected error, retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries}): {str(e)}")
                await asyncio.sleep(delay)
        
        return False, None, "Max retries exceeded"
        
    async def get_mapping(self) -> Dict[str, str]:
        """Get GitHub to Discord username mapping with caching and retry logic."""
        current_time = time.time()
        
        # Check if cache is still valid
        if (current_time - self._last_fetch) < self.cache_duration and self._cache:
            return self._cache
            
        # Fetch fresh data with retry logic
        async def api_call():
            url = f"{self.api_base_url}/api/mantis4mantis/github-discord-mapping/"
            
            # Run the blocking request in a thread pool
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, lambda: requests.get(url, timeout=10))
            response.raise_for_status()
            return response.json()
        
        success, data, error = await self._retry_with_exponential_backoff(api_call, max_retries=3, base_delay=1.0)
        
        if success and data:
            if data.get('success'):
                self._cache = data.get('mapping', {})
                self._last_fetch = current_time
                count = data.get('count', len(self._cache))
                print(f"✅ [{datetime.now().strftime('%H:%M:%S')}] Fetched {count} GitHub-Discord mappings from API")
            else:
                api_error = data.get('error', 'Unknown error')
                print(f"❌ [{datetime.now().strftime('%H:%M:%S')}] API returned error: {api_error}")
                return {}
        else:
            print(f"❌ [{datetime.now().strftime('%H:%M:%S')}] Failed to fetch member mapping after retries: {error}")
            # Return cached data if available, empty dict otherwise
            
        return self._cache
    
    def get_discord_username(self, github_username: str) -> Optional[str]:
        """Get Discord username for a given GitHub username from cache."""
        return self._cache.get(github_username)
    
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

# Global instance
member_mapping_cache = MemberMappingCache() 