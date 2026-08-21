"""Discord message ingestion helpers for the Mantis backend."""

from __future__ import annotations

import json
from typing import Any

import aiohttp
import discord

from config import M4M_DISCORD_API_KEY
from members.service import MemberNotFoundError, resolve_member

INGESTION_URL = (
    "https://kellis-h200-1.csail.mit.edu/api/mantis4mantis/internal-messages/"
)
MESSAGE_BATCH_SIZE = 500


def get_channel_name(channel: Any, fallback: str = "Direct Message") -> str:
    """Return the best available display name for a Discord channel."""
    name = getattr(channel, "name", None)
    if name:
        return name

    recipient = getattr(channel, "recipient", None)
    if recipient is not None:
        return recipient.display_name or recipient.name

    recipients = getattr(channel, "recipients", None)
    if recipients:
        names = [recipient.display_name or recipient.name for recipient in recipients]
        return ", ".join(names)

    return fallback


async def ingest_messages(bot: Any, messages: list[dict]) -> dict:
    """Send a batch of Discord messages to the Mantis backend."""
    del bot
    headers = {
        "Authorization": f"Api-Key {M4M_DISCORD_API_KEY or ''}",
        "Content-Type": "application/json",
    }
    async with aiohttp.ClientSession() as session, session.post(
        INGESTION_URL, data=json.dumps(messages), headers=headers
    ) as response:
        response.raise_for_status()
        return await response.json()


def build_message_payload(message: discord.Message) -> dict:
    """Map a Discord message to the internal-message API schema."""
    author = message.author
    author_discord_id = str(author.id)
    author_name = author.display_name or author.name
    try:
        member = resolve_member(author_discord_id, discord_id=author.id)
        author_mantis_id = member.id
    except MemberNotFoundError:
        author_mantis_id = None

    channel = message.channel
    return {
        "message_id": str(message.id),
        "channel_id": str(channel.id),
        "channel_name": get_channel_name(channel, author_name),
        "thread_id": (
            str(channel.parent_id) if isinstance(channel, discord.Thread) else None
        ),
        "author_discord_id": author_discord_id,
        "author_mantis_id": (
            str(author_mantis_id) if author_mantis_id is not None else None
        ),
        "author_name": author_name,
        "content": message.content,
        "created_at": message.created_at.isoformat(),
    }


async def ingest_channel_history(bot: Any, channel: Any, limit: int = 100) -> dict:
    """Upload channel history sequentially in batches of at most 500."""
    result = {"total": 0, "created": 0, "errors": 0}
    batch: list[dict] = []

    async def upload_batch() -> None:
        if not batch:
            return
        response = await ingest_messages(bot, batch)
        result["created"] += int(response.get("created", len(batch)))
        result["errors"] += int(response.get("errors", 0))
        batch.clear()

    async for message in channel.history(limit=limit):
        result["total"] += 1
        try:
            batch.append(build_message_payload(message))
        except Exception:  # noqa: BLE001
            result["errors"] += 1
            continue
        if len(batch) == MESSAGE_BATCH_SIZE:
            await upload_batch()

    await upload_batch()
    return result
