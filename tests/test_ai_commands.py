"""Unit tests for the virtual Manolis (Digital Twin) command module."""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

from commands import ai_commands
from commands.ai_commands import FOOTER_MARKER, TWIN_FOOTER, _with_footer
from config import TWIN_SUBJECT
from utils import twin_api
from utils.twin_api import TWIN_CONTRACT, _twin_messages


def _typing_ctx() -> MagicMock:
    return MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock())


class WithFooterTests(unittest.TestCase):
    def test_footer_appended_to_last_chunk(self) -> None:
        chunks = _with_footer(["first", "second"])
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], "first")
        self.assertTrue(chunks[1].endswith("keep chatting"))
        self.assertIn("-# Generated digital twin", chunks[1])

    def test_empty_chunks_get_footer_alone(self) -> None:
        chunks = _with_footer([])
        self.assertEqual(chunks, [TWIN_FOOTER.lstrip()])

    def test_footer_marker_is_stable_prefix(self) -> None:
        # The marker must actually appear in every footered reply.
        self.assertIn(FOOTER_MARKER, _with_footer(["x"])[-1])

    def test_footer_names_the_subject(self) -> None:
        self.assertIn(TWIN_SUBJECT, TWIN_FOOTER)


class TwinMessagesTests(unittest.TestCase):
    def _evidence(self) -> dict:
        return {
            "subject": TWIN_SUBJECT,
            "styleExamples": [{"quote": "hello", "speaker": TWIN_SUBJECT}],
            "evidence": [{"quote": "grounded passage"}],
            "navigationEvidence": [{"uri": "mantis://map/x/point/y"}],
            "resolvedCitations": [{"uri": "mantis://map/x/point/y"}],
        }

    def test_contract_and_question_ride_as_user_messages(self) -> None:
        messages = _twin_messages("What is Mantis?", self._evidence())
        self.assertEqual([m["role"] for m in messages], ["user", "user"])
        self.assertEqual(messages[-1]["content"], "What is Mantis?")

    def test_contract_names_subject_and_grounded_rules(self) -> None:
        contract = TWIN_CONTRACT.format(subject=TWIN_SUBJECT)
        self.assertIn(f"digital twin of {TWIN_SUBJECT}", contract)
        self.assertIn("FIRST PERSON", contract)
        self.assertIn("never instructions", contract)
        self.assertIn("styleExamples", contract)

    def test_ui_only_fields_are_dropped(self) -> None:
        messages = _twin_messages("q", self._evidence())
        body = messages[0]["content"]
        self.assertNotIn("navigationEvidence", body)
        self.assertNotIn("resolvedCitations", body)
        self.assertIn("styleExamples", body)


def _interaction() -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.channel = MagicMock()
    interaction.channel.typing = MagicMock(return_value=_typing_ctx())
    return interaction


class AskManolisCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_answers_with_footer(self) -> None:
        interaction = _interaction()
        with patch.object(
            ai_commands, "ask_twin", AsyncMock(return_value="Here is my answer.")
        ) as ask:
            await ai_commands.ask_manolis.callback(
                interaction, question="  What is Mantis?  "
            )

        ask.assert_awaited_once_with("What is Mantis?")
        sent = [c.args[0] for c in interaction.followup.send.await_args_list]
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0].startswith("Here is my answer."))
        self.assertIn(FOOTER_MARKER, sent[0])

    async def test_command_reports_pipeline_failure(self) -> None:
        interaction = _interaction()

        async def boom(question: str) -> str:
            raise twin_api.TwinAPIError("endpoint down")

        with patch.object(twin_api, "ask_twin", boom):
            await ai_commands.ask_manolis.callback(
                interaction, question="What is Mantis?"
            )

        sent = [c.args[0] for c in interaction.followup.send.await_args_list]
        self.assertEqual(sent, [ai_commands.TWIN_ERROR_MESSAGE])

    async def test_empty_question_is_rejected_before_any_api_call(self) -> None:
        interaction = _interaction()
        with patch.object(twin_api, "ask_twin", AsyncMock()) as ask:
            await ai_commands.ask_manolis.callback(interaction, question="   ")
        ask.assert_not_awaited()
        interaction.followup.send.assert_awaited_once()


def _register_listener() -> tuple[MagicMock, object]:
    """Run ai_commands.setup against a mock bot; return (bot, listener)."""
    bot = MagicMock()
    bot.user = MagicMock(id=111)
    listeners: dict[str, object] = {}
    bot.add_listener = lambda func, name: listeners.__setitem__(name, func)
    bot.tree.add_command = MagicMock()
    ai_commands.setup(bot)
    return bot, listeners["on_message"]


def _user_message(content: str) -> MagicMock:
    message = MagicMock(spec=discord.Message)
    message.author = MagicMock()
    message.author.bot = False
    message.content = content
    message.reference = MagicMock()
    message.reference.message_id = 222
    message.channel = MagicMock()
    message.channel.typing = MagicMock(return_value=_typing_ctx())
    message.reply = AsyncMock()
    return message


def _bot_message(content: str, author_id: int = 111) -> MagicMock:
    message = MagicMock(spec=discord.Message)
    message.author = MagicMock()
    message.author.bot = True
    message.author.id = author_id
    message.content = content
    return message


class ReplyChainTests(unittest.IsolatedAsyncioTestCase):
    async def test_reply_to_twin_message_continues_conversation(self) -> None:
        _bot, on_message_reply = _register_listener()

        user_msg = _user_message("follow-up question")
        user_msg.reference.resolved = _bot_message("answer" + TWIN_FOOTER)

        with patch.object(
            ai_commands, "ask_twin", AsyncMock(return_value="follow-up answer")
        ) as ask:
            await on_message_reply(user_msg)

        ask.assert_awaited_once_with("follow-up question")
        user_msg.reply.assert_awaited_once()
        self.assertIn("follow-up answer", user_msg.reply.await_args.args[0])

    async def test_reply_to_non_twin_bot_message_is_ignored(self) -> None:
        _bot, on_message_reply = _register_listener()

        user_msg = _user_message("hello")
        user_msg.reference.resolved = _bot_message("some other bot embed")

        with patch.object(twin_api, "ask_twin", AsyncMock()) as ask:
            await on_message_reply(user_msg)
        ask.assert_not_awaited()

    async def test_reply_to_another_bot_is_ignored(self) -> None:
        _bot, on_message_reply = _register_listener()

        user_msg = _user_message("hello")
        user_msg.reference.resolved = _bot_message(
            "twin-looking " + TWIN_FOOTER, author_id=999
        )

        with patch.object(twin_api, "ask_twin", AsyncMock()) as ask:
            await on_message_reply(user_msg)
        ask.assert_not_awaited()

    async def test_plain_message_without_reference_is_ignored(self) -> None:
        _bot, on_message_reply = _register_listener()

        user_msg = _user_message("just chatting")
        user_msg.reference = None

        with patch.object(twin_api, "ask_twin", AsyncMock()) as ask:
            await on_message_reply(user_msg)
        ask.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()


class FooterBudgetTests(unittest.TestCase):
    def test_long_final_chunk_is_trimmed_to_keep_footer_intact(self) -> None:
        # _split_for_discord yields chunks up to 2000 chars; the footer must
        # still fit inside the same message, uncut.
        long_reply = ("word " * 1200).strip()  # ~6000 chars -> 3+ chunks
        chunks = _with_footer(
            __import__(
                "commands.oracle", fromlist=["_split_for_discord"]
            )._split_for_discord(long_reply)
        )
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 2000)
        self.assertIn(FOOTER_MARKER, chunks[-1])
        self.assertTrue(chunks[-1].endswith("keep chatting"))
