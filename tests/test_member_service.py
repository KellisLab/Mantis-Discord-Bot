"""Unit tests for member input validation."""

from __future__ import annotations

import unittest

from member_service import MemberServiceError, normalize_full_name


class FullNameValidationTests(unittest.TestCase):
    def test_accepts_exact_first_last_format(self) -> None:
        self.assertEqual(normalize_full_name("First Last"), "First Last")
        self.assertIsNone(normalize_full_name(None))
        self.assertIsNone(normalize_full_name(""))

    def test_rejects_wrong_capitalization_middle_names_and_extra_spacing(self) -> None:
        invalid_names = (
            "first Last",
            "First last",
            "FIRST LAST",
            "First Middle Last",
            "First",
            " First Last",
            "First  Last",
            "First Last ",
            "First LAsT",
            "First Last-Name",
        )
        for name in invalid_names:
            with self.subTest(name=name), self.assertRaises(MemberServiceError):
                normalize_full_name(name)


if __name__ == "__main__":
    unittest.main()
