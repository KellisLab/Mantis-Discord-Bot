import discord
import re
import asyncio
from typing import List, Optional, Tuple
from config import GRAPHQL_URL, HEADERS, CHANNEL_PROJECT_MAPPING, MYREPOBOT_ID, SOURCE_CHANNEL_ID
from utils.network import retry_with_exponential_backoff
import requests

def setup(bot):
    """Register GitHub webhook handlers with the bot."""
    bot.add_listener(on_message_webhook, 'on_message')
    
    # Test the parsing logic with sample data
    # print("🧪 Testing GitHub webhook parsing...")
    # test_parse_logic()
    # print("✅ GitHub webhook listener registered successfully!")

async def on_message_webhook(message):
    """Handle messages from MyRepoBot in the designated channel."""
    # Add debug logging for all messages in the source channel
    if message.channel.id == SOURCE_CHANNEL_ID:
        print(f"🔍 Message in source channel from {message.author.name} (ID: {message.author.id})")
        if message.embeds:
            print(f"📋 Message has {len(message.embeds)} embed(s)")
            for i, embed in enumerate(message.embeds):
                print(f"  Embed {i+1}: {embed.description[:100] if embed.description else 'No description'}...")
    
    # Only process messages from MyRepoBot in the specific channel
    if (message.author.id != MYREPOBOT_ID or 
        message.channel.id != SOURCE_CHANNEL_ID or
        not message.embeds):
        return
    
    print(f"✅ Processing MyRepoBot message with {len(message.embeds)} embed(s)")
    
    try:
        # Process each embed in the message
        for embed in message.embeds:
            await process_issue_notification(message, embed)
    except Exception as e:
        print(f"❌ Error processing MyRepoBot webhook: {e}")

async def process_issue_notification(message, embed):
    """Process a single embed from MyRepoBot for issue notifications."""
    if not embed.description:
        return
    
    # Parse the embed to extract issue information
    issue_info = parse_issue_embed(embed.description)
    if not issue_info:
        return
    
    repo_owner, repo_name, issue_number, event_type = issue_info
    print(f"🔍 Processing {event_type} for issue #{issue_number} in {repo_owner}/{repo_name}")
    
    # Get project memberships for this issue
    project_numbers = await get_issue_projects(repo_owner, repo_name, issue_number)
    if not project_numbers:
        print(f"📋 Issue #{issue_number} is not in any projects")
        return
    
    # Find channels to notify based on project mappings
    channels_to_notify = get_mapped_channels(project_numbers)
    if not channels_to_notify:
        print(f"📋 No channel mappings found for projects: {project_numbers}")
        return
    
    # Forward the notification to mapped channels
    await forward_notification_to_channels(message, embed, channels_to_notify, issue_info)

def parse_issue_embed(description: str) -> Optional[Tuple[str, str, int, str]]:
    """
    Parse MyRepoBot embed description to extract issue information.
    
    Returns:
        Tuple of (repo_owner, repo_name, issue_number, event_type) or None
    """
    # Look for GitHub issue URLs in the format: https://github.com/owner/repo/issues/number
    issue_url_pattern = r'https://github\.com/([^/]+)/([^/]+)/issues/(\d+)'
    url_match = re.search(issue_url_pattern, description)
    
    if not url_match:
        return None
    
    repo_owner = url_match.group(1)
    repo_name = url_match.group(2) 
    issue_number = int(url_match.group(3))
    
    # Determine event type from the description
    if 'New Issue created' in description or '🟢' in description:
        event_type = 'opened'
    elif 'Issue was closed' in description or '❌' in description:
        event_type = 'closed'
    else:
        event_type = 'unknown'
    
    return repo_owner, repo_name, issue_number, event_type

async def get_issue_projects(repo_owner: str, repo_name: str, issue_number: int) -> List[int]:
    """
    Query GitHub API to get project numbers that contain this issue.
    
    Returns:
        List of project numbers
    """
    graphql_query = """
    query GetIssueProjects($owner: String!, $name: String!, $issueNumber: Int!) {
      repository(owner: $owner, name: $name) {
        issue(number: $issueNumber) {
          projectItems(first: 20) {
            nodes {
              project {
                number
                title
              }
            }
          }
        }
      }
    }
    """
    
    variables = {
        "owner": repo_owner,
        "name": repo_name,
        "issueNumber": issue_number,
    }
    
    try:
        async def api_call():
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None, 
                lambda: requests.post(
                    GRAPHQL_URL, 
                    headers=HEADERS, 
                    json={"query": graphql_query, "variables": variables},
                    timeout=30
                )
            )
            response.raise_for_status()
            return response.json()
        
        success, result, error = await retry_with_exponential_backoff(api_call, max_retries=3, base_delay=1.0)
        
        if not success:
            print(f"❌ GitHub API request failed: {error}")
            return []
        
        # Parse the response to extract project numbers
        data = result.get("data", {})
        repository = data.get("repository")
        if not repository:
            print(f"❌ Repository {repo_owner}/{repo_name} not found")
            return []
        
        issue = repository.get("issue")
        if not issue:
            print(f"❌ Issue #{issue_number} not found in {repo_owner}/{repo_name}")
            return []
        
        project_items = issue.get("projectItems", {}).get("nodes", [])
        project_numbers = []
        
        for item in project_items:
            project = item.get("project")
            if project and project.get("number"):
                project_numbers.append(project["number"])
        
        print(f"📋 Issue #{issue_number} found in projects: {project_numbers}")
        return project_numbers
        
    except Exception as e:
        print(f"❌ Error querying GitHub API for issue projects: {e}")
        return []

def get_mapped_channels(project_numbers: List[int]) -> List[int]:
    """
    Get Discord channel IDs that should be notified based on project numbers.
    
    Args:
        project_numbers: List of GitHub project numbers
        
    Returns:
        List of Discord channel IDs to notify
    """
    channels_to_notify = []
    
    for channel_id, mapped_project_number in CHANNEL_PROJECT_MAPPING.items():
        if mapped_project_number in project_numbers:
            channels_to_notify.append(channel_id)
    
    return channels_to_notify

async def forward_notification_to_channels(
    original_message, 
    embed, 
    channel_ids: List[int], 
    issue_info: Tuple[str, str, int, str]
):
    """
    Forward the issue notification to mapped channels.
    
    Args:
        original_message: Original Discord message from MyRepoBot
        embed: Original embed from MyRepoBot
        channel_ids: List of channel IDs to notify
        issue_info: Tuple of (repo_owner, repo_name, issue_number, event_type)
    """
    repo_owner, repo_name, issue_number, event_type = issue_info
    
    # Create a similar embed for forwarding
    forwarded_embed = discord.Embed(
        title=embed.title if embed.title else None,
        description=embed.description,
        color=embed.color,
    )
    
    # Copy fields if any
    for field in embed.fields:
        forwarded_embed.add_field(
            name=field.name,
            value=field.value, 
            inline=field.inline
        )
    
    # Add footer to indicate this is forwarded
    forwarded_embed.set_footer(text=f"Forwarded from project notifications • Issue #{issue_number}")
    
    bot = original_message._state._get_client()
    
    # Send to each mapped channel
    for channel_id in channel_ids:
        try:
            channel = bot.get_channel(channel_id)
            if channel:
                await channel.send(embed=forwarded_embed)
                print(f"✅ Forwarded {event_type} notification for issue #{issue_number} to #{channel.name}")
            else:
                print(f"❌ Could not find channel with ID {channel_id}")
        except discord.HTTPException as e:
            print(f"❌ Failed to send notification to channel {channel_id}: {e}")
        except Exception as e:
            print(f"❌ Unexpected error sending to channel {channel_id}: {e}")

def test_parse_logic():
    """Test the embed parsing logic with sample data."""
    # Test case 1: New issue created
    test_description_1 = """📋🟢 New Issue created by DemonizedCrush

Title: [Test Issue](@https://github.com/KellisLab/Mantis/issues/1000 )
Repository: [Mantis](@https://github.com/KellisLab/Mantis )
Description: This is a test"""
    
    result_1 = parse_issue_embed(test_description_1)
    print(f"✅ Test 1 (new issue): {result_1}")
    
    # Test case 2: Issue closed
    test_description_2 = """📋❌ Issue was closed by DemonizedCrush

Title: [Test Issue](@https://github.com/KellisLab/Mantis/issues/1000 )
Repository: [Mantis](@https://github.com/KellisLab/Mantis )"""
    
    result_2 = parse_issue_embed(test_description_2)
    print(f"✅ Test 2 (closed issue): {result_2}")
    
    # Test expected results
    expected_1 = ('KellisLab', 'Mantis', 1000, 'opened')
    expected_2 = ('KellisLab', 'Mantis', 1000, 'closed')
    
    if result_1 == expected_1:
        print("✅ Test 1 passed!")
    else:
        print(f"❌ Test 1 failed: expected {expected_1}, got {result_1}")
    
    if result_2 == expected_2:
        print("✅ Test 2 passed!")
    else:
        print(f"❌ Test 2 failed: expected {expected_2}, got {result_2}")
    
    print("🧪 GitHub webhook parsing tests completed!") 