"""Unit tests for DATABASE_URL scheme normalization."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")

import subprocess
import sys
import unittest
from pathlib import Path

from sqlalchemy import create_engine

from database import normalize_database_url

REPO_ROOT = Path(__file__).resolve().parent.parent
REST = "user:pw@localhost:5432/mantis"


class NormalizeDatabaseUrlTests(unittest.TestCase):
    def test_rewrites_plain_postgres_prefixes(self) -> None:
        for prefix in ("postgres://", "postgresql://", "postgresql+psycopg2://"):
            with self.subTest(prefix=prefix):
                self.assertEqual(
                    normalize_database_url(prefix + REST),
                    "postgresql+psycopg://" + REST,
                )

    def test_leaves_psycopg_url_unchanged(self) -> None:
        url = "postgresql+psycopg://" + REST
        self.assertEqual(normalize_database_url(url), url)

    def test_leaves_other_schemes_unchanged(self) -> None:
        for url in ("sqlite://", "sqlite:///tmp/test.db", "mysql://" + REST, ""):
            with self.subTest(url=url):
                self.assertEqual(normalize_database_url(url), url)

    def test_only_prefix_changes_for_special_password_and_query(self) -> None:
        rest = "admin:p%40ss@w:rd/x%25@db.example.com:5432/mantis?sslmode=require&application_name=bot"
        normalized = normalize_database_url("postgresql://" + rest)

        self.assertTrue(normalized.startswith("postgresql+psycopg://"))
        self.assertEqual(normalized.removeprefix("postgresql+psycopg://"), rest)

    def test_normalized_url_selects_psycopg3_dialect(self) -> None:
        engine = create_engine(normalize_database_url("postgresql://" + REST))
        try:
            self.assertEqual(engine.dialect.driver, "psycopg")
        finally:
            engine.dispose()

    def test_database_module_normalizes_env_url_at_import(self) -> None:
        env = {**os.environ, "DATABASE_URL": "postgresql://" + REST}
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import database; "
                "print(database.engine.dialect.driver, "
                "database.DATABASE_URL.startswith('postgresql+psycopg://'))",
            ],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["psycopg", "True"])


if __name__ == "__main__":
    unittest.main()
