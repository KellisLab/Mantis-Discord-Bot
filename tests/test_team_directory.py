"""Unit tests for paginating the logical #teams directory."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

from teams.discord import _pack_directory_pages


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


if __name__ == "__main__":
    unittest.main()
