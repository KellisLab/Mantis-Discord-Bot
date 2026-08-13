"""Unit tests for member input validation."""

from __future__ import annotations

import unittest

from members.service import MemberServiceError, normalize_full_name


class FullNameValidationTests(unittest.TestCase):
    def test_accepts_exact_first_last_format(self) -> None:
        self.assertEqual(normalize_full_name("First Last"), "First Last")
        self.assertEqual(normalize_full_name("McDonald DeMarco"), "McDonald DeMarco")
        self.assertEqual(normalize_full_name("FIRST LAST"), "FIRST LAST")
        self.assertEqual(normalize_full_name("First LAsT"), "First LAsT")
        self.assertEqual(
            normalize_full_name("Anne-Marie O'Neill"), "Anne-Marie O'Neill"
        )
        self.assertEqual(
            normalize_full_name("D’Arcy Smith-Jones"), "D’Arcy Smith-Jones"
        )
        self.assertEqual(normalize_full_name("Thomas de Chillaz"), "Thomas de Chillaz")
        self.assertEqual(normalize_full_name("Juan de la Cruz"), "Juan de la Cruz")
        self.assertEqual(
            normalize_full_name("Gabriel García Márquez"), "Gabriel García Márquez"
        )
        self.assertIsNone(normalize_full_name(None))
        self.assertIsNone(normalize_full_name(""))

    def test_rejects_lowercase_initials_bad_particles_and_spacing(self) -> None:
        invalid_names = (
            "first Last",
            "First last",
            "First",
            " First Last",
            "First  Last",
            "First Last ",
            "Thomas unknown Chillaz",
            "First -Last",
            "First Last-",
            "First O''Neill",
            "First Last_Name",
        )
        for name in invalid_names:
            with self.subTest(name=name), self.assertRaises(MemberServiceError):
                normalize_full_name(name)


if __name__ == "__main__":
    unittest.main()
