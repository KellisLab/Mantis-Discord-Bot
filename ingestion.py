"""Discord message ingestion helpers for the Mantis backend."""

from __future__ import annotations

import json
import os
from typing import Any

import aiohttp
import discord
from sqlmodel import select

from database import get_session
from members.models import User


async def ingest_messages(bot: Any, messages: list[dict]) -> dict:
    """Send a batch of Discord messages to the Mantis backend."""
    del bot
    api_key = os.getenv("M4M_DISCORD_API_KEY", "")
    headers = {
        "Authorization": f"Api-Key {api_key}",
        "Content-Type": "application/json",
    }
    url = "https://kellis-h200-1.csail.mit.edu/api/mantis4mantis/internal-messages/"

    async with aiohttp.ClientSession() as session, session.post(
        url, data=json.dumps(messages), headers=headers
    ) as response:
        response.raise_for_status()
        return await response.json()


def build_message_payload(message: discord.Message) -> dict:
    """Map a Discord message to the internal-message API schema."""
    with get_session() as session:
        statement = select(User).where(User.discord_id == str(message.author.id))
        author = session.exec(statement).one_or_none()
        author_mantis_id = author.id if author is not None else None

    channel = message.channel
    channel_name = getattr(channel, "name", None) or (
        getattr(message.author, "display_name", None)
        or getattr(message.author, "name", None)
        or "Direct Message"
    )
    return {
        "message_id": str(message.id),
        "channel_id": str(channel.id),
        "channel_name": channel_name,
        "thread_id": (
            str(channel.parent_id) if isinstance(channel, discord.Thread) else None
        ),
        "author_discord_id": str(message.author.id),
        "author_mantis_id": (
            str(author_mantis_id) if author_mantis_id is not None else None
        ),
        "author_name": message.author.display_name or message.author.name,
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
        if len(batch) == 500:
            await upload_batch()

    await upload_batch()
    return result
