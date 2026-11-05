"""Virtual Manolis: the Digital Twin of Prof. Kellis, as a community presence.

Replaces the retired "Manolis GPT" (OpenAI assistant) implementation. The
/manolis command and its reply chain are now answered by the Digital Twin
pipeline: a question-scoped evidence packet from the Mantis platform's
"Manolis — Digital Twin" space, answered in first person through the M4M
Oracle endpoint with the twin persona contract (see utils/twin_api.py).
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from commands.oracle import _split_for_discord
from config import TWIN_SUBJECT
from utils.twin_api import TwinAPIError, ask_twin

logger = logging.getLogger(__name__)

DISCORD_CHAR_LIMIT = 2000

TWIN_ERROR_MESSAGE = (
    "Sorry, I couldn't reach the digital twin just now. Please try again in a bit."
)

# -# renders as inline subtext in Discord. Keeps the generated-twin label on
# every reply (the platform contract labels this once in the UI; a chat
# message has no such chrome, so it rides along as a footer).
TWIN_FOOTER = (
    "\n\n-# Generated digital twin of "
    f"{TWIN_SUBJECT} · grounded in the Mantis meeting archive · "
    "Reply to this message to keep chatting"
)

# Stable prefix used to recognize our own twin replies in reply chains.
FOOTER_MARKER = TWIN_FOOTER.lstrip()[:60]


class _NoTyping:
    """Stand-in typing indicator for channels that have none."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


def _typing(channel):
    if isinstance(channel, (discord.TextChannel, discord.Thread, discord.DMChannel)):
        return channel.typing()
    return _NoTyping()


def _with_footer(chunks: list[str]) -> list[str]:
    if not chunks:
        return [TWIN_FOOTER.lstrip()]
    return chunks[:-1] + [chunks[-1] + TWIN_FOOTER]


async def _send_chunks(send, reply: str) -> None:
    for chunk in _with_footer(_split_for_discord(reply)):
        await send(chunk[:DISCORD_CHAR_LIMIT])


async def _answer(send, channel, question: str) -> None:
    """Fetch a twin answer and deliver it through ``send``."""
    async with _typing(channel):
        try:
            reply = await ask_twin(question)
        except TwinAPIError:
            logger.exception("Digital Twin pipeline failed")
            await send(TWIN_ERROR_MESSAGE)
            return
    await _send_chunks(send, reply)


@discord.app_commands.command(
    name="manolis",
    description=f"Ask the digital twin of {TWIN_SUBJECT} a question.",
)
async def ask_manolis(interaction: discord.Interaction, question: str):
    """Answer a question as the digital twin of Prof. Kellis."""
    await interaction.response.defer()
    question = question.strip()
    if not question:
        await interaction.followup.send("Please include a question.")
        return
    await _answer(interaction.followup.send, interaction.channel, question)


def setup(bot: commands.Bot):
    """Register the virtual Manolis command and reply listener."""
    bot.tree.add_command(ask_manolis)

    async def on_message_reply(message: discord.Message):
        """Continue a twin conversation when a user replies to a twin reply."""
        if message.author.bot:
            return
        if not message.reference or not message.reference.message_id:
            return
        if not message.content or not message.content.strip():
            return

        try:
            referenced = message.reference.resolved
            if not isinstance(referenced, discord.Message):
                referenced = await message.channel.fetch_message(
                    message.reference.message_id
                )
            # Twin replies are the bot's own messages carrying the footer.
            if (
                referenced.author.bot
                and referenced.author.id == bot.user.id
                and FOOTER_MARKER in referenced.content
            ):
                await _answer(
                    lambda text: message.reply(text, mention_author=False),
                    message.channel,
                    message.content.strip(),
                )
        except discord.HTTPException:
            logger.exception("Failed to follow a /manolis reply chain")

    bot.add_listener(on_message_reply, "on_message")
