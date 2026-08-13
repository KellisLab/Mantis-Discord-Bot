"""Unit tests for paginating the logical #teams directory."""

from __future__ import annotations

import os
import unittest

import discord

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

from team_discord import (
    ALL_TEAMS_ALLOWED_PERMISSIONS,
    _allow_all_teams_permissions,
    _pack_directory_pages,
)


class TeamDirectoryPaginationTests(unittest.TestCase):
    def test_all_teams_overwrite_grants_read_and_write_permissions(self) -> None:
        overwrite = discord.PermissionOverwrite(view_channel=False)

        self.assertTrue(_allow_all_teams_permissions(overwrite))
        self.assertTrue(
            all(
                getattr(overwrite, permission) is True
                for permission in ALL_TEAMS_ALLOWED_PERMISSIONS
            )
        )
        self.assertFalse(_allow_all_teams_permissions(overwrite))

    def test_large_directory_is_split_without_losing_content(self) -> None:
        blocks = ["A" * 2000, "B" * 2000, "C" * 5000]
        pages = _pack_directory_pages(blocks)

        self.assertGreater(len(pages), 1)
        self.assertTrue(all(len(page) <= 3900 for page in pages))
        rendered = "\n\n".join(pages)
        self.assertEqual(rendered.count("A"), 2000)
        self.assertEqual(rendered.count("B"), 2000)
        self.assertEqual(rendered.count("C"), 5000)


if __name__ == "__main__":
    unittest.main()
