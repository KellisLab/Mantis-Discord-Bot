import discord
from discord.ui import Button, View
from discord.ext import commands
from discord import app_commands
from config import GITHUB_ORG_NAME, HEADERS, OPENAI_API_KEY, GITHUB_TOKEN
import requests
import openai
import re
import csv
from io import StringIO
import random
import traceback
from typing import Optional, Dict, Any

G_SHEET = "https://docs.google.com/spreadsheets/d/128HP4RuiJdRqe9Ukd9HboEgBq6GuA37N2vdy2ej07ok/export?format=csv&gid=887541815" # Mentor list exported as CSV

# Initialize OpenAI client
client = openai.OpenAI(api_key=OPENAI_API_KEY)

DISCORD_CHAR_LIMIT = 2000

# --- GitHub Task and Mentor Functions ---
# These can remain outside the Cog as they don't depend on bot state.
def get_org_tasks():
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

def recommend_tasks_primary(user_interests_text):
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that recommends GitHub tasks. Based on the user's interests, recommend relevant tasks from the provided list. Only list 5-8 tasks in the format '1) Task (link)'. Do not include any other text."},
                {"role": "user", "content": f"User interests: {user_interests_text}\n\nAvailable tasks:\n\n{get_org_tasks()}"}
            ],
            max_tokens=250,
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except openai.APIError as e:
        return f"Error generating recommendations: {str(e)}"

def recommend_tasks_secondary(existing_tasks_context):
    try:
        all_tasks = get_org_tasks()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that recommends GitHub tasks. The user was not satisfied with the previous recommendations. Please provide a new set of 5-8 unique tasks from the available tasks. Do not recommend any of the tasks from the previous list. Only list the new tasks in the format '1) Task (link)'. Do not include any other text."},
                {"role": "user", "content": f"Previous recommendations:\n{existing_tasks_context}\n\nAvailable tasks:\n\n{all_tasks}"}
            ],
            max_tokens=250,
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Error generating more recommendations: {str(e)}"

def assign_task_to_user(github_username, issue_url):
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
    response = requests.get(G_SHEET)
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

def recommend_mentors_via_openai(mentors, user_interests_text, assigned_tasks_text):
    mentor_list_text = "\n".join([f"- {m['full_name']} (Teams: {m['teams']})" for m in mentors])
    prompt = (
        "You are a helpful assistant that recommends mentors. Based on the user's interests, assigned task, and team preferences, "
        "recommend 3-5 mentors from the provided list. For each recommendation, provide the mentor's full name "
        "exactly as listed and a brief, one-sentence explanation for why they are a good match, explicitly considering their teams.\n\n"
        "**Important Matching Rule**: A user's interest in a full team name like 'Team Integrations' must match a mentor in 'Team I'. "
        "Similarly, 'Team Development' matches 'Team D', 'Team Core' matches 'Team C', and so on. Use this rule when evaluating mentors.\n\n"
        "Use the following format for each recommendation and nothing else:\n"
        "Mentor Name: [Full Name]\n"
        "Reason: [Your one-sentence explanation]\n\n"
        f"Available Mentors:\n{mentor_list_text}\n\n"
        f"User Interests (contains team preferences and skills):\n{user_interests_text}\n\n"
        f"Assigned Task:\n{assigned_tasks_text}"
    )
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a mentor recommendation engine that provides reasons for its choices based on team and skill matching."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=300,
            temperature=0.7,
        )
        response_text = response.choices[0].message.content.strip()
        recommendations = []
        pattern = re.compile(r"Mentor Name:\s*(.*?)\s*\nReason:\s*(.*)", re.IGNORECASE)
        matches = pattern.findall(response_text)
        for name, reason in matches:
            for mentor in mentors:
                if name.strip().lower() == mentor['full_name'].lower():
                    recommendations.append({
                        "full_name": mentor['full_name'], "whatsapp": mentor['whatsapp'],
                        "teams": mentor['teams'], "reason": reason.strip()
                    })
                    break
        if not recommendations:
            random_mentors = random.sample(mentors, min(3, len(mentors)))
            for m in random_mentors:
                recommendations.append({**m, "reason": "Recommended as a generally available and experienced mentor."})
        return recommendations
    except Exception:
        random_mentors = random.sample(mentors, min(3, len(mentors)))
        return [{**m, "reason": "Recommended as a generally available and experienced mentor."} for m in random_mentors]

def draft_outreach_message(user_interests_text, assigned_tasks_text, mentor_name):
    prompt = (
        f"Write a friendly, concise WhatsApp message that a user could send to a mentor named {mentor_name}. "
        f"The user is interested in these areas:\n{user_interests_text}\n\n"
        f"The user plans to work on these tasks:\n{assigned_tasks_text}\n\n"
        "The message should be polite, enthusiastic, and ask for mentorship."
    )
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a message drafting assistant."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=150,
            temperature=0.7,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return "Sorry, I couldn't draft a message at this time."


### --- Cog and Discord Views ---

class MantisCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Use a single dictionary on the Cog instance to manage session data
        self.sessions: Dict[int, Dict[str, Any]] = {}

    class M4MView(View):
        def __init__(self, cog, user_id: int, *, timeout=180):
            super().__init__(timeout=timeout)
            self.cog = cog
            self.user_id = user_id

        @discord.ui.button(label="Find more tasks", style=discord.ButtonStyle.primary)
        async def find_more_button(self, interaction: discord.Interaction, button: Button):
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(view=self)
            
            await interaction.followup.send("Searching for more tasks...", ephemeral=True)
            
            new_tasks = ""
            async with interaction.channel.typing():
                session = self.cog.sessions.get(self.user_id, {})
                existing_context = session.get("issue_context", "")
                new_tasks = recommend_tasks_secondary(existing_context)
                session["issue_context"] = existing_context + "\n\n" + new_tasks
                self.cog.sessions[self.user_id] = session

            final_message = ("Here are some more tasks you might like:\n\n" + new_tasks)[:DISCORD_CHAR_LIMIT]
            
            await interaction.followup.send(content=final_message)
            await interaction.followup.send("What would you like to do next?", view=self.cog.M4MView(self.cog, self.user_id))

        @discord.ui.button(label="I have a task, assign me", style=discord.ButtonStyle.success)
        async def assign_task_button(self, interaction: discord.Interaction, button: Button):
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(view=self)
            
            session = self.cog.sessions.get(self.user_id, {})
            session["stage"] = "awaiting_github_username"
            self.cog.sessions[self.user_id] = session
            
            await interaction.followup.send("Great! Please reply to this message with your **GitHub username**.")

    class MentorButton(Button):
        def __init__(self, cog, mentor_name, whatsapp_number, user_id, user_interests_text, assigned_tasks_text):
            super().__init__(label=mentor_name, style=discord.ButtonStyle.secondary)
            self.cog = cog
            self.mentor_name = mentor_name
            self.whatsapp_number = whatsapp_number
            self.user_id = user_id
            self.user_interests_text = user_interests_text
            self.assigned_tasks_text = assigned_tasks_text

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True, thinking=True)
            draft = draft_outreach_message(self.user_interests_text, self.assigned_tasks_text, self.mentor_name)
            await interaction.followup.send(f"Here is a WhatsApp message you can send to **{self.mentor_name}** ({self.whatsapp_number}):\n\n> {draft}", ephemeral=True)

    class MentorSelectionView(View):
        def __init__(self, cog, user_id, mentors, user_interests_text, assigned_tasks_text, *, timeout=300):
            super().__init__(timeout=timeout)
            self.cog = cog
            self.user_id = user_id
            for mentor in mentors:
                self.add_item(self.cog.MentorButton(
                    self.cog,
                    mentor['full_name'],
                    mentor['whatsapp'],
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

        stage = session.get("stage")

        try:
            if stage == 0:
                interests = message.content.strip()
                session["user_interests"] = interests
                
                recommended_tasks = ""
                await message.reply("Thanks! Finding some suitable tasks based on your interests...")
                async with message.channel.typing():
                    recommended_tasks = recommend_tasks_primary(interests)
                
                session["issue_context"] = recommended_tasks

                intro = "Based on what you told me, I think you'll like these tasks:\n\n"
                full = f"{intro}{recommended_tasks}"
                final = full[:DISCORD_CHAR_LIMIT]

                await message.channel.send(final)
                await message.channel.send("What would you like to do next?", view=self.M4MView(self, user_id))
                session["stage"] = 1
                self.sessions[user_id] = session

            elif stage == "awaiting_github_username":
                session["github_username"] = message.content.strip()
                session["stage"] = "awaiting_issue_url"
                self.sessions[user_id] = session
                await message.reply("Got it! Now, please reply with the full **GitHub issue URL** you'd like to be assigned to.")

            elif stage == "awaiting_issue_url":
                github_username = session.get("github_username")
                if not github_username:
                    session["stage"] = "awaiting_github_username"
                    self.sessions[user_id] = session
                    return await message.reply("I don't have your GitHub username yet. Please send it first.")

                issue_url = message.content.strip()
                
                assign_response = ""
                await message.reply("Perfect. Let me try to assign that to you now...")
                async with message.channel.typing():
                    assign_response = assign_task_to_user(github_username, issue_url)
                
                await message.channel.send(assign_response)

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

                    await message.channel.send("Now that you have a task, let's find you a mentor! Searching...")
                    async with message.channel.typing():
                        interests = session.get("user_interests", "")
                        tasks = session.get("assigned_task", "")
                        mentors = get_mentors_from_public_sheet()
                        recommended_mentors = recommend_mentors_via_openai(mentors, interests, tasks)
                        
                        mentor_message = "I've found some mentors who might be a good fit:\n"
                        for mentor in recommended_mentors:
                            mentor_message += f"\n**{mentor['full_name']}** (Teams: {mentor['teams']})\n"
                            mentor_message += f"**Reason**: *{mentor['reason']}*\n"
                        
                        mentor_message += "\nIf you want to see other mentors who are open to taking on new mentees, check out this [Google Sheet](https://docs.google.com/spreadsheets/d/128HP4RuiJdRqe9Ukd9HboEgBq6GuA37N2vdy2ej07ok/edit?usp=sharing) for the entire list.\n\nYou can click a button below to get a pre-drafted outreach message for the mentors I found:"
            
                        view = self.MentorSelectionView(self, user_id, recommended_mentors, interests, tasks)
                        await message.channel.send(mentor_message, view=view)
                else:
                    await message.channel.send("Since the assignment didn't succeed, mentor recommendations are not available. You can try assigning another task!")

                # Clean up user session data
                self.sessions.pop(user_id, None)

        except Exception:
            traceback.print_exc()
            await message.channel.send("Sorry, something went wrong. Please try running the `/m4m` command again.")
            self.sessions.pop(user_id, None)

    @app_commands.command(name="m4m", description="Find a task and mentor to contribute to Mantis.")
    async def m4m_task_mentor_agent(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        # Create a new session for the user
        self.sessions[user_id] = {"stage": 0}
        await interaction.response.send_message(
            "Hi! I'll help you find a task and mentor to begin contributing to Mantis. "
            "Can you **hover over this message and click 'Reply'** to tell me about:\n\n"
            "1. Teams you are interested in contributing to (e.g., Team Integrations, Team Core).\n"
            "2. AI or programming-related projects you have built.\n\n"
            "This will help me get a better sense of what tasks and mentors to recommend!"
        )


### --- Setup Function ---
async def setup(bot):
    await bot.add_cog(MantisCog(bot))