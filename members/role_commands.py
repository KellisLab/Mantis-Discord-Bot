"""Slash commands letting members request stage, mentor, and leadership roles."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from members.commands import STAGE_CHOICES
from members.models import Stage
from members.role_discord import (
    on_role_request_messages_deleted,
    post_role_request,
    restore_role_request_views,
)
from members.role_models import RoleRequestType
from members.role_service import (
    RoleRequestServiceError,
    create_role_request,
    discard_unposted_role_request,
)

LOGGER = logging.getLogger(__name__)

request_roles_group = app_commands.Group(
    name="request-roles",
    description="Request a stage advancement, Journey Mentor, or Leadership role.",
    guild_only=True,
)


def setup(bot: commands.Bot) -> None:
    bot.tree.add_command(request_roles_group)

    _ready_ran = False

    async def ready_listener() -> None:
        nonlocal _ready_ran
        await restore_role_request_views(bot)
        _ready_ran = True

    async def raw_message_delete_listener(
        payload: discord.RawMessageDeleteEvent,
    ) -> None:
        await on_role_request_messages_deleted((payload.message_id,))

    async def raw_bulk_message_delete_listener(
        payload: discord.RawBulkMessageDeleteEvent,
    ) -> None:
        await on_role_request_messages_deleted(payload.message_ids)

    bot.add_listener(ready_listener, "on_ready")
    bot.add_listener(raw_message_delete_listener, "on_raw_message_delete")
    bot.add_listener(raw_bulk_message_delete_listener, "on_raw_bulk_message_delete")


def _collect_evidence_urls(
    attachments: tuple[discord.Attachment | None, ...],
) -> tuple[str, ...]:
    return tuple(attachment.url for attachment in attachments if attachment is not None)


async def _submit(
    interaction: discord.Interaction,
    request_type: RoleRequestType,
    *,
    requested_stage: Stage | None = None,
    justification: str | None = None,
    evidence_urls: tuple[str, ...] = (),
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        details = await asyncio.to_thread(
            create_role_request,
            interaction.user.id,
            request_type,
            requested_stage=requested_stage,
            justification=justification,
            evidence_urls=evidence_urls,
        )
    except RoleRequestServiceError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return
    except Exception:
        LOGGER.exception("Unexpected /request-roles failure")
        await interaction.followup.send(
            "Your request could not be submitted due to an unexpected error.",
            ephemeral=True,
        )
        return

    try:
        await post_role_request(interaction.client, details)
    except Exception:
        # A request without its Approve/Reject message cannot be acted on and
        # would block retries via the pending-request unique index.
        await asyncio.to_thread(discard_unposted_role_request, details.request.uuid)
        LOGGER.exception("Could not post role request %s", details.request.uuid)
        await interaction.followup.send(
            "Your request could not be posted to #leadership. Please try again.",
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        "Your request was submitted to Leadership for review.", ephemeral=True
    )


@request_roles_group.command(
    name="stage", description="Request advancement to a higher progression stage."
)
@app_commands.describe(
    stage="The stage you are requesting.",
    justification="Describe why you are have earned this stage, mentioning journey mentors or other individuals who can support your request.",
    evidence_1="Optional supporting evidence (image, PDF, etc.).",
    evidence_2="Optional supporting evidence (image, PDF, etc.).",
)
@app_commands.choices(stage=STAGE_CHOICES)
@app_commands.guild_only()
async def request_stage(
    interaction: discord.Interaction,
    stage: app_commands.Choice[str],
    justification: str | None = None,
    evidence_1: discord.Attachment | None = None,
    evidence_2: discord.Attachment | None = None,
) -> None:
    await _submit(
        interaction,
        RoleRequestType.STAGE,
        requested_stage=Stage(stage.value),
        justification=justification,
        evidence_urls=_collect_evidence_urls((evidence_1, evidence_2)),
    )


@request_roles_group.command(
    name="journey-mentor", description="Request Journey Mentor status."
)
@app_commands.describe(
    justification="Describe why you believe you have earned Journey Mentor status, mentioning journey mentors or other individuals who can support your request.",
    evidence_1="Optional supporting evidence (image, PDF, etc.).",
    evidence_2="Optional supporting evidence (image, PDF, etc.).",
)
@app_commands.guild_only()
async def request_journey_mentor(
    interaction: discord.Interaction,
    justification: str | None = None,
    evidence_1: discord.Attachment | None = None,
    evidence_2: discord.Attachment | None = None,
) -> None:
    await _submit(
        interaction,
        RoleRequestType.JOURNEY_MENTOR,
        justification=justification,
        evidence_urls=_collect_evidence_urls((evidence_1, evidence_2)),
    )


@request_roles_group.command(
    name="leadership", description="Request Leadership status."
)
@app_commands.describe(
    justification="Describe why you believe you have earned Leadership status, mentioning journey mentors or other individuals who can support your request.",
    evidence_1="Optional supporting evidence (image, PDF, etc.).",
    evidence_2="Optional supporting evidence (image, PDF, etc.).",
)
@app_commands.guild_only()
async def request_leadership(
    interaction: discord.Interaction,
    justification: str | None = None,
    evidence_1: discord.Attachment | None = None,
    evidence_2: discord.Attachment | None = None,
) -> None:
    await _submit(
        interaction,
        RoleRequestType.LEADERSHIP,
        justification=justification,
        evidence_urls=_collect_evidence_urls((evidence_1, evidence_2)),
    )
