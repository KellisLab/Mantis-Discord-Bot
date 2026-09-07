"""Unit tests for access token issuance and revocation."""

from __future__ import annotations

import os

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GITHUB_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")

import hashlib
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

from members.token_models import AccessToken
from members.token_service import TOKEN_TTL, issue_token, revoke_token


def _session_cm(session: MagicMock):
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=session)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


class IssueTokenTests(unittest.TestCase):
    def test_issue_revokes_existing_active_tokens(self) -> None:
        user_uuid = uuid4()
        existing = AccessToken(
            uuid=uuid4(),
            user_uuid=user_uuid,
            token_hash="old-hash",
            expires_at=datetime.now(timezone.utc),
        )

        session = MagicMock()
        session.exec.return_value.all.return_value = [existing]

        with patch(
            "members.token_service.get_session", return_value=_session_cm(session)
        ):
            issued = issue_token(user_uuid)

        self.assertIsNotNone(existing.revoked_at)
        self.assertIsNotNone(issued.expires_at.tzinfo)
        self.assertGreater(issued.expires_at, datetime.now(timezone.utc))

    def test_issue_stores_only_the_token_hash(self) -> None:
        user_uuid = uuid4()
        session = MagicMock()
        session.exec.return_value.all.return_value = []

        added_rows = []
        session.add.side_effect = added_rows.append

        with patch(
            "members.token_service.get_session", return_value=_session_cm(session)
        ):
            issued = issue_token(user_uuid)

        stored_token = added_rows[0]
        self.assertEqual(
            stored_token.token_hash, hashlib.sha256(issued.token.encode()).hexdigest()
        )
        self.assertNotEqual(stored_token.token_hash, issued.token)

    def test_issued_token_expires_after_fourteen_days(self) -> None:
        user_uuid = uuid4()
        session = MagicMock()
        session.exec.return_value.all.return_value = []

        before = datetime.now(timezone.utc)
        with patch(
            "members.token_service.get_session", return_value=_session_cm(session)
        ):
            issued = issue_token(user_uuid)
        after = datetime.now(timezone.utc)

        self.assertGreaterEqual(issued.expires_at, before + TOKEN_TTL)
        self.assertLessEqual(issued.expires_at, after + TOKEN_TTL)


class RevokeTokenTests(unittest.TestCase):
    def test_revoke_sets_revoked_at_on_active_tokens(self) -> None:
        user_uuid = uuid4()
        active = AccessToken(
            uuid=uuid4(),
            user_uuid=user_uuid,
            token_hash="hash",
            expires_at=datetime.now(timezone.utc),
        )

        session = MagicMock()
        session.exec.return_value.all.return_value = [active]

        with patch(
            "members.token_service.get_session", return_value=_session_cm(session)
        ):
            revoke_token(user_uuid)

        self.assertIsNotNone(active.revoked_at)
        session.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
