import discord
from discord.ui import Button, View
from discord.ext import commands
from discord import app_commands
from config import M4M_PARTICIPANT_LIST, M4M_ONLY_CONSIDER_AFFILIATION, HEADERS, ASSISTANT_ID, OPENAI_API_KEY
from typing import Dict, Any
import requests
from io import StringIO
import csv
import random
import re
import time
import openai
from collections import defaultdict, Counter
import json
import asyncio
import traceback
from utils.meeting_transcripts_api import MeetingTranscriptsAPI 
from functools import lru_cache
import cachetools

# Cache for members list
members_cache = cachetools.TTLCache(maxsize=1, ttl=3600)

# Cache for fallback recommendations
fallback_cache = cachetools.TTLCache(maxsize=1, ttl=7200)

client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)

# Development: testing the fallback heuristic without an actual OpenAI API failure.
FORCE_FALLBACK_TEST = False

# --- Helper Function to Run Assistant ---
async def run_assistant(user_message: str, timeout_seconds: int = 90) -> str:
    try:
        thread = await client.beta.threads.create()
        await client.beta.threads.messages.create(thread_id=thread.id, role="user", content=user_message)
        run = await client.beta.threads.runs.create(thread_id=thread.id, assistant_id=ASSISTANT_ID)
        start_time = time.time()

        while run.status in ["queued", "in_progress", "requires_action"]:
            if time.time() - start_time > timeout_seconds:
                return "The assistant took too long to respond. Please try again."

            if run.status == "requires_action":
                tool_outputs = []
                transcripts_api = MeetingTranscriptsAPI()
                for tool_call in run.required_action.submit_tool_outputs.tool_calls:
                    function_name = tool_call.function.name
                    function_args = json.loads(tool_call.function.arguments)
                    if function_name == "get_meeting_transcripts":
                        try:
                            team_name = function_args.get('team_name')
                            start_date = function_args.get('start_date')
                            end_date = function_args.get('end_date')
                            limit = function_args.get('limit', 100)
                            success, formatted_data = await transcripts_api.get_filtered_transcripts(
                                team_name=team_name,
                                start_date=start_date,
                                end_date=end_date,
                                limit=limit
                            )
                            if success:
                                output = json.dumps(formatted_data)
                            else:
                                output = json.dumps({
                                    "meetings_summary": {"total_transcripts": 0, "error": "Failed to fetch transcripts"},
                                    "transcripts": [],
                                    "error": formatted_data.get('error', 'Unknown error')
                                })
                        except Exception as e:
                            output = json.dumps({
                                "meetings_summary": {"total_transcripts": 0, "error": f"Function execution error: {str(e)}"},
                                "transcripts": [],
                                "error": str(e)
                            })
                    else:
                        output = json.dumps({"error": f"Unknown function: {function_name}"})
                    tool_outputs.append({"tool_call_id": tool_call.id, "output": output})

                run = await client.beta.threads.runs.submit_tool_outputs(
                    thread_id=thread.id,
                    run_id=run.id,
                    tool_outputs=tool_outputs
                )
            else:
                await asyncio.sleep(1)
                run = await client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)

        if run.status == "completed":
            messages = await client.beta.threads.messages.list(thread_id=thread.id)
            for msg in messages.data:
                if msg.role == "assistant" and msg.content:
                    return msg.content[0].text.value.strip()

        return f"The assistant run failed with status: {run.status}"

    except Exception as e:
        print(f"An error occurred while running the assistant: {e}")
        traceback.print_exc()
        return "Sorry, an error occurred while communicating with the assistant."

# --- Helper Function for GitHub API ---
@lru_cache(maxsize=128)
def get_issue_info_from_github(issue_path: str) -> str:
    issue_api_url = f"https://api.github.com/repos/{issue_path}"
    try:
        response = requests.get(issue_api_url, headers=HEADERS)
        response.raise_for_status()
        response_json = response.json()
        return f"Issue Title: {response_json.get('title', 'No title found')}\nIssue Description: {response_json.get('body', 'No description found')}"
    except Exception as e:
        print(f"An error occurred while getting issue info: {e}")
        traceback.print_exc()
        return "Sorry, an error occurred while fetching the issue information."

# --- Helper Function for CSV Data ---
@cachetools.cached(cache=members_cache, key=lambda: 'members_list')
def get_active_members_from_public_sheet() -> str:
    response = requests.get(M4M_PARTICIPANT_LIST)
    response.raise_for_status()
    f = StringIO(response.text)
    reader = csv.DictReader(f)
    active_members_list = []
    for row in reader:
        if not M4M_ONLY_CONSIDER_AFFILIATION or (row.get("Teams", "") or row.get("Role", "")):
            formatted_string = f"{row.get('Full Name')}: (Role): {row.get('Role', 'N/A')}, (Teams): {row.get('Teams', 'N/A')}, (WhatsApp Mobile Number): {row.get('WhatsApp Mobile number', 'N/A')}, (Email): {row.get('For Emailing')}"
            active_members_list.append(formatted_string)
    random.shuffle(active_members_list)
    return "\n".join(active_members_list)

@cachetools.cached(cache=fallback_cache, key=lambda: 'fallback_recommendations')
def recommend_assignees_fallback_heuristic() -> str:
    # Recommend assignees least frequently assigned to issues as a fallback (using GitHub GraphQL).
    # Fallback if OpenAI API times out
    query = """
    query {
    search(
        type: ISSUE,
        query: "org:KellisLab Mantis in:repository",
        first: 100
    ) {
        nodes {
        ... on Issue {
            title
            url
            repository {
            name
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
    try:
        response = requests.post("https://api.github.com/graphql", headers=HEADERS, data=json.dumps({"query": query}))
        response.raise_for_status()
        data = response.json()
        all_assignees = []
        for node in data["data"]["search"]["nodes"]:
            try:
                logins = node["assignees"]["nodes"]
                for login in logins:
                    all_assignees.append(login["login"])
            except:
                continue
        assignee_counts = Counter(all_assignees)
        least_recorded_assignees_with_counts = assignee_counts.most_common()[:-8:-1]
        final_message = "I had trouble connecting to OpenAI, but I found some members from GitHub who haven't been assigned to a task frequently. I'd recommending assigning the following people:\n\n"
        for assignee, count in least_recorded_assignees_with_counts:
            final_message = final_message + f"{assignee} (GitHub username), assigned {str(count)} times.\n"
        return final_message
    except Exception as e:
        return "Sorry, I'm having trouble accessing OpenAI and GitHub right now. Please try this command again later and let one of the developers know."

# --- Assignee Recommendation Functions ---
async def recommend_assignees_primary(task_given: str) -> str:
    try:
        if FORCE_FALLBACK_TEST:
            raise openai.APIStatusError(message="Forcing fallback for testing purposes.", response=None, body=None)
        
        active_members_string = await asyncio.get_event_loop().run_in_executor(None, get_active_members_from_public_sheet)
        user_prompt = (
            "You are a helpful assistant that recommends assignees for a GitHub task. Only list 5-8 assignees using markdown: "
            "'1) Assignee Name ((Country Emoji + Country Code only if given) + Phone Number, Email). Reason for choosing: (Explanation)'. "
            "No prelude, epilogue, or follow-up questions.\n\n"
            f"Task in need of an assignee:\n\n{task_given}\n"
            f"Available assignees: {active_members_string}\n\n"
        )
        response = await run_assistant(user_prompt)
        return response + "\n\nLet me know if I should recommend more assignees!"
    except Exception as e:
        print(f"OpenAI API call failed. Falling back to heuristic. Error: {e}")
        return await asyncio.get_event_loop().run_in_executor(None, recommend_assignees_fallback_heuristic)


async def recommend_assignees_secondary(past_replies: list[str], task_given: str, user_messages: list[str]) -> str:
    try:
        if FORCE_FALLBACK_TEST:
            raise openai.APIStatusError(message="Forcing fallback for testing purposes.", response=None, body=None)

        conversation_context = ""
        for i in range(len(user_messages)):
            conversation_context += f"User request {i+1}: {user_messages[i]}\n"
            if i < len(past_replies):
                conversation_context += f"Bot reply {i+1}: {past_replies[i]}\n"
        active_members_string = await asyncio.get_event_loop().run_in_executor(None, get_active_members_from_public_sheet)
        user_prompt = (
            "You are a helpful assistant recommending assignees for a GitHub task. Consider all previous user requests and your past replies. "
            "Only list 5-8 assignees using markdown: "
            "'1) Assignee Name ((Country Emoji + Country Code only if given) + Phone Number, Email). Reason for choosing: (Explanation)'."
            "No prelude, epilogue, or follow-up questions.\n\n"
            f"Task in need of an assignee:\n\n{task_given}\n"
            f"Conversation so far:\n{conversation_context}\n"
            f"Available assignees: {active_members_string}\n\n"
        )
        response = await run_assistant(user_prompt)
        return response + "\n\nLet me know if I should recommend more assignees!"
    except Exception as e:
        print(f"OpenAI API call failed. Falling back to heuristic. Error: {e}")
        return await asyncio.get_event_loop().run_in_executor(None, recommend_assignees_fallback_heuristic)


# --- Cog Definition ---
class MantisAssigneeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: dict[int, dict[str, Any]] = defaultdict(dict)
        self.task_given: dict[int, dict[str, Any]] = defaultdict(dict)
        self.replies: dict[int, list[str]] = defaultdict(dict)
        self.user_messages: dict[int, list[str]] = defaultdict(dict)

    @commands.Cog.listener('on_message')
    async def on_message_reply(self, message: discord.Message):
        if message.author.bot or not message.reference:
            return
        user_id = message.author.id
        if user_id not in self.sessions:
            return
        if user_id not in self.replies:
            self.replies[user_id] = []
        if user_id not in self.user_messages:
            self.user_messages[user_id] = []
        if user_id not in self.task_given:
            self.task_given[user_id] = {}

        session = self.sessions[user_id]
        stage = session.get("stage", 0)
        self.user_messages[user_id].append(message.content)

        async with message.channel.typing():
            if stage == 0:
                await message.reply("Here are some people who I think might be a good fit for the task you gave me...")
                github_url_pattern = r'https://github\.com/([^/]+)/([^/]+)/issues/(\d+)'
                match = re.search(github_url_pattern, message.content)
                if match:
                    owner, repo, issue_number = match.groups()
                    issue_path = f"{owner}/{repo}/issues/{issue_number}"
                    self.task_given[user_id]["task"] = await self.bot.loop.run_in_executor(None, get_issue_info_from_github, issue_path)
                    reply = await recommend_assignees_primary(self.task_given[user_id]["task"])
                else:
                    reply = await recommend_assignees_primary(message.content)
                self.replies[user_id].append(reply)
                await message.reply(reply)
                session["stage"] = 1
            else:
                await message.reply("Here are some people who I think might be a good fit for the task you gave me...")
                reply = await recommend_assignees_secondary(
                    self.replies[user_id],
                    self.task_given[user_id].get("task", ""),
                    self.user_messages[user_id]
                )
                self.replies[user_id].append(reply)
                await message.reply(reply)

    @app_commands.command(name="m4m_find_assignee", description="Find an assignee for your task (via a description or GitHub task)")
    async def m4m_find_assignee_command(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        self.sessions[user_id] = {"stage": 0}
        await interaction.response.send_message(
            "Hi, I'll help you find an assignee for your task. Just **hover over this message and click reply** to give me a GitHub URL or description of the task."
        )


# --- Setup Function ---
async def setup(bot: commands.Bot):
    await bot.add_cog(MantisAssigneeCog(bot))