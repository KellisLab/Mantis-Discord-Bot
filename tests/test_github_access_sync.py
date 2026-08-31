from __future__ import annotations

import json
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
    ListedGitHubAccount,
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
        team_accounts: dict[str, list[ListedGitHubAccount]] | None = None,
    ):
        self.memberships = memberships or set()
        self.in_org = in_org
        self.team_accounts = team_accounts or {}
        self.calls: list[tuple[str, tuple]] = []

    async def account_by_login(self, login: str):
        return SimpleAccount(42, login)

    async def account_by_id(self, account_id: int):
        return SimpleAccount(account_id, "old-login")

    async def organization_membership(self, org: str, login: str):
        return self.in_org

    async def team_id(self, org: str, slug: str):
        return MANAGED_TEAM_SLUGS.index(slug) + 1

    async def team_membership(self, org: str, slug: str, login: str):
        return "maintainer" if slug in self.memberships else None

    async def create_invitation(self, org: str, account_id: int, team_ids: list[int]):
        self.calls.append(("invite", (org, account_id, team_ids)))

    async def add_team_membership(self, org: str, slug: str, login: str):
        self.calls.append(("add", (org, slug, login)))

    async def remove_team_membership(self, org: str, slug: str, login: str):
        self.calls.append(("remove", (org, slug, login)))

    async def list_team_accounts(self, org: str, slug: str):
        return self.team_accounts.get(slug, [])


class SimpleAccount:
    def __init__(self, account_id: int, login: str):
        self.id = account_id
        self.login = login


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
        invitation = next(call for call in client.calls if call[0] == "invite")
        self.assertEqual(invitation[1][2], [1, 2, 3])

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
        deletes = [call for call in client.calls if call[0] == "remove"]
        self.assertEqual(len(deletes), 3)

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
        client = FakeGitHubClient(
            team_accounts={
                "mantis-cartographers": [
                    ListedGitHubAccount(id=99, login="legacy-user")
                ]
            }
        )
        provider = GitHubAccessProvider(client=client)
        with (
            patch.object(provider, "_load_all_users", return_value=[]),
            patch.object(provider, "_member_lookup", return_value=({}, {})),
            patch.object(provider, "_identities_by_member", return_value={}),
        ):
            dry_run = await provider.bulk_reconcile(dry_run=True)

        self.assertEqual(len(dry_run.actions), 1)
        self.assertEqual(dry_run.actions[0].detail, "reverse sweep")
        self.assertFalse(any(call[0] == "remove" for call in client.calls))

        client.calls.clear()
        with (
            patch.object(provider, "_load_all_users", return_value=[]),
            patch.object(provider, "_member_lookup", return_value=({}, {})),
            patch.object(provider, "_identities_by_member", return_value={}),
        ):
            applied = await provider.bulk_reconcile(dry_run=False)
        self.assertEqual(dry_run.actions, applied.actions)
        self.assertEqual(sum(call[0] == "remove" for call in client.calls), 1)

    async def test_bulk_reconcile_skips_members_already_in_desired_state(self) -> None:
        member_uuid = uuid4()
        user = User(
            id=member_uuid,
            email="settled@example.com",
            github_username="settled-user",
            stage=Stage.CARTOGRAPHER,
        )
        client = FakeGitHubClient(
            team_accounts={
                "mantis-cartographers": [
                    ListedGitHubAccount(id=7, login="settled-user")
                ]
            }
        )
        provider = GitHubAccessProvider(client=client)
        with (
            patch.object(provider, "_load_all_users", return_value=[user]),
            patch.object(
                provider, "_member_lookup", return_value=({}, {"settled-user": user})
            ),
            patch.object(provider, "_identities_by_member", return_value={}),
            patch.object(provider, "reconcile") as reconcile,
        ):
            result = await provider.bulk_reconcile(dry_run=True)

        reconcile.assert_not_called()
        self.assertEqual(result.actions, [])

    async def test_bulk_reconcile_reconciles_members_out_of_desired_state(
        self,
    ) -> None:
        member_uuid = uuid4()
        user = User(
            id=member_uuid,
            email="needs-add@example.com",
            github_username="needs-add-user",
            stage=Stage.CARTOGRAPHER,
        )
        client = FakeGitHubClient(in_org=True)
        provider = GitHubAccessProvider(client=client)
        with (
            patch.object(provider, "_load_all_users", return_value=[user]),
            patch.object(provider, "_member_lookup", return_value=({}, {})),
            patch.object(provider, "_identities_by_member", return_value={}),
            patch.object(provider, "_load_state", return_value=(user, None)),
            patch.object(provider, "_save_identity"),
        ):
            result = await provider.bulk_reconcile(dry_run=True)

        self.assertEqual([action.action for action in result.actions], ["add"])

    async def test_bulk_reconcile_isolates_per_member_errors(self) -> None:
        ok_uuid, failing_uuid = uuid4(), uuid4()
        ok_user = User(
            id=ok_uuid,
            email="ok@example.com",
            github_username="ok-user",
            stage=Stage.CARTOGRAPHER,
        )
        failing_user = User(
            id=failing_uuid,
            email="failing@example.com",
            github_username="failing-user",
            stage=Stage.CARTOGRAPHER,
        )
        client = FakeGitHubClient(in_org=True)
        provider = GitHubAccessProvider(client=client)

        async def fake_reconcile(member_uuid, *, dry_run=False):
            if member_uuid == failing_uuid:
                raise AccessSyncError("boom", retryable=False)
            return await GitHubAccessProvider.reconcile(
                provider, member_uuid, dry_run=dry_run
            )

        with (
            patch.object(
                provider, "_load_all_users", return_value=[ok_user, failing_user]
            ),
            patch.object(provider, "_member_lookup", return_value=({}, {})),
            patch.object(provider, "_identities_by_member", return_value={}),
            patch.object(
                provider,
                "_load_state",
                side_effect=lambda uuid: (
                    ok_user if uuid == ok_uuid else failing_user,
                    None,
                ),
            ),
            patch.object(provider, "_save_identity"),
            patch.object(provider, "reconcile", side_effect=fake_reconcile),
        ):
            result = await provider.bulk_reconcile(dry_run=True)

        actions_by_member = {action.member: action.action for action in result.actions}
        self.assertEqual(actions_by_member["ok@example.com"], "add")
        self.assertEqual(actions_by_member["failing@example.com"], "error")


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
                await client.account_by_login("test-user")
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

    async def test_githubkit_invitation_endpoint_and_body(self) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(201, json={})

        client = GitHubClient("token", transport=httpx.MockTransport(handler))
        await client.create_invitation("KellisLab", 42, [1, 2, 3])

        self.assertEqual(captured[0].url.path, "/orgs/KellisLab/invitations")
        self.assertEqual(
            json.loads(captured[0].content),
            {"invitee_id": 42, "role": "direct_member", "team_ids": [1, 2, 3]},
        )

    async def test_githubkit_lists_team_members_and_invitations(self) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json=[])

        client = GitHubClient("token", transport=httpx.MockTransport(handler))
        accounts = await client.list_team_accounts("KellisLab", "mantis-engineers")

        self.assertEqual(accounts, [])
        self.assertEqual(
            [request.url.path for request in captured],
            [
                "/orgs/KellisLab/teams/mantis-engineers/members",
                "/orgs/KellisLab/teams/mantis-engineers/invitations",
            ],
        )

    async def test_githubkit_requests_do_not_close_shared_transport(self) -> None:
        class CloseAwareTransport(httpx.AsyncBaseTransport):
            def __init__(self) -> None:
                self.closed = False
                self.close_count = 0

            async def handle_async_request(
                self, request: httpx.Request
            ) -> httpx.Response:
                if self.closed:
                    raise RuntimeError("transport is closed")
                return httpx.Response(201, json={})

            async def aclose(self) -> None:
                self.closed = True
                self.close_count += 1

        transport = CloseAwareTransport()
        client = GitHubClient("token", transport=transport)

        await client.create_invitation("KellisLab", 41, [1])
        await client.create_invitation("KellisLab", 42, [1])

        self.assertFalse(transport.closed)
        await client.close()
        self.assertTrue(transport.closed)
        self.assertEqual(transport.close_count, 1)

    async def test_empty_transport_error_includes_exception_type(self) -> None:
        class EmptyError(RuntimeError):
            def __str__(self) -> str:
                return ""

        self.assertEqual(GitHubClient._exception_message(EmptyError()), "EmptyError")

    async def test_transport_error_identifies_failed_operation(self) -> None:
        class FailingTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(
                self, request: httpx.Request
            ) -> httpx.Response:
                raise RuntimeError()

        client = GitHubClient("token", transport=FailingTransport())
        try:
            with self.assertRaisesRegex(
                AccessSyncError,
                "GitHub user lookup for 'test-user' failed: RuntimeError",
            ):
                await client.account_by_login("test-user")
        finally:
            await client.close()


if __name__ == "__main__":
    unittest.main()
