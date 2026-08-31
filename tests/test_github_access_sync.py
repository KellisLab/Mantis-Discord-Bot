from __future__ import annotations

import os
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")
os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

from access_sync.github_provider import (
    MANAGED_TEAM_SLUGS,
    GitHubAccessProvider,
    GitHubClient,
    desired_github_teams,
)
from access_sync.models import AccessSyncIdentity
from access_sync.types import AccessSyncError
from members.models import Stage, User


class FakeGitHubClient:
    def __init__(
        self,
        *,
        memberships: set[str] | None = None,
        in_org: bool = True,
        pages: dict[str, list[dict]] | None = None,
    ):
        self.memberships = memberships or set()
        self.in_org = in_org
        self.page_data = pages or {}
        self.calls: list[tuple[str, str, dict]] = []

    async def request(self, method: str, path: str, *, missing_ok=False, **kwargs):
        self.calls.append((method, path, kwargs))
        if path.startswith("/users/"):
            login = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"id": 42, "login": login})
        if path.startswith("/user/"):
            return httpx.Response(200, json={"id": 41, "login": "old-login"})
        if "/memberships/" in path and method == "GET":
            if "/teams/" in path:
                slug = path.split("/teams/", 1)[1].split("/", 1)[0]
                if slug in self.memberships:
                    return httpx.Response(
                        200, json={"state": "active", "role": "maintainer"}
                    )
                return None
            return httpx.Response(200, json={"state": "active"}) if self.in_org else None
        if path.startswith("/orgs/KellisLab/teams/") and method == "GET":
            slug = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"id": MANAGED_TEAM_SLUGS.index(slug) + 1})
        return httpx.Response(200, json={})

    async def pages(self, path: str):
        return self.page_data.get(path, [])


class GitHubStageMappingTests(unittest.TestCase):
    def test_cumulative_stage_mapping(self) -> None:
        expected = {
            Stage.PREBOARDING: set(),
            Stage.ONBOARDING: set(),
            Stage.CARTOGRAPHER: {MANAGED_TEAM_SLUGS[0]},
            Stage.NAVIGATOR: {MANAGED_TEAM_SLUGS[0]},
            Stage.SAVANT: {MANAGED_TEAM_SLUGS[0]},
            Stage.ADMIRAL: {MANAGED_TEAM_SLUGS[0]},
            Stage.DEVELOPER: set(MANAGED_TEAM_SLUGS[:2]),
            Stage.ENGINEER: set(MANAGED_TEAM_SLUGS),
            Stage.ARCHITECT: set(MANAGED_TEAM_SLUGS),
        }
        for stage, teams in expected.items():
            self.assertEqual(desired_github_teams(stage), teams)


class GitHubProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_outside_org_is_invited_with_all_desired_teams(self) -> None:
        client = FakeGitHubClient(in_org=False)
        provider = GitHubAccessProvider(client=client)
        member_uuid = uuid4()
        user = User(
            id=member_uuid,
            email="engineer@example.com",
            github_username="engineer",
            stage=Stage.ENGINEER,
        )
        with (
            patch.object(provider, "_load_state", return_value=(user, None)),
            patch.object(provider, "_save_identity"),
        ):
            result = await provider.reconcile(member_uuid)

        self.assertEqual([action.action for action in result.actions], ["invite"])
        invitation = next(call for call in client.calls if call[1].endswith("/invitations"))
        self.assertEqual(invitation[2]["json"]["team_ids"], [1, 2, 3])

    async def test_preboarding_removes_only_managed_memberships(self) -> None:
        client = FakeGitHubClient(memberships=set(MANAGED_TEAM_SLUGS))
        provider = GitHubAccessProvider(client=client)
        member_uuid = uuid4()
        user = User(
            id=member_uuid,
            email="member@example.com",
            github_username="member",
            stage=Stage.PREBOARDING,
        )
        with (
            patch.object(provider, "_load_state", return_value=(user, None)),
            patch.object(provider, "_save_identity"),
        ):
            result = await provider.reconcile(member_uuid)

        self.assertEqual(
            {action.target for action in result.actions}, set(MANAGED_TEAM_SLUGS)
        )
        deletes = [call[1] for call in client.calls if call[0] == "DELETE"]
        self.assertEqual(len(deletes), 3)
        self.assertFalse(any(path.endswith("/members/member") for path in deletes))

    async def test_changed_identity_revokes_old_account_before_adding_new(self) -> None:
        client = FakeGitHubClient(memberships={MANAGED_TEAM_SLUGS[0]})
        provider = GitHubAccessProvider(client=client)
        member_uuid = uuid4()
        user = User(
            id=member_uuid,
            email="member@example.com",
            github_username="new-login",
            stage=Stage.CARTOGRAPHER,
        )
        identity = AccessSyncIdentity(
            member_uuid=member_uuid,
            provider="github",
            external_id="41",
            external_login="old-login",
        )
        with (
            patch.object(provider, "_load_state", return_value=(user, identity)),
            patch.object(provider, "_save_identity"),
        ):
            result = await provider.reconcile(member_uuid)

        self.assertEqual(result.actions[0].detail, "old identity")

    async def test_reverse_sweep_removes_unmatched_managed_member(self) -> None:
        members_path = "/orgs/KellisLab/teams/mantis-cartographers/members"
        client = FakeGitHubClient(
            pages={members_path: [{"id": 99, "login": "legacy-user"}]}
        )
        provider = GitHubAccessProvider(client=client)
        with (
            patch.object(provider, "_load_all_users", return_value=[]),
            patch.object(provider, "_member_lookup", return_value=({}, {})),
        ):
            dry_run = await provider.bulk_reconcile(dry_run=True)

        self.assertEqual(len(dry_run.actions), 1)
        self.assertEqual(dry_run.actions[0].detail, "reverse sweep")
        self.assertFalse(any(call[0] == "DELETE" for call in client.calls))

        client.calls.clear()
        with (
            patch.object(provider, "_load_all_users", return_value=[]),
            patch.object(provider, "_member_lookup", return_value=({}, {})),
        ):
            applied = await provider.bulk_reconcile(dry_run=False)
        self.assertEqual(dry_run.actions, applied.actions)
        self.assertEqual(sum(call[0] == "DELETE" for call in client.calls), 1)
        self.assertFalse(any("/orgs/KellisLab/members/" in call[1] for call in client.calls))


class GitHubClientErrorTests(unittest.IsolatedAsyncioTestCase):
    async def _error(self, status: int, headers=None) -> AccessSyncError:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                status, headers=headers, json={"message": "failure"}
            )
        )
        client = GitHubClient("token", transport=transport)
        try:
            with self.assertRaises(AccessSyncError) as raised:
                await client.request("GET", "/test")
            return raised.exception
        finally:
            await client.close()

    async def test_terminal_statuses(self) -> None:
        for status in (400, 401, 403, 404, 422):
            self.assertFalse((await self._error(status)).retryable)

    async def test_retryable_statuses(self) -> None:
        for status in (408, 409, 429, 500, 503):
            self.assertTrue((await self._error(status)).retryable)

    async def test_rate_limited_403_uses_retry_after(self) -> None:
        error = await self._error(
            403, {"x-ratelimit-remaining": "0", "retry-after": "17"}
        )
        self.assertTrue(error.retryable)
        self.assertEqual(error.retry_after, 17)


if __name__ == "__main__":
    unittest.main()
