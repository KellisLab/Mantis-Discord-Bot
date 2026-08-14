"""Transactional team operations.

Discord handlers call this synchronous layer through ``asyncio.to_thread``. The
database is authoritative: Discord messages/channels are updated only after
these functions commit successfully.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from database import get_session
from members.models import User
from members.permissions import has_leadership
from members.service import _resolve_member_in_session
from teams.models import (
    CloseAttempt,
    CloseAttemptStatus,
    CloseVote,
    JoinRequest,
    JoinRequestStatus,
    Team,
    TeamMembership,
    TeamStatus,
)


class TeamServiceError(ValueError):
    """A requested team operation is invalid or unauthorized."""


class TeamNotFoundError(TeamServiceError):
    pass


class TeamPermissionError(TeamServiceError):
    pass


class TeamConflictError(TeamServiceError):
    pass


@dataclass(frozen=True)
class TeamMember:
    uuid: UUID
    discord_id: str | None
    display_name: str
    rank: int


@dataclass(frozen=True)
class TeamDetails:
    team: Team
    members: tuple[TeamMember, ...]


@dataclass(frozen=True)
class JoinRequestDetails:
    request: JoinRequest
    team: Team
    member: User


@dataclass(frozen=True)
class CloseVoteResult:
    accepted: bool
    quorum: bool
    rank_at_vote: int
    team_uuid: UUID


@dataclass(frozen=True)
class CloseAttemptDetails:
    attempt: CloseAttempt
    team: Team


def _detach(session: Session, value):
    """Return a fully loaded object that remains usable after session close."""

    session.refresh(value)
    session.expunge(value)
    return value


def _user_by_discord(session: Session, discord_id: str | int) -> User:
    user = session.exec(
        select(User).where(User.discord_id == str(discord_id))
    ).one_or_none()
    if user is None:
        raise TeamPermissionError(
            "Your Discord account is not linked to a Mantis profile."
        )
    return user


def _locked_team(session: Session, team_uuid: UUID) -> Team:
    """Serialize mutations so concurrent commands/button clicks cannot race."""

    team = session.exec(
        select(Team).where(Team.uuid == team_uuid).with_for_update()
    ).one_or_none()
    if team is None:
        raise TeamNotFoundError("That team no longer exists.")
    return team


def _active(team: Team) -> None:
    if team.status != TeamStatus.ACTIVE:
        raise TeamConflictError("Closed or closing teams cannot be changed.")


def _membership(
    session: Session, team_uuid: UUID, member_uuid: UUID
) -> TeamMembership | None:
    return session.get(TeamMembership, (team_uuid, member_uuid))


def _actor_rank(session: Session, team: Team, actor: User) -> int | None:
    membership = _membership(session, team.uuid, actor.id)
    return membership.rank if membership is not None else None


def _management_rank(session: Session, team: Team, actor: User) -> int:
    """Return 0 for Leadership override, otherwise the actor's management rank."""

    if has_leadership(actor):
        return 0
    rank = _actor_rank(session, team, actor)
    if rank not in (1, 2):
        raise TeamPermissionError(
            "Only this team's Lead, Co-Lead, or Leadership may do that."
        )
    return rank


def _commit(session: Session, conflict_message: str) -> None:
    """Translate uniqueness races into a user-safe domain conflict."""

    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise TeamConflictError(conflict_message) from error


def get_user_by_discord(discord_id: str | int) -> User | None:
    with get_session() as session:
        user = session.exec(
            select(User).where(User.discord_id == str(discord_id))
        ).one_or_none()
        return _detach(session, user) if user is not None else None


def get_team(team_uuid: UUID) -> Team | None:
    with get_session() as session:
        team = session.get(Team, team_uuid)
        return _detach(session, team) if team is not None else None


def get_team_by_channel(channel_id: str | int) -> Team | None:
    with get_session() as session:
        team = session.exec(
            select(Team).where(Team.discord_channel_id == str(channel_id))
        ).one_or_none()
        return _detach(session, team) if team is not None else None


def list_active_teams() -> tuple[Team, ...]:
    with get_session() as session:
        teams = session.exec(
            select(Team)
            .where(Team.status == TeamStatus.ACTIVE)
            .order_by(func.lower(Team.name))
        ).all()
        for team in teams:
            session.expunge(team)
        return tuple(teams)


def get_team_details(team_uuid: UUID) -> TeamDetails:
    with get_session() as session:
        team = session.get(Team, team_uuid)
        if team is None:
            raise TeamNotFoundError("That team no longer exists.")
        rows = session.exec(
            select(TeamMembership, User)
            .join(User, User.id == TeamMembership.member_uuid)
            .where(TeamMembership.team_uuid == team_uuid)
            .order_by(
                TeamMembership.rank, func.lower(User.full_name), func.lower(User.email)
            )
        ).all()
        members = tuple(
            TeamMember(
                uuid=user.id,
                discord_id=user.discord_id,
                display_name=user.full_name or user.email,
                rank=membership.rank,
            )
            for membership, user in rows
        )
        session.expunge(team)
        return TeamDetails(team=team, members=members)


def create_team(
    name: str,
    description: str | None,
    channel_id: str | int | None,
    creator_id: str | int,
) -> Team:
    """Atomically create the team and its one required Lead membership.

    ``channel_id`` may be ``None`` during the first half of Discord provisioning.
    Startup reconciliation turns an abandoned provisioning row into ORPHANED.
    """

    normalized_name = name.strip()
    if not normalized_name:
        raise TeamServiceError("Team name is required.")
    with get_session() as session:
        creator = _user_by_discord(session, creator_id)
        if not has_leadership(creator):
            raise TeamPermissionError("Only Leadership may create teams.")
        team = Team(
            name=normalized_name,
            description=(description or "").strip() or None,
            discord_channel_id=str(channel_id) if channel_id is not None else None,
        )
        session.add(team)
        session.add(TeamMembership(team_uuid=team.uuid, member_uuid=creator.id, rank=1))
        _commit(session, f"An active team named {normalized_name!r} already exists.")
        return _detach(session, team)


def set_team_channel_id(team_uuid: UUID, channel_id: str | int) -> Team:
    """Finish two-phase provisioning by attaching the Discord channel."""

    with get_session() as session:
        team = _locked_team(session, team_uuid)
        _active(team)
        team.discord_channel_id = str(channel_id)
        session.add(team)
        _commit(session, "That Discord channel is already assigned to another team.")
        return _detach(session, team)


def set_info_message_id(team_uuid: UUID, message_id: str | int | None) -> None:
    with get_session() as session:
        team = _locked_team(session, team_uuid)
        team.info_message_id = str(message_id) if message_id is not None else None
        session.add(team)
        session.commit()


def edit_team(
    team_uuid: UUID, actor_id: str | int, *, name: str | None, description: str | None
) -> Team:
    if name is None and description is None:
        raise TeamServiceError("Supply a name, a description, or both.")
    with get_session() as session:
        team = _locked_team(session, team_uuid)
        _active(team)
        actor = _user_by_discord(session, actor_id)
        if not has_leadership(actor) and _actor_rank(session, team, actor) != 1:
            raise TeamPermissionError(
                "Only this team's Lead or Leadership may edit it."
            )
        if name is not None:
            normalized = name.strip()
            if not normalized:
                raise TeamServiceError("Team name cannot be blank.")
            team.name = normalized
        if description is not None:
            team.description = description.strip() or None
        session.add(team)
        _commit(session, f"An active team named {team.name!r} already exists.")
        return _detach(session, team)


def add_team_member(
    team_uuid: UUID,
    actor_id: str | int,
    identifier: str,
    rank: int,
    *,
    discord_id: str | int | None = None,
) -> None:
    """Add exactly one membership after rank-scoped authorization."""

    if rank not in (2, 3, 4):
        raise TeamServiceError("Rank must be 2, 3, or 4.")
    with get_session() as session:
        team = _locked_team(session, team_uuid)
        _active(team)
        actor = _user_by_discord(session, actor_id)
        actor_rank = _management_rank(session, team, actor)
        if actor_rank == 2 and rank not in (3, 4):
            raise TeamPermissionError("Co-Leads may only add Engineers or Developers.")
        target = _resolve_member_in_session(session, identifier, discord_id=discord_id)
        # Give a clear error before relying on the composite PK as the final
        # concurrency-safe duplicate-membership backstop.
        if _membership(session, team.uuid, target.id) is not None:
            raise TeamConflictError(
                f"{target.full_name or target.email} is already on this team."
            )
        session.add(
            TeamMembership(team_uuid=team.uuid, member_uuid=target.id, rank=rank)
        )
        _commit(session, "That member was added concurrently.")


def remove_team_member(
    team_uuid: UUID,
    actor_id: str | int,
    identifier: str,
    *,
    discord_id: str | int | None = None,
) -> None:
    with get_session() as session:
        team = _locked_team(session, team_uuid)
        _active(team)
        actor = _user_by_discord(session, actor_id)
        actor_rank = _management_rank(session, team, actor)
        target = _resolve_member_in_session(session, identifier, discord_id=discord_id)
        membership = _membership(session, team.uuid, target.id)
        if membership is None:
            raise TeamConflictError("That member is not on this team.")
        if membership.rank == 1:
            raise TeamConflictError("Transfer the Lead role before removing the Lead.")
        if actor_rank == 2 and membership.rank not in (3, 4):
            raise TeamPermissionError(
                "Co-Leads may only remove Engineers or Developers."
            )
        session.delete(membership)
        session.commit()


def set_team_rank(
    team_uuid: UUID,
    actor_id: str | int,
    identifier: str,
    rank: int,
    *,
    discord_id: str | int | None = None,
) -> None:
    if rank not in (2, 3, 4):
        raise TeamServiceError("Rank must be 2, 3, or 4.")
    with get_session() as session:
        team = _locked_team(session, team_uuid)
        _active(team)
        actor = _user_by_discord(session, actor_id)
        actor_rank = _management_rank(session, team, actor)
        target = _resolve_member_in_session(session, identifier, discord_id=discord_id)
        membership = _membership(session, team.uuid, target.id)
        if membership is None:
            raise TeamConflictError("That member is not on this team.")
        if membership.rank == 1:
            raise TeamConflictError("Use `/team transfer-lead` to change the Lead.")
        if actor_rank == 2 and (membership.rank not in (3, 4) or rank not in (3, 4)):
            raise TeamPermissionError("Co-Leads may only manage ranks 3 and 4.")
        membership.rank = rank
        session.add(membership)
        session.commit()


def transfer_team_lead(
    team_uuid: UUID,
    actor_id: str | int,
    identifier: str,
    *,
    discord_id: str | int | None = None,
) -> None:
    """Swap Lead and Co-Lead ranks in one database transaction."""

    with get_session() as session:
        team = _locked_team(session, team_uuid)
        _active(team)
        actor = _user_by_discord(session, actor_id)
        current_lead = session.exec(
            select(TeamMembership).where(
                TeamMembership.team_uuid == team.uuid, TeamMembership.rank == 1
            )
        ).one_or_none()
        if current_lead is None:
            raise TeamConflictError(
                "This team has no Lead. Contact Leadership to resolve this."
            )
        if not has_leadership(actor) and current_lead.member_uuid != actor.id:
            raise TeamPermissionError(
                "Only the current Lead or Leadership may transfer the Lead role."
            )
        target = _resolve_member_in_session(session, identifier, discord_id=discord_id)
        target_membership = _membership(session, team.uuid, target.id)
        if target_membership is None:
            raise TeamConflictError("The new Lead must already belong to this team.")
        if target_membership.member_uuid == current_lead.member_uuid:
            raise TeamConflictError("That member is already the Lead.")
        # Avoid the partial unique index while preserving one lead at commit.
        current_lead.rank = 2
        session.add(current_lead)
        session.flush()
        target_membership.rank = 1
        session.add(target_membership)
        session.commit()


def leave_team(team_uuid: UUID, actor_id: str | int) -> None:
    with get_session() as session:
        team = _locked_team(session, team_uuid)
        _active(team)
        actor = _user_by_discord(session, actor_id)
        membership = _membership(session, team.uuid, actor.id)
        if membership is None:
            raise TeamConflictError("You are not on this team.")
        if membership.rank == 1:
            raise TeamConflictError("Transfer the Lead role before leaving the team.")
        session.delete(membership)
        session.commit()


def mark_team_orphaned(team_uuid: UUID) -> None:
    """Preserve team history when its Discord channel cannot be found."""

    with get_session() as session:
        team = _locked_team(session, team_uuid)
        if team.status in (TeamStatus.ACTIVE, TeamStatus.CLOSING):
            team.status = TeamStatus.ORPHANED
            team.closed_at = datetime.now(timezone.utc)
            session.add(team)
            session.commit()


def join_team_as_leadership(team_uuid: UUID, actor_id: str | int) -> None:
    """Let Leadership join directly at the normal self-join rank of Developer.

    This is intentionally separate from management ``add``: the reacting user
    can add only themselves, and the database re-checks Leadership at commit time.
    """

    with get_session() as session:
        team = _locked_team(session, team_uuid)
        _active(team)
        actor = _user_by_discord(session, actor_id)
        if not has_leadership(actor):
            raise TeamPermissionError(
                "Only Leadership can bypass the team join-request workflow."
            )
        if _membership(session, team.uuid, actor.id) is not None:
            raise TeamConflictError("You already belong to that team.")
        session.add(TeamMembership(team_uuid=team.uuid, member_uuid=actor.id, rank=4))
        _commit(session, "You were added to that team concurrently.")


def create_join_request(team_uuid: UUID, requester_id: str | int) -> JoinRequestDetails:
    with get_session() as session:
        team = _locked_team(session, team_uuid)
        _active(team)
        requester = _user_by_discord(session, requester_id)
        if _membership(session, team.uuid, requester.id) is not None:
            raise TeamConflictError("You already belong to that team.")
        request = JoinRequest(team_uuid=team.uuid, member_uuid=requester.id)
        session.add(request)
        _commit(session, "You already have a pending request for that team.")
        # commit expires every loaded model. Refresh all values returned to the
        # Discord layer before detaching them; otherwise reading the team channel
        # or requester name raises DetachedInstanceError before channel.send().
        return JoinRequestDetails(
            request=_detach(session, request),
            team=_detach(session, team),
            member=_detach(session, requester),
        )


def set_join_request_message_id(request_uuid: UUID, message_id: str | int) -> None:
    with get_session() as session:
        request = session.get(JoinRequest, request_uuid)
        if request is not None and request.status == JoinRequestStatus.PENDING:
            request.discord_message_id = str(message_id)
            session.add(request)
            session.commit()


def discard_unposted_join_request(request_uuid: UUID) -> bool:
    """Delete a pending request that never received a Discord control message."""

    with get_session() as session:
        request = session.exec(
            select(JoinRequest)
            .where(JoinRequest.uuid == request_uuid)
            .with_for_update()
        ).one_or_none()
        if (
            request is None
            or request.status != JoinRequestStatus.PENDING
            or request.discord_message_id is not None
        ):
            return False
        session.delete(request)
        session.commit()
        return True


def discard_join_requests_by_message_ids(
    message_ids: Iterable[str | int],
) -> int:
    """Delete pending requests whose Discord control messages were deleted."""

    normalized_ids = tuple(str(message_id) for message_id in message_ids)
    if not normalized_ids:
        return 0
    with get_session() as session:
        requests = session.exec(
            select(JoinRequest).where(
                JoinRequest.status == JoinRequestStatus.PENDING,
                JoinRequest.discord_message_id.in_(normalized_ids),
            )
        ).all()
        for request in requests:
            session.delete(request)
        if requests:
            session.commit()
        return len(requests)


def get_pending_join_requests() -> tuple[JoinRequest, ...]:
    with get_session() as session:
        requests = session.exec(
            select(JoinRequest).where(JoinRequest.status == JoinRequestStatus.PENDING)
        ).all()
        for request in requests:
            session.expunge(request)
        return tuple(requests)


def get_join_request_details(request_uuid: UUID) -> JoinRequestDetails:
    with get_session() as session:
        request = session.get(JoinRequest, request_uuid)
        if request is None:
            raise TeamNotFoundError("That join request no longer exists.")
        team = session.get(Team, request.team_uuid)
        member = session.get(User, request.member_uuid)
        if team is None or member is None:
            raise TeamNotFoundError("That join request is no longer valid.")
        for value in (request, team, member):
            session.expunge(value)
        return JoinRequestDetails(request=request, team=team, member=member)


def resolve_join_request(
    request_uuid: UUID, resolver_id: str | int, approve: bool
) -> JoinRequestDetails:
    """Resolve once under a row lock, optionally adding a Developer membership."""

    with get_session() as session:
        request = session.exec(
            select(JoinRequest)
            .where(JoinRequest.uuid == request_uuid)
            .with_for_update()
        ).one_or_none()
        if request is None:
            raise TeamNotFoundError("That join request no longer exists.")
        if request.status != JoinRequestStatus.PENDING:
            raise TeamConflictError("That join request has already been resolved.")
        team = _locked_team(session, request.team_uuid)
        _active(team)
        resolver = _user_by_discord(session, resolver_id)
        rank = _actor_rank(session, team, resolver)
        if not has_leadership(resolver) and rank not in (1, 2):
            raise TeamPermissionError(
                "Only this team's Lead, Co-Lead, or Leadership may resolve requests."
            )
        member = session.get(User, request.member_uuid)
        if member is None:
            raise TeamConflictError("The requesting member no longer exists.")
        if approve:
            if _membership(session, team.uuid, member.id) is not None:
                raise TeamConflictError("The requester already belongs to this team.")
            session.add(
                TeamMembership(team_uuid=team.uuid, member_uuid=member.id, rank=4)
            )
            request.status = JoinRequestStatus.APPROVED
        else:
            request.status = JoinRequestStatus.REJECTED
        request.resolved_at = datetime.now(timezone.utc)
        request.resolved_by = resolver.id
        session.add(request)
        _commit(session, "That request was resolved concurrently.")
        for value in (request, team, member):
            session.refresh(value)
            session.expunge(value)
        return JoinRequestDetails(request=request, team=team, member=member)


def begin_close_vote(team_uuid: UUID, actor_id: str | int) -> CloseAttempt:
    """Start a new attempt without deleting votes from prior attempts."""

    with get_session() as session:
        team = _locked_team(session, team_uuid)
        _active(team)
        actor = _user_by_discord(session, actor_id)
        if not has_leadership(actor) and _actor_rank(session, team, actor) is None:
            raise TeamPermissionError(
                "Only current team members or Leadership may start a close vote."
            )
        existing = session.exec(
            select(CloseAttempt).where(
                CloseAttempt.team_uuid == team.uuid,
                CloseAttempt.status == CloseAttemptStatus.OPEN,
            )
        ).one_or_none()
        if existing is not None:
            raise TeamConflictError("A close vote is already active for this team.")
        attempt = CloseAttempt(team_uuid=team.uuid)
        session.add(attempt)
        _commit(session, "A close vote was started concurrently for this team.")
        return _detach(session, attempt)


def set_close_vote_message_id(close_attempt_uuid: UUID, message_id: str | int) -> None:
    with get_session() as session:
        attempt = session.exec(
            select(CloseAttempt)
            .where(CloseAttempt.uuid == close_attempt_uuid)
            .with_for_update()
        ).one_or_none()
        if attempt is None or attempt.status != CloseAttemptStatus.OPEN:
            raise TeamConflictError("That close attempt is no longer open.")
        attempt.discord_message_id = str(message_id)
        session.add(attempt)
        session.commit()


def cancel_close_attempt(close_attempt_uuid: UUID) -> None:
    with get_session() as session:
        attempt = session.exec(
            select(CloseAttempt)
            .where(CloseAttempt.uuid == close_attempt_uuid)
            .with_for_update()
        ).one_or_none()
        if attempt is not None and attempt.status == CloseAttemptStatus.OPEN:
            attempt.status = CloseAttemptStatus.CANCELLED
            session.add(attempt)
            session.commit()


def cancel_close_attempts_by_message_ids(
    message_ids: Iterable[str | int],
) -> int:
    """Cancel open attempts whose Discord controls were deleted.

    Attempts and their votes remain as immutable audit history. Moving the
    attempt out of OPEN also releases the partial unique index so `/team close`
    may start a fresh attempt for the same team.
    """

    normalized_ids = tuple({str(message_id) for message_id in message_ids})
    if not normalized_ids:
        return 0
    with get_session() as session:
        attempts = session.exec(
            select(CloseAttempt)
            .where(
                CloseAttempt.discord_message_id.in_(normalized_ids),
                CloseAttempt.status == CloseAttemptStatus.OPEN,
            )
            .with_for_update()
        ).all()
        for attempt in attempts:
            attempt.status = CloseAttemptStatus.CANCELLED
            session.add(attempt)
        if attempts:
            session.commit()
        return len(attempts)


def get_open_close_attempts() -> tuple[CloseAttemptDetails, ...]:
    with get_session() as session:
        rows = session.exec(
            select(CloseAttempt, Team)
            .join(Team, Team.uuid == CloseAttempt.team_uuid)
            .where(
                CloseAttempt.status == CloseAttemptStatus.OPEN,
                Team.status == TeamStatus.ACTIVE,
            )
        ).all()
        details = []
        for attempt, team in rows:
            session.expunge(attempt)
            session.expunge(team)
            details.append(CloseAttemptDetails(attempt=attempt, team=team))
        return tuple(details)


def cast_close_vote(close_attempt_uuid: UUID, actor_id: str | int) -> CloseVoteResult:
    """Record one attempt-scoped vote and atomically advance quorum state."""

    with get_session() as session:
        attempt = session.exec(
            select(CloseAttempt)
            .where(CloseAttempt.uuid == close_attempt_uuid)
            .with_for_update()
        ).one_or_none()
        if attempt is None or attempt.status != CloseAttemptStatus.OPEN:
            raise TeamConflictError("This close vote is no longer active.")
        team = _locked_team(session, attempt.team_uuid)
        _active(team)
        actor = _user_by_discord(session, actor_id)
        membership = _membership(session, team.uuid, actor.id)
        if membership is None and not has_leadership(actor):
            raise TeamPermissionError(
                "Only current team members or Leadership may vote."
            )
        # Leadership is rank-1-equivalent for quorum. Otherwise this stores the
        # member's current rank as an audit snapshot for this attempt.
        rank = 1 if has_leadership(actor) else membership.rank
        existing = _membership_vote(session, attempt.uuid, actor.id)
        accepted = existing is None
        if accepted:
            session.add(
                CloseVote(
                    close_attempt_uuid=attempt.uuid,
                    member_uuid=actor.id,
                    rank_at_vote=rank,
                )
            )
            session.flush()
        votes = session.exec(
            select(CloseVote).where(CloseVote.close_attempt_uuid == attempt.uuid)
        ).all()
        quorum = any(vote.rank_at_vote == 1 for vote in votes) or (
            any(vote.rank_at_vote == 2 for vote in votes)
            and any(vote.rank_at_vote == 3 for vote in votes)
        )
        if quorum:
            attempt.status = CloseAttemptStatus.PASSED
            team.status = TeamStatus.CLOSING
            session.add(attempt)
            session.add(team)
        session.commit()
        return CloseVoteResult(
            accepted=accepted,
            quorum=quorum,
            rank_at_vote=rank,
            team_uuid=team.uuid,
        )


def _membership_vote(
    session: Session, close_attempt_uuid: UUID, member_uuid: UUID
) -> CloseVote | None:
    return session.get(CloseVote, (close_attempt_uuid, member_uuid))


def finish_team_close(
    team_uuid: UUID, close_attempt_uuid: UUID, *, success: bool
) -> None:
    """Finalize DB state after the shared Discord archive service returns."""

    with get_session() as session:
        team = _locked_team(session, team_uuid)
        attempt = session.get(CloseAttempt, close_attempt_uuid)
        if attempt is None or attempt.team_uuid != team.uuid:
            raise TeamConflictError("That close attempt does not belong to this team.")
        if success:
            team.status = TeamStatus.CLOSED
            team.closed_at = datetime.now(timezone.utc)
        else:
            team.status = TeamStatus.ACTIVE
            attempt.status = CloseAttemptStatus.CANCELLED
            session.add(attempt)
        session.add(team)
        session.commit()
