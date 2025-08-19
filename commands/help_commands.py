import discord
import requests
import aiohttp
import time
from config import GRAPHQL_URL, HEADERS, GITHUB_ORG_NAME, PROJECTS_PER_PAGE


def setup(bot):
    """Register help commands with the bot."""
    bot.tree.add_command(help_command)
    bot.tree.add_command(projects_command)
    bot.tree.add_command(network_test)


@discord.app_commands.command(name="help", description="Shows how to use the Mantis Bot.")
async def help_command(interaction: discord.Interaction):
    """Displays a help message for the bot."""
    await interaction.response.defer(ephemeral=True)

    embed = discord.Embed(
        title="Mantis Bot Help",
        description="Hello! I'm here to help you view tasks from our GitHub Projects and learn more about Mantis.",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="1. `/tasks` command",
        value=(
            "Use this command in a project-specific channel to see tasks.\n"
            "Example: `/tasks status:In Progress` in channel <#1376189017552457728>\n"
            "If no status is provided, it defaults to 'To Do'.\n"
            "This command automatically knows which project to fetch based on the channel it's used in."
        ),
        inline=False
    )

    embed.add_field(
        name="2. `/project_tasks` command",
        value=(
            "Use this command to view tasks for *any* project by specifying its number.\n"
            "Example: `/project_tasks number:2 status:Done`\n"
            "This is useful if you want to check tasks for a project not associated with the current channel, or if a channel isn't mapped."
        ),
        inline=False
    )
    
    embed.add_field(
        name="3. `/projects` command",
        value=(
            f"Use this command to see a list of all projects in the {GITHUB_ORG_NAME} organization with their numbers.\n"
            "This helps you find the right project number to use with `/project_tasks`."
        ),
        inline=False
    )
    
    embed.add_field(
        name="4. `/issues` command",
        value=(
            f"View open issues in {GITHUB_ORG_NAME} repositories (Mantis and MantisAPI).\n"
            "Example: `/issues repository:Mantis` or `/issues` to see both repos.\n"
            "Issues are sorted by creation date (newest first)."
        ),
        inline=False
    )
    
    embed.add_field(
        name="5. `/prs` command",
        value=(
            f"View open and draft pull requests in {GITHUB_ORG_NAME} repositories.\n"
            "Example: `/prs state:Draft repository:MantisAPI`\n"
            "Shows PR status indicators (🚧 Draft, ✅ Approved, etc.)."
        ),
        inline=False
    )
    
    embed.add_field(
        name="6. `/manolis` command",
        value=(
            "Ask ManolisGPT a question to learn more about Mantis.\n"
            "Example: `/manolis question:What are the key features of Mantis?`\n"
            "You can also reply to ManolisGPT responses to continue the conversation."
        ),
        inline=False
    )

    embed.add_field(
        name="7. `/m4m` command",
        value=(
            "Initiate a workflow to find a task and corresponding mentor in Mantis.\n"
            "Example: Use `/m4m` and hover over messages to reply to the bot with additional context about your interests.\n"
            "`/m4m` can also assign you to a task you like automatically and in the future, process your CV."
        ),
        inline=False
    )

    embed.add_field(
        name="8. `/m4m_find_assignee`",
        value=(
            "Initiate a workflow to find an assignee for a task.\n"
            "Example: Use `/m4m_find_assignee` and reply to the bot's message with a GitHub URL or description of the task.\n"
            "You can also reply to responses from the bot to get more relevant recommendations."
        ),
        inline=False
    )
    
    # embed.add_field(
    #     name="Status Options",
    #     value="When using `status`, you can choose from: `To Do`, `In Progress`, `In Review`, `Done`, `No Status`.",
    #     inline=False
    # )

    embed.set_footer(text="Mantis AI Cognitive Cartography")
    await interaction.followup.send(embed=embed, ephemeral=True)


@discord.app_commands.command(name="projects", description=f"Lists all projects in the {GITHUB_ORG_NAME} organization with their numbers.")
async def projects_command(interaction: discord.Interaction):
    """Displays a list of all projects in the organization."""
    await interaction.response.defer(ephemeral=True)
    
    accumulated_projects = []
    current_cursor = None
    has_next_page = True
    page_count = 0

    graphql_query_template = """
    query GetOrgProjects($login: String!, $projectsPerPage: Int!, $cursor: String) {
      organization(login: $login) {
        projectsV2(first: $projectsPerPage, after: $cursor, orderBy: {field: CREATED_AT, direction: DESC}) {
          pageInfo {
            endCursor
            hasNextPage
          }
          nodes {
            id
            title
            number
            url
            closed
          }
        }
      }
    }
    """

    # Fetch all projects with pagination
    while has_next_page:
        page_count += 1
        variables = {
            "login": GITHUB_ORG_NAME,
            "projectsPerPage": PROJECTS_PER_PAGE,
            "cursor": current_cursor,
        }

        try:
            resp = requests.post(GRAPHQL_URL, headers=HEADERS, json={"query": graphql_query_template, "variables": variables})
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            await interaction.followup.send(f"❌ Failed to connect to GitHub API (Page {page_count}): {e}", ephemeral=True)
            return

        try:
            data = resp.json()
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to parse GitHub API response (Page {page_count}): {e}", ephemeral=True)
            return

        gql_errors_from_api = data.get("errors")
        data_root = data.get("data", {})

        if gql_errors_from_api:
            error_messages = [err.get("message", "Unknown GraphQL error") for err in gql_errors_from_api]
            full_error_msg = f"❌ GitHub API Error(s) (Page {page_count}):\n" + "\n".join(f"- {msg}" for msg in error_messages)
            await interaction.followup.send(full_error_msg[:1900], ephemeral=True)
            return

        organization_data = data_root.get("organization")
        if not organization_data:
            await interaction.followup.send(f"❌ Organization '{GITHUB_ORG_NAME}' not found or not accessible (Page {page_count}). Check token permissions.", ephemeral=True)
            return

        projects_data = organization_data.get("projectsV2", {})
        page_projects = projects_data.get("nodes", [])
        
        # Filter out closed projects and add to accumulated list
        active_projects = [p for p in page_projects if p and not p.get("closed", False)]
        accumulated_projects.extend(active_projects)

        page_info = projects_data.get("pageInfo", {})
        has_next_page = page_info.get("hasNextPage", False)
        current_cursor = page_info.get("endCursor")

        if not has_next_page:
            break

    if not accumulated_projects:
        await interaction.followup.send(f"❌ No active projects found in the {GITHUB_ORG_NAME} organization.", ephemeral=True)
        return

    # Sort projects by number
    accumulated_projects.sort(key=lambda p: p.get("number", 0))

    # Create embed
    embed = discord.Embed(
        title=f"{GITHUB_ORG_NAME} Projects",
        description=f"Here are all the active projects in the {GITHUB_ORG_NAME} organization ({len(accumulated_projects)} total):",
        color=discord.Color.green()
    )

    # Group projects for better display (Discord embed has field limits)
    project_lines = []
    for project in accumulated_projects:
        number = project.get("number", "?")
        title = project.get("title", "Untitled")
        url = project.get("url", "")
        
        if url:
            project_line = f"**#{number}** - [{title}]({url})"
        else:
            project_line = f"**#{number}** - {title}"
        
        project_lines.append(project_line)

    # Split projects into chunks to fit in embed fields (Discord has a 1024 char limit per field)
    chunk_size = 10
    for i in range(0, len(project_lines), chunk_size):
        chunk = project_lines[i:i + chunk_size]
        field_name = f"Projects {i+1}-{min(i+chunk_size, len(project_lines))}" if len(project_lines) > chunk_size else "Projects"
        field_value = "\n".join(chunk)
        embed.add_field(name=field_name, value=field_value, inline=False)

    embed.add_field(
        name="💡 Usage Tip",
        value="Use the project number with `/project_tasks number:<number>` to view tasks for any specific project!",
        inline=False
    )

    embed.set_footer(text="Mantis AI Cognitive Cartography")
    await interaction.followup.send(embed=embed, ephemeral=True)


@discord.app_commands.command(name="network-test", description="Test network connectivity to Discord and GitHub APIs.")
async def network_test(interaction: discord.Interaction):
    """Test network connectivity and diagnose potential issues."""
    await interaction.response.defer(ephemeral=True)
    
    embed = discord.Embed(
        title="🔧 Network Diagnostic Test",
        description="Testing connectivity to various services...",
        color=discord.Color.orange(),
    )
    
    results = []
    
    # Test 1: Discord Gateway
    try:
        start_time = time.time()
        async with aiohttp.ClientSession() as session:
            async with session.get('https://discord.com/api/v10/gateway') as resp:
                gateway_time = round((time.time() - start_time) * 1000, 2)
                if resp.status == 200:
                    gateway_data = await resp.json()
                    results.append(f"✅ Discord Gateway: {gateway_time}ms")
                    results.append(f"   URL: {gateway_data.get('url', 'Unknown')}")
                else:
                    results.append(f"❌ Discord Gateway: HTTP {resp.status}")
    except Exception as e:
        results.append(f"❌ Discord Gateway: {str(e)[:50]}")
    
    # Test 2: Discord API - Current User
    try:
        start_time = time.time()
        async with aiohttp.ClientSession() as session:
            headers = {'Authorization': f'Bot {interaction.client.http.token}'}
            async with session.get('https://discord.com/api/v10/users/@me', headers=headers) as resp:
                api_time = round((time.time() - start_time) * 1000, 2)
                if resp.status == 200:
                    results.append(f"✅ Discord API (Bot): {api_time}ms")
                else:
                    results.append(f"❌ Discord API (Bot): HTTP {resp.status}")
    except Exception as e:
        results.append(f"❌ Discord API (Bot): {str(e)[:50]}")
    
    # Test 3: GitHub API
    try:
        start_time = time.time()
        resp = requests.get(GRAPHQL_URL, headers=HEADERS, timeout=10)
        github_time = round((time.time() - start_time) * 1000, 2)
        if resp.status_code == 200:
            results.append(f"✅ GitHub API: {github_time}ms")
        else:
            results.append(f"❌ GitHub API: HTTP {resp.status_code}")
    except Exception as e:
        results.append(f"❌ GitHub API: {str(e)[:50]}")
    
    # Test 4: OpenAI API (if configured)
    try:
        from config import OPENAI_API_KEY
        if OPENAI_API_KEY:
            start_time = time.time()
            async with aiohttp.ClientSession() as session:
                headers = {'Authorization': f'Bearer {OPENAI_API_KEY}'}
                async with session.get('https://api.openai.com/v1/models', headers=headers) as resp:
                    openai_time = round((time.time() - start_time) * 1000, 2)
                    if resp.status == 200:
                        results.append(f"✅ OpenAI API: {openai_time}ms")
                    else:
                        results.append(f"❌ OpenAI API: HTTP {resp.status}")
        else:
            results.append("⚠️ OpenAI API: Not configured")
    except Exception as e:
        results.append(f"❌ OpenAI API: {str(e)[:50]}")
    
    # Test 5: DNS Resolution
    try:
        import socket
        start_time = time.time()
        socket.gethostbyname('discord.com')
        dns_time = round((time.time() - start_time) * 1000, 2)
        results.append(f"✅ DNS Resolution: {dns_time}ms")
    except Exception as e:
        results.append(f"❌ DNS Resolution: {str(e)[:50]}")
    
    # Test 6: WebSocket Connection Test
    try:
        gateway_url = "wss://gateway.discord.gg/?v=10&encoding=json"
        start_time = time.time()
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(gateway_url) as ws:
                ws_time = round((time.time() - start_time) * 1000, 2)
                results.append(f"✅ WebSocket Connection: {ws_time}ms")
                await ws.close()
    except Exception as e:
        results.append(f"❌ WebSocket Connection: {str(e)[:50]}")
    
    # Add bot status info
    results.append("\n**Bot Status:**")
    results.append(f"Latency: {round(interaction.client.latency * 1000, 2)}ms")
    results.append(f"Guilds: {len(interaction.client.guilds)}")
    results.append(f"Users: {len(interaction.client.users)}")
    
    # Add system info
    try:
        import platform
        import psutil
        results.append("\n**System Info:**")
        results.append(f"Platform: {platform.system()} {platform.release()}")
        results.append(f"Python: {platform.python_version()}")
        results.append(f"Memory: {psutil.virtual_memory().percent}% used")
        results.append(f"CPU: {psutil.cpu_percent()}% used")
    except ImportError:
        results.append("\n**System Info:** psutil not available")
    except Exception as e:
        results.append(f"\n**System Info:** Error - {str(e)[:30]}")
    
    embed.description = "```\n" + "\n".join(results) + "\n```"
    embed.set_footer(text="Run this test periodically to monitor connectivity")
    
    await interaction.followup.send(embed=embed, ephemeral=True)