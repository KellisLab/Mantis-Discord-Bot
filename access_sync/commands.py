"""Leadership operations for access-sync inspection and reconciliation."""

from __future__ import annotations

import asyncio
import csv
import io
import logging

import discord
from discord import app_commands

from access_sync.github_provider import GitHubAccessProvider
from access_sync.types import AccessSyncError, SyncAction
from members.commands import member_commands
from members.service import MemberServiceError, resolve_member
from slash_commands.access import LEADERSHIP, allow_groups
from utils.member_identifier import IDENTIFIER_DESCRIPTION, discord_id_from_tag

LOGGER = logging.getLogger(__name__)


def _github_provider(interaction: discord.Interaction) -> GitHubAccessProvider | None:
    engine = getattr(interaction.client, "access_sync", None)
    provider = engine.providers.get("github") if engine is not None else None
    return provider if isinstance(provider, GitHubAccessProvider) else None


def _report(actions: list[SyncAction]) -> tuple[str, discord.File]:
    counts: dict[str, int] = {}
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(("provider", "member", "action", "target", "detail"))
    for action in actions:
        counts[action.action] = counts.get(action.action, 0) + 1
        writer.writerow(
            (action.provider, action.member, action.action, action.target, action.detail)
        )
    summary = ", ".join(f"{key}: {counts[key]}" for key in sorted(counts))
    if not summary:
        summary = "no changes"
    payload = io.BytesIO(output.getvalue().encode("utf-8"))
    return summary, discord.File(payload, filename="access-sync-report.csv")


@member_commands.command(
    name="sync-access",
    description="Preview or apply access synchronization for one member.",
)
@app_commands.describe(
    identifier=IDENTIFIER_DESCRIPTION,
    apply="Apply changes; defaults to a read-only preview.",
)
@app_commands.guild_only()
@allow_groups(LEADERSHIP)
async def sync_member_access(
    interaction: discord.Interaction,
    identifier: str,
    apply: bool = False,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    provider = _github_provider(interaction)
    if provider is None:
        await interaction.followup.send("GitHub access sync is unavailable.", ephemeral=True)
        return
    try:
        discord_id = discord_id_from_tag(interaction.guild, identifier)
        member = await asyncio.to_thread(
            resolve_member, identifier, discord_id=discord_id
        )
        result = await provider.reconcile(member.id, dry_run=not apply)
    except (MemberServiceError, AccessSyncError) as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return
    summary, report = _report(result.actions)
    mode = "Applied" if apply else "Dry run"
    await interaction.followup.send(
        f"{mode}: {summary}.", file=report, ephemeral=True
    )


@member_commands.command(
    name="sync-access-all",
    description="Preview or apply forward and reverse GitHub access sweeps.",
)
@app_commands.describe(apply="Apply changes; defaults to a read-only preview.")
@app_commands.guild_only()
@allow_groups(LEADERSHIP)
async def sync_all_access(
    interaction: discord.Interaction,
    apply: bool = False,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    provider = _github_provider(interaction)
    if provider is None:
        await interaction.followup.send("GitHub access sync is unavailable.", ephemeral=True)
        return
    try:
        await provider.validate_configuration()
        result = await provider.bulk_reconcile(dry_run=not apply)
    except AccessSyncError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return
    summary, report = _report(result.actions)
    mode = "Applied" if apply else "Dry run"
    await interaction.followup.send(
        f"{mode}: {summary}.", file=report, ephemeral=True
    )


@member_commands.command(
    name="sync-access-status",
    description="Show failed access synchronization jobs.",
)
@app_commands.guild_only()
@allow_groups(LEADERSHIP)
async def sync_access_status(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    engine = getattr(interaction.client, "access_sync", None)
    jobs = await asyncio.to_thread(engine.failed_jobs) if engine is not None else []
    lines = [
        f"{job.provider} / {job.member_uuid}: "
        f"{(job.last_error or 'unknown error')[:160]}"
        for job in jobs[:10]
    ]
    await interaction.followup.send(
        "\n".join(lines) if lines else "No failed access-sync jobs.",
        ephemeral=True,
    )


@member_commands.command(
    name="sync-access-retry",
    description="Retry all failed access synchronization jobs.",
)
@app_commands.guild_only()
@allow_groups(LEADERSHIP)
async def sync_access_retry(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    engine = getattr(interaction.client, "access_sync", None)
    retried = await asyncio.to_thread(engine.retry_failed) if engine is not None else 0
    await interaction.followup.send(
        f"Queued {retried} failed job(s) for retry.", ephemeral=True
    )
