"""Discord projections and persistent controls for the team subsystem.

This module deliberately treats Discord as a projection: database commits happen
in ``teams.service`` first, then these helpers create or repair messages/channels.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterable
from uuid import UUID

import discord
from discord.ext import commands

from config import TEAMS_CATEGORY_ID, TEAMS_DIRECTORY_CHANNEL_ID
from database import get_session
from slash_commands.closechannel import close_channel
from storage import get_value, set_value
from teams.service import (
    JoinRequestDetails,
    TeamDetails,
    TeamServiceError,
    cancel_close_attempt,
    cancel_close_attempts_by_message_ids,
    cast_close_vote,
    create_join_request,
    finish_team_close,
    get_join_request_details,
    get_open_close_attempts,
    get_pending_join_requests,
    get_team,
    get_team_details,
    get_user_by_discord,
    join_team_as_leadership,
    list_active_teams,
    mark_team_orphaned,
    resolve_join_request,
    set_close_vote_message_id,
    set_info_message_id,
    set_join_request_message_id,
)

LOGGER = logging.getLogger(__name__)
DIRECTORY_NAMESPACE = "teams"
DIRECTORY_KEY = "directory"
MAX_DIRECTORY_REACTIONS = 20
ALL_TEAMS_ROLE_NAME = "AllTeams"
ALL_TEAMS_ALLOWED_PERMISSIONS = (
    "view_channel",
    "read_message_history",
    "send_messages",
    "send_messages_in_threads",
    "add_reactions",
    "embed_links",
    "attach_files",
    "create_public_threads",
    "create_private_threads",
    "use_application_commands",
)
TEAM_MEMBER_ALLOWED_PERMISSIONS = ALL_TEAMS_ALLOWED_PERMISSIONS
BOT_CHANNEL_ALLOWED_PERMISSIONS = (
    *TEAM_MEMBER_ALLOWED_PERMISSIONS,
    "manage_messages",
    "manage_threads",
)
RANK_NAMES = {1: "Lead", 2: "Co-Lead", 3: "Engineer", 4: "Developer"}
REACTION_EMOJIS = (
    "🚀",
    "🛰️",
    "🌎",
    "🌙",
    "⭐",
    "🌟",
    "💫",
    "⚡",
    "🔥",
    "🌈",
    "❄️",
    "🌊",
    "🍀",
    "🌻",
    "🌵",
    "🍎",
    "🍊",
    "🍋",
    "🥝",
    "🍇",
    "🍓",
    "🫐",
    "🥥",
    "🥑",
    "🥨",
    "🧀",
    "🎯",
    "🎨",
    "🎭",
    "🎪",
    "🎲",
    "♟️",
    "🎸",
    "🎺",
    "🥁",
    "💎",
    "🔮",
    "🧭",
    "⚙️",
    "🧪",
    "🧬",
    "🔭",
    "💡",
    "📚",
    "🗺️",
    "🛠️",
    "🛡️",
    "🐝",
    "🦊",
    "🐙",
    "🦋",
    "🐢",
    "🦉",
    "🐬",
    "🐳",
)


def _directory_state() -> dict:
    """Load TOC/detail message IDs and the authoritative emoji mapping."""

    with get_session() as session:
        record = get_value(session, DIRECTORY_NAMESPACE, DIRECTORY_KEY)
        return dict(record.value) if record is not None else {}


def _save_directory_state(value: dict) -> None:
    with get_session() as session:
        set_value(session, DIRECTORY_NAMESPACE, DIRECTORY_KEY, value)


def channel_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return (slug or "team")[:90]


def _find_category(guild: discord.Guild) -> discord.CategoryChannel | None:
    configured = guild.get_channel(TEAMS_CATEGORY_ID) if TEAMS_CATEGORY_ID else None
    if isinstance(configured, discord.CategoryChannel):
        return configured
    return discord.utils.find(
        lambda category: category.name.casefold() == "teams", guild.categories
    )


def directory_channel(guild: discord.Guild) -> discord.TextChannel | None:
    state = _directory_state()
    configured_id = TEAMS_DIRECTORY_CHANNEL_ID or state.get("channel_id")
    configured = guild.get_channel(int(configured_id)) if configured_id else None
    if isinstance(configured, discord.TextChannel):
        return configured
    return discord.utils.find(
        lambda channel: channel.name.casefold() == "teams", guild.text_channels
    )


async def create_team_channel(guild: discord.Guild, name: str) -> discord.TextChannel:
    category = _find_category(guild)
    if category is None:
        category = await guild.create_category("Teams", reason="Mantis team management")
    # Supply privacy overwrites at creation time. Creating a category-synced
    # public channel and locking it down afterward creates a real visibility
    # window in which @everyone can read team traffic.
    return await guild.create_text_channel(
        channel_slug(name),
        category=category,
        overwrites=_base_team_channel_overwrites(guild),
        reason=f"Creating Mantis team {name}",
    )


def _set_permissions(
    overwrite: discord.PermissionOverwrite,
    permissions: tuple[str, ...],
    value: bool,
) -> bool:
    """Set an explicit permission policy and report whether it changed."""

    changed = False
    for permission in permissions:
        if getattr(overwrite, permission) is not value:
            setattr(overwrite, permission, value)
            changed = True
    return changed


def _allow_all_teams_permissions(
    overwrite: discord.PermissionOverwrite,
) -> bool:
    """Apply the AllTeams read/write grants and report whether anything changed."""

    return _set_permissions(overwrite, ALL_TEAMS_ALLOWED_PERMISSIONS, True)


def _allow_team_member_permissions(
    overwrite: discord.PermissionOverwrite,
) -> bool:
    """Grant one linked team member normal read/write channel access."""

    return _set_permissions(overwrite, TEAM_MEMBER_ALLOWED_PERMISSIONS, True)


def _deny_everyone_permissions(overwrite: discord.PermissionOverwrite) -> bool:
    """Make the channel private regardless of its category defaults."""

    return _set_permissions(overwrite, ("view_channel",), False)


def _allow_bot_permissions(overwrite: discord.PermissionOverwrite) -> bool:
    """Keep the managing bot able to repair messages, threads, and permissions."""

    return _set_permissions(overwrite, BOT_CHANNEL_ALLOWED_PERMISSIONS, True)


def _base_team_channel_overwrites(
    guild: discord.Guild,
) -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
    """Build private-by-default overwrites used during channel provisioning."""

    everyone = discord.PermissionOverwrite()
    _deny_everyone_permissions(everyone)
    overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {
        guild.default_role: everyone
    }

    role = discord.utils.get(guild.roles, name=ALL_TEAMS_ROLE_NAME)
    if role is not None:
        role_overwrite = discord.PermissionOverwrite()
        _allow_all_teams_permissions(role_overwrite)
        overwrites[role] = role_overwrite

    # @everyone is denied above, so the bot also needs an explicit operational
    # path even when its ordinary guild role does not grant Administrator.
    if guild.me is not None:
        bot_overwrite = discord.PermissionOverwrite()
        _allow_bot_permissions(bot_overwrite)
        overwrites[guild.me] = bot_overwrite
    return overwrites


async def _resolve_linked_team_members(
    guild: discord.Guild, details: TeamDetails
) -> tuple[dict[int, discord.Member], set[int]]:
    """Resolve linked Discord IDs and identify IDs safe to retain temporarily.

    A confirmed Discord ``NotFound`` means a former overwrite may be removed.
    Transient API failures retain an existing overwrite so a network problem
    cannot accidentally revoke a valid member's channel access.
    """

    resolved: dict[int, discord.Member] = {}
    retain_ids: set[int] = set()
    for team_member in details.members:
        if not team_member.discord_id:
            continue
        try:
            discord_id = int(team_member.discord_id)
        except (TypeError, ValueError):
            LOGGER.warning(
                "Team member %s has invalid Discord ID %r",
                team_member.uuid,
                team_member.discord_id,
            )
            continue

        member = guild.get_member(discord_id)
        if member is None:
            try:
                member = await guild.fetch_member(discord_id)
            except discord.NotFound:
                continue
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.exception(
                    "Could not resolve linked Discord member %s in guild %s",
                    discord_id,
                    guild.id,
                )
                retain_ids.add(discord_id)
                continue
        resolved[discord_id] = member
        retain_ids.add(discord_id)
    return resolved, retain_ids


async def reconcile_team_channel_permissions(
    channel: discord.TextChannel, details: TeamDetails
) -> bool:
    """Make channel access exactly match team membership and override policy.

    Role overwrites other than @everyone and AllTeams are preserved. Individual
    overwrites are bot-managed: linked team members are granted access, stale
    users are removed, and the managing bot keeps its operational overwrite.
    """

    guild = channel.guild
    resolved_members, retain_member_ids = await _resolve_linked_team_members(
        guild, details
    )
    overwrites = dict(channel.overwrites)
    changed = False

    everyone_overwrite = overwrites.get(
        guild.default_role, discord.PermissionOverwrite()
    )
    changed |= _deny_everyone_permissions(everyone_overwrite)
    overwrites[guild.default_role] = everyone_overwrite

    all_teams_role = discord.utils.get(guild.roles, name=ALL_TEAMS_ROLE_NAME)
    if all_teams_role is None:
        LOGGER.warning(
            "Guild %s has no %s role; cannot apply the team-channel override",
            guild.id,
            ALL_TEAMS_ROLE_NAME,
        )
    else:
        all_teams_overwrite = overwrites.get(
            all_teams_role, discord.PermissionOverwrite()
        )
        changed |= _allow_all_teams_permissions(all_teams_overwrite)
        overwrites[all_teams_role] = all_teams_overwrite

    bot_member = guild.me
    bot_member_id = bot_member.id if bot_member is not None else None
    if bot_member is not None:
        bot_overwrite = overwrites.get(bot_member, discord.PermissionOverwrite())
        changed |= _allow_bot_permissions(bot_overwrite)
        overwrites[bot_member] = bot_overwrite

    # Remove access immediately when a member leaves the team. Role overwrites
    # remain untouched so server-level moderation policy is not destroyed.
    for target in tuple(overwrites):
        if not isinstance(target, discord.Member):
            continue
        if target.id == bot_member_id or target.id in retain_member_ids:
            continue
        del overwrites[target]
        changed = True

    for member in resolved_members.values():
        member_overwrite = overwrites.get(member, discord.PermissionOverwrite())
        changed |= _allow_team_member_permissions(member_overwrite)
        overwrites[member] = member_overwrite

    if not changed:
        return True
    try:
        # One bulk edit avoids exposing a half-reconciled channel while several
        # individual set_permissions requests are still in flight.
        await channel.edit(
            overwrites=overwrites,
            reason=f"Reconciling Mantis team access for {details.team.name}",
        )
    except (discord.Forbidden, discord.HTTPException):
        LOGGER.exception("Could not reconcile permissions in channel %s", channel.id)
        return False
    return True


def _team_text(details: TeamDetails, *, directory: bool = False) -> str:
    team = details.team
    lines = [f"**{discord.utils.escape_markdown(team.name.upper())}**"]
    if team.description:
        lines.extend(
            ("", "**Description:**", discord.utils.escape_markdown(team.description))
        )
    lines.extend(("", "**Members:**"))
    if details.members:
        lines.extend(
            f"[{member.rank}] {discord.utils.escape_markdown(member.display_name)} — {RANK_NAMES[member.rank]}"
            for member in details.members
        )
    else:
        lines.append("No members")
    if not directory:
        lines.extend(("", "Use `/team` subcommands to manage this team."))
    return "\n".join(lines)


def _fit_description(text: str, limit: int = 4096) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: limit - 32].rstrip()}\n\n…display truncated"


async def _fetch_team_channel(
    bot: commands.Bot, guild: discord.Guild, channel_id: str | None
) -> discord.TextChannel | None:
    """Fetch past cache misses; only Discord NotFound proves a channel is gone."""

    if channel_id is None:
        return None
    channel = bot.get_channel(int(channel_id))
    if isinstance(channel, discord.TextChannel):
        return channel
    try:
        fetched = await guild.fetch_channel(int(channel_id))
    except discord.NotFound:
        return None
    except (discord.Forbidden, discord.HTTPException):
        LOGGER.exception("Could not check team channel %s", channel_id)
        raise
    return fetched if isinstance(fetched, discord.TextChannel) else None


async def refresh_team_info(
    bot: commands.Bot, guild: discord.Guild, team_uuid: UUID
) -> bool:
    """Upsert one team's pinned projection, preserving DB state on HTTP failure."""

    details = await asyncio.to_thread(get_team_details, team_uuid)
    try:
        channel = await _fetch_team_channel(bot, guild, details.team.discord_channel_id)
    except (discord.Forbidden, discord.HTTPException):
        return False
    if channel is None:
        await asyncio.to_thread(mark_team_orphaned, team_uuid)
        LOGGER.warning(
            "Marked team %s orphaned because Discord channel %s is missing",
            team_uuid,
            details.team.discord_channel_id,
        )
        return False

    # Every refresh repairs privacy, adds newly linked/current members, and
    # removes stale member overwrites after membership changes.
    await reconcile_team_channel_permissions(channel, details)

    embed = discord.Embed(
        description=_fit_description(_team_text(details)), color=discord.Color.blurple()
    )
    message = None
    if details.team.info_message_id:
        try:
            message = await channel.fetch_message(int(details.team.info_message_id))
        except discord.NotFound:
            message = None
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.exception("Could not fetch managed message for team %s", team_uuid)
            return True
    try:
        if message is None:
            message = await channel.send(embed=embed)
            await message.pin(reason="Managed Mantis team information")
            await asyncio.to_thread(set_info_message_id, team_uuid, message.id)
        else:
            await message.edit(content=None, embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        LOGGER.exception("Could not synchronize managed message for team %s", team_uuid)
    return True


def _pack_directory_pages(blocks: list[str], limit: int = 3900) -> list[str]:
    """Pack complete team blocks into embed-safe pages without losing content."""

    pages: list[str] = []
    current: list[str] = []
    current_length = 0
    for block in blocks:
        remaining = block
        pieces: list[str] = []
        while len(remaining) > limit:
            split_at = remaining.rfind("\n", 0, limit + 1)
            if split_at <= 0:
                split_at = limit
            pieces.append(remaining[:split_at])
            remaining = remaining[split_at:].lstrip("\n")
        if remaining:
            pieces.append(remaining)
        for piece in pieces:
            added_length = len(piece) + (2 if current else 0)
            if current and current_length + added_length > limit:
                pages.append("\n\n".join(current))
                current = []
                current_length = 0
            current.append(piece)
            current_length += len(piece) + (2 if len(current) > 1 else 0)
    if current:
        pages.append("\n\n".join(current))
    return pages


async def refresh_directory(bot: commands.Bot, guild: discord.Guild) -> None:
    """Reconcile one logical directory across a TOC and detail messages."""

    channel = directory_channel(guild)
    if channel is None:
        LOGGER.error("Could not find #teams; directory was not refreshed")
        return

    teams = list(await asyncio.to_thread(list_active_teams))
    valid = []
    for team in teams:
        try:
            team_channel = await _fetch_team_channel(
                bot, guild, team.discord_channel_id
            )
        except (discord.Forbidden, discord.HTTPException):
            # A permission/network failure is not evidence that the channel was deleted.
            valid.append(team)
            continue
        if team_channel is None:
            await asyncio.to_thread(mark_team_orphaned, team.uuid)
            LOGGER.warning(
                "Marked team %s orphaned because its channel is missing", team.uuid
            )
        else:
            valid.append(team)

    reaction_count = min(len(valid), MAX_DIRECTORY_REACTIONS)
    selected = valid[:reaction_count]
    # Deterministic emoji assignment: sort teams by name so the same team
    # always gets the same emoji across refreshes, avoiding user confusion.
    selected_sorted = sorted(selected, key=lambda t: t.name.casefold())
    emojis = REACTION_EMOJIS[:reaction_count]
    mapping = {emoji: str(team.uuid) for emoji, team in zip(emojis, selected_sorted)}
    toc_lines = [
        "# TEAMS",
        "React with a team's emoji to request to join.",
        "",
    ]
    toc_lines.extend(
        f"{index}. {emoji} {discord.utils.escape_markdown(team.name)}"
        for index, (team, emoji) in enumerate(zip(selected_sorted, emojis), start=1)
    )
    if not selected:
        toc_lines.append("No active teams.")
    toc_embed = discord.Embed(
        description=_fit_description("\n".join(toc_lines)),
        color=discord.Color.blurple(),
    )

    detail_blocks: list[str] = []
    for index, (team, emoji) in enumerate(zip(selected_sorted, emojis), 1):
        try:
            details = await asyncio.to_thread(get_team_details, team.uuid)
        except TeamServiceError:
            continue
        body = _team_text(details, directory=True).splitlines()
        body[0] = (
            f"**[{index}] {discord.utils.escape_markdown(team.name.upper())}** {emoji}"
        )
        detail_blocks.append("\n".join(body))
    detail_pages = _pack_directory_pages(detail_blocks)

    state = _directory_state()
    same_channel = str(state.get("channel_id")) == str(channel.id)
    old_toc_id = (
        state.get("toc_message_id") or state.get("message_id") if same_channel else None
    )
    old_detail_ids = state.get("detail_message_ids", []) if same_channel else []
    try:
        toc_message = None
        if old_toc_id:
            try:
                toc_message = await channel.fetch_message(int(old_toc_id))
            except discord.NotFound:
                toc_message = None
        if toc_message is None:
            toc_message = await channel.send(embed=toc_embed)
        else:
            await toc_message.edit(content=None, embed=toc_embed)
            await toc_message.clear_reactions()

        detail_messages: list[discord.Message] = []
        for page_index, page in enumerate(detail_pages):
            detail_message = None
            if page_index < len(old_detail_ids):
                try:
                    detail_message = await channel.fetch_message(
                        int(old_detail_ids[page_index])
                    )
                except discord.NotFound:
                    detail_message = None
            page_embed = discord.Embed(
                title=f"TEAMS — Details {page_index + 1}/{len(detail_pages)}",
                description=page,
                color=discord.Color.blurple(),
            )
            if detail_message is None:
                detail_message = await channel.send(embed=page_embed)
            else:
                await detail_message.edit(content=None, embed=page_embed)
            detail_messages.append(detail_message)

        for stale_id in old_detail_ids[len(detail_pages) :]:
            try:
                stale_message = await channel.fetch_message(int(stale_id))
                if bot.user is not None and stale_message.author.id == bot.user.id:
                    await stale_message.delete()
            except discord.NotFound:
                pass

        # Persist IDs and mapping before publishing reactions. A reaction event
        # never derives its target from mutable list ordering.
        directory_state = {
            "version": 2,
            "channel_id": str(channel.id),
            "toc_message_id": str(toc_message.id),
            "detail_message_ids": [str(message.id) for message in detail_messages],
            "mapping": mapping,
        }
        await asyncio.to_thread(_save_directory_state, directory_state)
        for emoji in emojis:
            await toc_message.add_reaction(emoji)
    except (discord.Forbidden, discord.HTTPException):
        LOGGER.exception("Could not synchronize the #teams directory")
        return


async def refresh_team_artifacts(
    bot: commands.Bot, guild: discord.Guild, team_uuid: UUID
) -> None:
    await refresh_team_info(bot, guild, team_uuid)
    await refresh_directory(bot, guild)


async def resync_all_team_artifacts(bot: commands.Bot) -> None:
    """Retry all managed Discord projections after a reconnect."""
    touched_guilds = {
        guild.id for guild in bot.guilds if directory_channel(guild) is not None
    }
    for team in await asyncio.to_thread(list_active_teams):
        channel = (
            bot.get_channel(int(team.discord_channel_id))
            if team.discord_channel_id is not None
            else None
        )
        if not isinstance(channel, discord.TextChannel):
            continue
        touched_guilds.add(channel.guild.id)
        await refresh_team_info(bot, channel.guild, team.uuid)
    for guild_id in touched_guilds:
        guild = bot.get_guild(guild_id)
        if guild is not None:
            await refresh_directory(bot, guild)


def _join_request_content(
    details: JoinRequestDetails, team_details: TeamDetails
) -> str:
    # Discord limits message content to 2000 characters. For large teams,
    # inline mentions alone could exceed this. Fall back to no inline mentions
    # and rely on allowed_mentions in the send call for notification.
    mention_str = " ".join(
        f"<@{member.discord_id}>"
        for member in team_details.members
        if member.discord_id
    )
    requester = details.member.full_name or details.member.email
    body = (
        f"**Team join request**\n"
        f"{discord.utils.escape_markdown(requester)} would like to join "
        f"**{discord.utils.escape_markdown(details.team.name)}** as a [4] Developer."
    )
    full_content = f"{mention_str}\n{body}"
    if len(full_content) > 2000:
        return body
    return full_content.strip()


async def post_join_request(
    bot: commands.Bot, guild: discord.Guild, details: JoinRequestDetails
) -> None:
    channel = await _fetch_team_channel(bot, guild, details.team.discord_channel_id)
    if channel is None:
        await asyncio.to_thread(mark_team_orphaned, details.team.uuid)
        raise TeamServiceError("That team's Discord channel no longer exists.")
    team_details = await asyncio.to_thread(get_team_details, details.team.uuid)
    message = await channel.send(
        _join_request_content(details, team_details),
        view=JoinRequestView(details.request.uuid),
        allowed_mentions=discord.AllowedMentions(
            users=True, roles=False, everyone=False
        ),
    )
    await asyncio.to_thread(
        set_join_request_message_id, details.request.uuid, message.id
    )


class JoinRequestView(discord.ui.View):
    def __init__(self, request_uuid: UUID):
        super().__init__(timeout=None)
        self.request_uuid = request_uuid
        approve = discord.ui.Button(
            label="Approve",
            style=discord.ButtonStyle.success,
            custom_id=f"team:join:approve:{request_uuid}",
        )
        reject = discord.ui.Button(
            label="Reject",
            style=discord.ButtonStyle.danger,
            custom_id=f"team:join:reject:{request_uuid}",
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
                resolve_join_request, self.request_uuid, interaction.user.id, approve
            )
        except TeamServiceError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return
        status = "APPROVED" if approve else "REJECTED"
        team_details = await asyncio.to_thread(get_team_details, details.team.uuid)
        if interaction.message is not None:
            try:
                await interaction.message.edit(
                    content=(
                        f"{_join_request_content(details, team_details)}\n\n"
                        f"**{status}** by {interaction.user.mention}"
                    ),
                    view=None,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.exception(
                    "Could not update resolved join request %s", self.request_uuid
                )
        await interaction.followup.send(
            f"Join request {status.casefold()}.", ephemeral=True
        )
        if approve and interaction.guild is not None:
            await refresh_team_artifacts(
                interaction.client, interaction.guild, details.team.uuid
            )


class CloseVoteView(discord.ui.View):
    """Persistent control keyed by attempt UUID, not by team UUID."""

    def __init__(self, close_attempt_uuid: UUID):
        super().__init__(timeout=None)
        self.close_attempt_uuid = close_attempt_uuid
        vote = discord.ui.Button(
            label="Vote to close",
            style=discord.ButtonStyle.danger,
            custom_id=f"team:close:vote:{close_attempt_uuid}",
        )
        vote.callback = self._vote
        self.add_item(vote)

    async def _vote(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This vote is only valid in a server.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await asyncio.to_thread(
                cast_close_vote, self.close_attempt_uuid, interaction.user.id
            )
        except TeamServiceError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return
        if not result.quorum:
            duplicate = (
                " Your earlier vote was already counted." if not result.accepted else ""
            )
            await interaction.followup.send(
                f"Vote recorded.{duplicate}", ephemeral=True
            )
            return

        team = await asyncio.to_thread(get_team, result.team_uuid)
        if team is None:
            await interaction.followup.send(
                "That team no longer exists.", ephemeral=True
            )
            return
        if interaction.message is not None:
            try:
                await interaction.message.edit(
                    content="**Close quorum reached. Archiving this channel…**",
                    view=None,
                )
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.exception(
                    "Could not update close vote message for %s",
                    self.close_attempt_uuid,
                )
        await refresh_directory(interaction.client, guild)
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await asyncio.to_thread(mark_team_orphaned, team.uuid)
            await interaction.followup.send(
                "The team channel is no longer available.", ephemeral=True
            )
            return
        close_result = await close_channel(
            channel, bot=interaction.client, closed_by=interaction.user
        )
        # Both /close-channel and this quorum path call the same archive service;
        # this view only translates the result into team lifecycle state.
        await asyncio.to_thread(
            finish_team_close,
            team.uuid,
            self.close_attempt_uuid,
            success=close_result.success,
        )
        await refresh_directory(interaction.client, guild)
        await interaction.followup.send(close_result.message, ephemeral=True)


async def restore_team_views(bot: commands.Bot) -> None:
    """Re-register persistent controls and reconcile missing managed messages."""

    views_already_registered = getattr(bot, "_team_views_restored", False)
    bot._team_views_restored = True
    for request in await asyncio.to_thread(get_pending_join_requests):
        try:
            details = await asyncio.to_thread(get_join_request_details, request.uuid)
            channel = (
                bot.get_channel(int(details.team.discord_channel_id))
                if details.team.discord_channel_id is not None
                else None
            )
            if not isinstance(channel, discord.TextChannel):
                continue
            if request.discord_message_id:
                try:
                    await channel.fetch_message(int(request.discord_message_id))
                except discord.NotFound:
                    await post_join_request(bot, channel.guild, details)
                else:
                    if not views_already_registered:
                        bot.add_view(
                            JoinRequestView(request.uuid),
                            message_id=int(request.discord_message_id),
                        )
            else:
                await post_join_request(bot, channel.guild, details)
        except (TeamServiceError, discord.Forbidden, discord.HTTPException):
            LOGGER.exception("Could not restore join request %s", request.uuid)
    for details in await asyncio.to_thread(get_open_close_attempts):
        attempt = details.attempt
        team = details.team
        channel = (
            bot.get_channel(int(team.discord_channel_id))
            if team.discord_channel_id is not None
            else None
        )
        if not isinstance(channel, discord.TextChannel):
            continue
        try:
            message = None
            if attempt.discord_message_id is not None:
                try:
                    message = await channel.fetch_message(
                        int(attempt.discord_message_id)
                    )
                except discord.NotFound:
                    # A deleted vote control cancels this round. Recreating it
                    # would make intentional moderator deletion ineffective.
                    await asyncio.to_thread(cancel_close_attempt, attempt.uuid)
                    LOGGER.info(
                        "Cancelled close attempt %s because message %s is missing",
                        attempt.uuid,
                        attempt.discord_message_id,
                    )
                    continue
            if message is None:
                message = await channel.send(
                    "**Close team vote**\nVote to archive and close this team. "
                    "Quorum is the Lead/Leadership, or a Co-Lead plus an Engineer.",
                    view=CloseVoteView(attempt.uuid),
                )
                await asyncio.to_thread(
                    set_close_vote_message_id, attempt.uuid, message.id
                )
            else:
                if not views_already_registered:
                    bot.add_view(CloseVoteView(attempt.uuid), message_id=message.id)
        except (TeamServiceError, discord.Forbidden, discord.HTTPException):
            LOGGER.exception("Could not restore close attempt %s", attempt.uuid)


async def on_team_messages_deleted(message_ids: Iterable[int | str]) -> int:
    """Cancel close attempts when Discord reports their controls deleted."""

    normalized_ids = tuple(str(message_id) for message_id in message_ids)
    cancelled = await asyncio.to_thread(
        cancel_close_attempts_by_message_ids, normalized_ids
    )
    if cancelled:
        LOGGER.info(
            "Cancelled %s close attempt(s) after Discord message deletion",
            cancelled,
        )
    return cancelled


async def on_directory_reaction(
    bot: commands.Bot, payload: discord.RawReactionActionEvent
) -> None:
    """Resolve reactions exclusively through the persisted emoji→team mapping."""

    if bot.user is None or payload.user_id == bot.user.id or payload.guild_id is None:
        return
    state = await asyncio.to_thread(_directory_state)
    toc_message_id = state.get("toc_message_id") or state.get("message_id")
    if str(payload.message_id) != str(toc_message_id):
        return
    team_uuid_value = state.get("mapping", {}).get(str(payload.emoji))
    if not team_uuid_value:
        return
    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return
    channel = guild.get_channel(payload.channel_id)
    if isinstance(channel, discord.TextChannel):
        try:
            message = await channel.fetch_message(payload.message_id)
            await message.remove_reaction(
                payload.emoji, discord.Object(id=payload.user_id)
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            LOGGER.exception("Could not remove a #teams directory reaction")
    try:
        team_uuid = UUID(team_uuid_value)
        actor = await asyncio.to_thread(get_user_by_discord, payload.user_id)
        if actor is not None and actor.is_leadership:
            # Leadership override is a direct self-join, not an auto-approved
            # request. The service re-checks the flag and duplicate membership
            # under the team lock before inserting rank 4.
            await asyncio.to_thread(join_team_as_leadership, team_uuid, payload.user_id)
            await refresh_team_artifacts(bot, guild, team_uuid)
            return
        details = await asyncio.to_thread(
            create_join_request, team_uuid, payload.user_id
        )
        await post_join_request(bot, guild, details)
    except TeamServiceError as error:
        member = guild.get_member(payload.user_id)
        if member is not None:
            try:
                await member.send(f"Your team join request was not created: {error}")
            except (discord.Forbidden, discord.HTTPException):
                pass
    except (discord.Forbidden, discord.HTTPException):
        LOGGER.exception("Could not post a team join request")
