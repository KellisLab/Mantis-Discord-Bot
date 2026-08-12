"""Archive and lock a Discord text channel with ``/close-channel``."""

from __future__ import annotations

import asyncio
import html
import io
import logging
import re
import tempfile
import zipfile
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import PurePath
from typing import BinaryIO

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageOps, UnidentifiedImageError

LOGGER = logging.getLogger(__name__)

# Channel deletion is intentionally disabled. The channel remains locked after a
# successful archive upload. Set this to True only when deletion is ready to ship.
DELETE_CHANNEL_AFTER_ARCHIVE = False

MAX_IMAGE_DIMENSION = 1600
JPEG_QUALITY = 78
SPOOL_TO_DISK_AFTER_BYTES = 8 * 1024 * 1024
IMAGE_EXTENSIONS = {
    ".avif",
    ".bmp",
    ".gif",
    ".heic",
    ".heif",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".tif",
    ".tiff",
    ".webp",
}


class ArchiveTooLargeError(Exception):
    """Raised when Discord will not accept the generated archive."""


def setup(bot: commands.Bot) -> None:
    bot.tree.add_command(close_channel)


def _safe_filename(value: str, fallback: str = "channel") -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return value[:80] or fallback


def _is_image_attachment(attachment: discord.Attachment) -> bool:
    content_type = (attachment.content_type or "").lower()
    extension = PurePath(attachment.filename).suffix.lower()
    return content_type.startswith("image/") or extension in IMAGE_EXTENSIONS


def _original_image_extension(filename: str) -> str:
    extension = PurePath(filename).suffix.lower()
    return extension if extension in IMAGE_EXTENSIONS else ".img"


def _compress_image(data: bytes, filename: str) -> tuple[bytes, str]:
    """Moderately resize/compress an image, retaining unsupported formats."""
    try:
        with Image.open(io.BytesIO(data)) as source:
            # Keep animated files intact; flattening them would discard content.
            if getattr(source, "is_animated", False):
                return data, _original_image_extension(filename)

            image = ImageOps.exif_transpose(source)
            if not image:
                raise ValueError("Failed to load image")

            image.thumbnail(
                (MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION),
                Image.Resampling.LANCZOS,
            )

            output = io.BytesIO()
            has_alpha = image.mode in ("RGBA", "LA") or (
                image.mode == "P" and "transparency" in image.info
            )
            if has_alpha:
                image.save(output, format="PNG", optimize=True, compress_level=7)
                extension = ".png"
            else:
                image.convert("RGB").save(
                    output,
                    format="JPEG",
                    quality=JPEG_QUALITY,
                    optimize=True,
                    progressive=True,
                )
                extension = ".jpg"

            return output.getvalue(), extension
    except (OSError, ValueError, UnidentifiedImageError):
        # SVG, HEIC without codec support, and other valid Discord images may not
        # be readable by Pillow. Preserve those attachments byte-for-byte.
        return data, _original_image_extension(filename)


def _author_name(message: discord.Message) -> str:
    return str(getattr(message.author, "display_name", message.author))


def _message_timestamp(message: discord.Message) -> datetime:
    timestamp = message.created_at
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _reply_html(
    message: discord.Message,
    messages_by_id: dict[int, discord.Message],
) -> str:
    reference = message.reference
    if reference is None or reference.message_id is None:
        return ""

    target = messages_by_id.get(reference.message_id)
    if target is None:
        label = f"message {reference.message_id} (unavailable)"
    else:
        excerpt = " ".join(target.content.split())
        if len(excerpt) > 140:
            excerpt = f"{excerpt[:137]}..."
        label = f"{_author_name(target)}: {excerpt or '[no text]'}"

    return (
        '<a class="reply" href="#message-'
        f'{reference.message_id}">Reply to {html.escape(label)}</a>'
    )


def _attachment_html(
    message: discord.Message,
    archived_images: dict[int, str],
) -> str:
    items: list[str] = []
    for attachment in message.attachments:
        filename = html.escape(attachment.filename)
        local_path = archived_images.get(attachment.id)
        if local_path is not None:
            escaped_path = html.escape(local_path, quote=True)
            items.append(
                '<li class="image-attachment">'
                f'<a href="{escaped_path}"><img src="{escaped_path}" '
                f'alt="{filename}" loading="lazy"></a>'
                f"<span>{filename}</span></li>"
            )
        else:
            url = html.escape(attachment.url, quote=True)
            items.append(f'<li><a href="{url}">{filename}</a></li>')

    if not items:
        return ""
    return f'<ul class="attachments">{"".join(items)}</ul>'


def render_transcript_html(
    channel: discord.TextChannel,
    messages: Iterable[discord.Message],
    archived_images: dict[int, str],
    closed_by: discord.abc.User,
    closed_at: datetime,
) -> str:
    """Render a standalone, escaped HTML transcript."""
    message_list = list(messages)
    messages_by_id = {message.id: message for message in message_list}
    channel_name = html.escape(channel.name)
    closer_name = html.escape(str(closed_by))

    rendered_messages: list[str] = []
    for message in message_list:
        author = html.escape(_author_name(message))
        author_id = html.escape(str(message.author.id))
        timestamp = _message_timestamp(message)
        timestamp_label = timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
        content = html.escape(message.content)
        reply = _reply_html(message, messages_by_id)
        attachments = _attachment_html(message, archived_images)
        rendered_messages.append(
            f'<article class="message" id="message-{message.id}">'
            "<header>"
            f'<strong>{author}</strong> <span class="author-id">({author_id})</span>'
            f'<time datetime="{timestamp.isoformat()}">{timestamp_label}</time>'
            "</header>"
            f"{reply}"
            f'<div class="content">{content}</div>'
            f"{attachments}"
            "</article>"
        )

    closed_at_utc = closed_at.astimezone(timezone.utc)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Archive of #{channel_name}</title>
  <style>
    :root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
    body {{ max-width: 960px; margin: 2rem auto; padding: 0 1rem; line-height: 1.45; }}
    .summary {{ border-bottom: 1px solid #8886; padding-bottom: 1rem; }}
    .message {{ border-bottom: 1px solid #8884; padding: .9rem 0; }}
    header {{ display: flex; align-items: baseline; gap: .4rem; flex-wrap: wrap; }}
    time {{ color: #777; font-size: .85rem; margin-left: auto; }}
    .author-id {{ color: #777; font-size: .8rem; }}
    .reply {{ display: block; border-left: 3px solid #5865f2; margin: .45rem 0;
              padding-left: .6rem; color: inherit; opacity: .8; text-decoration: none; }}
    .content {{ margin-top: .35rem; white-space: pre-wrap; overflow-wrap: anywhere; }}
    .attachments {{ list-style: none; padding: 0; margin-bottom: 0; }}
    .image-attachment {{ display: inline-flex; flex-direction: column; margin: .5rem .7rem .2rem 0; }}
    .image-attachment img {{ max-width: min(480px, 85vw); max-height: 360px; border-radius: 6px; }}
    .image-attachment span {{ font-size: .8rem; margin-top: .2rem; }}
  </style>
</head>
<body>
  <section class="summary">
    <h1>#{channel_name}</h1>
    <div>Closed by {closer_name}</div>
    <div>Closed at {closed_at_utc.strftime("%Y-%m-%d %H:%M:%S UTC")}</div>
    <div>{len(message_list)} messages</div>
  </section>
  <main>{"".join(rendered_messages)}</main>
</body>
</html>
"""


async def build_archive(
    channel: discord.TextChannel,
    messages: list[discord.Message],
    closed_by: discord.abc.User,
    closed_at: datetime,
) -> BinaryIO:
    """Build the transcript ZIP in a spooled temporary file."""
    archive: BinaryIO = tempfile.SpooledTemporaryFile(
        max_size=SPOOL_TO_DISK_AFTER_BYTES,
        mode="w+b",
    )
    archived_images: dict[int, str] = {}

    try:
        with zipfile.ZipFile(
            archive,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as zip_file:
            for message in messages:
                for attachment in message.attachments:
                    if not _is_image_attachment(attachment):
                        continue

                    image_data = await attachment.read(use_cached=True)
                    compressed_data, extension = await asyncio.to_thread(
                        _compress_image,
                        image_data,
                        attachment.filename,
                    )
                    archive_path = f"images/{message.id}-{attachment.id}{extension}"
                    zip_file.writestr(archive_path, compressed_data)
                    archived_images[attachment.id] = archive_path

            transcript = render_transcript_html(
                channel=channel,
                messages=messages,
                archived_images=archived_images,
                closed_by=closed_by,
                closed_at=closed_at,
            )
            zip_file.writestr("transcript.html", transcript.encode("utf-8"))

        archive.seek(0)
        return archive
    except Exception:
        archive.close()
        raise


def _general_channel(guild: discord.Guild) -> discord.TextChannel | None:
    return discord.utils.find(
        lambda channel: channel.name.casefold() == "general",
        guild.text_channels,
    )


def _bot_member(guild: discord.Guild, bot: commands.Bot) -> discord.Member | None:
    if guild.me is not None:
        return guild.me
    if bot.user is None:
        return None
    return guild.get_member(bot.user.id)


async def _send_failure(interaction: discord.Interaction, message: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


@app_commands.command(
    name="close-channel",
    description="Lock this channel, archive it, and post the archive in #general.",
)
@app_commands.guild_only()
@app_commands.default_permissions(manage_channels=True)
@app_commands.checks.has_permissions(manage_channels=True)
async def close_channel(interaction: discord.Interaction) -> None:
    """Lock and archive the current text channel, without deleting it."""
    await interaction.response.defer(ephemeral=True, thinking=True)

    guild = interaction.guild
    channel = interaction.channel
    if guild is None or not isinstance(channel, discord.TextChannel):
        await interaction.followup.send(
            "This command can only be used in a server text channel.",
            ephemeral=True,
        )
        return

    general = _general_channel(guild)
    if general is None:
        await interaction.followup.send(
            "I could not find a text channel named #general, so nothing was locked.",
            ephemeral=True,
        )
        return
    if general.id == channel.id:
        await interaction.followup.send(
            "#general is the archive destination and cannot archive itself.",
            ephemeral=True,
        )
        return

    bot = interaction.client
    bot_member = _bot_member(guild, bot)
    if bot_member is None:
        await interaction.followup.send(
            "I could not verify my server permissions, so nothing was locked.",
            ephemeral=True,
        )
        return

    source_permissions = channel.permissions_for(bot_member)
    destination_permissions = general.permissions_for(bot_member)
    missing_permissions: list[str] = []
    if not source_permissions.manage_channels:
        missing_permissions.append("Manage Channels in this channel")
    if not source_permissions.read_message_history:
        missing_permissions.append("Read Message History in this channel")
    if not destination_permissions.send_messages:
        missing_permissions.append("Send Messages in #general")
    if not destination_permissions.attach_files:
        missing_permissions.append("Attach Files in #general")
    if missing_permissions:
        formatted = ", ".join(missing_permissions)
        await interaction.followup.send(
            f"I am missing required permissions: {formatted}. Nothing was locked.",
            ephemeral=True,
        )
        return

    everyone = guild.default_role
    had_everyone_overwrite = everyone in channel.overwrites
    previous_everyone_overwrite = channel.overwrites_for(everyone)
    locked_overwrite = discord.PermissionOverwrite.from_pair(
        *previous_everyone_overwrite.pair()
    )
    locked_overwrite.send_messages = False
    locked_overwrite.send_messages_in_threads = False
    locked_overwrite.create_public_threads = False
    locked_overwrite.create_private_threads = False

    locked = False
    archive_uploaded = False
    archive: BinaryIO | None = None

    try:
        await channel.set_permissions(
            everyone,
            overwrite=locked_overwrite,
            reason=f"Channel archive started by {interaction.user} ({interaction.user.id})",
        )
        locked = True

        messages = [
            message async for message in channel.history(limit=None, oldest_first=True)
        ]
        closed_at = discord.utils.utcnow()
        archive = await build_archive(
            channel=channel,
            messages=messages,
            closed_by=interaction.user,
            closed_at=closed_at,
        )

        archive.seek(0, io.SEEK_END)
        archive_size = archive.tell()
        archive.seek(0)
        upload_limit = guild.filesize_limit
        if archive_size > upload_limit:
            raise ArchiveTooLargeError(
                f"The archive is {archive_size / 1024 / 1024:.1f} MB, but this "
                f"server's upload limit is {upload_limit / 1024 / 1024:.1f} MB."
            )

        archive_name = (
            f"{_safe_filename(channel.name)}-archive-"
            f"{closed_at.strftime('%Y-%m-%d')}.zip"
        )
        closed_date = closed_at.astimezone(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
        announcement = "\n".join(
            (
                "**CHANNEL ARCHIVED**",
                f"Channel name: #{channel.name}",
                f"Closed by: {interaction.user}",
                f"Closed date: {closed_date}",
                f"Message count: {len(messages)}",
            )
        )
        await general.send(
            announcement,
            file=discord.File(archive, filename=archive_name),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        archive_uploaded = True

        if DELETE_CHANNEL_AFTER_ARCHIVE:
            await channel.delete(
                reason=f"Archive uploaded by {interaction.user} ({interaction.user.id})"
            )

    except ArchiveTooLargeError as error:
        LOGGER.warning("Could not archive #%s: %s", channel.name, error)
        await interaction.followup.send(
            f"Archive upload failed: {error} The channel lock was restored.",
            ephemeral=True,
        )
    except (discord.Forbidden, discord.HTTPException):
        LOGGER.exception("Discord rejected the archive operation for #%s", channel.name)
        await interaction.followup.send(
            "Discord rejected part of the archive operation. The channel was not "
            "deleted, and its lock was restored if the upload did not complete.",
            ephemeral=True,
        )
    except Exception:
        LOGGER.exception("Unexpected failure while archiving #%s", channel.name)
        await interaction.followup.send(
            "The archive could not be completed. The channel was not deleted, and "
            "its lock was restored.",
            ephemeral=True,
        )
    finally:
        if locked and not archive_uploaded:
            try:
                await channel.set_permissions(
                    everyone,
                    overwrite=(
                        previous_everyone_overwrite if had_everyone_overwrite else None
                    ),
                    reason="Restoring permissions after failed channel archive",
                )
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.exception(
                    "Could not restore permissions after archiving #%s failed",
                    channel.name,
                )
        if archive is not None and not archive.closed:
            archive.close()

    if archive_uploaded:
        status = "The channel remains locked; automatic deletion is currently disabled."
        if DELETE_CHANNEL_AFTER_ARCHIVE:
            status = "The archive was posted and the channel was deleted."
        await interaction.followup.send(
            f"Archive uploaded to {general.mention}. {status}",
            ephemeral=True,
        )


@close_channel.error
async def close_channel_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> None:
    if isinstance(error, app_commands.MissingPermissions):
        await _send_failure(
            interaction,
            "You need the Manage Channels permission to use `/close-channel`.",
        )
        return
    LOGGER.exception("Unhandled /close-channel command error", exc_info=error)
    await _send_failure(interaction, "`/close-channel` could not be started.")
