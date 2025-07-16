import discord
import requests
import typing
from config import (
    GRAPHQL_URL, 
    HEADERS, 
    GITHUB_ORG_NAME,
    ITEMS_PER_PAGE,
    MAX_ITEMS_TO_DISPLAY,
    DISCORD_FIELD_CHAR_LIMIT,
)


def estimate_embed_size(embed):
    """Estimate the total size of a Discord embed to prevent exceeding limits."""
    size = 0
    if embed.title:
        size += len(embed.title)
    if embed.description:
        size += len(embed.description)
    if embed.footer and embed.footer.text:
        size += len(embed.footer.text)
    
    for field in embed.fields:
        if field.name:
            size += len(field.name)
        if field.value:
            size += len(field.value)
    
    return size


def setup(bot):
    """Register issue and PR commands with the bot."""
    bot.tree.add_command(issues)
    bot.tree.add_command(prs)


@discord.app_commands.command(
    name="issues",
    description=f"View open issues in {GITHUB_ORG_NAME} repositories (Mantis and MantisAPI).",
)
@discord.app_commands.describe(
    repository="Filter by specific repository (default: both).",
)
@discord.app_commands.choices(repository=[
    discord.app_commands.Choice(name="Mantis", value="Mantis"),
    discord.app_commands.Choice(name="MantisAPI", value="MantisAPI"),
    discord.app_commands.Choice(name="Both", value="Both"),
])
async def issues(
    interaction: discord.Interaction,
    repository: typing.Optional[discord.app_commands.Choice[str]] = None,
):
    """Fetches open issues from KellisLab repositories and displays them."""
    await interaction.response.defer()

    # Determine which repositories to query
    repos_to_query = []
    if repository is None or repository.value == "Both":
        repos_to_query = ["Mantis", "MantisAPI"]
        repo_display = "Both repositories"
    else:
        repos_to_query = [repository.value]
        repo_display = repository.value

    all_issues = []

    # GraphQL query template for issues
    graphql_query_template = """
    query GetRepoIssues($owner: String!, $name: String!, $first: Int!, $cursor: String) {
      repository(owner: $owner, name: $name) {
        issues(first: $first, after: $cursor, states: OPEN, orderBy: {field: CREATED_AT, direction: DESC}) {
          pageInfo {
            endCursor
            hasNextPage
          }
          nodes {
            title
            url
            number
            createdAt
            author {
              login
            }
            labels(first: 10) {
              nodes {
                name
                color
              }
            }
          }
        }
      }
    }
    """

    # Fetch issues from each repository
    for repo_name in repos_to_query:
        current_cursor = None
        has_next_page = True
        page_count = 0

        while has_next_page:
            page_count += 1
            variables = {
                "owner": GITHUB_ORG_NAME,
                "name": repo_name,
                "first": ITEMS_PER_PAGE,
                "cursor": current_cursor,
            }

            try:
                resp = requests.post(GRAPHQL_URL, headers=HEADERS, json={"query": graphql_query_template, "variables": variables})
                resp.raise_for_status()
            except requests.exceptions.RequestException as e:
                await interaction.followup.send(f"❌ Failed to connect to GitHub API for {repo_name} (Page {page_count}): {e}", ephemeral=True)
                return

            try:
                data = resp.json()
            except Exception as e:
                await interaction.followup.send(f"❌ Failed to parse GitHub API response for {repo_name} (Page {page_count}): {e}", ephemeral=True)
                return

            gql_errors = data.get("errors")
            if gql_errors:
                error_messages = [err.get("message", "Unknown GraphQL error") for err in gql_errors]
                full_error_msg = f"❌ GitHub API Error(s) for {repo_name} (Page {page_count}):\n" + "\n".join(f"- {msg}" for msg in error_messages)
                await interaction.followup.send(full_error_msg[:1900], ephemeral=True)
                return

            data_root = data.get("data", {})
            repository_data = data_root.get("repository")
            if not repository_data:
                await interaction.followup.send(f"❌ Repository '{repo_name}' not found or not accessible in '{GITHUB_ORG_NAME}'. Check token permissions.", ephemeral=True)
                return

            issues_data = repository_data.get("issues", {})
            page_issues = issues_data.get("nodes", [])
            
            # Add repository name to each issue for display
            for issue in page_issues:
                if issue:
                    issue["repository"] = repo_name
                    all_issues.append(issue)

            page_info = issues_data.get("pageInfo", {})
            has_next_page = page_info.get("hasNextPage", False)
            current_cursor = page_info.get("endCursor")

            # Limit total issues to prevent excessive API calls and embed size issues
            # Be more conservative when showing multiple repositories
            max_fetch = MAX_ITEMS_TO_DISPLAY if len(repos_to_query) == 1 else MAX_ITEMS_TO_DISPLAY // 2
            if len(all_issues) >= max_fetch:
                has_next_page = False

    # Sort all issues by creation date (newest first)
    all_issues.sort(key=lambda x: x.get("createdAt", ""), reverse=True)

    # Limit display - be more conservative when showing multiple repositories
    if len(repos_to_query) > 1:
        # When showing both repos, limit to 25 total items to prevent embed size issues
        max_display = min(MAX_ITEMS_TO_DISPLAY // 2, 25)
    else:
        # When showing single repo, use full limit
        max_display = MAX_ITEMS_TO_DISPLAY
    
    issues_to_display = all_issues[:max_display]

    # Build embed
    embed_title = f"Open Issues - {repo_display}"
    embed = discord.Embed(
        title=embed_title,
        color=discord.Color.green(),
        description=f"Showing {len(issues_to_display)}/{len(all_issues)} most recent open issues"
    )

    if not issues_to_display:
        embed.add_field(name="No Issues Found", value="No open issues found in the specified repositories.", inline=False)
    else:
        # Group issues by repository for better organization
        repo_groups = {}
        for issue in issues_to_display:
            repo = issue.get("repository", "Unknown")
            if repo not in repo_groups:
                repo_groups[repo] = []
            repo_groups[repo].append(issue)

        for repo, repo_issues in repo_groups.items():
            field_chunks = []
            current_chunk_lines = []
            current_chunk_char_count = 0

            for issue in repo_issues:
                title = issue.get("title", "Untitled")
                number = issue.get("number", "")
                url = issue.get("url", "")
                author = issue.get("author", {}).get("login", "Unknown") if issue.get("author") else "Unknown"
                
                # Create issue display text
                if url and number:
                    issue_text = f"[[#{number}]({url})] {title} - @{author}"
                else:
                    issue_text = f"[#{number}] {title} - @{author}"

                # Truncate if too long
                if len(issue_text) > DISCORD_FIELD_CHAR_LIMIT:
                    issue_text = issue_text[:DISCORD_FIELD_CHAR_LIMIT - 4] + "..."

                # Check if adding this issue would exceed field limit
                len_with_newline = len(issue_text) + (1 if current_chunk_lines else 0)
                
                if current_chunk_char_count + len_with_newline <= DISCORD_FIELD_CHAR_LIMIT:
                    current_chunk_lines.append(issue_text)
                    current_chunk_char_count += len_with_newline
                else:
                    if current_chunk_lines:
                        field_chunks.append("\n".join(current_chunk_lines))
                    current_chunk_lines = [issue_text]
                    current_chunk_char_count = len(issue_text)

            if current_chunk_lines:
                field_chunks.append("\n".join(current_chunk_lines))

            # Add fields for this repository
            if not field_chunks:
                embed.add_field(name=f"{repo} Issues", value="_(No displayable issues)_", inline=False)
            else:
                num_chunks = len(field_chunks)
                for i, chunk_value in enumerate(field_chunks):
                    field_name = f"{repo} Issues"
                    if num_chunks > 1:
                        field_name += f" (Part {i+1}/{num_chunks})"
                    embed.add_field(name=field_name, value=chunk_value, inline=False)

    embed.set_footer(text=f"Mantis AI Cognitive Cartography · {len(all_issues)} total open issues")
    
    # Safety check for embed size
    embed_size = estimate_embed_size(embed)
    if embed_size > 5500:  # Leave buffer below 6000 limit
        # If embed is too large, remove some fields and add a warning
        while len(embed.fields) > 1 and estimate_embed_size(embed) > 5500:
            embed.remove_field(-1)  # Remove last field
        
        embed.add_field(
            name="⚠️ Content Truncated",
            value="Some items were hidden due to Discord's size limits. Try filtering by a specific repository.",
            inline=False
        )
    
    await interaction.followup.send(embed=embed)


@discord.app_commands.command(
    name="prs",
    description=f"View open and draft pull requests in {GITHUB_ORG_NAME} repositories (Mantis and MantisAPI).",
)
@discord.app_commands.describe(
    repository="Filter by specific repository (default: both).",
    state="Filter by PR state (default: open).",
)
@discord.app_commands.choices(
    repository=[
        discord.app_commands.Choice(name="Mantis", value="Mantis"),
        discord.app_commands.Choice(name="MantisAPI", value="MantisAPI"),
        discord.app_commands.Choice(name="Both", value="Both"),
    ],
    state=[
        discord.app_commands.Choice(name="Open", value="OPEN"),
        discord.app_commands.Choice(name="Draft", value="DRAFT"),
        discord.app_commands.Choice(name="Open + Draft", value="BOTH"),
    ]
)
async def prs(
    interaction: discord.Interaction,
    repository: typing.Optional[discord.app_commands.Choice[str]] = None,
    state: typing.Optional[discord.app_commands.Choice[str]] = None,
):
    """Fetches open and draft pull requests from KellisLab repositories and displays them."""
    await interaction.response.defer()

    # Determine which repositories to query
    repos_to_query = []
    if repository is None or repository.value == "Both":
        repos_to_query = ["Mantis", "MantisAPI"]
        repo_display = "Both repositories"
    else:
        repos_to_query = [repository.value]
        repo_display = repository.value

    # Determine PR states to query
    pr_states = []
    state_display = "Open"
    if state is None or state.value == "OPEN":
        pr_states = ["OPEN"]
        state_display = "Open"
    elif state.value == "DRAFT":
        pr_states = ["OPEN"]  # Draft PRs are technically OPEN but with isDraft=true
        state_display = "Draft"
    else:  # BOTH
        pr_states = ["OPEN"]
        state_display = "Open + Draft"

    all_prs = []

    # GraphQL query template for pull requests
    graphql_query_template = """
    query GetRepoPRs($owner: String!, $name: String!, $first: Int!, $cursor: String, $states: [PullRequestState!]) {
      repository(owner: $owner, name: $name) {
        pullRequests(first: $first, after: $cursor, states: $states, orderBy: {field: CREATED_AT, direction: DESC}) {
          pageInfo {
            endCursor
            hasNextPage
          }
          nodes {
            title
            url
            number
            createdAt
            isDraft
            author {
              login
            }
            baseRefName
            headRefName
            mergeable
            reviewDecision
          }
        }
      }
    }
    """

    # Fetch PRs from each repository
    for repo_name in repos_to_query:
        current_cursor = None
        has_next_page = True
        page_count = 0

        while has_next_page:
            page_count += 1
            variables = {
                "owner": GITHUB_ORG_NAME,
                "name": repo_name,
                "first": ITEMS_PER_PAGE,
                "cursor": current_cursor,
                "states": pr_states,
            }

            try:
                resp = requests.post(GRAPHQL_URL, headers=HEADERS, json={"query": graphql_query_template, "variables": variables})
                resp.raise_for_status()
            except requests.exceptions.RequestException as e:
                await interaction.followup.send(f"❌ Failed to connect to GitHub API for {repo_name} (Page {page_count}): {e}", ephemeral=True)
                return

            try:
                data = resp.json()
            except Exception as e:
                await interaction.followup.send(f"❌ Failed to parse GitHub API response for {repo_name} (Page {page_count}): {e}", ephemeral=True)
                return

            gql_errors = data.get("errors")
            if gql_errors:
                error_messages = [err.get("message", "Unknown GraphQL error") for err in gql_errors]
                full_error_msg = f"❌ GitHub API Error(s) for {repo_name} (Page {page_count}):\n" + "\n".join(f"- {msg}" for msg in error_messages)
                await interaction.followup.send(full_error_msg[:1900], ephemeral=True)
                return

            data_root = data.get("data", {})
            repository_data = data_root.get("repository")
            if not repository_data:
                await interaction.followup.send(f"❌ Repository '{repo_name}' not found or not accessible in '{GITHUB_ORG_NAME}'. Check token permissions.", ephemeral=True)
                return

            prs_data = repository_data.get("pullRequests", {})
            page_prs = prs_data.get("nodes", [])
            
            # Filter by draft status if needed and add repository name
            for pr in page_prs:
                if pr:
                    pr["repository"] = repo_name
                    
                    # Apply draft filtering
                    if state and state.value == "DRAFT":
                        if pr.get("isDraft"):
                            all_prs.append(pr)
                    elif state and state.value == "OPEN":
                        if not pr.get("isDraft"):
                            all_prs.append(pr)
                    else:  # BOTH or None (default to all open)
                        all_prs.append(pr)

            page_info = prs_data.get("pageInfo", {})
            has_next_page = page_info.get("hasNextPage", False)
            current_cursor = page_info.get("endCursor")

            # Limit total PRs to prevent excessive API calls and embed size issues
            # Be more conservative when showing multiple repositories
            max_fetch = MAX_ITEMS_TO_DISPLAY if len(repos_to_query) == 1 else MAX_ITEMS_TO_DISPLAY // 2
            if len(all_prs) >= max_fetch:
                has_next_page = False

    # Sort all PRs by creation date (newest first)
    all_prs.sort(key=lambda x: x.get("createdAt", ""), reverse=True)

    # Limit display - be more conservative when showing multiple repositories
    if len(repos_to_query) > 1:
        # When showing both repos, limit to 25 total items to prevent embed size issues
        max_display = min(MAX_ITEMS_TO_DISPLAY // 2, 25)
    else:
        # When showing single repo, use full limit
        max_display = MAX_ITEMS_TO_DISPLAY
    
    prs_to_display = all_prs[:max_display]

    # Build embed
    embed_title = f"{state_display} Pull Requests - {repo_display}"
    embed = discord.Embed(
        title=embed_title,
        color=discord.Color.blue(),
        description=f"Showing {len(prs_to_display)}/{len(all_prs)} most recent {state_display.lower()} pull requests"
    )

    if not prs_to_display:
        embed.add_field(name="No Pull Requests Found", value=f"No {state_display.lower()} pull requests found in the specified repositories.", inline=False)
    else:
        # Group PRs by repository for better organization
        repo_groups = {}
        for pr in prs_to_display:
            repo = pr.get("repository", "Unknown")
            if repo not in repo_groups:
                repo_groups[repo] = []
            repo_groups[repo].append(pr)

        for repo, repo_prs in repo_groups.items():
            field_chunks = []
            current_chunk_lines = []
            current_chunk_char_count = 0

            for pr in repo_prs:
                title = pr.get("title", "Untitled")
                number = pr.get("number", "")
                url = pr.get("url", "")
                author = pr.get("author", {}).get("login", "Unknown") if pr.get("author") else "Unknown"
                is_draft = pr.get("isDraft", False)
                review_decision = pr.get("reviewDecision", "")
                
                # Create status indicators
                status_indicators = []
                if is_draft:
                    status_indicators.append("🚧 Draft")
                if review_decision == "APPROVED":
                    status_indicators.append("✅ Approved")
                elif review_decision == "CHANGES_REQUESTED":
                    status_indicators.append("🔄 Changes Requested")
                elif review_decision == "REVIEW_REQUIRED":
                    status_indicators.append("👀 Review Required")

                status_text = " " + " ".join(status_indicators) if status_indicators else ""
                
                # Create PR display text
                if url and number:
                    pr_text = f"[[#{number}]({url})] {title} - @{author}{status_text}"
                else:
                    pr_text = f"[#{number}] {title} - @{author}{status_text}"

                # Truncate if too long
                if len(pr_text) > DISCORD_FIELD_CHAR_LIMIT:
                    pr_text = pr_text[:DISCORD_FIELD_CHAR_LIMIT - 4] + "..."

                # Check if adding this PR would exceed field limit
                len_with_newline = len(pr_text) + (1 if current_chunk_lines else 0)
                
                if current_chunk_char_count + len_with_newline <= DISCORD_FIELD_CHAR_LIMIT:
                    current_chunk_lines.append(pr_text)
                    current_chunk_char_count += len_with_newline
                else:
                    if current_chunk_lines:
                        field_chunks.append("\n".join(current_chunk_lines))
                    current_chunk_lines = [pr_text]
                    current_chunk_char_count = len(pr_text)

            if current_chunk_lines:
                field_chunks.append("\n".join(current_chunk_lines))

            # Add fields for this repository
            if not field_chunks:
                embed.add_field(name=f"{repo} Pull Requests", value="_(No displayable pull requests)_", inline=False)
            else:
                num_chunks = len(field_chunks)
                for i, chunk_value in enumerate(field_chunks):
                    field_name = f"{repo} Pull Requests"
                    if num_chunks > 1:
                        field_name += f" (Part {i+1}/{num_chunks})"
                    embed.add_field(name=field_name, value=chunk_value, inline=False)

    embed.set_footer(text=f"Mantis AI Cognitive Cartography · {len(all_prs)} total {state_display.lower()} PRs")
    
    # Safety check for embed size
    embed_size = estimate_embed_size(embed)
    if embed_size > 5500:  # Leave buffer below 6000 limit
        # If embed is too large, remove some fields and add a warning
        while len(embed.fields) > 1 and estimate_embed_size(embed) > 5500:
            embed.remove_field(-1)  # Remove last field
        
        embed.add_field(
            name="⚠️ Content Truncated",
            value="Some items were hidden due to Discord's size limits. Try filtering by a specific repository.",
            inline=False
        )
    
    await interaction.followup.send(embed=embed) 