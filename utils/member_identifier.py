"""Shared Discord-facing member identifier helpers."""

from __future__ import annotations

import discord

IDENTIFIER_DESCRIPTION = (
    "GitHub username, email, full name, Discord tag, mention, or ID."
)


def discord_id_from_tag(guild: discord.Guild | None, identifier: str) -> int | None:
    """Resolve an exact Discord username/tag from the current guild cache."""

    if guild is None:
        return None

    normalized = identifier.strip().casefold()
    matches: list[discord.Member] = []
    for member in guild.members:
        tags = {member.name.casefold(), str(member).casefold()}
        if member.discriminator != "0":
            tags.add(f"{member.name}#{member.discriminator}".casefold())
        if normalized in tags:
            matches.append(member)

    if len(matches) == 1:
        return matches[0].id
    return None
