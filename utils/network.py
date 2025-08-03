import requests
import asyncio
import random
import aiohttp

async def retry_with_exponential_backoff(func, max_retries: int = 3, base_delay: float = 1.0):
    """
    Retry a function with exponential backoff for transient failures.
    
    Args:
        func: The async function to retry
        max_retries: Maximum number of retry attempts
        base_delay: Base delay in seconds for exponential backoff
        
    Returns:
        tuple: (success: bool, result: any, error: str)
    """
    for attempt in range(max_retries):
        try:
            result = await func()
            return True, result, ""
        except (requests.exceptions.RequestException, aiohttp.ClientError) as e:
            if attempt == max_retries - 1:
                return False, None, str(e)
            
            status_code = None
            
            # Extract status code from either requests or aiohttp exceptions
            if hasattr(e, 'response') and e.response is not None:
                # requests exception
                status_code = e.response.status_code
            elif isinstance(e, aiohttp.ClientResponseError):
                # aiohttp exception
                status_code = e.status
            
            if status_code is not None:
                # Handle authentication errors - don't retry these
                if status_code == 401:
                    return False, None, "Missing or invalid Authorization header"
                elif status_code == 403:
                    return False, None, "Invalid API key"
                elif status_code == 500:
                    return False, None, "Server configuration error"
                
                # For rate limits and other server errors, wait longer
                if status_code in [429, 502, 503]:
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                    print(f"⏳ API request failed (status {status_code}), retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})")
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