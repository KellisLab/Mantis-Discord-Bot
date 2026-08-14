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
        async def run_in_thread(function, message_ids):
            if function.__name__ == "cancel_close_attempts_by_message_ids":
                return 2
            if function.__name__ == "discard_join_requests_by_message_ids":
                return 1
            self.fail(f"Unexpected function: {function.__name__}")

        with patch(
            "teams.discord.asyncio.to_thread", new=AsyncMock(side_effect=run_in_thread)
        ) as run:
            cancelled = await on_team_messages_deleted({"100", 200})

        self.assertEqual(cancelled, 3)
        self.assertEqual(run.await_count, 2)
        for call in run.await_args_list:
            _, message_ids = call.args
            self.assertEqual(set(message_ids), {"100", "200"})


if __name__ == "__main__":
    unittest.main()
