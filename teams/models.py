"""Database models for persistent Discord teams."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
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

from members.models import utc_now


class TeamStatus(str, Enum):
    """Lifecycle state; ORPHANED preserves history after Discord-side loss."""

    ACTIVE = "active"
    CLOSING = "closing"
    CLOSED = "closed"
    ORPHANED = "orphaned"


class JoinRequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class CloseAttemptStatus(str, Enum):
    """A vote attempt is immutable history once passed or cancelled."""

    OPEN = "open"
    PASSED = "passed"
    CANCELLED = "cancelled"


class Team(SQLModel, table=True):
    """Canonical team state; Discord channels are projections of this row."""

    __tablename__ = "teams"
    __table_args__ = (
        Index("ix_teams_discord_channel_id", "discord_channel_id"),
        # Closed/orphaned names can be reused, but active names are unique
        # case-insensitively so Discord capitalization cannot bypass the rule.
        Index(
            "uq_teams_active_name",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    uuid: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(sa_column=Column(Text, nullable=False))
    description: str | None = Field(default=None, sa_column=Column(Text))
    discord_channel_id: str | None = Field(
        # Nullable only during two-phase provisioning (DB row, then Discord).
        default=None,
        sa_column=Column(Text, nullable=True, unique=True),
    )
    info_message_id: str | None = Field(default=None, sa_column=Column(Text))
    status: TeamStatus = Field(
        default=TeamStatus.ACTIVE,
        sa_column=Column(
            SQLAlchemyEnum(
                TeamStatus,
                name="team_status",
                values_callable=lambda values: [value.value for value in values],
            ),
            nullable=False,
            server_default=text("'active'"),
        ),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=func.now()
        ),
    )
    closed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )


class TeamMembership(SQLModel, table=True):
    """One member's rank within one team."""

    __tablename__ = "team_memberships"
    __table_args__ = (
        CheckConstraint("rank BETWEEN 1 AND 4", name="ck_team_membership_rank"),
        # The composite PK below permits the same member on other teams. This
        # partial index independently enforces at most one Lead per team.
        Index(
            "uq_team_memberships_one_lead",
            "team_uuid",
            unique=True,
            postgresql_where=text("rank = 1"),
        ),
    )

    team_uuid: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("teams.uuid", ondelete="CASCADE"),
            primary_key=True,
        )
    )
    member_uuid: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        )
    )
    rank: int = Field(sa_column=Column(Integer, nullable=False))
    joined_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=func.now()
        ),
    )


class JoinRequest(SQLModel, table=True):
    """Auditable request history, including resolved requests."""

    __tablename__ = "team_join_requests"
    __table_args__ = (
        # Multiple historical requests are allowed; only one may be pending.
        Index(
            "uq_team_join_requests_pending_member",
            "team_uuid",
            "member_uuid",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    uuid: UUID = Field(default_factory=uuid4, primary_key=True)
    team_uuid: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("teams.uuid", ondelete="CASCADE"),
            nullable=False,
        )
    )
    member_uuid: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    discord_message_id: str | None = Field(default=None, sa_column=Column(Text))
    status: JoinRequestStatus = Field(
        default=JoinRequestStatus.PENDING,
        sa_column=Column(
            SQLAlchemyEnum(
                JoinRequestStatus,
                name="team_join_request_status",
                values_callable=lambda values: [value.value for value in values],
            ),
            nullable=False,
            server_default=text("'pending'"),
        ),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=func.now()
        ),
    )
    resolved_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    resolved_by: UUID | None = Field(
        default=None,
        sa_column=Column(
            PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
        ),
    )


class CloseAttempt(SQLModel, table=True):
    """One closure round, used to scope votes and persistent Discord controls."""

    __tablename__ = "team_close_attempts"
    __table_args__ = (
        # Prevent two simultaneous close-vote messages for the same team.
        Index(
            "uq_team_close_attempts_open",
            "team_uuid",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )

    uuid: UUID = Field(default_factory=uuid4, primary_key=True)
    team_uuid: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("teams.uuid", ondelete="CASCADE"),
            nullable=False,
        )
    )
    discord_message_id: str | None = Field(default=None, sa_column=Column(Text))
    status: CloseAttemptStatus = Field(
        default=CloseAttemptStatus.OPEN,
        sa_column=Column(
            SQLAlchemyEnum(
                CloseAttemptStatus,
                name="team_close_attempt_status",
                values_callable=lambda values: [value.value for value in values],
            ),
            nullable=False,
            server_default=text("'open'"),
        ),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=func.now()
        ),
    )


class CloseVote(SQLModel, table=True):
    """A member's rank snapshot for one specific close attempt."""

    __tablename__ = "team_close_votes"
    __table_args__ = (
        CheckConstraint("rank_at_vote BETWEEN 1 AND 4", name="ck_team_close_vote_rank"),
    )

    close_attempt_uuid: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("team_close_attempts.uuid", ondelete="CASCADE"),
            primary_key=True,
        )
    )
    member_uuid: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        )
    )
    rank_at_vote: int = Field(sa_column=Column(Integer, nullable=False))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=func.now()
        ),
    )
