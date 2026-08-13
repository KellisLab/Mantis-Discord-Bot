"""Tests for the Leadership storage export."""

from __future__ import annotations

import json
import unittest
import zipfile
from unittest.mock import MagicMock, patch
from uuid import uuid4

from members.models import Stage, User
from slash_commands.download_storage import build_storage_export, download_storage
from teams.models import Team, TeamMembership, TeamStatus


class DownloadStorageTests(unittest.TestCase):
    @patch("slash_commands.download_storage.get_session")
    def test_export_contains_users_and_complete_team_storage(self, get_session) -> None:
        member_uuid = uuid4()
        team_uuid = uuid4()
        user = User(
            id=member_uuid,
            email="leader@example.com",
            stage=Stage.ENGINEER,
            is_leadership=True,
        )
        team = Team(uuid=team_uuid, name="Atlas", status=TeamStatus.ACTIVE)
        membership = TeamMembership(
            team_uuid=team_uuid,
            member_uuid=member_uuid,
            rank=1,
        )

        session = MagicMock()
        get_session.return_value.__enter__.return_value = session
        result_sets = ([user], [team], [membership], [], [], [])
        session.exec.side_effect = [
            MagicMock(all=MagicMock(return_value=rows)) for rows in result_sets
        ]

        archive = build_storage_export()
        self.addCleanup(archive.close)
        with zipfile.ZipFile(archive) as zip_file:
            self.assertEqual(
                set(zip_file.namelist()),
                {"manifest.json", "users.json", "teams.json"},
            )
            manifest = json.loads(zip_file.read("manifest.json"))
            users = json.loads(zip_file.read("users.json"))
            teams = json.loads(zip_file.read("teams.json"))

        self.assertEqual(manifest["files"]["users.json"]["users"], 1)
        self.assertEqual(users["users"][0]["id"], str(member_uuid))
        self.assertEqual(users["users"][0]["stage"], "engineer")
        self.assertTrue(users["users"][0]["is_leadership"])
        self.assertEqual(teams["teams"][0]["uuid"], str(team_uuid))
        self.assertEqual(teams["team_memberships"][0]["rank"], 1)
        self.assertEqual(teams["team_join_requests"], [])
        self.assertEqual(teams["team_close_attempts"], [])
        self.assertEqual(teams["team_close_votes"], [])

    def test_command_is_guild_only_and_checked(self) -> None:
        self.assertTrue(download_storage.guild_only)
        self.assertEqual(len(download_storage.checks), 1)


if __name__ == "__main__":
    unittest.main()
