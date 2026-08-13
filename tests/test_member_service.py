"""Unit tests for member input validation."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from members.models import Stage
from members.service import (
    MemberServiceError,
    import_members,
    normalize_full_name,
)


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


class MemberImportTests(unittest.TestCase):
    @patch("members.service.add_member")
    def test_import_passes_stage_and_role_flags_to_new_profile(self, add_member) -> None:
        result = import_members(
            [
                {
                    "email": "leader@example.com",
                    "full_name": "Test Leader",
                    "github_username": "leader",
                    "whatsapp": "+1 202 555 0100",
                    "stage": "ENGINEER",
                    "is_leadership": "yes",
                    "is_journey_mentor": "enabled",
                }
            ]
        )

        self.assertEqual((result.created, result.skipped, result.errors), (1, 0, 0))
        add_member.assert_called_once_with(
            email="leader@example.com",
            full_name="Test Leader",
            github_username="leader",
            whatsapp_number="+1 202 555 0100",
            stage=Stage.ENGINEER,
            is_leadership=True,
            is_journey_mentor=True,
            flexible_phone_format=True,
        )

    @patch("members.service.add_member")
    def test_import_defaults_blank_role_flags_to_false(self, add_member) -> None:
        result = import_members([{"email": "member@example.com"}])

        self.assertEqual((result.created, result.skipped, result.errors), (1, 0, 0))
        self.assertFalse(add_member.call_args.kwargs["is_leadership"])
        self.assertFalse(add_member.call_args.kwargs["is_journey_mentor"])

    @patch("members.service.add_member")
    def test_import_rejects_invalid_role_category(self, add_member) -> None:
        result = import_members(
            [{"email": "member@example.com", "is_leadership": "sometimes"}]
        )

        self.assertEqual((result.created, result.skipped, result.errors), (0, 0, 1))
        add_member.assert_not_called()


if __name__ == "__main__":
    unittest.main()
