import discord
import requests
import typing
from config import (
    GRAPHQL_URL, 
    HEADERS, 
    PROJECT_FIELDS_FRAGMENT, 
    CHANNEL_PROJECT_MAPPING,
    GITHUB_ORG_NAME,
    ITEMS_PER_PAGE,
    STATUS_FIELD_NAME,
    UNASSIGNED_STATUS_NAME,
    DEFAULT_STATUS,
    MAX_ITEMS_TO_DISPLAY,
    DISCORD_FIELD_CHAR_LIMIT,
)


def setup(bot):
    """Register project commands with the bot."""
    bot.tree.add_command(project_tasks)
    bot.tree.add_command(tasks)


@discord.app_commands.command(
    name="project_tasks",
    description=f"View tasks in the {GITHUB_ORG_NAME} GitHub Project.",
)
@discord.app_commands.describe(
    number="The project number (e.g., 1).",
    status="Filter tasks by a specific status (default: Todo).",
    _deferred_by_caller="Don't use this parameter.",
)
@discord.app_commands.choices(status=[
    discord.app_commands.Choice(name="To Do", value="Todo"),
    discord.app_commands.Choice(name="In Progress", value="In Progress"),
    discord.app_commands.Choice(name="In Review", value="In Review"),
    discord.app_commands.Choice(name="Done", value="Done"),
    discord.app_commands.Choice(name="No Status", value=UNASSIGNED_STATUS_NAME),
])
async def project_tasks(
    interaction: discord.Interaction,
    number: int,
    status: typing.Optional[discord.app_commands.Choice[str]] = None,
    _deferred_by_caller: bool = False,
):
    """Fetches items from a GitHub Project and displays them, optionally filtered by status."""
    if not _deferred_by_caller:
        await interaction.response.defer()

    accumulated_items_raw_nodes = [] # To store all raw item nodes from all pages
    project_node_details = None # To store details like title, fields from the first GQL response
    current_cursor = None
    has_next_page = True
    page_count = 0

    graphql_query_template = f"""
    query GetOrgProjectTasks($login: String!, $projectNumber: Int!, $itemsPerPage: Int!, $cursor: String) {{
      organization(login: $login) {{
        projectV2(number: $projectNumber) {{
          {PROJECT_FIELDS_FRAGMENT}
        }}
      }}
    }}
    """

    while has_next_page:
        page_count += 1
        variables = {
            "projectNumber": number,
            "login": GITHUB_ORG_NAME,
            "itemsPerPage": ITEMS_PER_PAGE,
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

        current_project_node_page_data = organization_data.get("projectV2")
        if not current_project_node_page_data:
            await interaction.followup.send(f"❌ Project V2 #{number} not found in '{GITHUB_ORG_NAME}' or lacks permissions (Page {page_count}).", ephemeral=True)
            return

        if project_node_details is None: # Store overall project details from the first page
            project_node_details = current_project_node_page_data

        items_data = current_project_node_page_data.get("items", {})
        page_items = items_data.get("nodes", [])
        accumulated_items_raw_nodes.extend(page_items)

        page_info = items_data.get("pageInfo", {})
        has_next_page = page_info.get("hasNextPage", False)
        current_cursor = page_info.get("endCursor")

        if not has_next_page:
            break

    if project_node_details is None: # Safety check if loop didn't run or project was not found initially
        await interaction.followup.send(f"❌ Could not retrieve project details for Project #{number} in '{GITHUB_ORG_NAME}'.", ephemeral=True)
        return

    project_title = project_node_details.get("title", "Untitled Project")
    project_url = project_node_details.get("url", "#")

    # --- Sort all fetched items by creation date (newest first) ---
    def get_created_at(item_node):
        content = item_node.get("content")
        if content and "createdAt" in content and content["createdAt"] is not None:
            return content["createdAt"]
        return "0000-00-00T00:00:00Z" # Fallback for items without a creation date

    sorted_accumulated_items_nodes = sorted(accumulated_items_raw_nodes, key=get_created_at, reverse=True)

    # --- Determine target status for filtering ---
    effective_display_status: str
    user_selected_status_name: typing.Optional[str] = None
    if status:
        effective_display_status = status.value # "Todo", "In Progress", etc.
        user_selected_status_name = status.name # "To Do", "In Progress", etc. (for messages)
    else:
        effective_display_status = DEFAULT_STATUS # Default

    # --- Process Project Data ---
    status_field_target_id = None
    
    project_fields_nodes = project_node_details.get("fields", {}).get("nodes", [])
    found_status_field = False
    
    # No special handling needed if there are no fields to process
    if project_fields_nodes:
        for field in project_fields_nodes:
            if field:
                field_typename = field.get("__typename", "UnknownType")
                field_name = field.get("name", "Unnamed Field")
                field_id = field.get("id") # Get ID, might be None if not present
                
                if field_typename == "ProjectV2SingleSelectField" and field_name.lower() == STATUS_FIELD_NAME.lower():
                    status_field_target_id = field_id
                    options = field.get("options", [])
                    status_field_options_ordered = [opt["name"] for opt in options if opt and isinstance(opt, dict) and "name" in opt]
                    found_status_field = True
                    break
    
    if not found_status_field:
        pass # Handled by embed logic later
    elif not status_field_target_id:
        # This case implies an issue if found_status_field is true but ID is None.
        # For robustness, if ID is missing, treat as if status field wasn't properly found.
        found_status_field = False 

    columns_content = {name: [] for name in status_field_options_ordered}
    
    if found_status_field:
        columns_content[UNASSIGNED_STATUS_NAME] = []
    else:
        columns_content["All Tasks"] = []

    # --- Process the *globally sorted* list of items to populate columns_content ---
    # This ensures items within each column are also sorted by creation date.
    for item_node in sorted_accumulated_items_nodes:
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
            if num and url:
                item_display_text = f"[[#{num}]({url})] {title}"
            elif num:
                item_display_text = f"[#{num}] {title}"
            else:
                item_display_text = title if title else f"({typename}) (untitled)"
        elif typename == "DraftIssue":
            item_display_text = f"[Draft] {title}" if title else "[Draft] (untitled)"
        else:
            item_display_text = title if title else f"({typename}) (untitled)"

        # Find the item's current status
        current_item_status_name = None
        if found_status_field:
            field_values_nodes = item_node.get("fieldValues", {}).get("nodes", [])
            for fv_node in field_values_nodes:
                if fv_node and fv_node.get("__typename") == "ProjectV2ItemFieldSingleSelectValue":
                    field_of_fv = fv_node.get("field")
                    if field_of_fv and field_of_fv.get("id") == status_field_target_id:
                        selected_option_name = fv_node.get("name")
                        if selected_option_name:
                            current_item_status_name = selected_option_name
                            break
        
        if found_status_field:
            if current_item_status_name and current_item_status_name in columns_content:
                columns_content[current_item_status_name].append(item_display_text)
            else:
                columns_content[UNASSIGNED_STATUS_NAME].append(item_display_text)
        else:
            columns_content["All Tasks"].append(item_display_text)
    
    # --- Build Embed ---
    project_base_title = f"Project #{number} ({project_title})"
    embed = discord.Embed(url=project_url, color=discord.Color.blurple())

    footer_suffix = "" # Initialize suffix for the footer text

    if not sorted_accumulated_items_nodes: # Project is completely empty (no items from API)
        embed.title = project_base_title
        if not found_status_field:
            embed.description = f"Project is empty and no '{STATUS_FIELD_NAME}' field was found. Cannot filter by status '{effective_display_status}'."
            footer_suffix = f" · Project empty or no '{STATUS_FIELD_NAME}' field"
        else:
            embed.title += f" - {effective_display_status}" # Add status to title if filtering
            embed.description = f"Project has a '{STATUS_FIELD_NAME}' field but no items. The '{effective_display_status}' column is therefore empty."
            footer_suffix = " · Project is empty"
            if effective_display_status in columns_content: # Check if the status is a valid column
                 embed.add_field(name=effective_display_status, value="_(empty)_", inline=False)
            else: # Defaulted to "Todo", but project calls it "To-Do", for example
                embed.description += (
                    f"\nAdditionally, the status '{effective_display_status}' is not a configured column in this project. "
                    f"Available statuses: {', '.join(status_field_options_ordered) if status_field_options_ordered else 'None'}."
                )
                footer_suffix = f" · Status '{effective_display_status}' not in project options"

    elif found_status_field:
        embed.title = f"{project_base_title} - {effective_display_status}"
        items_in_target_status_full = columns_content.get(effective_display_status) # Get all items for this status (already sorted)

        if items_in_target_status_full is not None: # The target status column exists in the project's setup
            items_to_display = items_in_target_status_full[:MAX_ITEMS_TO_DISPLAY] # Truncate to display up to max newest
            field_base_name = effective_display_status
            
            num_displayed = len(items_to_display)
            total_in_category = len(items_in_target_status_full)
            footer_suffix = f" · Showing {num_displayed}/{total_in_category} tasks for '{effective_display_status}'"

            if not items_to_display:
                embed.add_field(name=field_base_name, value="_(empty)_", inline=False)
            else:
                field_chunks_values = []
                current_chunk_lines = []
                current_chunk_char_count = 0

                for item_text_original in items_to_display:
                    item_text = item_text_original
                    if len(item_text) > DISCORD_FIELD_CHAR_LIMIT: # Truncate individual super long items
                        item_text = item_text[:DISCORD_FIELD_CHAR_LIMIT - 4] + "..."
                    
                    # Length of new item + 1 for newline (if not the first item in chunk)
                    len_of_item_with_newline = len(item_text) + (1 if current_chunk_lines else 0)

                    if current_chunk_char_count + len_of_item_with_newline <= DISCORD_FIELD_CHAR_LIMIT:
                        current_chunk_lines.append(item_text)
                        current_chunk_char_count += len_of_item_with_newline
                    else:
                        if current_chunk_lines: # Finalize current chunk
                            field_chunks_values.append("\n".join(current_chunk_lines))
                        
                        # Start new chunk with current item (already truncated if needed)
                        current_chunk_lines = [item_text]
                        current_chunk_char_count = len(item_text)
                
                if current_chunk_lines: # Add the last remaining chunk
                    field_chunks_values.append("\n".join(current_chunk_lines))

                if not field_chunks_values:
                    embed.add_field(name=field_base_name, value="_(No displayable items)_", inline=False)
                else:
                    num_chunks = len(field_chunks_values)
                    for i, chunk_value_str in enumerate(field_chunks_values):
                        field_name_display = field_base_name
                        if num_chunks > 1:
                            field_name_display += f" (Part {i+1}/{num_chunks})"
                        embed.add_field(name=field_name_display, value=chunk_value_str, inline=False)
        else: # The requested effective_display_status (e.g., "Todo") is not an actual column name
            embed.description = (
                f"The status '{effective_display_status}' is not recognized as a status column in this project for Project #{number} ('{project_title}').\n"
                f"Available statuses in project: {', '.join(status_field_options_ordered) if status_field_options_ordered else '(No statuses defined in project)'}."
            )
            footer_suffix = f" · Status '{effective_display_status}' not found"

    else: # No 'Status' field found, but project has items (found_status_field is False)
        embed.title = project_base_title
        if user_selected_status_name: # User explicitly asked for a status filter
            embed.description = f"This project does not have a '{STATUS_FIELD_NAME}' field, so tasks cannot be filtered by '{user_selected_status_name}'. Showing all tasks instead."
        else: # Defaulted to "Todo", but no status field overall
            embed.description = f"This project does not have a '{STATUS_FIELD_NAME}' field (tried to show '{effective_display_status}' column). Showing all tasks."
        
        all_tasks_list_full = columns_content.get("All Tasks", []) # Get all tasks (already sorted)
        items_to_display = all_tasks_list_full[:MAX_ITEMS_TO_DISPLAY] # Truncate to display up to max newest
        field_base_name = "All Tasks"

        num_displayed = len(items_to_display)
        total_in_category = len(all_tasks_list_full)
        footer_suffix = f" · Showing {num_displayed}/{total_in_category} tasks (All Tasks)"

        if not items_to_display:
            embed.add_field(name=field_base_name, value="_(empty)_", inline=False)
        else:
            field_chunks_values = []
            current_chunk_lines = []
            current_chunk_char_count = 0

            for item_text_original in items_to_display:
                item_text = item_text_original
                if len(item_text) > DISCORD_FIELD_CHAR_LIMIT: # Truncate individual super long items
                    item_text = item_text[:DISCORD_FIELD_CHAR_LIMIT - 4] + "..."
                
                len_of_item_with_newline = len(item_text) + (1 if current_chunk_lines else 0)

                if current_chunk_char_count + len_of_item_with_newline <= DISCORD_FIELD_CHAR_LIMIT:
                    current_chunk_lines.append(item_text)
                    current_chunk_char_count += len_of_item_with_newline
                else:
                    if current_chunk_lines:
                        field_chunks_values.append("\n".join(current_chunk_lines))
                    
                    current_chunk_lines = [item_text]
                    current_chunk_char_count = len(item_text)
            
            if current_chunk_lines:
                field_chunks_values.append("\n".join(current_chunk_lines))

            if not field_chunks_values:
                 embed.add_field(name=field_base_name, value="_(No displayable items)_", inline=False)
            else:
                num_chunks = len(field_chunks_values)
                for i, chunk_value_str in enumerate(field_chunks_values):
                    field_name_display = field_base_name
                    if num_chunks > 1:
                        field_name_display += f" (Part {i+1}/{num_chunks})"
                    embed.add_field(name=field_name_display, value=chunk_value_str, inline=False)

    # Fallback if somehow no description and no fields were set (e.g. logic error above)
    if not embed.fields and not embed.description:
        embed.title = project_base_title # Ensure title is set
        embed.description = "No tasks found matching the criteria, or the project is empty."
        # Footer suffix might already be set from a prior condition, or use a generic one if needed.
        if not footer_suffix: # If no specific condition above set a suffix
            if effective_display_status and found_status_field:
                footer_suffix = f" · No tasks for '{effective_display_status}'"
            else:
                footer_suffix = " · No tasks found"

    embed.set_footer(text=f"Mantis AI Cognitive Cartography{footer_suffix}")
    await interaction.followup.send(embed=embed)


@discord.app_commands.command(
    name="tasks",
    description=f"View tasks in the {GITHUB_ORG_NAME} GitHub Project.",
)
@discord.app_commands.describe(
    status="Filter tasks by a specific status (default: Todo).",
)
@discord.app_commands.choices(status=[
    discord.app_commands.Choice(name="To Do", value="Todo"),
    discord.app_commands.Choice(name="In Progress", value="In Progress"),
    discord.app_commands.Choice(name="In Review", value="In Review"),
    discord.app_commands.Choice(name="Done", value="Done"),
    discord.app_commands.Choice(name="No Status", value=UNASSIGNED_STATUS_NAME),
])
async def tasks(
    interaction: discord.Interaction,
    status: typing.Optional[discord.app_commands.Choice[str]] = None,
):
    """Fetches items from the GitHub Project and displays them, optionally filtered by status."""
    await interaction.response.defer()

    channel_id = interaction.channel_id
    project_number = CHANNEL_PROJECT_MAPPING.get(channel_id)

    if project_number is None:
        await interaction.followup.send(
            f"❌ This channel (ID: {channel_id}) is not mapped to a GitHub Project. "
            "Please ask an admin to configure it.",
            ephemeral=True,
        )
        return

    # Call the existing project_tasks command logic
    await project_tasks.callback(interaction, number=project_number, status=status, _deferred_by_caller=True)