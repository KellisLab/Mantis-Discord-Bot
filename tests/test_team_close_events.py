"""Tests for mapping Discord message deletion onto close-attempt lifecycle."""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

from teams.discord import on_team_messages_deleted


class TeamCloseMessageDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_deleted_message_ids_cancel_matching_attempts(self) -> None:
        with patch(
            "teams.discord.asyncio.to_thread", new=AsyncMock(return_value=2)
        ) as run:
            cancelled = await on_team_messages_deleted({"100", 200})

        self.assertEqual(cancelled, 2)
        function, message_ids = run.await_args.args
        self.assertEqual(function.__name__, "cancel_close_attempts_by_message_ids")
        self.assertEqual(set(message_ids), {"100", "200"})


if __name__ == "__main__":
    unittest.main()
