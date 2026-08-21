"""Unit tests for paginating the logical #teams directory."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

from teams.discord import (
    DirectoryButtonTeam,
    _pack_directory_entries,
    _pack_directory_pages,
)


class TeamDirectoryPaginationTests(unittest.TestCase):
    def test_large_directory_is_split_without_losing_content(self) -> None:
        blocks = ["A" * 2000, "B" * 2000, "C" * 5000]
        pages = _pack_directory_pages(blocks)

        self.assertGreater(len(pages), 1)
        self.assertTrue(all(len(page) <= 3900 for page in pages))
        rendered = "\n\n".join(pages)
        self.assertEqual(rendered.count("A"), 2000)
        self.assertEqual(rendered.count("B"), 2000)
        self.assertEqual(rendered.count("C"), 5000)

    def test_directory_buttons_are_packed_across_many_teams(self) -> None:
        entries = [
            (
                f"Team {index}\nDescription",
                DirectoryButtonTeam(index=index, uuid=str(index), name=f"Team {index}"),
            )
            for index in range(1, 61)
        ]

        pages = _pack_directory_entries(entries)

        self.assertEqual(len(pages), 3)
        self.assertTrue(all(len(buttons) <= 25 for _, buttons in pages))
        rendered_buttons = [button.uuid for _, buttons in pages for button in buttons]
        self.assertEqual(rendered_buttons, [str(index) for index in range(1, 61)])


if __name__ == "__main__":
    unittest.main()
