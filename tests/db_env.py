"""Test database environment helpers.

Production always lives in DATABASE_URL. Tests that need real writes must route
through TEAM_TEST_DATABASE_URL so an RDS URL is never used accidentally.
"""

from __future__ import annotations

import os

LOCAL_TEAM_TEST_DATABASE_URL = (
    "postgresql+psycopg://mantis:mantis@localhost:5432/mantis"
)


def _looks_like_prod(url: str) -> bool:
    normalized = url.casefold()
    return "rds.amazonaws.com" in normalized or "prod" in normalized


def use_team_test_database() -> str:
    database_url = os.getenv("TEAM_TEST_DATABASE_URL", LOCAL_TEAM_TEST_DATABASE_URL)
    if _looks_like_prod(database_url):
        raise RuntimeError(
            "TEAM_TEST_DATABASE_URL must point to the local team test database, "
            "not production."
        )
    os.environ["DATABASE_URL"] = database_url
    return database_url
