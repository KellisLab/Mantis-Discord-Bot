"""Discord projections and persistent controls for member role requests."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

import discord
from discord.ext import commands

from config import LEADERSHIP_CHANNEL_ID
from members.role_models import RoleRequest, RoleRequestType
from members.role_service import (
    RoleRequestDetails,
    RoleRequestServiceError,
    discard_role_requests_by_message_ids,
    discard_unposted_role_request,
    get_pending_role_requests,
    resolve_role_request,
    set_role_request_message_id,
)

LOGGER = logging.getLogger(__name__)

REQUEST_TYPE_LABELS = {
    RoleRequestType.STAGE: "Stage Advancement",
    RoleRequestType.JOURNEY_MENTOR: "Journey Mentor",
    RoleRequestType.LEADERSHIP: "Leadership",
}


def _requested_value(request: RoleRequest) -> str:
    if request.request_type is RoleRequestType.STAGE:
        stage = request.requested_stage
        return stage.value.replace("_", " ").title() if stage else "Unknown"
    return REQUEST_TYPE_LABELS[request.request_type]


def _current_value(request: RoleRequest, requester) -> str:
    if request.request_type is RoleRequestType.STAGE:
        return requester.stage.value.replace("_", " ").title()
    if request.request_type is RoleRequestType.JOURNEY_MENTOR:
        return "Yes" if requester.is_journey_mentor else "No"
    if request.request_type is RoleRequestType.LEADERSHIP:
        return "Yes" if requester.is_leadership else "No"
    return "Unknown"


def build_role_request_embed(
    details: RoleRequestDetails,
    *,
    resolved_by: discord.abc.User | None = None,
) -> discord.Embed:
    request = details.request
    requester = details.requester
    label = REQUEST_TYPE_LABELS[request.request_type]

    embed = discord.Embed(
        title=f"Role Request — {label}",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Requester",
        value=requester.full_name or requester.email,
        inline=True,
    )
    embed.add_field(
        name="Current",
        value=_current_value(request, requester),
        inline=True,
    )
    embed.add_field(
        name="Requested",
        value=_requested_value(request),
        inline=True,
    )
    embed.add_field(
        name="Justification",
        value=request.justification or "None provided",
        inline=False,
    )
    if request.evidence_urls:
        embed.add_field(
            name="Evidence",
            value="\n".join(request.evidence_urls),
            inline=False,
        )
    embed.add_field(name="Request ID", value=str(request.uuid), inline=False)

    if request.status.value != "pending":
        status_text = request.status.value.upper()
        if resolved_by is not None:
            status_text = f"{status_text} by {resolved_by.mention}"
        embed.add_field(name="Status", value=status_text, inline=False)

    return embed


async def _leadership_channel(bot: commands.Bot) -> discord.TextChannel | None:
    channel = bot.get_channel(LEADERSHIP_CHANNEL_ID)
    if isinstance(channel, discord.TextChannel):
        return channel
    try:
        fetched = await bot.fetch_channel(LEADERSHIP_CHANNEL_ID)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        LOGGER.exception("Could not fetch the #leadership channel")
        return None
    return fetched if isinstance(fetched, discord.TextChannel) else None


async def post_role_request(bot: commands.Bot, details: RoleRequestDetails) -> None:
    """Post a new role request to #leadership with a persistent review view."""

    channel = await _leadership_channel(bot)
    if channel is None:
        raise RoleRequestServiceError(
            "The #leadership channel could not be found to post this request."
        )

    guild = channel.guild
    leadership_role = discord.utils.get(guild.roles, name="M • Leadership")
    content = leadership_role.mention if leadership_role is not None else None

    message = await channel.send(
        content=content,
        embed=build_role_request_embed(details),
        view=RoleRequestView(details.request.uuid),
        allowed_mentions=discord.AllowedMentions(
            roles=True, users=False, everyone=False
        ),
    )
    await asyncio.to_thread(
        set_role_request_message_id, details.request.uuid, message.id
    )


class RoleRequestView(discord.ui.View):
    def __init__(self, request_uuid: UUID):
        super().__init__(timeout=None)
        self.request_uuid = request_uuid
        approve = discord.ui.Button(
            label="Approve",
            style=discord.ButtonStyle.success,
            custom_id=f"role_request:approve:{request_uuid}",
        )
        reject = discord.ui.Button(
            label="Reject",
            style=discord.ButtonStyle.danger,
            custom_id=f"role_request:reject:{request_uuid}",
        )
        approve.callback = self._approve
        reject.callback = self._reject
        self.add_item(approve)
        self.add_item(reject)

    async def _approve(self, interaction: discord.Interaction) -> None:
        await self._resolve(interaction, True)

    async def _reject(self, interaction: discord.Interaction) -> None:
        await self._resolve(interaction, False)

    async def _resolve(self, interaction: discord.Interaction, approve: bool) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            details = await asyncio.to_thread(
                resolve_role_request, self.request_uuid, interaction.user.id, approve
            )
        except RoleRequestServiceError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return

        if interaction.message is not None:
            try:
                await interaction.message.edit(
                    embed=build_role_request_embed(
                        details, resolved_by=interaction.user
                    ),
                    view=None,
                )
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.exception(
                    "Could not update resolved role request %s", self.request_uuid
                )

        status = "approved" if approve else "rejected"
        await interaction.followup.send(f"Role request {status}.", ephemeral=True)

        if approve and details.requester.discord_id is not None:
            role_sync = getattr(interaction.client, "user_role_sync", None)
            if role_sync is not None:
                role_sync.enqueue(details.requester.discord_id)


async def restore_role_request_views(bot: commands.Bot) -> None:
    """Re-register persistent controls and reconcile missing managed messages."""

    views_already_registered = getattr(bot, "_role_request_views_restored", False)
    bot._role_request_views_restored = True

    for request in await asyncio.to_thread(get_pending_role_requests):
        try:
            if request.discord_message_id is None:
                await asyncio.to_thread(discard_unposted_role_request, request.uuid)
                LOGGER.info(
                    "Discarded pending role request %s without a Discord message ID",
                    request.uuid,
                )
                continue

            channel = await _leadership_channel(bot)
            if channel is None:
                continue
            try:
                await channel.fetch_message(int(request.discord_message_id))
            except discord.NotFound:
                await asyncio.to_thread(
                    discard_role_requests_by_message_ids,
                    (request.discord_message_id,),
                )
                LOGGER.info(
                    "Discarded pending role request %s because message %s is missing",
                    request.uuid,
                    request.discord_message_id,
                )
            else:
                if not views_already_registered:
                    bot.add_view(
                        RoleRequestView(request.uuid),
                        message_id=int(request.discord_message_id),
                    )
        except (RoleRequestServiceError, discord.Forbidden, discord.HTTPException):
            LOGGER.exception("Could not restore role request %s", request.uuid)


async def on_role_request_messages_deleted(message_ids: tuple[int | str, ...]) -> int:
    """Discard pending role requests when their #leadership message is deleted."""

    normalized_ids = tuple(str(message_id) for message_id in message_ids)
    discarded = await asyncio.to_thread(
        discard_role_requests_by_message_ids, normalized_ids
    )
    if discarded:
        LOGGER.info(
            "Discarded %s role request(s) after Discord message deletion", discarded
        )
    return discarded
