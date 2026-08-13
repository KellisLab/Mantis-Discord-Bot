"""Unit tests for private team-channel permission reconciliation."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

from teams.discord import (
    ALL_TEAMS_ALLOWED_PERMISSIONS,
    BOT_CHANNEL_ALLOWED_PERMISSIONS,
    TEAM_MEMBER_ALLOWED_PERMISSIONS,
    _allow_all_teams_permissions,
    _base_team_channel_overwrites,
    reconcile_team_channel_permissions,
)


def _role(role_id: int, name: str) -> MagicMock:
    role = MagicMock(spec=discord.Role)
    role.id = role_id
    role.name = name
    return role


def _member(member_id: int) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = member_id
    return member


class TeamPermissionPolicyTests(unittest.IsolatedAsyncioTestCase):
    def test_new_channel_overwrites_are_private_before_creation(self) -> None:
        everyone = _role(1, "@everyone")
        all_teams = _role(2, "AllTeams")
        bot_member = _member(99)
        guild = MagicMock(spec=discord.Guild)
        guild.default_role = everyone
        guild.roles = [everyone, all_teams]
        guild.me = bot_member

        overwrites = _base_team_channel_overwrites(guild)

        self.assertIs(overwrites[everyone].view_channel, False)
        self.assertTrue(
            all(
                getattr(overwrites[all_teams], permission) is True
                for permission in ALL_TEAMS_ALLOWED_PERMISSIONS
            )
        )
        self.assertTrue(
            all(
                getattr(overwrites[bot_member], permission) is True
                for permission in BOT_CHANNEL_ALLOWED_PERMISSIONS
            )
        )

    def test_all_teams_permission_helper_is_idempotent(self) -> None:
        overwrite = discord.PermissionOverwrite(view_channel=False)

        self.assertTrue(_allow_all_teams_permissions(overwrite))
        self.assertTrue(
            all(
                getattr(overwrite, permission) is True
                for permission in ALL_TEAMS_ALLOWED_PERMISSIONS
            )
        )
        self.assertFalse(_allow_all_teams_permissions(overwrite))

    async def test_reconcile_adds_members_and_removes_stale_users(self) -> None:
        everyone = _role(1, "@everyone")
        all_teams = _role(2, "AllTeams")
        moderator_role = _role(3, "Moderators")
        bot_member = _member(99)
        current_member = _member(101)
        stale_member = _member(202)

        guild = MagicMock(spec=discord.Guild)
        guild.id = 500
        guild.default_role = everyone
        guild.roles = [everyone, all_teams, moderator_role]
        guild.me = bot_member
        guild.get_member.side_effect = lambda member_id: (
            current_member if member_id == current_member.id else None
        )
        guild.fetch_member = AsyncMock()

        moderator_overwrite = discord.PermissionOverwrite(manage_messages=True)
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 700
        channel.guild = guild
        channel.overwrites = {
            moderator_role: moderator_overwrite,
            stale_member: discord.PermissionOverwrite(view_channel=True),
        }
        channel.edit = AsyncMock()
        details = SimpleNamespace(
            team=SimpleNamespace(name="Atlas"),
            members=(
                SimpleNamespace(uuid="member-uuid", discord_id=str(current_member.id)),
            ),
        )

        self.assertTrue(await reconcile_team_channel_permissions(channel, details))

        submitted = channel.edit.await_args.kwargs["overwrites"]
        self.assertIs(submitted[everyone].view_channel, False)
        self.assertNotIn(stale_member, submitted)
        self.assertIs(submitted[moderator_role], moderator_overwrite)
        self.assertTrue(
            all(
                getattr(submitted[current_member], permission) is True
                for permission in TEAM_MEMBER_ALLOWED_PERMISSIONS
            )
        )
        self.assertTrue(
            all(
                getattr(submitted[all_teams], permission) is True
                for permission in ALL_TEAMS_ALLOWED_PERMISSIONS
            )
        )


if __name__ == "__main__":
    unittest.main()
