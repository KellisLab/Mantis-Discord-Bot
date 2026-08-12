"""User progression and special-role data models."""

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, Text, func
from sqlalchemy import Enum as SQLAlchemyEnum
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Stage(str, Enum):
    PREBOARDING = "preboarding"
    ONBOARDING = "onboarding"
    CARTOGRAPHER = "cartographer"
    NAVIGATOR = "navigator"
    SAVANT = "savant"
    ADMIRAL = "admiral"
    DEVELOPER = "developer"
    ENGINEER = "engineer"
    ARCHITECT = "architect"


class User(SQLModel, table=True):
    """A member's progression stage and independent special roles."""

    __tablename__ = "users"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    full_name: str = Field(sa_column=Column(Text, nullable=False))
    discord_id: str = Field(
        sa_column=Column(Text, nullable=False, unique=True, index=True),
    )
    github_username: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True, unique=True, index=True),
    )
    email: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True, unique=True, index=True),
    )
    whatsapp_number: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    stage: Stage = Field(
        sa_column=Column(
            SQLAlchemyEnum(
                Stage,
                name="user_stage",
                values_callable=lambda stages: [stage.value for stage in stages],
            ),
            nullable=False,
        ),
    )
    is_journey_mentor: bool = Field(default=False)
    is_leadership: bool = Field(default=False)
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
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
