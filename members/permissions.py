"""Shared member authorization policy."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from members.models import User


PERMANENT_LEADERSHIP_DISCORD_IDS = frozenset({"1121533121951707283"})


def has_leadership(
    user: User | None = None,
    *,
    discord_id: str | int | None = None,
) -> bool:
    """Return effective Leadership, including permanent Discord-ID grants."""

    resolved_discord_id = (
        discord_id if discord_id is not None else user.discord_id if user else None
    )
    return (
        resolved_discord_id is not None
        and str(resolved_discord_id) in PERMANENT_LEADERSHIP_DISCORD_IDS
    ) or (user is not None and user.is_leadership)
