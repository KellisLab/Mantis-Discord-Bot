"""Unit tests for the Mantis Oracle cog."""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock

import discord

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

from commands.oracle import CONTINUE_HINT, MantisOracleCog, _split_for_discord
from config import ORACLE_QUESTIONS_CHANNEL_ID


class SplitForDiscordTests(unittest.TestCase):
    def test_short_text_is_single_chunk(self) -> None:
        self.assertEqual(_split_for_discord("hello"), ["hello"])

    def test_long_text_splits_on_newline_boundary(self) -> None:
        text = ("a" * 1990 + "\n") + ("b" * 50)
        chunks = _split_for_discord(text)
        self.assertEqual(len(chunks), 2)
        self.assertTrue(chunks[0].endswith("a"))
        self.assertEqual(chunks[1], "b" * 50)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 2000)


class BuildHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_maps_roles_and_skips_empty_messages(self) -> None:
        bot = MagicMock()
        bot.user = MagicMock(id=111)
        cog = MantisOracleCog(bot)

        def _msg(author_id, content, is_bot, msg_type=discord.MessageType.default):
            m = MagicMock(spec=discord.Message)
            m.author = MagicMock(id=author_id, bot=is_bot)
            m.content = content
            m.type = msg_type
            return m

        messages = [
            _msg(1, "hi there", False),
            _msg(111, "hello!", True),
            _msg(1, "", False),
            _msg(1, "another question", False, discord.MessageType.reply),
        ]

        thread = MagicMock(spec=discord.Thread)

        async def fake_history(limit=None, oldest_first=None):
            for m in messages:
                yield m

        thread.history = fake_history

        history = await cog._build_history(thread)

        self.assertEqual(
            history,
            [
                {"role": "user", "content": "hi there"},
                {"role": "assistant", "content": "hello!"},
                {"role": "user", "content": "another question"},
            ],
        )

    async def test_history_strips_continue_hint_from_bot_messages(self) -> None:
        bot = MagicMock()
        bot.user = MagicMock(id=111)
        cog = MantisOracleCog(bot)

        def _msg(author_id, content, is_bot):
            m = MagicMock(spec=discord.Message)
            m.author = MagicMock(id=author_id, bot=is_bot)
            m.content = content
            m.type = discord.MessageType.default
            return m

        messages = [_msg(111, "Here's the answer." + CONTINUE_HINT, True)]
        thread = MagicMock(spec=discord.Thread)

        async def fake_history(limit=None, oldest_first=None):
            for m in messages:
                yield m

        thread.history = fake_history

        history = await cog._build_history(thread)

        self.assertEqual(
            history, [{"role": "assistant", "content": "Here's the answer."}]
        )


class IsReplyToBotTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_resolved_reference_without_fetch(self) -> None:
        bot = MagicMock()
        bot.user = MagicMock(id=111)
        cog = MantisOracleCog(bot)

        bot_msg = MagicMock(spec=discord.Message)
        bot_msg.author = MagicMock(id=111)

        message = MagicMock(spec=discord.Message)
        message.reference = MagicMock(message_id=42, resolved=bot_msg)
        message.channel = MagicMock()
        message.channel.fetch_message = AsyncMock()

        self.assertTrue(await cog._is_reply_to_bot(message))
        message.channel.fetch_message.assert_not_called()

    async def test_no_reference_is_false(self) -> None:
        bot = MagicMock()
        bot.user = MagicMock(id=111)
        cog = MantisOracleCog(bot)

        message = MagicMock(spec=discord.Message)
        message.reference = None

        self.assertFalse(await cog._is_reply_to_bot(message))


class ChannelConfigTests(unittest.TestCase):
    def test_questions_channel_id_configured(self) -> None:
        self.assertIsInstance(ORACLE_QUESTIONS_CHANNEL_ID, int)


if __name__ == "__main__":
    unittest.main()
