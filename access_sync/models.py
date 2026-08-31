"""Persistence models for durable access synchronization."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy import Enum as SQLAlchemyEnum
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AccessSyncJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    FAILED = "failed"


class AccessSyncEvent(SQLModel, table=True):
    """One durable notification that a member's canonical row changed."""

    __tablename__ = "access_sync_events"

    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
    )
    member_uuid: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=func.now()
        ),
    )


class AccessSyncJob(SQLModel, table=True):
    """One provider reconciliation attempt for a member."""

    __tablename__ = "access_sync_jobs"
    __table_args__ = (
        Index(
            "uq_access_sync_jobs_pending_member_provider",
            "member_uuid",
            "provider",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    uuid: UUID = Field(default_factory=uuid4, primary_key=True)
    member_uuid: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    provider: str = Field(sa_column=Column(Text, nullable=False))
    status: AccessSyncJobStatus = Field(
        default=AccessSyncJobStatus.PENDING,
        sa_column=Column(
            SQLAlchemyEnum(
                AccessSyncJobStatus,
                name="access_sync_job_status",
                values_callable=lambda statuses: [status.value for status in statuses],
            ),
            nullable=False,
            server_default=text("'pending'"),
        ),
    )
    attempts: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default=text("0")),
    )
    available_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=func.now()
        ),
    )
    last_error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=func.now()
        ),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
        ),
    )


class AccessSyncIdentity(SQLModel, table=True):
    """Last successfully reconciled external identity for a provider."""

    __tablename__ = "access_sync_identities"

    member_uuid: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        )
    )
    provider: str = Field(sa_column=Column(Text, primary_key=True))
    external_id: str = Field(sa_column=Column(Text, nullable=False))
    external_login: str | None = Field(default=None, sa_column=Column(Text))
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
        ),
    )
