"""Tests for slash-command coverage in the help embed."""

import unittest

from commands.help_commands import HELP_SECTIONS, _help_embed


class HelpCommandTests(unittest.TestCase):
    def test_help_lists_new_command_families(self) -> None:
        text = "\n".join(
            line for name, lines in HELP_SECTIONS for line in (name, *lines)
        )
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
            "/team create",
            "/team edit",
            "/team add",
            "/team remove",
            "/team set-rank",
            "/team transfer-lead",
            "/team leave",
            "/team close",
            "/close-channel",
        )
        for command in expected_commands:
            with self.subTest(command=command):
                self.assertIn(command, text)

    def test_help_embed_stays_within_discord_limits(self) -> None:
        embed = _help_embed()

        self.assertLessEqual(len(embed.fields), 25)
        self.assertTrue(all(len(field.value) <= 1024 for field in embed.fields))
        self.assertLessEqual(len(embed), 6000)

    def test_member_import_documents_exact_categories(self) -> None:
        text = "\n".join(
            line for name, lines in HELP_SECTIONS for line in (name, *lines)
        )
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
