"""Focused tests for the Discord-only MANTIS Agent role exception."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

from utils.user_role_sync import TEAM_ROLE, UserRoleSync


class UserRoleSyncTests(unittest.IsolatedAsyncioTestCase):
    def test_mantis_agent_match_is_exact_and_requires_a_bot(self) -> None:
        matching = SimpleNamespace(bot=True, name="MANTIS Agent", discriminator="5695")
        wrong_discriminator = SimpleNamespace(
            bot=True, name="MANTIS Agent", discriminator="0001"
        )
        human = SimpleNamespace(bot=False, name="MANTIS Agent", discriminator="5695")

        self.assertTrue(UserRoleSync._is_mantis_agent(matching))
        self.assertFalse(UserRoleSync._is_mantis_agent(wrong_discriminator))
        self.assertFalse(UserRoleSync._is_mantis_agent(human))

    async def test_mantis_agent_receives_only_the_managed_team_role(self) -> None:
        agent = SimpleNamespace(
            id=5695,
            bot=True,
            name="MANTIS Agent",
            discriminator="5695",
        )
        guild = SimpleNamespace(get_member=lambda member_id: agent)
        sync = UserRoleSync(SimpleNamespace(guilds=[guild]))
        sync._load_user = lambda discord_id: None
        sync._sync_member_roles = AsyncMock()
        sync._sync_member_nickname = AsyncMock()

        await sync._sync_user(str(agent.id))

        sync._sync_member_roles.assert_awaited_once_with(agent, {TEAM_ROLE})
        sync._sync_member_nickname.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
