"""SQLModel-backed key/value storage for persistent bot data."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, DateTime, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Session, SQLModel, select


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StoredData(SQLModel, table=True):
    """A namespaced JSON value stored under a unique key."""

    __tablename__ = "stored_data"
    __table_args__ = (
        UniqueConstraint(
            "namespace",
            "key",
            name="uq_stored_data_namespace_key",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    namespace: str = Field(max_length=100, index=True)
    key: str = Field(max_length=255)
    value: Any = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False),
    )
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


def get_value(
    session: Session,
    namespace: str,
    key: str,
) -> StoredData | None:
    """Return one stored value, or ``None`` when the key does not exist."""

    statement = select(StoredData).where(
        StoredData.namespace == namespace,
        StoredData.key == key,
    )
    return session.exec(statement).one_or_none()


def set_value(
    session: Session,
    namespace: str,
    key: str,
    value: Any,
) -> StoredData:
    """Create or replace a stored value and commit the change."""

    record = get_value(session, namespace, key)
    if record is None:
        record = StoredData(namespace=namespace, key=key, value=value)
    else:
        record.value = value
        record.updated_at = utc_now()

    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def delete_value(session: Session, namespace: str, key: str) -> bool:
    """Delete a stored value and return whether a row was removed."""

    record = get_value(session, namespace, key)
    if record is None:
        return False

    session.delete(record)
    session.commit()
    return True
