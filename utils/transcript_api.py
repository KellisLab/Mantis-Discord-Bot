import aiohttp
from datetime import datetime
from typing import List, Optional, Dict, Any, Tuple
from config import DJANGO_API_BASE_URL, M4M_DISCORD_API_KEY
from .network import retry_with_exponential_backoff


class TranscriptAPI:
    """API client for submitting Discord conversation transcripts to the Django backend."""
    
    def __init__(self):
        """Initialize the TranscriptAPI client with configuration from config.py."""
        self.base_url = DJANGO_API_BASE_URL.rstrip('/')
        self.api_key = M4M_DISCORD_API_KEY
        
        if not self.api_key:
            print("⚠️ Warning: M4M_DISCORD_API_KEY not set. Transcript API calls will fail.")
    
    async def create_discord_transcript(
        self,
        channel_name: str,
        channel_type: str,
        channel_id: str,
        description: str,
        timestamp: datetime,
        people_involved_names: Optional[List[str]] = None
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Create a Discord transcript entry in the Mantis database.
        
        Args:
            channel_name: Name of the Discord channel (without #)
            channel_type: Either "text" or "voice" 
            channel_id: Discord channel ID as string
            description: AI-generated summary of the conversation
            timestamp: When the conversation occurred
            people_involved_names: List of real names (not Discord usernames)
        
        Returns:
            Tuple of (success: bool, response_data: dict)
        """
        url = f"{self.base_url}/api/mantis4mantis/discord-transcript/"
        
        # Prepare the payload according to API specification
        payload = {
            "channel_name": channel_name,
            "channel_type": channel_type,
            "channel_id": channel_id,
            "description": description,
            "timestamp": timestamp.isoformat(),
        }
        
        if people_involved_names:
            payload["people_involved_names"] = people_involved_names
        
        # Prepare headers with API key authentication
        headers = {
            "Authorization": f"Api-Key {self.api_key}",
            "Content-Type": "application/json"
        }
        
        # Define the API call function for retry logic
        async def api_call():
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers) as response:
                    response_data = await response.json()
                    
                    if response.status == 201:
                        print(f"✅ Created transcript for #{channel_name} (ID: {response_data.get('data', {}).get('id', 'unknown')})")
                        return True, response_data
                    else:
                        # Log the error but let retry logic handle it
                        error_msg = self._format_api_error(response.status, response_data)
                        print(f"❌ Failed to create transcript for #{channel_name}: {error_msg}")
                        
                        # Raise exception to trigger retry logic for retryable errors
                        if response.status in [429, 500, 502, 503]:
                            # These are retryable errors
                            response.raise_for_status()
                        else:
                            # These are non-retryable errors (400, 401, 403, etc.)
                            return False, response_data
        
        # Use existing retry logic with exponential backoff
        try:
            success, result, error = await retry_with_exponential_backoff(
                api_call, 
                max_retries=3, 
                base_delay=1.0
            )
            
            if success:
                return result  # result is already (bool, dict) from api_call
            else:
                print(f"❌ Transcript API call failed after retries: {error}")
                return False, {"error": error}
                
        except Exception as e:
            print(f"❌ Unexpected error in transcript API call: {str(e)}")
            return False, {"error": str(e)}
    
    def _format_api_error(self, status_code: int, response_data: Dict[str, Any]) -> str:
        """Format API error response into a readable error message."""
        if status_code == 400:
            # Bad request - validation errors
            error_details = response_data.get('error', {})
            if isinstance(error_details, dict):
                error_parts = []
                for field, errors in error_details.items():
                    if isinstance(errors, list):
                        error_parts.append(f"{field}: {', '.join(errors)}")
                    else:
                        error_parts.append(f"{field}: {errors}")
                return f"Validation error - {'; '.join(error_parts)}"
            else:
                return f"Bad request: {error_details}"
        
        elif status_code == 401:
            return "Missing or invalid Authorization header"
        
        elif status_code == 403:
            return "Invalid API key"
        
        elif status_code == 429:
            return "Rate limited - too many requests"
        
        elif status_code >= 500:
            return f"Server error (status {status_code})"
        
        else:
            return f"HTTP {status_code}: {response_data.get('error', 'Unknown error')}"
