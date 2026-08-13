"""Focused tests for the Discord-only MANTIS Agent role exception."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

from utils.user_role_sync import MANTIS_AGENT_APP_ID, TEAM_ROLE, UserRoleSync


class UserRoleSyncTests(unittest.IsolatedAsyncioTestCase):
    def test_mantis_agent_match_requires_bot_and_correct_app_id(self) -> None:
        matching = SimpleNamespace(bot=True, id=MANTIS_AGENT_APP_ID)
        wrong_id = SimpleNamespace(bot=True, id=999999999999999999)
        human = SimpleNamespace(bot=False, id=MANTIS_AGENT_APP_ID)

        self.assertTrue(UserRoleSync._is_mantis_agent(matching))
        self.assertFalse(UserRoleSync._is_mantis_agent(wrong_id))
        self.assertFalse(UserRoleSync._is_mantis_agent(human))

    async def test_mantis_agent_receives_only_the_managed_team_role(self) -> None:
        agent = SimpleNamespace(
            id=MANTIS_AGENT_APP_ID,
            bot=True,
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
