"""Database models for member-initiated role advancement requests."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import Column, DateTime, ForeignKey, Index, Text, text
from sqlalchemy import Enum as SQLAlchemyEnum
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlmodel import Field, SQLModel

from members.models import Stage, utc_now


class RoleRequestType(str, Enum):
    STAGE = "stage"
    JOURNEY_MENTOR = "journey_mentor"
    LEADERSHIP = "leadership"


class RoleRequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class RoleRequest(SQLModel, table=True):
    """One member's request to advance a stage or gain a special role."""

    __tablename__ = "role_requests"
    __table_args__ = (
        # A member may have at most one pending request per request type.
        Index(
            "uq_role_requests_pending_requester_type",
            "requester_uuid",
            "request_type",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    uuid: UUID = Field(default_factory=uuid4, primary_key=True)
    requester_uuid: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    request_type: RoleRequestType = Field(
        sa_column=Column(
            SQLAlchemyEnum(
                RoleRequestType,
                name="role_request_type",
                values_callable=lambda values: [value.value for value in values],
            ),
            nullable=False,
        )
    )
    requested_stage: Stage | None = Field(
        default=None,
        sa_column=Column(
            SQLAlchemyEnum(
                Stage,
                name="user_stage",
                values_callable=lambda stages: [stage.value for stage in stages],
            ),
            nullable=True,
        ),
    )
    justification: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    evidence_urls: list[str] = Field(
        default_factory=list,
        sa_column=Column(
            ARRAY(sa.Text()), nullable=False, server_default=text("'{}'")
        ),
    )
    discord_message_id: str | None = Field(default=None, sa_column=Column(Text))
    status: RoleRequestStatus = Field(
        default=RoleRequestStatus.PENDING,
        sa_column=Column(
            SQLAlchemyEnum(
                RoleRequestStatus,
                name="role_request_status",
                values_callable=lambda values: [value.value for value in values],
            ),
            nullable=False,
            server_default=text("'pending'"),
        ),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=sa.func.now()
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
