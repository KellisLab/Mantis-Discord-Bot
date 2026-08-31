"""Tests for projecting team join requests into private team channels."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import discord

from members.models import User
from teams.discord import (
    _create_directory_join_request,
    _join_request_content,
    post_join_request,
    restore_team_views,
)
from teams.models import JoinRequest, Team
from teams.service import JoinRequestDetails, TeamDetails


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
    async def test_posts_request_without_rewriting_channel_permissions(self) -> None:
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
            patch("teams.discord.ensure_team_role", new=AsyncMock(return_value=None)),
            patch(
                "teams.discord.reconcile_team_channel_permissions",
                new=AsyncMock(),
            ) as reconcile,
            patch("teams.discord.set_join_request_message_id") as save_message_id,
        ):
            await post_join_request(bot, guild, details)

        reconcile.assert_not_awaited()
        channel.send.assert_awaited_once()
        save_message_id.assert_called_once_with(details.request.uuid, 900)

    async def test_join_request_mentions_team_role_not_members(self) -> None:
        role = MagicMock(spec=discord.Role)
        role.mention = "<@&444>"

        content = _join_request_content(role)

        self.assertEqual(content, "<@&444>")
        self.assertNotIn("<@101>", content)

    async def test_join_request_no_content_without_role(self) -> None:
        self.assertIsNone(_join_request_content(None))

    async def test_failed_button_post_discards_phantom_pending_request(self) -> None:
        details = _request_details()
        bot = MagicMock()
        guild = MagicMock(spec=discord.Guild)

        with (
            patch("teams.discord.get_user_by_discord", return_value=details.member),
            patch("teams.discord.create_join_request", return_value=details),
            patch(
                "teams.discord.post_join_request",
                new=AsyncMock(side_effect=RuntimeError("cannot post")),
            ),
            patch("teams.discord.discard_unposted_join_request") as discard,
            self.assertRaises(RuntimeError),
        ):
            await _create_directory_join_request(bot, guild, 101, details.team.uuid)

        discard.assert_called_once_with(details.request.uuid)

    async def test_startup_discards_request_without_message_id(self) -> None:
        details = _request_details()
        bot = MagicMock()
        bot._team_views_restored = False

        with (
            patch("teams.discord._directory_state", return_value={}),
            patch(
                "teams.discord.get_pending_join_requests",
                return_value=(details.request,),
            ),
            patch("teams.discord.get_open_close_attempts", return_value=()),
            patch("teams.discord.discard_unposted_join_request") as discard,
            patch("teams.discord.post_join_request", new=AsyncMock()) as post,
        ):
            await restore_team_views(bot)

        discard.assert_called_once_with(details.request.uuid)
        post.assert_not_awaited()

    async def test_startup_discards_request_when_message_is_missing(self) -> None:
        details = _request_details()
        details.request.discord_message_id = "900"
        channel = MagicMock(spec=discord.TextChannel)
        response = MagicMock(status=404, reason="Not Found")
        channel.fetch_message = AsyncMock(
            side_effect=discord.NotFound(response, "Unknown Message")
        )
        channel.guild = MagicMock(spec=discord.Guild)
        bot = MagicMock()
        bot._team_views_restored = False
        bot.get_channel.return_value = channel

        with (
            patch("teams.discord._directory_state", return_value={}),
            patch(
                "teams.discord.get_pending_join_requests",
                return_value=(details.request,),
            ),
            patch("teams.discord.get_join_request_details", return_value=details),
            patch("teams.discord.get_open_close_attempts", return_value=()),
            patch("teams.discord.discard_join_requests_by_message_ids") as discard,
        ):
            await restore_team_views(bot)

        discard.assert_called_once_with(("900",))


if __name__ == "__main__":
    unittest.main()
