"""Discord projections and persistent controls for the team subsystem.

This module deliberately treats Discord as a projection: database commits happen
in ``teams.service`` first, then these helpers create or repair messages/channels.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

import discord
from discord.ext import commands

from config import TEAMS_CATEGORY_ID, TEAMS_DIRECTORY_CHANNEL_ID
from database import get_session
from members.permissions import has_leadership
from slash_commands.closechannel import close_channel
from storage import get_value, set_value
from teams.service import (
    RANK_NAMES,
    JoinRequestDetails,
    TeamDetails,
    TeamServiceError,
    cancel_close_attempt,
    cancel_close_attempts_by_message_ids,
    cast_close_vote,
    create_join_request,
    discard_join_requests_by_message_ids,
    discard_unposted_join_request,
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
    set_team_role_id,
)

LOGGER = logging.getLogger(__name__)
DIRECTORY_NAMESPACE = "teams"
DIRECTORY_KEY = "directory"
MAX_DIRECTORY_BUTTONS = 25
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
TEAM_ROLE_PREFIX = "M • Team • "
DISCORD_ROLE_NAME_LIMIT = 100


@dataclass(frozen=True)
class DirectoryButtonTeam:
    index: int
    uuid: str
    name: str


def _directory_state() -> dict:
    """Load TOC/detail message IDs and directory button metadata."""

    with get_session() as session:
        record = get_value(session, DIRECTORY_NAMESPACE, DIRECTORY_KEY)
        return dict(record.value) if record is not None else {}


def _save_directory_state(value: dict) -> None:
    with get_session() as session:
        set_value(session, DIRECTORY_NAMESPACE, DIRECTORY_KEY, value)


def channel_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return (slug or "team")[:90]


def team_role_name(name: str) -> str:
    normalized = re.sub(r"\s+", " ", name).strip() or "Team"
    return f"{TEAM_ROLE_PREFIX}{normalized}"[:DISCORD_ROLE_NAME_LIMIT]


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


async def create_team_role(guild: discord.Guild, name: str) -> discord.Role:
    return await guild.create_role(
        name=team_role_name(name),
        mentionable=True,
        reason=f"Creating Mantis team role for {name}",
    )


async def create_team_channel(
    guild: discord.Guild, name: str, team_role: discord.Role | None = None
) -> discord.TextChannel:
    category = _find_category(guild)
    if category is None:
        category = await guild.create_category("Teams", reason="Mantis team management")
    # Supply privacy overwrites at creation time. Creating a category-synced
    # public channel and locking it down afterward creates a real visibility
    # window in which @everyone can read team traffic.
    return await guild.create_text_channel(
        channel_slug(name),
        category=category,
        overwrites=_base_team_channel_overwrites(guild, team_role),
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
    """Grant the managed team role normal read/write channel access."""

    return _set_permissions(overwrite, TEAM_MEMBER_ALLOWED_PERMISSIONS, True)


def _deny_everyone_permissions(overwrite: discord.PermissionOverwrite) -> bool:
    """Make the channel private regardless of its category defaults."""

    return _set_permissions(overwrite, ("view_channel",), False)


def _allow_bot_permissions(overwrite: discord.PermissionOverwrite) -> bool:
    """Keep the managing bot able to repair messages, threads, and permissions."""

    return _set_permissions(overwrite, BOT_CHANNEL_ALLOWED_PERMISSIONS, True)


def _base_team_channel_overwrites(
    guild: discord.Guild, team_role: discord.Role | None = None
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

    if team_role is not None:
        team_role_overwrite = discord.PermissionOverwrite()
        _allow_team_member_permissions(team_role_overwrite)
        overwrites[team_role] = team_role_overwrite

    # @everyone is denied above, so the bot also needs an explicit operational
    # path even when its ordinary guild role does not grant Administrator.
    if guild.me is not None:
        bot_overwrite = discord.PermissionOverwrite()
        _allow_bot_permissions(bot_overwrite)
        overwrites[guild.me] = bot_overwrite
    return overwrites


def _get_role_by_id(
    guild: discord.Guild, role_id: str | int | None
) -> discord.Role | None:
    if role_id is None:
        return None
    try:
        return guild.get_role(int(role_id))
    except (TypeError, ValueError):
        LOGGER.warning("Team has invalid Discord role ID %r", role_id)
        return None


async def ensure_team_role(guild: discord.Guild, details: TeamDetails) -> discord.Role:
    """Find, repair, or create the Discord role for one active team."""

    expected_name = team_role_name(details.team.name)
    role = _get_role_by_id(guild, details.team.discord_role_id)
    if role is None:
        role = discord.utils.get(guild.roles, name=expected_name)
    if role is None:
        role = await create_team_role(guild, details.team.name)

    if role.name != expected_name or getattr(role, "mentionable", False) is not True:
        edited = await role.edit(
            name=expected_name,
            mentionable=True,
            reason=f"Reconciling Mantis team role for {details.team.name}",
        )
        if edited is not None:
            role = edited

    if str(role.id) != str(details.team.discord_role_id):
        await asyncio.to_thread(set_team_role_id, details.team.uuid, role.id)
        details.team.discord_role_id = str(role.id)
    return role


async def delete_team_role(guild: discord.Guild, team) -> None:
    role = _get_role_by_id(guild, getattr(team, "discord_role_id", None))
    if role is None:
        role = discord.utils.get(guild.roles, name=team_role_name(team.name))
    if role is None:
        return
    try:
        await role.delete(reason=f"Deleting Mantis team role for {team.name}")
    except (discord.Forbidden, discord.HTTPException):
        LOGGER.exception("Could not delete Discord role for team %s", team.uuid)


async def reconcile_team_role_members(
    guild: discord.Guild, role: discord.Role, details: TeamDetails
) -> bool:
    """Make managed Discord role membership match database team membership."""

    resolved_members, retain_member_ids = await _resolve_linked_team_members(
        guild, details
    )
    changed = False
    guild_members = getattr(guild, "members", ())
    current_holders = {
        member
        for member in tuple(getattr(role, "members", ())) + tuple(guild_members)
        if role in getattr(member, "roles", ())
    }

    for member in current_holders:
        if member.id in retain_member_ids:
            continue
        try:
            await member.remove_roles(
                role,
                reason=f"Reconciling Mantis team role for {details.team.name}",
            )
            changed = True
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.exception(
                "Could not remove team role %s from member %s", role.id, member.id
            )

    for member in resolved_members.values():
        if role in getattr(member, "roles", ()):
            continue
        try:
            await member.add_roles(
                role,
                reason=f"Reconciling Mantis team role for {details.team.name}",
            )
            changed = True
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.exception(
                "Could not add team role %s to member %s", role.id, member.id
            )
    return changed


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
    channel: discord.TextChannel,
    details: TeamDetails,
    team_role: discord.Role | None = None,
) -> bool:
    """Make channel access exactly match team membership and override policy.

    Role overwrites other than @everyone and AllTeams are preserved. Individual
    overwrites are bot-managed: linked team members are granted access, stale
    users are removed, and the managing bot keeps its operational overwrite.
    """

    guild = channel.guild
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

    if team_role is not None:
        team_role_overwrite = overwrites.get(team_role, discord.PermissionOverwrite())
        changed |= _allow_team_member_permissions(team_role_overwrite)
        overwrites[team_role] = team_role_overwrite

    bot_member = guild.me
    bot_member_id = bot_member.id if bot_member is not None else None
    if bot_member is not None:
        bot_overwrite = overwrites.get(bot_member, discord.PermissionOverwrite())
        changed |= _allow_bot_permissions(bot_overwrite)
        overwrites[bot_member] = bot_overwrite

    # Remove stale bot-managed individual overwrites from the older access
    # model. Role overwrites remain untouched so moderation policy is preserved.
    for target in tuple(overwrites):
        if not isinstance(target, discord.Member):
            continue
        if target.id == bot_member_id:
            continue
        del overwrites[target]
        changed = True

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
        await delete_team_role(guild, details.team)
        LOGGER.warning(
            "Marked team %s orphaned because Discord channel %s is missing",
            team_uuid,
            details.team.discord_channel_id,
        )
        return False

    # Every refresh repairs privacy, adds newly linked/current members, and
    # removes stale member overwrites after membership changes.
    try:
        team_role = await ensure_team_role(guild, details)
    except (discord.Forbidden, discord.HTTPException):
        LOGGER.exception("Could not ensure Discord role for team %s", team_uuid)
        team_role = None
    if team_role is not None:
        await reconcile_team_role_members(guild, team_role, details)
    await reconcile_team_channel_permissions(channel, details, team_role)

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


def _split_directory_block(block: str, limit: int = 3900) -> list[str]:
    pieces: list[str] = []
    remaining = block
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at <= 0:
            split_at = limit
        pieces.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    if remaining:
        pieces.append(remaining)
    return pieces


def _pack_directory_entries(
    entries: list[tuple[str, DirectoryButtonTeam]],
    *,
    limit: int = 3900,
    max_buttons: int = MAX_DIRECTORY_BUTTONS,
) -> list[tuple[str, tuple[DirectoryButtonTeam, ...]]]:
    """Pack directory details and their buttons within Discord component limits."""

    pages: list[tuple[str, tuple[DirectoryButtonTeam, ...]]] = []
    current: list[str] = []
    current_buttons: list[DirectoryButtonTeam] = []
    current_button_ids: set[str] = set()
    current_length = 0

    def flush() -> None:
        nonlocal current, current_buttons, current_button_ids, current_length
        if current:
            pages.append(("\n\n".join(current), tuple(current_buttons)))
        current = []
        current_buttons = []
        current_button_ids = set()
        current_length = 0

    for block, button_team in entries:
        for piece in _split_directory_block(block, limit):
            needs_button = button_team.uuid not in current_button_ids
            added_length = len(piece) + (2 if current else 0)
            if current and (
                current_length + added_length > limit
                or (needs_button and len(current_buttons) >= max_buttons)
            ):
                flush()
                needs_button = True
                added_length = len(piece)
            current.append(piece)
            current_length += added_length
            if needs_button:
                current_buttons.append(button_team)
                current_button_ids.add(button_team.uuid)
    flush()
    return pages


def _directory_button_label(button_team: DirectoryButtonTeam) -> str:
    label = f"{button_team.index}. {button_team.name}"
    return label[:80]


class TeamJoinView(discord.ui.View):
    def __init__(self, teams: Iterable[DirectoryButtonTeam]):
        super().__init__(timeout=None)
        for button_team in teams:
            button = discord.ui.Button(
                label=_directory_button_label(button_team),
                style=discord.ButtonStyle.primary,
                custom_id=f"team:directory:join:{button_team.uuid}",
            )
            button.callback = self._join
            self.add_item(button)

    async def _join(self, interaction: discord.Interaction) -> None:
        custom_id = interaction.data.get("custom_id") if interaction.data else None
        if not isinstance(custom_id, str):
            await interaction.response.send_message(
                "That team button is no longer valid.", ephemeral=False
            )
            return
        try:
            team_uuid = UUID(custom_id.rsplit(":", 1)[-1])
        except ValueError:
            await interaction.response.send_message(
                "That team button is no longer valid.", ephemeral=False
            )
            return
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Team joins are only available in a server.", ephemeral=False
            )
            return
        await interaction.response.defer(ephemeral=False, thinking=True)
        try:
            message = await _create_directory_join_request(
                interaction.client, guild, interaction.user.id, team_uuid
            )
        except TeamServiceError as error:
            await interaction.followup.send(str(error), ephemeral=False)
            return
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.exception("Could not post a team join request")
            await interaction.followup.send(
                "Discord could not post your join request.", ephemeral=False
            )
            return
        except Exception:
            LOGGER.exception("Unexpected failure while posting a team join request")
            await interaction.followup.send(
                "Your team join request could not be posted. Please try again.",
                ephemeral=False,
            )
            return
        await interaction.followup.send(message, ephemeral=False)


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
            await delete_team_role(guild, team)
            LOGGER.warning(
                "Marked team %s orphaned because its channel is missing", team.uuid
            )
        else:
            valid.append(team)

    selected_sorted = sorted(valid, key=lambda t: t.name.casefold())
    toc_lines = [
        "# TEAMS",
        "Use a team's button below its directory page to request to join.",
        "",
    ]
    toc_lines.extend(
        f"{index}. {discord.utils.escape_markdown(team.name)}"
        for index, team in enumerate(selected_sorted, start=1)
    )
    if not selected_sorted:
        toc_lines.append("No active teams.")
    toc_embed = discord.Embed(
        description=_fit_description("\n".join(toc_lines)),
        color=discord.Color.blurple(),
    )

    detail_entries: list[tuple[str, DirectoryButtonTeam]] = []
    for index, team in enumerate(selected_sorted, 1):
        try:
            details = await asyncio.to_thread(get_team_details, team.uuid)
        except TeamServiceError:
            continue
        body = _team_text(details, directory=True).splitlines()
        body[0] = f"**[{index}] {discord.utils.escape_markdown(team.name.upper())}**"
        detail_entries.append(
            (
                "\n".join(body),
                DirectoryButtonTeam(index=index, uuid=str(team.uuid), name=team.name),
            )
        )
    detail_pages = _pack_directory_entries(detail_entries)

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
            await toc_message.edit(content=None, embed=toc_embed, view=None)
            await toc_message.clear_reactions()

        detail_messages: list[discord.Message] = []
        detail_page_teams: list[list[dict[str, str | int]]] = []
        for page_index, (page, button_teams) in enumerate(detail_pages):
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
                detail_message = await channel.send(
                    embed=page_embed, view=TeamJoinView(button_teams)
                )
            else:
                await detail_message.edit(
                    content=None, embed=page_embed, view=TeamJoinView(button_teams)
                )
            detail_messages.append(detail_message)
            detail_page_teams.append(
                [
                    {"index": team.index, "uuid": team.uuid, "name": team.name}
                    for team in button_teams
                ]
            )

        for stale_id in old_detail_ids[len(detail_pages) :]:
            try:
                stale_message = await channel.fetch_message(int(stale_id))
                if bot.user is not None and stale_message.author.id == bot.user.id:
                    await stale_message.delete()
            except discord.NotFound:
                pass

        directory_state = {
            "version": 3,
            "channel_id": str(channel.id),
            "toc_message_id": str(toc_message.id),
            "detail_message_ids": [str(message.id) for message in detail_messages],
            "detail_page_teams": detail_page_teams,
        }
        await asyncio.to_thread(_save_directory_state, directory_state)
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


def _join_request_content(team_role: discord.Role | None) -> str | None:
    return team_role.mention if team_role is not None else None


def build_join_request_embed(
    details: JoinRequestDetails,
    *,
    resolved_by: discord.abc.User | None = None,
    status: str | None = None,
) -> discord.Embed:
    requester = details.member.full_name or details.member.email
    embed = discord.Embed(
        title="Team Join Request",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Requester", value=requester, inline=True)
    embed.add_field(name="Team", value=details.team.name, inline=True)
    embed.add_field(name="Rank", value=RANK_NAMES[4], inline=True)
    embed.add_field(name="Request ID", value=str(details.request.uuid), inline=False)

    if status is not None:
        status_text = status.upper()
        if resolved_by is not None:
            status_text = f"{status_text} by {resolved_by.mention}"
        embed.add_field(name="Status", value=status_text, inline=False)
        embed.color = (
            discord.Color.green() if status == "approved" else discord.Color.red()
        )

    return embed


async def post_join_request(
    bot: commands.Bot, guild: discord.Guild, details: JoinRequestDetails
) -> None:
    channel = await _fetch_team_channel(bot, guild, details.team.discord_channel_id)
    if channel is None:
        await asyncio.to_thread(mark_team_orphaned, details.team.uuid)
        await delete_team_role(guild, details.team)
        raise TeamServiceError("That team's Discord channel no longer exists.")
    team_details = await asyncio.to_thread(get_team_details, details.team.uuid)
    try:
        team_role = await ensure_team_role(guild, team_details)
    except (discord.Forbidden, discord.HTTPException):
        LOGGER.exception(
            "Could not ensure Discord role for join request %s", details.request.uuid
        )
        team_role = None
    message = await channel.send(
        content=_join_request_content(team_role),
        embed=build_join_request_embed(details),
        view=JoinRequestView(details.request.uuid),
        allowed_mentions=discord.AllowedMentions(
            users=False, roles=True, everyone=False
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
        await interaction.response.defer(ephemeral=False, thinking=True)
        try:
            details = await asyncio.to_thread(
                resolve_join_request, self.request_uuid, interaction.user.id, approve
            )
        except TeamServiceError as error:
            await interaction.followup.send(str(error), ephemeral=False)
            return
        status = "approved" if approve else "rejected"
        if interaction.message is not None:
            try:
                await interaction.message.edit(
                    content=None,
                    embed=build_join_request_embed(
                        details, resolved_by=interaction.user, status=status
                    ),
                    view=None,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.exception(
                    "Could not update resolved join request %s", self.request_uuid
                )
        await interaction.followup.send(
            f"Join request {status.casefold()}.", ephemeral=False
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
                "This vote is only valid in a server.", ephemeral=False
            )
            return
        await interaction.response.defer(ephemeral=False, thinking=True)
        try:
            result = await asyncio.to_thread(
                cast_close_vote, self.close_attempt_uuid, interaction.user.id
            )
        except TeamServiceError as error:
            await interaction.followup.send(str(error), ephemeral=False)
            return
        if not result.quorum:
            duplicate = (
                " Your earlier vote was already counted." if not result.accepted else ""
            )
            await interaction.followup.send(
                f"Vote recorded.{duplicate}", ephemeral=False
            )
            return

        team = await asyncio.to_thread(get_team, result.team_uuid)
        if team is None:
            await interaction.followup.send(
                "That team no longer exists.", ephemeral=False
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
            await delete_team_role(guild, team)
            await interaction.followup.send(
                "The team channel is no longer available.", ephemeral=False
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
        if close_result.success:
            await delete_team_role(guild, team)
        await refresh_directory(interaction.client, guild)
        await interaction.followup.send(close_result.message, ephemeral=False)


async def restore_team_views(bot: commands.Bot) -> None:
    """Re-register persistent controls and reconcile missing managed messages."""

    views_already_registered = getattr(bot, "_team_views_restored", False)
    bot._team_views_restored = True
    if not views_already_registered:
        state = await asyncio.to_thread(_directory_state)
        detail_message_ids = state.get("detail_message_ids", [])
        detail_page_teams = state.get("detail_page_teams", [])
        for message_id, page_teams in zip(detail_message_ids, detail_page_teams):
            button_teams: list[DirectoryButtonTeam] = []
            for team in page_teams:
                try:
                    button_teams.append(
                        DirectoryButtonTeam(
                            index=int(team["index"]),
                            uuid=str(team["uuid"]),
                            name=str(team["name"]),
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    LOGGER.warning("Skipping invalid #teams directory button: %r", team)
            if button_teams:
                bot.add_view(TeamJoinView(button_teams), message_id=int(message_id))
    for request in await asyncio.to_thread(get_pending_join_requests):
        try:
            if request.discord_message_id is None:
                await asyncio.to_thread(discard_unposted_join_request, request.uuid)
                LOGGER.info(
                    "Discarded pending join request %s without a Discord message ID",
                    request.uuid,
                )
                continue
            details = await asyncio.to_thread(get_join_request_details, request.uuid)
            channel = (
                bot.get_channel(int(details.team.discord_channel_id))
                if details.team.discord_channel_id is not None
                else None
            )
            if not isinstance(channel, discord.TextChannel):
                continue
            try:
                await channel.fetch_message(int(request.discord_message_id))
            except discord.NotFound:
                await asyncio.to_thread(
                    discard_join_requests_by_message_ids,
                    (request.discord_message_id,),
                )
                LOGGER.info(
                    "Discarded pending join request %s because message %s is missing",
                    request.uuid,
                    request.discord_message_id,
                )
            else:
                if not views_already_registered:
                    bot.add_view(
                        JoinRequestView(request.uuid),
                        message_id=int(request.discord_message_id),
                    )
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
    """Clear pending controls when their Discord messages are deleted."""

    normalized_ids = tuple(str(message_id) for message_id in message_ids)
    cancelled = await asyncio.to_thread(
        cancel_close_attempts_by_message_ids, normalized_ids
    )
    discarded = await asyncio.to_thread(
        discard_join_requests_by_message_ids, normalized_ids
    )
    if cancelled:
        LOGGER.info(
            "Cancelled %s close attempt(s) after Discord message deletion",
            cancelled,
        )
    if discarded:
        LOGGER.info(
            "Discarded %s join request(s) after Discord message deletion",
            discarded,
        )
    return cancelled + discarded


async def _create_directory_join_request(
    bot: commands.Bot,
    guild: discord.Guild,
    user_id: int,
    team_uuid: UUID,
) -> str:
    actor = await asyncio.to_thread(get_user_by_discord, user_id)
    if has_leadership(actor, discord_id=user_id):
        # Leadership override is a direct self-join, not an auto-approved
        # request. The service re-checks effective Leadership and duplicate membership
        # under the team lock before inserting rank 4.
        await asyncio.to_thread(join_team_as_leadership, team_uuid, user_id)
        await refresh_team_artifacts(bot, guild, team_uuid)
        return "You joined that team."
    details = await asyncio.to_thread(create_join_request, team_uuid, user_id)
    try:
        await post_join_request(bot, guild, details)
    except Exception:
        # A request without its Approve/Reject message cannot be acted on
        # and would block retries via the pending-request unique index.
        await asyncio.to_thread(discard_unposted_join_request, details.request.uuid)
        raise
    return "Join request sent to that team."
