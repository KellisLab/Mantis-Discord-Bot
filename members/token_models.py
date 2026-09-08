"""Database models for member access tokens."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import Column, DateTime, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlmodel import Field, SQLModel

from members.models import utc_now


class AccessToken(SQLModel, table=True):
    """A hashed bearer token granting a member access to Mantis services."""

    __tablename__ = "access_tokens"
    __table_args__ = (
        # A member may have at most one non-revoked token row.
        Index(
            "uq_access_tokens_active_user",
            "user_uuid",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    uuid: UUID = Field(default_factory=uuid4, primary_key=True)
    user_uuid: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    token_hash: str = Field(
        sa_column=Column(Text, nullable=False, unique=True, index=True)
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    revoked_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
