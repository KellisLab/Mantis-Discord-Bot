"""Tests for projecting team join requests into private team channels."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import discord

from members.models import User
from teams.discord import on_directory_reaction, post_join_request
from teams.models import JoinRequest, Team
from teams.service import JoinRequestDetails, TeamDetails, TeamServiceError


def _request_details() -> JoinRequestDetails:
    team = Team(
        uuid=uuid4(),
        name="Atlas",
        discord_channel_id="700",
    )
    member = User(
        id=uuid4(),
        email="requester@example.com",
        discord_id="101",
    )
    request = JoinRequest(
        uuid=uuid4(),
        team_uuid=team.uuid,
        member_uuid=member.id,
    )
    return JoinRequestDetails(request=request, team=team, member=member)


class JoinRequestProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_repairs_channel_permissions_before_posting(self) -> None:
        details = _request_details()
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock(return_value=SimpleNamespace(id=900))
        bot = MagicMock()
        guild = MagicMock(spec=discord.Guild)

        with (
            patch(
                "teams.discord._fetch_team_channel",
                new=AsyncMock(return_value=channel),
            ),
            patch(
                "teams.discord.get_team_details",
                return_value=TeamDetails(team=details.team, members=()),
            ),
            patch(
                "teams.discord.reconcile_team_channel_permissions",
                new=AsyncMock(return_value=True),
            ) as reconcile,
            patch("teams.discord.set_join_request_message_id") as save_message_id,
        ):
            await post_join_request(bot, guild, details)

        reconcile.assert_awaited_once_with(channel, unittest.mock.ANY)
        channel.send.assert_awaited_once()
        save_message_id.assert_called_once_with(details.request.uuid, 900)

    async def test_permission_repair_failure_does_not_attempt_send(self) -> None:
        details = _request_details()
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()

        with (
            patch(
                "teams.discord._fetch_team_channel",
                new=AsyncMock(return_value=channel),
            ),
            patch(
                "teams.discord.get_team_details",
                return_value=TeamDetails(team=details.team, members=()),
            ),
            patch(
                "teams.discord.reconcile_team_channel_permissions",
                new=AsyncMock(return_value=False),
            ),
            self.assertRaisesRegex(TeamServiceError, "repair my access"),
        ):
            await post_join_request(MagicMock(), MagicMock(), details)

        channel.send.assert_not_awaited()

    async def test_failed_post_discards_phantom_pending_request(self) -> None:
        details = _request_details()
        bot = MagicMock()
        bot.user.id = 999
        guild = MagicMock(spec=discord.Guild)
        guild.get_channel.return_value = None
        guild.get_member.return_value = None
        bot.get_guild.return_value = guild
        payload = SimpleNamespace(
            user_id=101,
            guild_id=500,
            channel_id=600,
            message_id=800,
            emoji="🚀",
        )

        with (
            patch(
                "teams.discord._directory_state",
                return_value={
                    "toc_message_id": "800",
                    "mapping": {"🚀": str(details.team.uuid)},
                },
            ),
            patch("teams.discord.get_user_by_discord", return_value=details.member),
            patch("teams.discord.create_join_request", return_value=details),
            patch(
                "teams.discord.post_join_request",
                new=AsyncMock(side_effect=TeamServiceError("cannot post")),
            ),
            patch("teams.discord.discard_unposted_join_request") as discard,
        ):
            await on_directory_reaction(bot, payload)

        discard.assert_called_once_with(details.request.uuid)


if __name__ == "__main__":
    unittest.main()
