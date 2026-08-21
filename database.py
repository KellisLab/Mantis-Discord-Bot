"""Database engine and session management for the bot."""

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlmodel import Session, create_engine

DATABASE_URL = os.getenv("DATABASE_URL", "")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL must be set. Add it to your environment or local .env file."
    )

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,
)


@contextmanager
def get_session() -> Iterator[Session]:
    """Yield a SQLModel session and always close it after use."""

    with Session(engine) as session:
        yield session
