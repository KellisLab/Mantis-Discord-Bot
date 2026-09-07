"""Transactional operations for member access tokens."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlmodel import select

from database import get_session
from members.models import User
from members.token_models import AccessToken

TOKEN_TTL = timedelta(days=14)


class AccessTokenServiceError(ValueError):
    """An access token operation is invalid or unauthorized."""


class AccessTokenPermissionError(AccessTokenServiceError):
    pass


@dataclass(frozen=True)
class IssuedAccessToken:
    token: str
    expires_at: datetime


def _user_by_discord(session, discord_id: str | int) -> User:
    user = session.exec(
        select(User).where(User.discord_id == str(discord_id))
    ).one_or_none()
    if user is None:
        raise AccessTokenPermissionError(
            "Your Discord account is not linked to a Mantis profile."
        )
    return user


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _revoke_active_tokens(session, user_uuid: UUID) -> None:
    active_tokens = session.exec(
        select(AccessToken)
        .where(AccessToken.user_uuid == user_uuid, AccessToken.revoked_at.is_(None))
        .with_for_update()
    ).all()
    now = datetime.now(timezone.utc)
    for active_token in active_tokens:
        active_token.revoked_at = now
        session.add(active_token)


def issue_token(user_uuid: UUID) -> IssuedAccessToken:
    """Revoke any existing active token and issue a new one for the user."""

    plaintext = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_at = now + TOKEN_TTL

    with get_session() as session:
        _revoke_active_tokens(session, user_uuid)

        token_row = AccessToken(
            user_uuid=user_uuid,
            token_hash=_hash_token(plaintext),
            expires_at=expires_at,
        )
        session.add(token_row)
        session.commit()

    return IssuedAccessToken(token=plaintext, expires_at=expires_at)


def revoke_token(user_uuid: UUID) -> None:
    """Revoke any active token rows for the user."""

    with get_session() as session:
        _revoke_active_tokens(session, user_uuid)
        session.commit()
