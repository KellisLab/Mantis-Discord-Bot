"""Mantis Oracle: per-thread Q&A in the #questions channel."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from config import ORACLE_QUESTIONS_CHANNEL_ID
from utils.oracle_api import OracleAPIError, ask_oracle

logger = logging.getLogger(__name__)

DISCORD_CHAR_LIMIT = 2000
THREAD_NAME_QUESTION_CHARS = 90
MAX_HISTORY_MESSAGES = 100

CONTINUE_HINT = (
    "\n\n-# Reply directly to this message to keep chatting with the Oracle."
)

ORACLE_ERROR_MESSAGE = (
    "Sorry, I couldn't reach the Oracle just now. Please try again in a bit."
)

# The Oracle API has no separate system-prompt channel, so this guidance is
# prepended as the first message of every request instead.
UNCERTAINTY_INSTRUCTION = {
    "role": "user",
    "content": (
        "If unsure, say \"I'm not confident I have a reliable answer for "
        "that. Reply to this message with more context and I'll take "
        "another look.\" — don't elaborate."
    ),
}


def _split_for_discord(text: str) -> list[str]:
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= DISCORD_CHAR_LIMIT:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, DISCORD_CHAR_LIMIT)
        if split_at <= 0:
            split_at = DISCORD_CHAR_LIMIT
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    return chunks or [""]


class MantisOracleCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener("on_message")
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not message.content or not message.content.strip():
            return

        if (
            isinstance(message.channel, discord.TextChannel)
            and message.channel.id == ORACLE_QUESTIONS_CHANNEL_ID
        ):
            await self._handle_new_question(message)
        elif (
            isinstance(message.channel, discord.Thread)
            and message.channel.parent_id == ORACLE_QUESTIONS_CHANNEL_ID
        ):
            await self._handle_followup(message)

    async def _handle_new_question(self, message: discord.Message) -> None:
        question = message.content.strip()
        thread_name = f"Q: {question[:THREAD_NAME_QUESTION_CHARS]}"

        try:
            thread = await message.create_thread(name=thread_name)
        except discord.HTTPException:
            logger.exception("Failed to create Oracle thread")
            return

        async with thread.typing():
            try:
                reply = await ask_oracle(
                    [
                        UNCERTAINTY_INSTRUCTION,
                        {"role": "user", "content": question},
                    ]
                )
            except OracleAPIError:
                logger.exception("Oracle API call failed for new question")
                await thread.send(ORACLE_ERROR_MESSAGE)
                return

        await self._send_oracle_reply(thread, reply)

    async def _handle_followup(self, message: discord.Message) -> None:
        if not await self._is_reply_to_bot(message):
            return

        thread = message.channel
        history = await self._build_history(thread)

        async with thread.typing():
            try:
                reply = await ask_oracle([UNCERTAINTY_INSTRUCTION, *history])
            except OracleAPIError:
                logger.exception("Oracle API call failed for follow-up")
                await thread.send(ORACLE_ERROR_MESSAGE)
                return

        await self._send_oracle_reply(thread, reply)

    async def _is_reply_to_bot(self, message: discord.Message) -> bool:
        if not message.reference or not message.reference.message_id:
            return False
        resolved = message.reference.resolved
        if isinstance(resolved, discord.Message):
            return resolved.author.id == self.bot.user.id
        try:
            referenced = await message.channel.fetch_message(
                message.reference.message_id
            )
        except discord.HTTPException:
            return False
        return referenced.author.id == self.bot.user.id

    async def _build_history(self, thread: discord.Thread) -> list[dict[str, str]]:
        history: list[dict[str, str]] = []
        async for msg in thread.history(limit=MAX_HISTORY_MESSAGES, oldest_first=True):
            if msg.type not in (discord.MessageType.default, discord.MessageType.reply):
                continue
            content = msg.content
            if msg.author.bot:
                content = content.replace(CONTINUE_HINT, "")
            content = content.strip()
            if not content:
                continue
            role = "assistant" if msg.author.bot else "user"
            history.append({"role": role, "content": content})
        return history

    async def _send_oracle_reply(self, thread: discord.Thread, reply: str) -> None:
        chunks = _split_for_discord(reply)
        for i, chunk in enumerate(chunks):
            if i == len(chunks) - 1:
                chunk += CONTINUE_HINT
            await thread.send(chunk[:DISCORD_CHAR_LIMIT])


async def setup(bot: commands.Bot):
    await bot.add_cog(MantisOracleCog(bot))
