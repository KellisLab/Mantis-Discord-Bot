# Slash Command Access Control

Slash commands can be restricted using the database-backed access system in
`slash_commands/access.py`. Access is based on the user's stored Mantis account,
not on manually assigned Discord role names.

## Available groups

| Constant | A user belongs to the group when... |
| --- | --- |
| `LEADERSHIP` | `is_leadership` is enabled or their Discord ID has a permanent Leadership grant. |
| `JOURNEY_MENTOR` | `is_journey_mentor` is enabled. |
| `TEAM` | Their stage is neither `preboarding` nor `onboarding`. |
| `ONBOARDING` | Their stage is `onboarding`. |
| `PREBOARDING` | Their stage is `preboarding`. |

Users without a matching database record are denied access unless their Discord
ID has a permanent Leadership grant. This makes other access checks fail closed
if an account has not been provisioned correctly.

## Restricting a command

Import the required group and add `@allow_groups(...)` below the command
decorator:

```python
import discord

from slash_commands.access import LEADERSHIP, allow_groups


@discord.app_commands.command(
    name="leadership-report",
    description="Create a leadership report.",
)
@allow_groups(LEADERSHIP)
async def leadership_report(interaction: discord.Interaction) -> None:
    await interaction.response.send_message("Creating report...", ephemeral=True)
```

The bot responds privately when the user does not have access. Commands that do
not define a local error handler receive this behavior automatically from the
slash-command package.

## Allowing multiple groups

Multiple groups use **OR** logic. This example allows leadership members or
journey mentors:

```python
from slash_commands.access import (
    JOURNEY_MENTOR,
    LEADERSHIP,
    allow_groups,
)


@discord.app_commands.command(
    name="mentor-report",
    description="Create a mentor report.",
)
@allow_groups(LEADERSHIP, JOURNEY_MENTOR)
async def mentor_report(interaction: discord.Interaction) -> None:
    await interaction.response.send_message("Creating report...", ephemeral=True)
```

Do not call `@allow_groups()` without a group. The application will raise a
`ValueError` during command registration.

## Combining access groups with Discord permissions

Access groups and Discord permissions are separate checks. When both decorators
are present, the user must satisfy both requirements:

```python
from discord import app_commands
from slash_commands.access import LEADERSHIP, allow_groups


@app_commands.command(
    name="admin-action",
    description="Perform an administrative action.",
)
@app_commands.default_permissions(manage_channels=True)
@app_commands.checks.has_permissions(manage_channels=True)
@allow_groups(LEADERSHIP)
async def admin_action(interaction: discord.Interaction) -> None:
    await interaction.response.send_message("Done.", ephemeral=True)
```

`default_permissions` controls the command's default visibility in Discord.
`has_permissions` enforces the Discord permission when the command runs.
`allow_groups` enforces the Mantis group requirement.

The `/close-channel` command uses this pattern: the caller must be in leadership
and have Discord's Manage Channels permission.

## Commands with local error handlers

A command-specific error handler runs instead of the package-level handler. It
must pass access errors to `handle_access_denied`:

```python
from discord import app_commands
from slash_commands.access import handle_access_denied


@admin_action.error
async def admin_action_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> None:
    if await handle_access_denied(interaction, error):
        return

    # Handle the command's other errors here.
    raise error
```

The helper returns `True` when it handled an access denial and `False` for any
other error.

## Adding another access group

To introduce another group:

1. Add it to `AccessGroup` in `slash_commands/access.py`.
2. Export a matching module-level constant alongside the existing constants.
3. Add its database membership rule to `_belongs_to`.
4. Add the new group to the table in this document.

Keep membership rules based on canonical database fields. Discord roles are
synchronized representations of that data and should not become a second source
of truth for command authorization.
