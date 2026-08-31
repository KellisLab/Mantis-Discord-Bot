from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")

from access_sync.engine import AccessSyncEngine
from access_sync.models import AccessSyncJob, AccessSyncJobStatus


class _Result:
    def __init__(self, value):
        self.value = value

    def first(self):
        return self.value


class AccessSyncQueueInvariantTests(unittest.TestCase):
    def test_pending_unique_index_does_not_cover_running_jobs(self) -> None:
        index = next(
            index
            for index in AccessSyncJob.__table__.indexes
            if index.name == "uq_access_sync_jobs_pending_member_provider"
        )
        self.assertTrue(index.unique)
        self.assertEqual(
            str(index.dialect_options["postgresql"]["where"]),
            "status = 'pending'",
        )

    def test_claim_does_not_run_followup_beside_predecessor(self) -> None:
        candidate = SimpleNamespace(
            uuid=uuid4(),
            member_uuid=uuid4(),
            provider="github",
            status=AccessSyncJobStatus.PENDING,
            attempts=0,
        )
        session = SimpleNamespace()
        session.exec = unittest.mock.Mock(
            side_effect=[_Result(candidate), _Result(uuid4())]
        )

        @contextmanager
        def fake_session():
            yield session

        with patch("access_sync.engine.get_session", fake_session):
            claimed = AccessSyncEngine._claim_job()

        self.assertIsNone(claimed)
        self.assertEqual(candidate.status, AccessSyncJobStatus.PENDING)
        self.assertFalse(hasattr(session, "commit") and session.commit.called)


if __name__ == "__main__":
    unittest.main()
