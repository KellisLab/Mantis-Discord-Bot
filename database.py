"""Database engine and session management for the bot."""

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlmodel import Session, create_engine

PSYCOPG_URL_PREFIX = "postgresql+psycopg://"
_REWRITTEN_URL_PREFIXES = ("postgres://", "postgresql://", "postgresql+psycopg2://")


def normalize_database_url(url: str) -> str:
    """Point plain Postgres URLs at the installed psycopg 3 driver.

    Only the scheme prefix is swapped; the rest of the URL (credentials, host,
    query params) is kept byte-for-byte so escaped passwords survive.
    """

    for prefix in _REWRITTEN_URL_PREFIXES:
        if url.startswith(prefix):
            return PSYCOPG_URL_PREFIX + url[len(prefix) :]
    return url


DATABASE_URL = normalize_database_url(os.getenv("DATABASE_URL", ""))

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
