"""Member profile and leadership administration slash commands."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
from collections.abc import Callable

import discord
from discord import app_commands
from discord.ext import commands

from member_service import (
    AmbiguousMemberError,
    DiscordAlreadyLinkedError,
    DuplicateEmailError,
    MemberNotFoundError,
    MemberServiceError,
    add_member,
    create_or_link_profile,
    import_members,
    kick_member,
    set_member_stage,
    toggle_member_flag,
)
from slash_commands.access import LEADERSHIP, allow_groups
from users import Stage, User

LOGGER = logging.getLogger(__name__)
MAX_CSV_BYTES = 2 * 1024 * 1024
CSV_FIELDS = {"email", "full_name", "github_username", "whatsapp", "stage"}
STAGE_CHOICES = [
    app_commands.Choice(name=stage.value.replace("_", " ").title(), value=stage.value)
    for stage in Stage
]

member_commands = app_commands.Group(
    name="member",
    description="Manage canonical Mantis member profiles.",
    guild_only=True,
)


def setup(bot: commands.Bot) -> None:
    bot.tree.add_command(create_profile)
    bot.tree.add_command(member_commands)


def _enqueue_sync(interaction: discord.Interaction, member: User) -> None:
    if member.discord_id is None:
        return
    role_sync = getattr(interaction.client, "user_role_sync", None)
    if role_sync is not None:
        role_sync.enqueue(member.discord_id)


async def _run_member_change(
    interaction: discord.Interaction,
    operation: Callable[[], User],
) -> User | None:
    try:
        member = await asyncio.to_thread(operation)
    except (MemberNotFoundError, AmbiguousMemberError, MemberServiceError) as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return None
    except Exception:
        LOGGER.exception("Unexpected member command failure")
        await interaction.followup.send(
            "The member could not be updated due to an unexpected error.",
            ephemeral=True,
        )
        return None

    _enqueue_sync(interaction, member)
    return member


@app_commands.command(
    name="create-profile",
    description="Create or link your member profile by email.",
)
@app_commands.rename(
    full_name="full-name",
    github_username="github-username",
)
@app_commands.describe(
    email="Your member email (the only field used to find an existing profile).",
    full_name="Your full name.",
    github_username="Your GitHub username.",
    whatsapp="Full international number beginning with + and country code.",
)
@app_commands.guild_only()
async def create_profile(
    interaction: discord.Interaction,
    email: str,
    full_name: str | None = None,
    github_username: str | None = None,
    whatsapp: str | None = None,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        result = await asyncio.to_thread(
            create_or_link_profile,
            discord_id=interaction.user.id,
            email=email,
            full_name=full_name,
            github_username=github_username,
            whatsapp_number=whatsapp,
        )
    except DiscordAlreadyLinkedError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return
    except MemberServiceError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return
    except Exception:
        LOGGER.exception("Unexpected /create-profile failure")
        await interaction.followup.send(
            "Your profile could not be saved due to an unexpected error.",
            ephemeral=True,
        )
        return

    _enqueue_sync(interaction, result.member)
    if (
        result.previous_discord_id is not None
        and result.previous_discord_id != result.member.discord_id
    ):
        role_sync = getattr(interaction.client, "user_role_sync", None)
        if role_sync is not None:
            role_sync.enqueue(result.previous_discord_id)
    action = "created" if result.created else "linked and updated"
    await interaction.followup.send(
        f"Your member profile was {action}. Discord nickname and roles are syncing.",
        ephemeral=True,
    )


@member_commands.command(name="add", description="Add an unlinked member profile.")
@app_commands.rename(
    full_name="full-name",
    github_username="github-username",
)
@app_commands.describe(
    email="Member email (required and unique).",
    full_name="Member full name.",
    github_username="Member GitHub username.",
    whatsapp="Full international number beginning with + and country code.",
    stage="Initial progression stage (defaults to preboarding).",
)
@app_commands.choices(stage=STAGE_CHOICES)
@app_commands.guild_only()
@allow_groups(LEADERSHIP)
async def member_add(
    interaction: discord.Interaction,
    email: str,
    full_name: str | None = None,
    github_username: str | None = None,
    whatsapp: str | None = None,
    stage: app_commands.Choice[str] | None = None,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        member = await asyncio.to_thread(
            add_member,
            email=email,
            full_name=full_name,
            github_username=github_username,
            whatsapp_number=whatsapp,
            stage=stage.value if stage is not None else None,
        )
    except DuplicateEmailError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return
    except MemberServiceError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return
    except Exception:
        LOGGER.exception("Unexpected /member add failure")
        await interaction.followup.send(
            "The member could not be added due to an unexpected error.",
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        f"Added unlinked member {member.email} at stage {member.stage.value}.",
        ephemeral=True,
    )


@member_commands.command(
    name="edit-stage",
    description="Update a member's progression stage.",
)
@app_commands.describe(
    identifier="@Discord, exact email, or exact full name.",
    stage="New progression stage.",
)
@app_commands.choices(stage=STAGE_CHOICES)
@app_commands.guild_only()
@allow_groups(LEADERSHIP)
async def member_edit_stage(
    interaction: discord.Interaction,
    identifier: str,
    stage: app_commands.Choice[str],
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    member = await _run_member_change(
        interaction,
        lambda: set_member_stage(identifier, stage.value),
    )
    if member is not None:
        await interaction.followup.send(
            f"Updated {member.email} to {member.stage.value}; Discord roles are syncing.",
            ephemeral=True,
        )


@member_commands.command(
    name="leader",
    description="Toggle a member's Leadership status.",
)
@app_commands.describe(identifier="@Discord, exact email, or exact full name.")
@app_commands.guild_only()
@allow_groups(LEADERSHIP)
async def member_leader(
    interaction: discord.Interaction,
    identifier: str,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    member = await _run_member_change(
        interaction,
        lambda: toggle_member_flag(identifier, "is_leadership"),
    )
    if member is not None:
        status = "enabled" if member.is_leadership else "disabled"
        await interaction.followup.send(
            f"Leadership {status} for {member.email}; Discord roles are syncing.",
            ephemeral=True,
        )


@member_commands.command(
    name="journey-mentor",
    description="Toggle a member's Journey Mentor status.",
)
@app_commands.describe(identifier="@Discord, exact email, or exact full name.")
@app_commands.guild_only()
@allow_groups(LEADERSHIP)
async def member_journey_mentor(
    interaction: discord.Interaction,
    identifier: str,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    member = await _run_member_change(
        interaction,
        lambda: toggle_member_flag(identifier, "is_journey_mentor"),
    )
    if member is not None:
        status = "enabled" if member.is_journey_mentor else "disabled"
        await interaction.followup.send(
            f"Journey Mentor {status} for {member.email}; Discord roles are syncing.",
            ephemeral=True,
        )


@member_commands.command(
    name="kick",
    description="Reset a member to preboarding and remove special access.",
)
@app_commands.describe(identifier="@Discord, exact email, or exact full name.")
@app_commands.guild_only()
@allow_groups(LEADERSHIP)
async def member_kick(
    interaction: discord.Interaction,
    identifier: str,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    member = await _run_member_change(
        interaction,
        lambda: kick_member(identifier),
    )
    if member is not None:
        await interaction.followup.send(
            f"Reset {member.email} to preboarding; Discord roles are syncing.",
            ephemeral=True,
        )


@member_commands.command(name="import", description="Import unlinked members from CSV.")
@app_commands.describe(csv_file="CSV with the documented member profile columns.")
@app_commands.rename(csv_file="csv")
@app_commands.guild_only()
@allow_groups(LEADERSHIP)
async def member_import(
    interaction: discord.Interaction,
    csv_file: discord.Attachment,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    if csv_file.size > MAX_CSV_BYTES:
        await interaction.followup.send(
            "The CSV is too large; the maximum import size is 2 MB.",
            ephemeral=True,
        )
        return

    try:
        raw_csv = await csv_file.read(use_cached=True)
        text = raw_csv.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text), skipinitialspace=True)
        fieldnames = set(reader.fieldnames or ())
        if "email" not in fieldnames:
            await interaction.followup.send(
                "The CSV must include an email header.",
                ephemeral=True,
            )
            return
        unexpected_fields = fieldnames - CSV_FIELDS
        if unexpected_fields:
            LOGGER.info(
                "Ignoring extra member import fields: %s",
                ", ".join(sorted(unexpected_fields)),
            )
        rows = [{field: row.get(field) for field in CSV_FIELDS} for row in reader]
    except (UnicodeDecodeError, csv.Error):
        await interaction.followup.send(
            "The attachment is not a valid UTF-8 CSV.",
            ephemeral=True,
        )
        return
    except discord.HTTPException:
        await interaction.followup.send(
            "Discord could not provide the CSV attachment; please try again.",
            ephemeral=True,
        )
        return

    try:
        result = await asyncio.to_thread(import_members, rows)
    except Exception:
        LOGGER.exception("Unexpected /member import failure")
        await interaction.followup.send(
            "The CSV could not be imported due to an unexpected error.",
            ephemeral=True,
        )
        return
    await interaction.followup.send(
        f"Import complete — created: {result.created}, skipped: {result.skipped}, "
        f"errors: {result.errors}.",
        ephemeral=True,
    )
