"""Tests for bot startup resilience.

These cover the failure modes that previously turned one broken dependency into
a bot-wide outage: a single failing cog skipping every cog after it, and a
heartbeat that reports healthy while the gateway link is gone.
"""

from __future__ import annotations

import os

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GITHUB_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from discord.ext import commands

import bot as botmod


class LoadExtensionsTests(unittest.TestCase):
    def test_one_failing_extension_does_not_skip_the_rest(self) -> None:
        """A broken cog must not stop the cogs listed after it from loading."""

        attempted: list[str] = []

        async def load_extension(name: str) -> None:
            attempted.append(name)
            if name == "first":
                raise commands.ExtensionFailed(name, ValueError("no api key"))

        fake_bot = MagicMock()
        fake_bot.load_extension = load_extension

        with (
            patch.object(botmod, "bot", fake_bot),
            patch.object(botmod, "_EXTENSIONS", ("first", "second", "third")),
        ):
            asyncio.run(botmod._load_extensions())

        self.assertEqual(attempted, ["first", "second", "third"])

    def test_already_loaded_extension_is_not_an_error(self) -> None:
        """Reconnects re-run loading; an already-loaded cog is a no-op."""

        fake_bot = MagicMock()
        fake_bot.load_extension = AsyncMock(
            side_effect=commands.ExtensionAlreadyLoaded("only")
        )

        with (
            patch.object(botmod, "bot", fake_bot),
            patch.object(botmod, "_EXTENSIONS", ("only",)),
            patch.object(botmod.LOGGER, "exception") as logged,
        ):
            asyncio.run(botmod._load_extensions())

        logged.assert_not_called()


class HeartbeatTests(unittest.TestCase):
    def _run_heartbeat(self, *, closed: bool, latency: float) -> bool:
        fake_bot = MagicMock()
        fake_bot.is_closed.return_value = closed
        fake_bot.latency = latency

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "heartbeat"
            with (
                patch.object(botmod, "bot", fake_bot),
                patch.object(botmod, "HEARTBEAT_PATH", path),
            ):
                asyncio.run(botmod.heartbeat_task.coro())
            return path.exists()

    def test_heartbeat_written_while_connected(self) -> None:
        self.assertTrue(self._run_heartbeat(closed=False, latency=0.05))

    def test_no_heartbeat_when_client_closed(self) -> None:
        self.assertFalse(self._run_heartbeat(closed=True, latency=0.05))

    def test_no_heartbeat_without_a_gateway_connection(self) -> None:
        """discord.py reports NaN latency when there is no websocket."""

        self.assertFalse(self._run_heartbeat(closed=False, latency=float("nan")))


if __name__ == "__main__":
    unittest.main()
