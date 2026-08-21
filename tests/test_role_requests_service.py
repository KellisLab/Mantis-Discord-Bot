"""Unit tests for role request validation and resolution logic."""

from __future__ import annotations

import os

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GITHUB_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")

import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from members.models import Stage, User
from members.role_models import RoleRequest, RoleRequestStatus, RoleRequestType
from members.role_service import (
    RoleRequestPermissionError,
    RoleRequestServiceError,
    create_role_request,
    resolve_role_request,
)


def _session_cm(session: MagicMock):
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=session)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


class CreateRoleRequestTests(unittest.TestCase):
    def _session_with_user(self, user: User) -> MagicMock:
        session = MagicMock()
        session.exec.return_value.one_or_none.return_value = user
        session.refresh = MagicMock()
        session.expunge = MagicMock()
        return session

    def test_stage_request_must_target_a_higher_stage(self) -> None:
        user = User(
            id=uuid4(), email="a@example.com", discord_id="1", stage=Stage.NAVIGATOR
        )
        session = self._session_with_user(user)
        with patch(
            "members.role_service.get_session", return_value=_session_cm(session)
        ):
            with self.assertRaises(RoleRequestServiceError):
                create_role_request(
                    "1", RoleRequestType.STAGE, requested_stage=Stage.CARTOGRAPHER
                )

    def test_journey_mentor_request_rejected_if_already_mentor(self) -> None:
        user = User(
            id=uuid4(),
            email="a@example.com",
            discord_id="1",
            is_journey_mentor=True,
        )
        session = self._session_with_user(user)
        with patch(
            "members.role_service.get_session", return_value=_session_cm(session)
        ):
            with self.assertRaises(RoleRequestServiceError):
                create_role_request("1", RoleRequestType.JOURNEY_MENTOR)

    def test_leadership_request_rejected_if_already_leadership(self) -> None:
        user = User(
            id=uuid4(), email="a@example.com", discord_id="1", is_leadership=True
        )
        session = self._session_with_user(user)
        with patch(
            "members.role_service.get_session", return_value=_session_cm(session)
        ):
            with self.assertRaises(RoleRequestServiceError):
                create_role_request("1", RoleRequestType.LEADERSHIP)

    def test_unlinked_discord_account_is_rejected(self) -> None:
        session = self._session_with_user(None)
        with patch(
            "members.role_service.get_session", return_value=_session_cm(session)
        ):
            with self.assertRaises(RoleRequestPermissionError):
                create_role_request("1", RoleRequestType.LEADERSHIP)


class ResolveRoleRequestTests(unittest.TestCase):
    def test_only_leadership_may_resolve(self) -> None:
        requester = User(id=uuid4(), email="a@example.com", discord_id="1")
        resolver = User(id=uuid4(), email="b@example.com", discord_id="2")
        request = RoleRequest(
            uuid=uuid4(),
            requester_uuid=requester.id,
            request_type=RoleRequestType.LEADERSHIP,
            status=RoleRequestStatus.PENDING,
        )

        session = MagicMock()
        session.exec.return_value.one_or_none.side_effect = [request, resolver]
        session.refresh = MagicMock()
        session.expunge = MagicMock()

        with (
            patch(
                "members.role_service.get_session", return_value=_session_cm(session)
            ),
            patch("members.role_service.has_leadership", return_value=False),
        ):
            with self.assertRaises(RoleRequestPermissionError):
                resolve_role_request(request.uuid, "2", True)

    def test_approval_applies_leadership_flag(self) -> None:
        requester = User(id=uuid4(), email="a@example.com", discord_id="1")
        resolver = User(id=uuid4(), email="b@example.com", discord_id="2")
        request = RoleRequest(
            uuid=uuid4(),
            requester_uuid=requester.id,
            request_type=RoleRequestType.LEADERSHIP,
            status=RoleRequestStatus.PENDING,
        )

        session = MagicMock()
        session.exec.return_value.one_or_none.side_effect = [
            request,
            resolver,
            requester,
        ]
        session.refresh = MagicMock()
        session.expunge = MagicMock()

        with (
            patch(
                "members.role_service.get_session", return_value=_session_cm(session)
            ),
            patch("members.role_service.has_leadership", return_value=True),
        ):
            resolve_role_request(request.uuid, "2", True)

        self.assertTrue(requester.is_leadership)
        self.assertEqual(request.status, RoleRequestStatus.APPROVED)


if __name__ == "__main__":
    unittest.main()
