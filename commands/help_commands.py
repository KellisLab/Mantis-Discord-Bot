import discord
import requests
from config import GRAPHQL_URL, HEADERS, GITHUB_ORG_NAME, PROJECTS_PER_PAGE


def setup(bot):
    """Register help commands with the bot."""
    bot.tree.add_command(help_command)
    bot.tree.add_command(projects_command)


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