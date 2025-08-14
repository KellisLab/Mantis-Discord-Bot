import discord
from discord.ui import Button, View
from discord.ext import commands
from discord import app_commands
from config import M4M_PARTICIPANT_LIST, M4M_ONLY_CONSIDER_AFFILIATION, HEADERS, ASSISTANT_ID, OPENAI_API_KEY
from typing import Dict, Any
import requests
from io import StringIO
import csv
import traceback
import random
import re
import asyncio
import time
import openai

client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)

# --- Helper Function to Run Assistant ---
async def run_assistant(user_message: str, timeout_seconds: int = 90) -> str:
    try:
        thread = await client.beta.threads.create()
        await client.beta.threads.messages.create(thread_id=thread.id, role="user", content=user_message)
        run = await client.beta.threads.runs.create(thread_id=thread.id, assistant_id=ASSISTANT_ID)
        start_time = time.time()
        while run.status in ["queued", "in_progress"]:
            if time.time() - start_time > timeout_seconds:
                return "The assistant took too long to respond. Please try again."
            await asyncio.sleep(1)
            run = await client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
        if run.status == "completed":
            messages = await client.beta.threads.messages.list(thread_id=thread.id)
            response_content = messages.data[0].content[0].text.value
            return response_content.strip()
        else:
            return f"The assistant run failed with status: {run.status}"
    except Exception as e:
        print(f"An error occurred while running the assistant: {e}")
        traceback.print_exc()
        return "Sorry, an error occurred while communicating with the assistant."

# --- Helper Function for GitHub API ---
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
def get_active_members_from_public_sheet() -> str:
    response = requests.get(M4M_PARTICIPANT_LIST)
    response.raise_for_status()
    wanted_status = ["6=TopPerformer", "5=Onboarded+Active", "4C=JustOnboarded", "4=Onboarded+LessActive"]
    f = StringIO(response.text)
    reader = csv.DictReader(f)
    active_members_list = []
    for row in reader:
        current_status = row.get("eid", "")
        if current_status in wanted_status:
            if M4M_ONLY_CONSIDER_AFFILIATION:
                team_affiliation = row.get("Teams", "")
                role_affiliation = row.get("Role", "")
                if team_affiliation or role_affiliation:
                    formatted_string = f"{row.get('Full Name')}: (Role): {row.get('Role', 'N/A')}, (Teams): {row.get('Teams', 'N/A')}, (WhatsApp Mobile Number): {row.get('WhatsApp Mobile number', 'N/A')}, (Email): {row.get('For Emailing')}"
                    active_members_list.append(formatted_string)
            else:
                formatted_string = f"{row.get('Full Name')}: (Role): {row.get('Role', 'N/A')}, (Teams): {row.get('Teams', 'N/A')}, (WhatsApp Mobile Number): {row.get('WhatsApp Mobile number', 'N/A')}, (Email): {row.get('For Emailing')}"
                active_members_list.append(formatted_string)
    random.shuffle(active_members_list)
    return "\n".join(active_members_list)

# --- Assignee Recommendation Functions ---
async def recommend_assignees_primary(bot: commands.Bot, task_given: str) -> str:
    user_prompt = (
        "You are a helpful assistant that recommends assignees for a GitHub task. Only list 5-8 assignees using markdown: "
        "'1) Assignee Name ((Country Emoji + Country Code only if given) + Phone Number, Email). Reason for choosing: (Explanation)'. "
        "No prelude, epilogue, or follow-up questions.\n\n"
        f"Task in need of an assignee:\n\n{task_given}\n"
        f"Available assignees: {get_active_members_from_public_sheet()}\n\n"
    )
    response = await run_assistant(bot, user_prompt)
    return response + "\n\nLet me know if I should recommend more assignees - feel free to give me extra information about who you are looking for if need be!"


async def recommend_assignees_secondary(bot: commands.Bot, past_replies: list[str], task_given: str, user_messages: list[str]) -> str:
    """
    Generates assignee recommendations considering all previous bot replies and user requests.
    """
    conversation_context = ""
    for i in range(len(user_messages)):
        conversation_context += f"User request {i+1}: {user_messages[i]}\n"
        if i < len(past_replies):
            conversation_context += f"Bot reply {i+1}: {past_replies[i]}\n"
    user_prompt = (
        "You are a helpful assistant recommending assignees for a GitHub task. Consider all previous user requests and your past replies. "
        "Only list 5-8 assignees using markdown: "
        "'1) Assignee Name ((Country Emoji + Country Code only if given) + Phone Number, Email). Reason for choosing: (Explanation)'."
        "No prelude, epilogue, or follow-up questions.\n\n"
        f"Task in need of an assignee:\n\n{task_given}\n"
        f"Conversation so far:\n{conversation_context}\n"
        f"Available assignees: {get_active_members_from_public_sheet()}\n\n"
    )
    response = await run_assistant(bot, user_prompt)
    return response + "\n\nLet me know if I should recommend more assignees - feel free to give me extra information about who you are looking for if need be!"

# --- Cog Definition ---
class MantisAssigneeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: dict[int, dict[str, Any]] = {}
        self.task_given: dict[int, dict[str, Any]] = {}
        self.replies: dict[int, list[str]] = {}
        self.user_messages: dict[int, list[str]] = {}

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
                    self.task_given[user_id]["task"] = get_issue_info_from_github(issue_path)
                    reply = await recommend_assignees_primary(self.bot, self.task_given[user_id]["task"])
                else:
                    reply = await recommend_assignees_primary(self.bot, message.content)
                self.replies[user_id].append(reply)
                await message.reply(reply)
                session["stage"] = 1
            else:
                await message.reply("Here are some people who I think might be a good fit for the task you gave me...")
                reply = await recommend_assignees_secondary(
                    self.bot,
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
