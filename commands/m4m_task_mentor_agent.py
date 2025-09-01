import discord
from discord.ui import Button, View
from discord.ext import commands
from discord import app_commands
from config import GITHUB_ORG_NAME, HEADERS, OPENAI_API_KEY, GITHUB_TOKEN, M4M_MENTOR_LIST, ASSISTANT_ID
import requests
import openai
import re
import csv
from io import StringIO
import random
import traceback
import asyncio
import time
from typing import Dict, Any

# Initialize Asynchronous OpenAI client for discord.py
client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)

DISCORD_CHAR_LIMIT = 2000

# --- Assistant Runner Helper ---
async def run_assistant(assistant_id: str, user_message: str, timeout_seconds: int = 90) -> str:
    """
    Creates a thread, sends a message, runs the assistant using the native openai
    library, and returns the response.
    """
    try:
        # Create a new thread for the conversation
        thread = await client.beta.threads.create()

        # Add the user's message to the thread
        await client.beta.threads.messages.create(
            thread_id=thread.id,
            role="user",
            content=user_message
        )

        # Create a run to process the thread with the assistant
        run = await client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=assistant_id
        )

        # Poll the run status until it's completed or times out
        start_time = time.time()
        while run.status in ["queued", "in_progress"]:
            if time.time() - start_time > timeout_seconds:
                return "The assistant took too long to respond. Please try again."
            await asyncio.sleep(1)  # Wait a second before checking again
            run = await client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)

        # If the run completed successfully, retrieve the assistant's message
        if run.status == "completed":
            messages = await client.beta.threads.messages.list(thread_id=thread.id)
            # The assistant's response is the first message in the list
            response_content = messages.data[0].content[0].text.value
            return response_content.strip()
        else:
            # Handle other run statuses (e.g., failed, cancelled)
            return f"The assistant run failed with status: {run.status}"

    except Exception as e:
        print(f"An error occurred while running the assistant: {e}")
        traceback.print_exc()
        return "Sorry, an error occurred while communicating with the assistant."

# --- GitHub and Mentor Functions (Now using a Single Assistant) ---

def get_org_tasks():
    # This function remains synchronous as it deals with blocking network requests.
    all_tasks = []
    repos_url = f"https://api.github.com/orgs/{GITHUB_ORG_NAME}/repos"
    try:
        response = requests.get(repos_url, headers=HEADERS)
        response.raise_for_status()
        repos = response.json()
        for repo in repos:
            repo_name = repo['name']
            if "Mantis" in repo_name:
                issues_url = f"https://api.github.com/repos/{GITHUB_ORG_NAME}/{repo_name}/issues"
                params = {'state': 'open', 'assignee': 'none'}
                issues_response = requests.get(issues_url, headers=HEADERS, params=params)
                issues_response.raise_for_status()
                issues = issues_response.json()
                if issues:
                    all_tasks.append(f"--- Tasks from {repo_name} ---")
                    for issue in issues:
                        if "pull_request" not in issue:
                            all_tasks.append(f"- {issue['title']} ({issue['html_url']})")
                    all_tasks.append("")
    except requests.exceptions.RequestException as e:
        return f"Error retrieving tasks from GitHub: {str(e)}"
    if not all_tasks:
        return "No open tasks found in any repository."
    return "\n".join(all_tasks)

async def recommend_tasks_primary(user_interests_text: str) -> str:
    """
    Generates initial task recommendations by sending the original full prompt to the assistant.
    """
    loop = asyncio.get_running_loop()
    tasks = await loop.run_in_executor(None, get_org_tasks)
    user_prompt = (
        "You are a helpful assistant that recommends GitHub tasks. Based on the user's interests, "
        "recommend relevant tasks from the provided list. Only list 5-8 tasks in the format '1) Task (link)'. "
        "Do not include any other text.\n\n"
        f"User interests: {user_interests_text}\n\n"
        f"Available tasks:\n\n{tasks}"
    )
    assistant_id = ASSISTANT_ID
    if not assistant_id:
        return "Error: Assistant ID not configured"
    return await run_assistant(assistant_id, user_prompt)

async def recommend_tasks_secondary(existing_tasks_context: str) -> str:
    """
    Generates a new set of tasks by sending the original full prompt for secondary recommendations.
    """
    loop = asyncio.get_running_loop()
    tasks = await loop.run_in_executor(None, get_org_tasks)
    user_prompt = (
        "You are a helpful assistant that recommends GitHub tasks. The user was not satisfied with the previous recommendations. "
        "Please provide a new set of 5-8 unique tasks from the available tasks. Do not recommend any of the tasks from "
        "the previous list. Only list the new tasks in the format '1) Task (link)'. Do not include any other text.\n\n"
        f"Previous recommendations:\n{existing_tasks_context}\n\n"
        f"Available tasks:\n\n{tasks}"
    )
    assistant_id = ASSISTANT_ID
    if not assistant_id:
        return "Error: Assistant ID not configured"
    return await run_assistant(assistant_id, user_prompt)

def assign_task_to_user(github_username: str, issue_url: str) -> str:
    github_headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    match = re.search(r"https://github.com/([^/]+)/([^/]+)/issues/(\d+)", issue_url)
    if not match:
        return "Invalid GitHub issue URL provided."
    owner, repo, issue_number = match.groups()
    assignees_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/assignees"
    try:
        payload = {"assignees": [github_username]}
        response = requests.post(assignees_url, headers=github_headers, json=payload)
        response.raise_for_status()
        return f"Task #{issue_number} in {repo} has been assigned to **{github_username}**. Happy coding! 🧑‍💻"
    except requests.exceptions.RequestException as e:
        status_code = getattr(e.response, 'status_code', 'unknown')
        return f"Could not assign you to the task. (GitHub returned status {status_code})"

def get_mentors_from_public_sheet():
    # This function remains synchronous
    response = requests.get(M4M_MENTOR_LIST)
    response.raise_for_status()
    mentors = []
    f = StringIO(response.text)
    reader = csv.DictReader(f)
    for row in reader:
        open_status = row.get("Open for Mentees", "").strip().lower()
        if "no" not in open_status:
            mentors.append({
                "full_name": row.get("Full Name", "Unknown"),
                "whatsapp": row.get("WhatsApp Mobile number", "N/A"),
                "teams": row.get("Teams", "N/A")
            })
    return mentors

async def recommend_mentors_via_assistant(mentors: list, user_interests_text: str, assigned_tasks_text: str) -> list:
    """
    Recommends mentors using the single assistant and parses the response.
    """
    mentor_lookup = {m['full_name'].lower(): m for m in mentors}
    mentor_list_text = "\n".join([f"- {m['full_name']} (Teams: {m['teams']})" for m in mentors])
    user_prompt = (
        "You are a helpful assistant that recommends mentors for Mantis, a scientific computing platform for analyzing large biomedical datasets. "
        "Based on the user's interests, assigned task, and team preferences, recommend 3-5 mentors from the provided list. "
        "For each recommendation, provide the mentor's full name exactly as listed and a detailed explanation of why they are specifically good for this user, "
        "considering their teams, the user's technical background, and how their skills align with Mantis development needs.\n\n"
        "**Mantis Context**: Mantis involves distributed computing, data analysis pipelines, AI/ML integration, scientific workflows, "
        "drug discovery algorithms, biomedical data processing, and scalable infrastructure. Match mentors based on these technical areas.\n\n"
        "**Critical Team Matching Rules**: "
        "- If user mentions 'Team X' or interest in X area, prioritize mentors who have that team name in their teams list"
        "- 'Team Integrations' or 'Integrations' → mentors with 'Integrations' in teams"
        "- 'Team Drugs' or 'Drugs' → mentors with 'Drugs' in teams"
        "- 'Team Compute' or 'Compute' → mentors with 'Compute' in teams"
        "- 'Team Science' or 'Science' → mentors with 'Science' in teams"
        "- 'M4M' or 'Mantis4Mantis' → mentors with 'M4M' or 'Mantis4Mantis' in teams"
        "- Look for exact team name matches first, then consider related technical skills"
        "- Always prioritize mentors whose teams directly align with user's stated team interests\n\n"
        "Use the following format for each recommendation and nothing else:\n"
        "Mentor Name: [Full Name]\n"
        "Reason: [Your detailed, specific explanation of why this mentor is perfect for this user's background and goals]\n\n"
        f"Available Mentors:\n{mentor_list_text}\n\n"
        f"User Interests (contains team preferences and skills):\n{user_interests_text}\n\n"
        f"Assigned Task:\n{assigned_tasks_text}"
    )
    assistant_id = ASSISTANT_ID
    if not assistant_id:
        return []
    response_text = await run_assistant(assistant_id, user_prompt)
    recommendations = []
    pattern = re.compile(r"Mentor Name:\s*(.*?)\s*\nReason:\s*(.*)", re.IGNORECASE)
    matches = pattern.findall(response_text)
    for name, reason in matches:
        mentor_data = mentor_lookup.get(name.strip().lower())
        if mentor_data:
            recommendations.append({
                "full_name": mentor_data['full_name'],
                "whatsapp": mentor_data['whatsapp'],
                "teams": mentor_data['teams'],
                "reason": reason.strip()
            })

    if not recommendations: # Fallback to random mentors
        random_mentors = random.sample(mentors, min(3, len(mentors)))
        return [{**m, "reason": "Recommended as a generally available and experienced mentor."} for m in random_mentors]
    return recommendations


async def explain_skills_relation_to_mantis(user_interests_text: str) -> str:
    """
    Generates a personalized explanation of how the user's skills relate to Mantis.
    """
    user_prompt = (
        "You are explaining how a user's background relates to contributing to Mantis, a scientific computing platform for analyzing large biomedical datasets. "
        "Based on their skills and projects (NOT their team preferences), write a brief 1-2 sentence explanation of how their technical background applies to Mantis development. "
        "Focus only on connecting their programming skills, AI/ML experience, or technical projects to Mantis areas like distributed computing, data analysis pipelines, "
        "AI/ML integration, scientific workflows, drug discovery algorithms, biomedical data processing, and scalable infrastructure. "
        "Do not mention teams or future contributions - only focus on their existing technical skills. Use simple, conversational language without bullets or bold formatting.\n\n"
        f"User background and interests:\n{user_interests_text}"
    )
    assistant_id = ASSISTANT_ID
    if not assistant_id:
        return "• **Your background** → Valuable contributions to Mantis development"
    return await run_assistant(assistant_id, user_prompt)

async def handle_mentor_followup_question(user_question: str, user_interests: str, recommended_mentors: list, assigned_task: str = "") -> str:
    """
    Handles follow-up questions about mentor recommendations using the LLM.
    """
    mentor_list_text = "\n".join([f"- {m['full_name']} (Teams: {m['teams']}) - {m['reason']}" for m in recommended_mentors])
    user_prompt = (
        "You are helping a user understand mentor recommendations for contributing to Mantis, a scientific computing platform. "
        "The user has a follow-up question about the mentors that were recommended to them. Answer their question helpfully and conversationally. "
        "If they ask for more mentors, explain that you've already shown the best matches but can suggest looking at the full Google Sheet. "
        "If they ask why someone was picked, give a detailed explanation. Be concise but informative.\n\n"
        f"User's original interests: {user_interests}\n\n"
        f"Assigned task: {assigned_task or 'General mentorship'}\n\n"
        f"Recommended mentors:\n{mentor_list_text}\n\n"
        f"User's follow-up question: {user_question}"
    )
    assistant_id = ASSISTANT_ID
    if not assistant_id:
        return "I'd be happy to help, but I'm having trouble accessing my knowledge base right now. Please try again later."
    return await run_assistant(assistant_id, user_prompt)

async def draft_outreach_message(user_interests_text: str, assigned_tasks_text: str, mentor_name: str) -> str:
    """
    Drafts a WhatsApp outreach message by sending the original prompt to the assistant.
    """
    user_prompt = (
        f"Write a friendly, concise WhatsApp message that a user could send to a mentor named {mentor_name}. "
        f"The user is interested in these areas:\n{user_interests_text}\n\n"
        f"The user plans to work on these tasks:\n{assigned_tasks_text}\n\n"
        "The message should be polite, enthusiastic, and ask for mentorship."
    )
    assistant_id = ASSISTANT_ID
    if not assistant_id:
        return "Error: Assistant ID not configured"
    return await run_assistant(assistant_id, user_prompt)


### --- Cog and Discord Views ---

class MantisCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: Dict[int, Dict[str, Any]] = {}

    class M4MView(View):
        def __init__(self, cog: 'MantisCog', user_id: int, *, timeout=180):
            super().__init__(timeout=timeout)
            self.cog = cog
            self.user_id = user_id

        @discord.ui.button(label="Find more tasks", style=discord.ButtonStyle.primary)
        async def find_more_button(self, interaction: discord.Interaction, button: Button):
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(view=self)
            
            session = self.cog.sessions.get(self.user_id, {})
            last_msg_id = session.get("last_bot_message_id")
            if last_msg_id:
                last_msg = interaction.channel.get_partial_message(last_msg_id)
                await last_msg.reply("Searching for more tasks...", mention_author=False)
            else:
                await interaction.followup.send("Searching for more tasks...", ephemeral=True)


            async with interaction.channel.typing():
                existing_context = session.get("issue_context", "")
                new_tasks = await recommend_tasks_secondary(existing_context)
                session["issue_context"] = existing_context + "\n\n" + new_tasks
                self.cog.sessions[self.user_id] = session

            final_message = ("Here are some more tasks you might like:\n\n" + new_tasks)[:DISCORD_CHAR_LIMIT]
            sent_message = await interaction.followup.send(final_message)
            
            session["last_bot_message_id"] = sent_message.id
            self.cog.sessions[self.user_id] = session

            new_view = self.cog.M4MView(self.cog, self.user_id)
            await sent_message.edit(content=f"{sent_message.content}\n\nWhat would you like to do next?", view=new_view)
            
        @discord.ui.button(label="I have a task, assign me", style=discord.ButtonStyle.success)
        async def assign_task_button(self, interaction: discord.Interaction, button: Button):
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(view=self)
            session = self.cog.sessions.get(self.user_id, {})
            auto_github_username = None
            try:
                mapping = await self.cog.bot.member_cache.get_mapping()
                user = interaction.user
                candidate_names = {user.name, user.global_name, user.display_name}
                if user.discriminator != "0":
                    candidate_names.add(f"{user.name}#{user.discriminator}")
                lower_candidates = {str(c).lower() for c in candidate_names if c}
                for gh_username, info in mapping.items():
                    if isinstance(info, dict) and info.get("discord_username", "").lower() in lower_candidates:
                        auto_github_username = gh_username
                        break
            except Exception:
                auto_github_username = None

            if auto_github_username:
                session["github_username"] = auto_github_username
                session["stage"] = "awaiting_issue_url"
                sent_message = await interaction.followup.send(
                    f"I found your GitHub username from the member mapping: **@{auto_github_username}**.\n"
                    "Please reply with the full **GitHub issue URL** you'd like to be assigned to.",
                )
            else:
                session["stage"] = "awaiting_github_username"
                sent_message = await interaction.followup.send(
                    "Great! Please reply to this message with your **GitHub username**.",
                )
            
            session["last_bot_message_id"] = sent_message.id
            self.cog.sessions[self.user_id] = session

    class MentorButton(Button):
        def __init__(self, cog: 'MantisCog', mentor_name: str, whatsapp_number: str, user_id: int, user_interests_text: str, assigned_tasks_text: str):
            label = mentor_name if mentor_name and mentor_name.strip() else "View Mentor"
            super().__init__(label=label[:80], style=discord.ButtonStyle.secondary)
            self.cog = cog
            self.mentor_name = mentor_name
            self.whatsapp_number = whatsapp_number
            self.user_id = user_id
            self.user_interests_text = user_interests_text
            self.assigned_tasks_text = assigned_tasks_text

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True, thinking=True)
            draft = await draft_outreach_message(self.user_interests_text, self.assigned_tasks_text, self.mentor_name)
            await interaction.followup.send(f"Here is a WhatsApp message you can send to **{self.mentor_name}** ({self.whatsapp_number}):\n\n> {draft}", ephemeral=True)

    class MentorSelectionView(View):
        def __init__(self, cog: 'MantisCog', user_id: int, mentors: list, user_interests_text: str, assigned_tasks_text: str, *, timeout=300):
            super().__init__(timeout=timeout)
            for mentor in mentors:
                self.add_item(cog.MentorButton(
                    cog,
                    mentor.get('full_name'),
                    mentor.get('whatsapp', "N/A"),
                    user_id,
                    user_interests_text,
                    assigned_tasks_text
                ))

    @commands.Cog.listener('on_message')
    async def on_message_reply(self, message: discord.Message):
        if message.author.bot or not message.reference:
            return

        user_id = message.author.id
        session = self.sessions.get(user_id)
        if not session:
            return

        # Crucial check: only process the reply if it is to the last message the bot sent for this session.
        if message.reference.message_id != session.get("last_bot_message_id"):
            return

        stage = session.get("stage")
        try:
            if stage == "mentor_interests":
                interests = message.content.strip()
                session["user_interests"] = interests
                sent_message = await message.reply("Thanks! Let me find mentors who match your interests and skills...", mention_author=False)
                session["last_bot_message_id"] = sent_message.id

                async with message.channel.typing():
                    loop = asyncio.get_running_loop()
                    mentors = await loop.run_in_executor(None, get_mentors_from_public_sheet)
                    # For mentor-only mode, we don't have an assigned task, so we'll use a generic message
                    assigned_task_placeholder = "Looking for mentorship to get started with Mantis contributions"
                    recommended_mentors = await recommend_mentors_via_assistant(mentors, interests, assigned_task_placeholder)

                    # Send context message first
                    skills_explanation = await explain_skills_relation_to_mantis(interests)
                    context_message = f"## How Your Skills Relate to Mantis\n{skills_explanation}"
                    await message.channel.send(context_message)
                    
                    # Build mentor message with length checking
                    mentor_message = "## Recommended Mentors\nBased on your background, here are mentors who can specifically help you contribute:\n"
                    for mentor in recommended_mentors:
                        mentor_entry = f"\n**{mentor['full_name']}** (Teams: {mentor['teams']})\n*{mentor['reason']}*\n"
                        if len(mentor_message + mentor_entry) > 1800:  # Leave room for footer
                            await message.channel.send(mentor_message)
                            mentor_message = f"**(continued)**{mentor_entry}"
                        else:
                            mentor_message += mentor_entry
                    
                    mentor_message += "\nIf you want to see other mentors who are open to taking on new mentees, check out this [Google Sheet](https://docs.google.com/spreadsheets/d/128HP4RuiJdRqe9Ukd9HboEgBq6GuA37N2vdy2ej07ok/edit?usp=sharing) for the entire list.\n\n**Questions?** Reply to follow-up.\n"

                    view = self.MentorSelectionView(self, user_id, recommended_mentors, interests, assigned_task_placeholder)
                    sent_message = await message.channel.send(mentor_message[:DISCORD_CHAR_LIMIT], view=view)
                    session["last_bot_message_id"] = sent_message.id
                    
                    # Set up follow-up stage
                    session["stage"] = "mentor_followup"
                    session["recommended_mentors"] = recommended_mentors
                    session["user_interests"] = interests
                    session["assigned_task"] = assigned_task_placeholder
                    self.sessions[user_id] = session

            elif stage == 0:
                interests = message.content.strip()
                session["user_interests"] = interests
                sent_message = await message.reply("Thanks! Finding some suitable tasks based on your interests...", mention_author=False)
                session["last_bot_message_id"] = sent_message.id

                async with message.channel.typing():
                    recommended_tasks = await recommend_tasks_primary(interests)
                session["issue_context"] = recommended_tasks
                intro = "Based on what you told me, I think you'll like these tasks:\n\n"
                
                # Send the final response with buttons in one message
                final_content = f"{intro}{recommended_tasks}\n\nWhat would you like to do next?"
                button_message = await message.channel.send(
                    final_content[:DISCORD_CHAR_LIMIT],
                    view=self.M4MView(self, user_id)
                )
                session["last_bot_message_id"] = button_message.id
                session["stage"] = 1
                self.sessions[user_id] = session

            elif stage == "awaiting_github_username":
                session["github_username"] = message.content.strip()
                session["stage"] = "awaiting_issue_url"
                
                sent_message = await message.reply("Got it! Now, please reply with the full **GitHub issue URL** you'd like to be assigned to.", mention_author=False)
                session["last_bot_message_id"] = sent_message.id
                self.sessions[user_id] = session

            elif stage == "awaiting_issue_url":
                github_username = session.get("github_username")
                if not github_username:
                    sent_message = await message.reply("I don't have your GitHub username yet. Please send it first.", mention_author=False)
                    session["last_bot_message_id"] = sent_message.id
                    session["stage"] = "awaiting_github_username"
                    self.sessions[user_id] = session
                    return

                issue_url = message.content.strip()
                # sent_message = await message.reply("Perfect. Let me try to assign that to you now...", mention_author=False)
                # session["last_bot_message_id"] = sent_message.id
                
                async with message.channel.typing():
                    assign_response = assign_task_to_user(github_username, issue_url)
                
                # Update the last message ID to the assign response
                sent_message = await message.channel.send(assign_response)
                session["last_bot_message_id"] = sent_message.id

                if "has been assigned" in assign_response:
                    try:
                        match = re.search(r"https://github.com/([^/]+)/([^/]+)/issues/(\d+)", issue_url)
                        if match:
                            owner, repo, issue_num = match.groups()
                            issue_api_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_num}"
                            res = requests.get(issue_api_url, headers=HEADERS)
                            res.raise_for_status()
                            title = res.json().get("title", "Unnamed Task")
                            session["assigned_task"] = f"{title} ({issue_url})"
                        else:
                            session["assigned_task"] = issue_url
                    except Exception:
                        session["assigned_task"] = issue_url

                    sent_message = await message.channel.send("Now that you have a task, let's find you a mentor! Searching...")
                    session["last_bot_message_id"] = sent_message.id

                    async with message.channel.typing():
                        interests = session.get("user_interests", "")
                        tasks = session.get("assigned_task", "")
                        loop = asyncio.get_running_loop()
                        mentors = await loop.run_in_executor(None, get_mentors_from_public_sheet)
                        recommended_mentors = await recommend_mentors_via_assistant(mentors, interests, tasks)

                        # Send context message first
                        skills_explanation = await explain_skills_relation_to_mantis(interests)
                        context_message = f"## How Your Skills Relate to Mantis\n{skills_explanation}"
                        await message.channel.send(context_message)
                        
                        # Build mentor message with length checking
                        mentor_message = "## Recommended Mentors\nHere are mentors who can help you contribute effectively:\n"
                        for mentor in recommended_mentors:
                            mentor_entry = f"\n**{mentor['full_name']}** (Teams: {mentor['teams']})\n*{mentor['reason']}*\n"
                            if len(mentor_message + mentor_entry) > 1800:  # Leave room for footer
                                await message.channel.send(mentor_message)
                                mentor_message = mentor_entry
                            else:
                                mentor_message += mentor_entry
                        
                        mentor_message += "\nIf you want to see other mentors who are open to taking on new mentees, check out this [Google Sheet](https://docs.google.com/spreadsheets/d/128HP4RuiJdRqe9Ukd9HboEgBq6GuA37N2vdy2ej07ok/edit?usp=sharing) for the entire list.\n\n**Questions?** Reply to ask me why I picked someone specific, request different mentors, or ask anything else about these recommendations!"

                        view = self.MentorSelectionView(self, user_id, recommended_mentors, interests, tasks)
                        sent_message = await message.channel.send(mentor_message[:DISCORD_CHAR_LIMIT], view=view)
                        session["last_bot_message_id"] = sent_message.id
                        
                        # Set up follow-up stage
                        session["stage"] = "mentor_followup"
                        session["recommended_mentors"] = recommended_mentors
                        session["user_interests"] = interests
                        session["assigned_task"] = tasks
                        self.sessions[user_id] = session
                else:
                    sent_message = await message.channel.send("Since the assignment didn't succeed, mentor recommendations are unavailable. You can try assigning another task!")
                    session["last_bot_message_id"] = sent_message.id

                # Don't remove session - keep it for potential follow-ups
                
            elif stage == "mentor_followup":
                user_question = message.content.strip()
                interests = session.get("user_interests", "")
                recommended_mentors = session.get("recommended_mentors", [])
                assigned_task = session.get("assigned_task", "")
                
                # Check if user wants to exit or is done
                if any(word in user_question.lower() for word in ["thanks", "thank you", "done", "that's all", "no more"]):
                    await message.reply("You're welcome! Feel free to reach out to any of the mentors. Good luck with your Mantis contributions!", mention_author=False)
                    self.sessions.pop(user_id, None)
                    return
                
                sent_message = await message.reply("Let me think about that...", mention_author=False)
                session["last_bot_message_id"] = sent_message.id
                
                async with message.channel.typing():
                    response = await handle_mentor_followup_question(user_question, interests, recommended_mentors, assigned_task)
                
                follow_up_message = f"{response}\n\n*Feel free to ask more questions about the mentors, or say 'thanks' when you're ready to reach out to them!*"
                sent_message = await message.channel.send(follow_up_message[:DISCORD_CHAR_LIMIT])
                session["last_bot_message_id"] = sent_message.id
                self.sessions[user_id] = session

        except Exception:
            traceback.print_exc()
            await message.channel.send("Sorry, something went wrong. Please try running the `/m4m` or `/m4m_mentor` command again.")
            self.sessions.pop(user_id, None)

    @app_commands.command(name="m4m", description="Find a task and mentor to contribute to Mantis.")
    async def m4m_task_mentor_agent(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        self.sessions[user_id] = {"stage": 0, "last_bot_message_id": None}
        
        await interaction.response.send_message(
            "Hi! I'll help you find a task and mentor to begin contributing to Mantis\n\n"
            "Can you **hover over this message and click 'Reply'** to tell me about:\n\n"
            "• **Teams** you're interested in (Integrations, Compute, Drugs, Science, etc.)\n"
            "• **AI/ML or programming projects** you've built (your skills likely apply to Mantis workflows!)\n\n"
            "This helps me recommend tasks that match your technical background and mentors who can guide your contributions!"
        )
        initial_message = await interaction.original_response()
        self.sessions[user_id]["last_bot_message_id"] = initial_message.id

    @app_commands.command(name="m4m_mentor", description="Find a mentor based on your skills and interests.")
    async def m4m_mentor_only(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        self.sessions[user_id] = {"stage": "mentor_interests", "last_bot_message_id": None}
        
        await interaction.response.send_message(
            "Hi! I'll help you find a mentor to guide you in contributing to Mantis\n\n"
            "Can you **hover over this message and click 'Reply'** to tell me about:\n\n"
            "• **Teams** you're interested in (Integrations, Compute, Drugs, Science, etc.)\n"
            "• **AI/ML or programming projects** you've built (these skills apply directly to Mantis algorithms)\n"
            "• **Technical areas** you want to learn (distributed computing, data pipelines, biomedical analysis, etc.)\n\n"
            "Your background will help me match you with mentors who can guide you in contributing to Mantis's scientific workflows and infrastructure!"
        )
        initial_message = await interaction.original_response()
        self.sessions[user_id]["last_bot_message_id"] = initial_message.id


### --- Setup Function ---
async def setup(bot):
    await bot.add_cog(MantisCog(bot))