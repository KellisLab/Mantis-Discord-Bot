import aiohttp
import json
from typing import Optional, Dict, Any, Tuple
from config import DJANGO_API_BASE_URL, M4M_DISCORD_API_KEY
from .network import retry_with_exponential_backoff
from datetime import datetime

class MeetingTranscriptsAPI:
    """API client for fetching meeting transcripts from the Django backend."""
    
    def __init__(self):
        """Initialize the MeetingTranscriptsAPI client with configuration from config.py."""
        self.base_url = DJANGO_API_BASE_URL.rstrip('/')
        self.api_key = M4M_DISCORD_API_KEY
        
        if not self.api_key:
            print("⚠️ Warning: M4M_DISCORD_API_KEY not set. Meeting transcripts API calls will fail.")
    
    async def fetch_all_transcripts(self) -> Tuple[bool, Dict[str, Any]]:
        """
        Fetch ALL meeting transcripts from Django API.
        
        NOTE: The Django endpoint does not support filtering - it returns all transcripts.
        Filtering must be done client-side in the Discord bot.
        
        Returns:
            Tuple of (success: bool, response_data: dict)
        """
        url = f"{self.base_url}/api/mantis4mantis/meeting-transcripts/"
        
        # No query parameters - Django endpoint returns everything
        
        # Prepare headers with API key authentication
        headers = {
            "Authorization": f"Api-Key {self.api_key}",
            "Content-Type": "application/json"
        }
        
        # Define the API call function for retry logic
        async def api_call():
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    response_data = await response.json()
                    
                    if response.status == 200:
                        print(f"✅ Fetched {len(response_data.get('data', []))} meeting transcripts")
                        return True, response_data
                    else:
                        # Log the error but let retry logic handle it
                        error_msg = self._format_api_error(response.status, response_data)
                        print(f"❌ Failed to fetch meeting transcripts: {error_msg}")
                        
                        # Raise exception to trigger retry logic for retryable errors
                        if response.status in [429, 500, 502, 503]:
                            # These are retryable errors
                            response.raise_for_status()
                        else:
                            # These are non-retryable errors (400, 401, 403, etc.)
                            return False, response_data
        
        # Use existing retry logic with exponential backoff
        success, result, error = await retry_with_exponential_backoff(
            api_call, 
            max_retries=3, 
            base_delay=1.0
        )
        
        if success:
            return result
        else:
            # Return error information for function call response
            error_response = {
                "success": False,
                "error": f"Failed to fetch meeting transcripts: {error}",
                "data": []
            }
            return False, error_response
    
    def filter_transcripts_client_side(
        self,
        raw_data: Dict[str, Any],
        team_name: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 100
    ) -> Dict[str, Any]:
        """
        Apply client-side filtering to the raw transcript data.
        
        Args:
            raw_data: Raw response from Django API
            team_name: Optional team name to filter by
            start_date: Optional start date in YYYY-MM-DD format
            end_date: Optional end date in YYYY-MM-DD format
            limit: Maximum number of transcripts to return
            
        Returns:
            Filtered transcript data
        """
        if not raw_data.get('success') or not raw_data.get('data'):
            return raw_data
        
        transcripts = raw_data['data']
        filtered_transcripts = []
        
        for transcript in transcripts:
            meeting = transcript.get('meeting', {})
            
            # Filter by team name
            if team_name and meeting.get('team_name') != team_name:
                continue
            
            # Filter by date range
            meeting_date = meeting.get('meeting_date')
            if meeting_date:
                try:
                    # Using datetime objects for robust comparison.
                    meeting_date_obj = datetime.date.fromisoformat(meeting_date[:10])
                    
                    if start_date and meeting_date_obj < datetime.date.fromisoformat(start_date):
                        continue
                    if end_date and meeting_date_obj > datetime.date.fromisoformat(end_date):
                        continue
                except (ValueError, TypeError):
                    # Skip if date parsing fails
                    continue
            
            filtered_transcripts.append(transcript)
            
            # Apply limit
            if len(filtered_transcripts) >= limit:
                break
        
        # Create new response with filtered data
        filtered_response = {
            "success": True,
            "data": filtered_transcripts,
            "count": len(filtered_transcripts),
            "total_available": len(transcripts),
            "filters_applied": {
                "team_name": team_name,
                "start_date": start_date,
                "end_date": end_date,
                "limit": limit
            }
        }
        
        return filtered_response
    
    def smart_truncate_for_openai(self, formatted_data: Dict[str, Any], max_size_mb: float = 0.8) -> Dict[str, Any]:
        """
        Intelligently truncate transcript data to stay within OpenAI's function output limits.
        
        Args:
            formatted_data: Formatted transcript data
            max_size_mb: Maximum size in MB (default 0.8MB to leave buffer from 1MB limit)
            
        Returns:
            Truncated data that fits within size limits
        """
        max_size_bytes = int(max_size_mb * 1024 * 1024)
        
        # Check current size
        current_json = json.dumps(formatted_data)
        current_size = len(current_json.encode('utf-8'))
        
        if current_size <= max_size_bytes:
            return formatted_data
        
        print(f"⚠️ Response too large ({current_size/1024/1024:.1f}MB), truncating to {max_size_mb}MB")
        
        transcripts = formatted_data.get('transcripts', [])
        if not transcripts:
            return formatted_data
        
        # Sort by date (newest first) to prioritize recent data
        sorted_transcripts = sorted(
            transcripts,
            key=lambda x: x.get('meeting', {}).get('date') or '0000-00-00',
            reverse=True
        )
        
        # Binary search to find the right number of transcripts
        left, right = 0, len(sorted_transcripts)
        best_count = 0
        
        while left <= right:
            mid = (left + right) // 2
            
            test_data = {
                **formatted_data,
                'transcripts': sorted_transcripts[:mid]
            }
            test_data['meetings_summary']['total_transcripts'] = mid
            test_data['meetings_summary']['truncated'] = True
            test_data['meetings_summary']['original_count'] = len(transcripts)
            
            test_json = json.dumps(test_data)
            test_size = len(test_json.encode('utf-8'))
            
            if test_size <= max_size_bytes:
                best_count = mid
                left = mid + 1
            else:
                right = mid - 1
        
        # Create final truncated response
        truncated_data = {
            **formatted_data,
            'transcripts': sorted_transcripts[:best_count]
        }
        truncated_data['meetings_summary']['total_transcripts'] = best_count
        truncated_data['meetings_summary']['truncated'] = True
        truncated_data['meetings_summary']['original_count'] = len(transcripts)
        truncated_data['meetings_summary']['truncation_note'] = f"Response truncated to {best_count} most recent transcripts to fit size limits"
        
        final_size = len(json.dumps(truncated_data).encode('utf-8'))
        print(f"✅ Truncated to {best_count} transcripts ({final_size/1024/1024:.1f}MB)")
        
        return truncated_data
    
    def _format_api_error(self, status_code: int, response_data: Dict[str, Any]) -> str:
        """Format API error messages for consistent logging."""
        if isinstance(response_data, dict):
            if 'error' in response_data:
                return f"HTTP {status_code}: {response_data['error']}"
            elif 'detail' in response_data:
                return f"HTTP {status_code}: {response_data['detail']}"
            elif 'message' in response_data:
                return f"HTTP {status_code}: {response_data['message']}"
        
        return f"HTTP {status_code}: Unknown error"
    
    async def get_filtered_transcripts(
        self,
        team_name: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 100
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        High-level method that fetches all transcripts, applies client-side filtering,
        formats for assistant, and truncates to fit OpenAI limits.
        
        Args:
            team_name: Optional team name to filter by
            start_date: Optional start date in YYYY-MM-DD format
            end_date: Optional end date in YYYY-MM-DD format
            limit: Maximum number of transcripts to return
            
        Returns:
            Tuple of (success: bool, formatted_data: dict)
        """
        # Step 1: Fetch all transcripts from Django API
        success, raw_data = await self.fetch_all_transcripts()
        if not success:
            return False, raw_data
        
        # Step 2: Apply client-side filtering
        filtered_data = self.filter_transcripts_client_side(
            raw_data, team_name, start_date, end_date, limit
        )
        
        # Step 3: Format for assistant consumption
        formatted_data = self.format_transcripts_for_assistant(filtered_data)
        
        # Step 4: Truncate if needed to fit OpenAI limits
        final_data = self.smart_truncate_for_openai(formatted_data)
        
        return True, final_data
    
    def format_transcripts_for_assistant(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Format the raw API response into a structure optimized for the AI assistant.
        
        Args:
            raw_data: Raw response from the Django API
            
        Returns:
            Formatted data structure for assistant consumption
        """
        if not raw_data.get('success') or not raw_data.get('data'):
            return {
                "meetings_summary": {
                    "total_transcripts": 0,
                    "error": raw_data.get('error', 'No transcripts found')
                },
                "transcripts": []
            }
        
        transcripts_data = raw_data['data']
        
        # Extract summary information
        teams = set()
        dates = []
        
        formatted_transcripts = []
        for transcript in transcripts_data:
            meeting = transcript.get('meeting', {})
            teams.add(meeting.get('team_name', 'Unknown'))
            if meeting.get('meeting_date'):
                dates.append(meeting['meeting_date'])
            
            # Format individual transcript (optimized for size)
            formatted_transcript = {
                "id": transcript.get('id'),
                "speaker": transcript.get('speaker_name'),
                "content": transcript.get('content', ''),
                "meeting": {
                    "title": meeting.get('title'),
                    "date": meeting.get('meeting_date')[:10] if meeting.get('meeting_date') else None,  # Date only
                    "team": meeting.get('team_name'),
                    "type": meeting.get('meeting_type')
                },
                "timing": f"{transcript.get('start_time', '')}-{transcript.get('end_time', '')}"  # Compact timing
            }
            formatted_transcripts.append(formatted_transcript)
        
        # Create summary
        date_range = "No dates available"
        if dates:
            sorted_dates = sorted(dates)
            start_date = sorted_dates[0][:10]  # Extract date part
            end_date = sorted_dates[-1][:10]
            if start_date == end_date:
                date_range = start_date
            else:
                date_range = f"{start_date} to {end_date}"
        
        summary = {
            "meetings_summary": {
                "total_transcripts": len(transcripts_data),
                "date_range": date_range,
                "teams_included": sorted(list(teams))
            },
            "transcripts": formatted_transcripts
        }
        
        return summary