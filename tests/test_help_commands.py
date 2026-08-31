"""Tests for slash-command coverage in the help embeds."""

import unittest

from commands.help_commands import (
    HELP_DEPRECATED_SECTIONS,
    HELP_FORMAT_SECTIONS,
    HELP_SECTIONS,
    _deprecated_embed,
    _formats_embed,
    _help_embed,
)


def _flatten(sections) -> str:
    return "\n".join(line for name, lines in sections for line in (name, *lines))


class HelpCommandTests(unittest.TestCase):
    def test_help_lists_new_command_families(self) -> None:
        text = _flatten(HELP_SECTIONS)
        expected_commands = (
            "/create-profile",
            "/get-info",
            "/member add",
            "/member edit-stage",
            "/member leader",
            "/member journey-mentor",
            "/member kick",
            "/member import",
            "/member import-stages",
            "/member sync-access",
            "/member sync-access-all",
            "/member sync-access-status",
            "/member sync-access-retry",
            "/team create",
            "/team edit",
            "/team add",
            "/team remove",
            "/team set-rank",
            "/team transfer-lead",
            "/team leave",
            "/team close",
            "/close-channel",
            "/download-storage",
        )
        for command in expected_commands:
            with self.subTest(command=command):
                self.assertIn(command, text)

    def test_deprecated_lists_legacy_commands(self) -> None:
        text = _flatten(HELP_DEPRECATED_SECTIONS)
        expected_commands = (
            "/manolis",
            "/m4m",
            "/m4m_mentor",
            "/m4m_find_assignee",
            "/network-test",
            "/test-discord-lookup",
            "/test-member-mapping",
        )
        for command in expected_commands:
            with self.subTest(command=command):
                self.assertIn(command, text)

    def test_help_embeds_stay_within_discord_limits(self) -> None:
        for embed in (_help_embed(), _deprecated_embed(), _formats_embed()):
            self.assertLessEqual(len(embed.fields), 25)
            self.assertTrue(all(len(field.value) <= 1024 for field in embed.fields))
            self.assertLessEqual(len(embed), 6000)

    def test_member_import_documents_exact_categories(self) -> None:
        text = _flatten(HELP_FORMAT_SECTIONS)
        for column in ("stage", "is_leadership", "is_journey_mentor"):
            self.assertIn(f"`{column}`", text)
        for stage in StageValues:
            self.assertIn(f"`{stage}`", text)
        for value in BooleanValues:
            self.assertIn(f"`{value}`", text)


StageValues = (
    "preboarding",
    "onboarding",
    "cartographer",
    "navigator",
    "savant",
    "admiral",
    "developer",
    "engineer",
    "architect",
)
BooleanValues = (
    "true",
    "yes",
    "1",
    "enabled",
    "enable",
    "false",
    "no",
    "0",
    "disabled",
    "disable",
)


if __name__ == "__main__":
    unittest.main()
