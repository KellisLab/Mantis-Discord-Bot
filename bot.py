import os
import requests
import discord
from discord.ext import commands
import typing

# ─── Configuration ───────────────────────────────────────────────────────────

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

if not GITHUB_TOKEN or not DISCORD_TOKEN:
    raise RuntimeError("Make sure GITHUB_TOKEN and DISCORD_TOKEN are set in your environment!")

GRAPHQL_URL = "https://api.github.com/graphql"
HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

# ─── Bot Setup ────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
bot = commands.Bot(command_prefix=None, intents=intents)

# ─── GraphQL Fragment ────────────────────────────────────────────────────────

PROJECT_FIELDS_FRAGMENT = """
  id
  title
  url
  fields(first: 20) {
    nodes {
      ... on ProjectV2SingleSelectField {
        id
        name
        options {
          id
          name
        }
      }
    }
  }
  items(first: 100, orderBy: {field: POSITION, direction: ASC}) {
    nodes {
      id
      content {
        __typename
        ... on DraftIssue {
          title
        }
        ... on Issue {
          title
          url
          number
        }
        ... on PullRequest {
          title
          url
          number
        }
      }
      fieldValues(first: 10) {
        nodes {
          ... on ProjectV2ItemFieldSingleSelectValue {
            name
            field {
              ... on ProjectV2SingleSelectField {
                id
                name
              }
            }
          }
        }
      }
    }
  }
"""

# ─── Slash Command ───────────────────────────────────────────────────────────

@bot.tree.command(
    name="project_tasks",
    description="View tasks in a GitHub Project (V2)",
)
@discord.app_commands.describe(
    owner="Login for the user/organization, or the repository owner.",
    number="The project number (e.g., 1).",
    repo="Repository name (e.g., 'hello-world'). Omit for user/org projects.",
)
async def project_tasks(
    interaction: discord.Interaction,
    owner: str,
    number: int,
    repo: typing.Optional[str] = None,
):
    """Fetches all items from a GitHub Project (V2) and displays them grouped by status."""
    await interaction.response.defer()

    variables = {
        "projectNumber": number,
    }

    if repo:
        graphql_query = f"""
        query GetRepoProjectTasks($owner: String!, $repoName: String!, $projectNumber: Int!) {{
          repository(owner: $owner, name: $repoName) {{
            projectV2(number: $projectNumber) {{
              {PROJECT_FIELDS_FRAGMENT}
            }}
          }}
        }}
        """
        variables["owner"] = owner
        variables["repoName"] = repo
    else:
        graphql_query = f"""
        query GetOwnerProjectTasks($login: String!, $projectNumber: Int!) {{
          organization(login: $login) {{
            projectV2(number: $projectNumber) {{
              {PROJECT_FIELDS_FRAGMENT}
            }}
          }}
          user(login: $login) {{
            projectV2(number: $projectNumber) {{
              {PROJECT_FIELDS_FRAGMENT}
            }}
          }}
        }}
        """
        variables["login"] = owner

    try:
        resp = requests.post(GRAPHQL_URL, headers=HEADERS, json={"query": graphql_query, "variables": variables})
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        return await interaction.followup.send(f"❌ Failed to connect to GitHub API: {e}", ephemeral=True)

    data = resp.json()

    gql_errors_from_api = data.get("errors")
    data_root = data.get("data", {})

    project_node = None
    parent_name_for_display = ""

    if repo:
        # For repo-specific queries, any error in gql_errors_from_api is usually a showstopper.
        if gql_errors_from_api:
            error_messages = [err.get("message", "Unknown GraphQL error") for err in gql_errors_from_api]
            full_error_msg = "❌ GitHub API Error(s):\\n" + "\\n".join(f"- {msg}" for msg in error_messages)
            print(f"GraphQL Errors (Repo Query): {gql_errors_from_api}")
            return await interaction.followup.send(full_error_msg[:1900], ephemeral=True)

        # If no API errors, proceed to check data.
        repository_data = data_root.get("repository")
        if not repository_data: # Repo itself not found in data
            return await interaction.followup.send(f"❌ Repository '{owner}/{repo}' not found or not accessible (no data returned).", ephemeral=True)
        
        project_node = repository_data.get("projectV2")
        if not project_node: # Repo data present, but no projectV2 field or it's null
            return await interaction.followup.send(f"❌ Project V2 #{number} not found in repository '{owner}/{repo}', or you may lack permissions (project data missing).", ephemeral=True)
        parent_name_for_display = f"{owner}/{repo}"

    else: # User or Org project search
        org_data = data_root.get("organization") # Might be None if org not found or error for this path
        user_data = data_root.get("user")       # Might be None if user not found or error for this path

        # Attempt to find project node from organization or user data
        if org_data and org_data.get("projectV2"):
            project_node = org_data["projectV2"]
            parent_name_for_display = f"Organization: {owner}"
        elif user_data and user_data.get("projectV2"): # Check user if org project not found
            project_node = user_data["projectV2"]
            parent_name_for_display = f"User: {owner}"

        if project_node:
            # Project was successfully found. Check for ignorable errors.
            if gql_errors_from_api:
                critical_errors_after_success = []
                for err in gql_errors_from_api:
                    err_path = err.get("path", [])
                    err_type = err.get("type")
                    is_ignorable = False
                    if parent_name_for_display.startswith("Organization:") and \
                       err_type == "NOT_FOUND" and len(err_path) > 0 and err_path[0] == "user":
                        is_ignorable = True
                    elif parent_name_for_display.startswith("User:") and \
                         err_type == "NOT_FOUND" and len(err_path) > 0 and err_path[0] == "organization":
                        is_ignorable = True
                    
                    if is_ignorable:
                        print(f"Ignoring expected GQL error as project was found via other path: {err}")
                    else:
                        critical_errors_after_success.append(err)
                
                if critical_errors_after_success:
                    # Log these, but proceed with displaying the project.
                    error_messages = [err.get("message", "Unknown GraphQL error") for err in critical_errors_after_success]
                    print(f"⚠️ Unexpected GraphQL Errors (User/Org Query, project found but other errors exist): {critical_errors_after_success}")
                    # Optionally, send a subdued warning to the user or add to embed footer.
                    # For now, just logging them server-side.
        
        else: # project_node is still None for User/Org search.
            if gql_errors_from_api:
                contains_critical_error = False
                for err in gql_errors_from_api:
                    err_path = err.get("path", [])
                    err_type = err.get("type")
                    # An error is critical if it's NOT a "NOT_FOUND" for "user" or "organization" paths
                    if not (err_type == "NOT_FOUND" and len(err_path) > 0 and (err_path[0] == "user" or err_path[0] == "organization")):
                        contains_critical_error = True
                        break
                
                if contains_critical_error:
                    error_messages = [err.get("message", "Unknown GraphQL error") for err in gql_errors_from_api]
                    full_error_msg = "❌ GitHub API Error(s):\\n" + "\\n".join(f"- {msg}" for msg in error_messages)
                    print(f"Critical GraphQL Errors (User/Org Query, project not found): {gql_errors_from_api}")
                    return await interaction.followup.send(full_error_msg[:1900], ephemeral=True)

            # If here, project_node is None, and errors (if any) were only ignorable NOT_FOUNDs,
            # or there were no errors but still no project. Use specific "not found" messages.
            error_msg = f"❌ Could not find Project V2 #{number} for '{owner}'." # Default
            
            org_profile_absent_or_null = data_root.get("organization") is None
            user_profile_absent_or_null = data_root.get("user") is None

            # org_data and user_data variables hold the actual dictionary or None
            org_found_no_project = org_data and not org_data.get("projectV2")
            user_found_no_project = user_data and not user_data.get("projectV2")

            if org_profile_absent_or_null and user_profile_absent_or_null:
                error_msg = f"❌ Neither an organization nor a user named '{owner}' could be found or accessed via GitHub API."
            elif org_found_no_project and user_profile_absent_or_null:
                error_msg = f"❌ Organization '{owner}' was found, but Project V2 #{number} could not be retrieved from it. This might be due to missing permissions for the bot's GitHub token to access this specific project, or the project genuinely does not exist under this organization with that number. User '{owner}' was not found (this is expected if '{owner}' is an organization)."
            elif user_found_no_project and org_profile_absent_or_null:
                 error_msg = f"❌ User '{owner}' was found, but Project V2 #{number} could not be retrieved for them. This might be due to missing permissions for the bot's GitHub token to access this specific project, or the project genuinely does not exist for this user with that number. Organization '{owner}' was not found (this is expected if '{owner}' is a user)."
            elif org_found_no_project and user_found_no_project:
                error_msg = f"❌ Project V2 #{number} could not be retrieved for '{owner}'. The organization and user were found (or '{owner}' matched both types but without the project), but the project was not found under either, or it's not accessible with the current token permissions. Please check the project number and token permissions."
            elif org_found_no_project: # Implies user was not found or also had no project (covered by above)
                error_msg = f"❌ Organization '{owner}' was found, but Project V2 #{number} was not found within it or is not accessible with the current token permissions. Please check the project number and token permissions."
            elif user_found_no_project: # Implies org was not found or also had no project (covered by above)
                error_msg = f"❌ User '{owner}' was found, but Project V2 #{number} was not found for them or is not accessible with the current token permissions. Please check the project number and token permissions."
            else: # Fallback if logic above didn't catch a specific state.
                error_msg = f"❌ Project V2 #{number} not found for '{owner}' (checked as Org/User), or you may lack permissions. Ensure the project number is correct, it's a Project V2 type, and the token has project access."

            print(f"Detailed error message for user/org project not found: {error_msg}")
            if gql_errors_from_api:
              print(f"Associated GQL Errors (for detailed message): {gql_errors_from_api}")
            return await interaction.followup.send(error_msg, ephemeral=True)

    # If we successfully fall through, project_node should be set.
    if not project_node:
        # This is a fallback safety net, should ideally be caught by specific messages above.
        print(f"Error: project_node is unexpectedly None after error handling. Owner: {owner}, Repo: {repo}, Number: {number}")
        return await interaction.followup.send("❌ An unexpected error occurred while fetching project details. Project data could not be retrieved.", ephemeral=True)

    project_title = project_node.get("title", "Untitled Project")
    project_url = project_node.get("url", "#")

    # --- Process Project Data ---
    status_field_options_ordered = []
    status_field_target_name = "Status"
    
    project_fields_nodes = project_node.get("fields", {}).get("nodes", [])
    found_status_field = False
    for field in project_fields_nodes:
        if field and field.get("__typename") == "ProjectV2SingleSelectField" and field.get("name") == status_field_target_name:
            field_options = field.get("options", [])
            status_field_options_ordered = [opt["name"] for opt in field_options if opt and isinstance(opt, dict) and "name" in opt]
            found_status_field = True
            break
    
    columns_content = {name: [] for name in status_field_options_ordered}
    unassigned_items_list_name = "No Status / Other"
    
    if found_status_field:
        columns_content[unassigned_items_list_name] = []
    else:
        columns_content["All Tasks"] = []

    project_items_nodes = project_node.get("items", {}).get("nodes", [])
    for item_node in project_items_nodes:
        if not item_node:
          continue
        content = item_node.get("content")
        if not content:
            continue

        item_display_text = ""
        typename = content.get("__typename")
        title = content.get("title", "")

        if typename == "Issue" or typename == "PullRequest":
            num = content.get("number")
            url = content.get("url")
            item_display_text = f"[#{num}] {title} — <{url}>" if url else f"[#{num}] {title}"
        elif typename == "DraftIssue":
            item_display_text = title if title else "(untitled draft)"
        else:
            item_display_text = f"({typename}) {title if title else '(unknown item)'}"

        current_item_status_name = None
        if found_status_field:
            item_field_values = item_node.get("fieldValues", {}).get("nodes", [])
            for fv_node in item_field_values:
                if fv_node and fv_node.get("__typename") == "ProjectV2ItemFieldSingleSelectValue":
                    field_of_fv = fv_node.get("field")
                    if field_of_fv and isinstance(field_of_fv, dict) and field_of_fv.get("name") == status_field_target_name:
                        selected_option_name = fv_node.get("name")
                        if selected_option_name:
                            current_item_status_name = selected_option_name
                            break
        
        if found_status_field:
            if current_item_status_name and current_item_status_name in columns_content:
                columns_content[current_item_status_name].append(item_display_text)
            else:
                columns_content[unassigned_items_list_name].append(item_display_text)
        else:
            columns_content["All Tasks"].append(item_display_text)

    # --- Build Embed ---
    embed = discord.Embed(
        title=f"{parent_name_for_display} · Project #{number} ({project_title})",
        url=project_url,
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Data from GitHub Projects API (V2)")

    if not project_items_nodes and not found_status_field :
         embed.description = f"Project is empty or no '{status_field_target_name}' field found."
    elif not project_items_nodes and found_status_field:
         embed.description = "Project has a 'Status' field but no items."


    if found_status_field:
        for status_name in status_field_options_ordered:
            items_in_status = columns_content.get(status_name, [])
            value = "\n".join(items_in_status) if items_in_status else "_(empty)_"
            embed.add_field(name=status_name, value=value[:1024], inline=False)
        
        unassigned_items = columns_content.get(unassigned_items_list_name, [])
        if unassigned_items:
            value = "\n".join(unassigned_items) if unassigned_items else "_(empty)_"
            embed.add_field(name=unassigned_items_list_name, value=value[:1024], inline=False)
    
    else:
        all_tasks_list = columns_content.get("All Tasks", [])
        if all_tasks_list:
            value = "\n".join(all_tasks_list)
            embed.add_field(name="All Tasks", value=value[:1024], inline=False)
        elif not embed.description:
             embed.description = f"Project is empty and no '{status_field_target_name}' field found."


    if not embed.fields and not embed.description:
        embed.description = "Project is empty or could not be displayed."

    await interaction.followup.send(embed=embed)


# ─── Bot Events ──────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

# ─── Run Bot ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
