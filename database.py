"""Database engine and session management for the bot."""

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlmodel import Session, create_engine


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://mantis:mantis@localhost:5432/mantis",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


@contextmanager
def get_session() -> Iterator[Session]:
    """Yield a SQLModel session and always close it after use."""

    with Session(engine) as session:
        yield session
