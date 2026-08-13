"""PostgreSQL integration tests for transactional team behavior."""

from __future__ import annotations

import os
import unittest

from sqlmodel import select

TEAM_TEST_DATABASE_URL = os.getenv("TEAM_TEST_DATABASE_URL")
if TEAM_TEST_DATABASE_URL:
    os.environ["DATABASE_URL"] = TEAM_TEST_DATABASE_URL

from database import get_session
from members.models import Stage, User
from members.service import import_member_roles
from teams.models import (
    CloseAttempt,
    CloseAttemptStatus,
    JoinRequest,
    JoinRequestStatus,
    TeamStatus,
)
from teams.service import (
    TeamConflictError,
    TeamPermissionError,
    add_team_member,
    begin_close_vote,
    cancel_close_attempts_by_message_ids,
    cast_close_vote,
    create_join_request,
    create_team,
    finish_team_close,
    get_team,
    get_team_details,
    join_team_as_leadership,
    mark_team_orphaned,
    resolve_join_request,
    set_close_vote_message_id,
    set_team_channel_id,
    transfer_team_lead,
)


@unittest.skipUnless(
    TEAM_TEST_DATABASE_URL,
    "TEAM_TEST_DATABASE_URL must point to a disposable migrated PostgreSQL database",
)
class TeamServiceIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.users = {
            "lead": User(
                email="team-test-lead@example.invalid",
                full_name="Test Lead",
                discord_id="910000000001",
                is_leadership=True,
            ),
            "colead": User(
                email="team-test-colead@example.invalid",
                full_name="Test Co-Lead",
                discord_id="910000000002",
            ),
            "engineer": User(
                email="team-test-engineer@example.invalid",
                full_name="Test Engineer",
                discord_id="910000000003",
            ),
            "developer": User(
                email="team-test-developer@example.invalid",
                full_name="Test Developer",
                discord_id="910000000004",
            ),
            "requester": User(
                email="team-test-requester@example.invalid",
                full_name="Test Requester",
                discord_id="910000000005",
            ),
            "other_lead": User(
                email="team-test-other-lead@example.invalid",
                full_name="Other Leader",
                discord_id="910000000006",
                is_leadership=True,
            ),
            "role_import": User(
                email="team-test-role-import@example.invalid",
                full_name="Role Import",
                discord_id="910000000007",
            ),
        }
        with get_session() as session:
            session.add_all(cls.users.values())
            session.commit()
            for user in cls.users.values():
                session.refresh(user)
                session.expunge(user)

    def test_permissions_requests_transfer_and_close_quorum(self) -> None:
        team = create_team(
            "Atlas",
            "Integration test team",
            None,
            self.users["lead"].discord_id,
        )
        self.assertIsNone(team.discord_channel_id)
        team = set_team_channel_id(team.uuid, "920000000001")
        add_team_member(
            team.uuid,
            self.users["lead"].discord_id,
            self.users["colead"].email,
            2,
        )
        add_team_member(
            team.uuid,
            self.users["lead"].discord_id,
            self.users["engineer"].email,
            3,
        )
        add_team_member(
            team.uuid,
            self.users["colead"].discord_id,
            self.users["developer"].email,
            4,
        )

        with self.assertRaises(TeamPermissionError):
            add_team_member(
                team.uuid,
                self.users["colead"].discord_id,
                self.users["requester"].email,
                2,
            )
        with self.assertRaises(TeamConflictError):
            create_team(
                "atlas",
                None,
                "920000000002",
                self.users["lead"].discord_id,
            )

        request = create_join_request(team.uuid, self.users["requester"].discord_id)
        with self.assertRaises(TeamConflictError):
            create_join_request(team.uuid, self.users["requester"].discord_id)
        resolved = resolve_join_request(
            request.request.uuid, self.users["colead"].discord_id, True
        )
        self.assertEqual(resolved.request.status, JoinRequestStatus.APPROVED)
        with self.assertRaises(TeamConflictError):
            resolve_join_request(
                request.request.uuid, self.users["colead"].discord_id, False
            )

        attempt = begin_close_vote(team.uuid, self.users["developer"].discord_id)
        set_close_vote_message_id(attempt.uuid, "930000000001")
        first_vote = cast_close_vote(attempt.uuid, self.users["colead"].discord_id)
        self.assertFalse(first_vote.quorum)
        self.assertFalse(
            cast_close_vote(attempt.uuid, self.users["colead"].discord_id).accepted
        )
        second_vote = cast_close_vote(attempt.uuid, self.users["engineer"].discord_id)
        self.assertTrue(second_vote.quorum)
        self.assertEqual(get_team(team.uuid).status, TeamStatus.CLOSING)

        finish_team_close(team.uuid, attempt.uuid, success=False)
        self.assertEqual(get_team(team.uuid).status, TeamStatus.ACTIVE)
        with get_session() as session:
            failed_attempt = session.get(CloseAttempt, attempt.uuid)
            self.assertEqual(failed_attempt.status, CloseAttemptStatus.CANCELLED)

        later_attempt = begin_close_vote(team.uuid, self.users["developer"].discord_id)
        set_close_vote_message_id(later_attempt.uuid, "930000000002")
        later_vote = cast_close_vote(
            later_attempt.uuid, self.users["colead"].discord_id
        )
        self.assertTrue(later_vote.accepted)
        self.assertFalse(later_vote.quorum)
        self.assertEqual(cancel_close_attempts_by_message_ids(("930000000002",)), 1)
        replacement_attempt = begin_close_vote(
            team.uuid, self.users["developer"].discord_id
        )
        self.assertNotEqual(replacement_attempt.uuid, later_attempt.uuid)
        self.assertEqual(cancel_close_attempts_by_message_ids(()), 0)
        self.assertEqual(cancel_close_attempts_by_message_ids(("unknown",)), 0)

        transfer_team_lead(
            team.uuid,
            self.users["lead"].discord_id,
            self.users["colead"].email,
        )
        ranks = {
            member.uuid: member.rank for member in get_team_details(team.uuid).members
        }
        self.assertEqual(ranks[self.users["lead"].id], 2)
        self.assertEqual(ranks[self.users["colead"].id], 1)
        self.assertEqual(sum(rank == 1 for rank in ranks.values()), 1)

    def test_missing_channel_preserves_team_history(self) -> None:
        team = create_team(
            "Orphan Test",
            None,
            "920000000099",
            self.users["lead"].discord_id,
        )
        mark_team_orphaned(team.uuid)
        preserved = get_team_details(team.uuid)
        self.assertEqual(preserved.team.status, TeamStatus.ORPHANED)
        self.assertEqual(len(preserved.members), 1)
        self.assertEqual(preserved.members[0].uuid, self.users["lead"].id)

    def test_leadership_reaction_bypasses_join_request(self) -> None:
        team = create_team(
            "Leadership Join Test",
            None,
            "920000000098",
            self.users["other_lead"].discord_id,
        )

        join_team_as_leadership(team.uuid, self.users["lead"].discord_id)
        members = {
            member.uuid: member.rank for member in get_team_details(team.uuid).members
        }
        self.assertEqual(members[self.users["lead"].id], 4)
        with get_session() as session:
            pending_request = session.exec(
                select(JoinRequest).where(
                    JoinRequest.team_uuid == team.uuid,
                    JoinRequest.member_uuid == self.users["lead"].id,
                )
            ).one_or_none()
            self.assertIsNone(pending_request)

        with self.assertRaises(TeamConflictError):
            join_team_as_leadership(team.uuid, self.users["lead"].discord_id)
        with self.assertRaises(TeamPermissionError):
            join_team_as_leadership(team.uuid, self.users["developer"].discord_id)

    def test_bulk_role_import_sets_values_and_preserves_blanks(self) -> None:
        result = import_member_roles(
            [
                {
                    "identifier": self.users["role_import"].email,
                    "stage": "engineer",
                    "leadership": "yes",
                    "journey_mentor": "true",
                }
            ]
        )
        self.assertEqual((result.updated, result.errors), (1, 0))
        self.assertEqual(result.discord_ids, (self.users["role_import"].discord_id,))

        second = import_member_roles(
            [
                {
                    "identifier": self.users["role_import"].email,
                    "stage": "",
                    "leadership": "false",
                    "journey_mentor": "",
                },
                {
                    "identifier": self.users["role_import"].email,
                    "stage": "",
                    "leadership": "maybe",
                    "journey_mentor": "",
                },
            ]
        )
        self.assertEqual((second.updated, second.errors), (1, 1))
        with get_session() as session:
            member = session.get(User, self.users["role_import"].id)
            self.assertEqual(member.stage, Stage.ENGINEER)
            self.assertFalse(member.is_leadership)
            self.assertTrue(member.is_journey_mentor)


if __name__ == "__main__":
    unittest.main()
