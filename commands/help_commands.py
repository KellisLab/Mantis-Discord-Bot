import time

import aiohttp
import discord
import requests

from config import GITHUB_ORG_NAME, GRAPHQL_URL, HEADERS, PROJECTS_PER_PAGE

HELP_SECTIONS = (
    (
        "Projects and GitHub",
        (
            "`/tasks` — View tasks for the project mapped to this channel.",
            "`/project_tasks` — View tasks for a project number.",
            "`/projects` — List active projects and their numbers.",
            "`/issues` — View open issues in Mantis repositories.",
            "`/prs` — View open and draft pull requests.",
        ),
    ),
    (
        "AI guidance",
        (
            "`/manolis` — Ask ManolisGPT a question.",
            "`/m4m` — Find a task and mentor.",
            "`/m4m_mentor` — Find a mentor for your skills and interests.",
            "`/m4m_find_assignee` — Find an assignee for a task.",
        ),
    ),
    (
        "Member profiles",
        (
            "`/create-profile` — Create or claim your member profile by email.",
            "`/get-info` — Look up a complete member profile.",
            "`/member add` — Add an unlinked member profile. *(Leadership)*",
            "`/member edit-stage` — Change a member's stage. *(Leadership)*",
            "`/member leader` — Toggle Leadership. *(Leadership)*",
            "`/member journey-mentor` — Toggle Journey Mentor. *(Leadership)*",
            "`/member kick` — Reset stage and special access. *(Leadership)*",
            "`/member import-stages` — Bulk-update stages. *(Leadership)*",
            "`/member sync-access` — Preview/apply one access sync. *(Leadership)*",
            "`/member sync-access-all` — Preview/apply the full GitHub sweep. *(Leadership)*",
            "`/member sync-access-status` — Show failed sync jobs. *(Leadership)*",
            "`/member sync-access-retry` — Retry failed sync jobs. *(Leadership)*",
        ),
    ),
    (
        "`/member import` CSV *(Leadership)*",
        (
            "Creates unlinked profiles; duplicate emails are skipped.",
            "Required column: `email`.",
            (
                "Optional columns: `full_name`, `github_username`, `whatsapp`, "
                "`stage`, `is_leadership`, `is_journey_mentor`."
            ),
            (
                "Stages: `preboarding`, `onboarding`, `cartographer`, `navigator`, "
                "`savant`, `admiral`, `developer`, `engineer`, `architect`."
            ),
            (
                "Boolean true values: `true`, `yes`, `1`, `enabled`, `enable`; "
                "false values: `false`, `no`, `0`, `disabled`, `disable`. Blank "
                "role flags default to false."
            ),
        ),
    ),
    (
        "Teams",
        (
            "`/team create` — Create a team and channel. *(Leadership)*",
            "`/team edit` — Edit the current team's name or description.",
            "`/team add` — Add a member to the current team.",
            "`/team remove` — Remove a member from the current team.",
            "`/team set-rank` — Change a team member's rank.",
            "`/team transfer-lead` — Transfer the Lead role.",
            "`/team leave` — Leave the current team.",
            "`/team close` — Start a vote to close the team.",
        ),
    ),
    (
        "Channels and reminders",
        (
            "`/close-channel` — Lock and archive a channel. *(Leadership)*",
            "`/download-storage` — Download member and team storage. *(Leadership)*",
            "`/summarize_channel` — Summarize a configured channel.",
            "`/send-reminders` — Send stale issue and PR reminders.",
        ),
    ),
    (
        "Diagnostics",
        (
            "`/network-test` — Test service connectivity.",
            "`/test-discord-lookup` — Test Discord user lookup.",
            "`/test-member-mapping` — Test GitHub-to-Discord mapping.",
        ),
    ),
)


def _help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Mantis Bot Help",
        description=(
            "Available slash commands are grouped below. Commands marked "
            "Leadership require Leadership access."
        ),
        color=discord.Color.blue(),
    )
    for name, lines in HELP_SECTIONS:
        embed.add_field(name=name, value="\n".join(lines), inline=False)
    embed.set_footer(text="Mantis AI Cognitive Cartography")
    return embed


def setup(bot):
    """Register help commands with the bot."""
    bot.tree.add_command(help_command)
    bot.tree.add_command(projects_command)
    bot.tree.add_command(network_test)


@discord.app_commands.command(
    name="help", description="Shows how to use the Mantis Bot."
)
async def help_command(interaction: discord.Interaction):
    """Displays a help message for the bot."""
    await interaction.response.defer(ephemeral=True)
    await interaction.followup.send(embed=_help_embed(), ephemeral=True)


@discord.app_commands.command(
    name="projects",
    description=f"Lists all projects in the {GITHUB_ORG_NAME} organization with their numbers.",
)
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
            resp = requests.post(
                GRAPHQL_URL,
                headers=HEADERS,
                json={"query": graphql_query_template, "variables": variables},
            )
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            await interaction.followup.send(
                f"❌ Failed to connect to GitHub API (Page {page_count}): {e}",
                ephemeral=True,
            )
            return

        try:
            data = resp.json()
        except Exception as e:
            await interaction.followup.send(
                f"❌ Failed to parse GitHub API response (Page {page_count}): {e}",
                ephemeral=True,
            )
            return

        gql_errors_from_api = data.get("errors")
        data_root = data.get("data", {})

        if gql_errors_from_api:
            error_messages = [
                err.get("message", "Unknown GraphQL error")
                for err in gql_errors_from_api
            ]
            full_error_msg = (
                f"❌ GitHub API Error(s) (Page {page_count}):\n"
                + "\n".join(f"- {msg}" for msg in error_messages)
            )
            await interaction.followup.send(full_error_msg[:1900], ephemeral=True)
            return

        organization_data = data_root.get("organization")
        if not organization_data:
            await interaction.followup.send(
                f"❌ Organization '{GITHUB_ORG_NAME}' not found or not accessible (Page {page_count}). Check token permissions.",
                ephemeral=True,
            )
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
        await interaction.followup.send(
            f"❌ No active projects found in the {GITHUB_ORG_NAME} organization.",
            ephemeral=True,
        )
        return

    # Sort projects by number
    accumulated_projects.sort(key=lambda p: p.get("number", 0))

    # Create embed
    embed = discord.Embed(
        title=f"{GITHUB_ORG_NAME} Projects",
        description=f"Here are all the active projects in the {GITHUB_ORG_NAME} organization ({len(accumulated_projects)} total):",
        color=discord.Color.green(),
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
        chunk = project_lines[i : i + chunk_size]
        field_name = (
            f"Projects {i + 1}-{min(i + chunk_size, len(project_lines))}"
            if len(project_lines) > chunk_size
            else "Projects"
        )
        field_value = "\n".join(chunk)
        embed.add_field(name=field_name, value=field_value, inline=False)

    embed.add_field(
        name="💡 Usage Tip",
        value="Use the project number with `/project_tasks number:<number>` to view tasks for any specific project!",
        inline=False,
    )

    embed.set_footer(text="Mantis AI Cognitive Cartography")
    await interaction.followup.send(embed=embed, ephemeral=True)


@discord.app_commands.command(
    name="network-test",
    description="Test network connectivity to Discord and GitHub APIs.",
)
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
            async with session.get("https://discord.com/api/v10/gateway") as resp:
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
            headers = {"Authorization": f"Bot {interaction.client.http.token}"}
            async with session.get(
                "https://discord.com/api/v10/users/@me", headers=headers
            ) as resp:
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
                headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
                async with session.get(
                    "https://api.openai.com/v1/models", headers=headers
                ) as resp:
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
        socket.gethostbyname("discord.com")
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
