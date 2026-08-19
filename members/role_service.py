"""Transactional operations for member role advancement requests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from database import get_session
from members.models import Stage, User
from members.permissions import has_leadership
from members.role_models import RoleRequest, RoleRequestStatus, RoleRequestType

STAGE_ORDER = tuple(Stage)


class RoleRequestServiceError(ValueError):
    """A role request operation is invalid or unauthorized."""


class RoleRequestNotFoundError(RoleRequestServiceError):
    pass


class RoleRequestConflictError(RoleRequestServiceError):
    pass


class RoleRequestPermissionError(RoleRequestServiceError):
    pass


@dataclass(frozen=True)
class RoleRequestDetails:
    request: RoleRequest
    requester: User


def _detach(session: Session, value):
    session.refresh(value)
    session.expunge(value)
    return value


def _user_by_discord(session: Session, discord_id: str | int) -> User:
    user = session.exec(
        select(User).where(User.discord_id == str(discord_id))
    ).one_or_none()
    if user is None:
        raise RoleRequestPermissionError(
            "Your Discord account is not linked to a Mantis profile."
        )
    return user


def create_role_request(
    requester_discord_id: str | int,
    request_type: RoleRequestType | str,
    *,
    requested_stage: Stage | str | None = None,
    justification: str | None = None,
    evidence_urls: tuple[str, ...] = (),
) -> RoleRequestDetails:
    """Validate and persist a new pending role request for one requester."""

    normalized_type = RoleRequestType(request_type)
    with get_session() as session:
        requester = _user_by_discord(session, requester_discord_id)

        normalized_stage: Stage | None = None
        if normalized_type is RoleRequestType.STAGE:
            if requested_stage is None:
                raise RoleRequestServiceError("A requested stage is required.")
            normalized_stage = Stage(requested_stage)
            if STAGE_ORDER.index(normalized_stage) <= STAGE_ORDER.index(
                requester.stage
            ):
                raise RoleRequestServiceError(
                    f"Requested stage must be higher than your current stage "
                    f"({requester.stage.value})."
                )
        elif normalized_type is RoleRequestType.JOURNEY_MENTOR:
            if requester.is_journey_mentor:
                raise RoleRequestServiceError(
                    "You are already a Journey Mentor."
                )
        elif normalized_type is RoleRequestType.LEADERSHIP:
            if has_leadership(requester):
                raise RoleRequestServiceError("You already have Leadership status.")

        request = RoleRequest(
            requester_uuid=requester.id,
            request_type=normalized_type,
            requested_stage=normalized_stage,
            justification=justification,
            evidence_urls=list(evidence_urls),
        )
        session.add(request)
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise RoleRequestConflictError(
                "You already have a pending request of that type."
            ) from error
        return RoleRequestDetails(
            request=_detach(session, request),
            requester=_detach(session, requester),
        )


def set_role_request_message_id(request_uuid: UUID, message_id: str | int) -> None:
    with get_session() as session:
        request = session.exec(
            select(RoleRequest)
            .where(RoleRequest.uuid == request_uuid)
            .with_for_update()
        ).one_or_none()
        if request is not None and request.status == RoleRequestStatus.PENDING:
            request.discord_message_id = str(message_id)
            session.add(request)
            session.commit()


def discard_unposted_role_request(request_uuid: UUID) -> bool:
    """Delete a pending request that never received a Discord control message."""

    with get_session() as session:
        request = session.exec(
            select(RoleRequest)
            .where(RoleRequest.uuid == request_uuid)
            .with_for_update()
        ).one_or_none()
        if (
            request is None
            or request.status != RoleRequestStatus.PENDING
            or request.discord_message_id is not None
        ):
            return False
        session.delete(request)
        session.commit()
        return True


def discard_role_requests_by_message_ids(message_ids: tuple[str | int, ...]) -> int:
    """Delete pending requests whose Discord control messages were deleted."""

    normalized_ids = tuple(str(message_id) for message_id in message_ids)
    if not normalized_ids:
        return 0
    with get_session() as session:
        requests = session.exec(
            select(RoleRequest).where(
                RoleRequest.status == RoleRequestStatus.PENDING,
                RoleRequest.discord_message_id.in_(normalized_ids),
            )
        ).all()
        for request in requests:
            session.delete(request)
        if requests:
            session.commit()
        return len(requests)


def get_pending_role_requests() -> tuple[RoleRequest, ...]:
    with get_session() as session:
        requests = session.exec(
            select(RoleRequest).where(RoleRequest.status == RoleRequestStatus.PENDING)
        ).all()
        for request in requests:
            session.expunge(request)
        return tuple(requests)


def get_role_request_details(request_uuid: UUID) -> RoleRequestDetails:
    with get_session() as session:
        request = session.get(RoleRequest, request_uuid)
        if request is None:
            raise RoleRequestNotFoundError("That role request no longer exists.")
        requester = session.get(User, request.requester_uuid)
        if requester is None:
            raise RoleRequestNotFoundError("The requesting member no longer exists.")
        for value in (request, requester):
            session.expunge(value)
        return RoleRequestDetails(request=request, requester=requester)


def resolve_role_request(
    request_uuid: UUID, resolver_discord_id: str | int, approve: bool
) -> RoleRequestDetails:
    """Resolve once under a row lock, applying the role change on approval."""

    with get_session() as session:
        request = session.exec(
            select(RoleRequest)
            .where(RoleRequest.uuid == request_uuid)
            .with_for_update()
        ).one_or_none()
        if request is None:
            raise RoleRequestNotFoundError("That role request no longer exists.")
        if request.status != RoleRequestStatus.PENDING:
            raise RoleRequestConflictError("That request has already been resolved.")

        resolver = _user_by_discord(session, resolver_discord_id)
        if not has_leadership(resolver):
            raise RoleRequestPermissionError(
                "Only Leadership may approve or reject role requests."
            )

        requester = session.exec(
            select(User).where(User.id == request.requester_uuid).with_for_update()
        ).one_or_none()
        if requester is None:
            raise RoleRequestConflictError("The requesting member no longer exists.")

        if approve:
            if request.request_type is RoleRequestType.STAGE:
                requester.stage = request.requested_stage
            elif request.request_type is RoleRequestType.JOURNEY_MENTOR:
                requester.is_journey_mentor = True
            elif request.request_type is RoleRequestType.LEADERSHIP:
                requester.is_leadership = True
            session.add(requester)
            request.status = RoleRequestStatus.APPROVED
        else:
            request.status = RoleRequestStatus.REJECTED

        request.resolved_at = datetime.now(timezone.utc)
        request.resolved_by = resolver.id
        session.add(request)
        session.commit()

        for value in (request, requester):
            session.refresh(value)
            session.expunge(value)
        return RoleRequestDetails(request=request, requester=requester)
