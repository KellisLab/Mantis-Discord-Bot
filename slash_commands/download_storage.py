"""Leadership-only export of canonical member and team storage."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import zipfile
from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from sqlmodel import SQLModel, select

from database import get_session
from members.models import User
from slash_commands.access import LEADERSHIP, allow_groups
from teams.models import CloseAttempt, CloseVote, JoinRequest, Team, TeamMembership

LOGGER = logging.getLogger(__name__)
EXPORT_VERSION = 1


def setup(bot: commands.Bot) -> None:
    bot.tree.add_command(download_storage)


def _json_row(model: SQLModel) -> dict[str, Any]:
    """Return a JSON-safe representation of one stored model."""

    return model.model_dump(mode="json")


def _sort_rows(rows: list[dict[str, Any]], *keys: str) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: tuple(str(row.get(key, "")) for key in keys))


def build_storage_export() -> io.BytesIO:
    """Create a ZIP containing all canonical member and team table rows."""

    with get_session() as session:
        users = [_json_row(row) for row in session.exec(select(User)).all()]
        teams = [_json_row(row) for row in session.exec(select(Team)).all()]
        memberships = [
            _json_row(row) for row in session.exec(select(TeamMembership)).all()
        ]
        join_requests = [
            _json_row(row) for row in session.exec(select(JoinRequest)).all()
        ]
        close_attempts = [
            _json_row(row) for row in session.exec(select(CloseAttempt)).all()
        ]
        close_votes = [_json_row(row) for row in session.exec(select(CloseVote)).all()]

    users = _sort_rows(users, "id")
    teams = _sort_rows(teams, "uuid")
    memberships = _sort_rows(memberships, "team_uuid", "member_uuid")
    join_requests = _sort_rows(join_requests, "uuid")
    close_attempts = _sort_rows(close_attempts, "uuid")
    close_votes = _sort_rows(close_votes, "close_attempt_uuid", "member_uuid")

    exported_at = datetime.now(timezone.utc).isoformat()
    members_document = {
        "export_version": EXPORT_VERSION,
        "exported_at": exported_at,
        "users": users,
    }
    teams_document = {
        "export_version": EXPORT_VERSION,
        "exported_at": exported_at,
        "teams": teams,
        "team_memberships": memberships,
        "team_join_requests": join_requests,
        "team_close_attempts": close_attempts,
        "team_close_votes": close_votes,
    }
    manifest = {
        "export_version": EXPORT_VERSION,
        "exported_at": exported_at,
        "format": "JSON documents in a ZIP archive",
        "files": {
            "users.json": {"users": len(users)},
            "teams.json": {
                "teams": len(teams),
                "team_memberships": len(memberships),
                "team_join_requests": len(join_requests),
                "team_close_attempts": len(close_attempts),
                "team_close_votes": len(close_votes),
            },
        },
    }

    archive = io.BytesIO()
    with zipfile.ZipFile(
        archive,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as zip_file:
        for filename, document in (
            ("manifest.json", manifest),
            ("users.json", members_document),
            ("teams.json", teams_document),
        ):
            zip_file.writestr(
                filename,
                json.dumps(document, indent=2, sort_keys=True).encode("utf-8"),
            )
    archive.seek(0)
    return archive


@app_commands.command(
    name="download-storage",
    description="Download all canonical member and team storage as JSON.",
)
@app_commands.guild_only()
@allow_groups(LEADERSHIP)
async def download_storage(interaction: discord.Interaction) -> None:
    """Send a private ZIP snapshot to a Leadership member."""

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        archive = await asyncio.to_thread(build_storage_export)
    except Exception:
        LOGGER.exception("Unexpected /download-storage failure")
        await interaction.followup.send(
            "The member and team storage could not be exported.",
            ephemeral=True,
        )
        return

    size = archive.getbuffer().nbytes
    guild_limit = getattr(interaction.guild, "filesize_limit", 10 * 1024 * 1024)
    if size > guild_limit:
        archive.close()
        await interaction.followup.send(
            "The storage export is larger than this server's attachment limit "
            f"({guild_limit / (1024 * 1024):.0f} MB).",
            ephemeral=True,
        )
        return

    filename = f"mantis-storage-{datetime.now(timezone.utc):%Y%m%d-%H%M%SZ}.zip"
    try:
        await interaction.followup.send(
            "Member and team storage export. This archive contains private member "
            "data; store and share it securely.",
            file=discord.File(archive, filename=filename),
            ephemeral=True,
        )
    except (discord.Forbidden, discord.HTTPException):
        LOGGER.exception("Discord rejected the /download-storage attachment")
        await interaction.followup.send(
            "Discord could not upload the storage export.",
            ephemeral=True,
        )
    finally:
        archive.close()
