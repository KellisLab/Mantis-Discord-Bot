"""One-way synchronization of Mantis stages into managed GitHub teams."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar
from uuid import UUID

import httpx
from githubkit import GitHub
from githubkit.exception import (
    GitHubException,
    RateLimitExceeded,
    RequestError,
    RequestFailed,
)
from sqlmodel import select

from access_sync.models import AccessSyncIdentity
from access_sync.types import AccessSyncError, SyncAction, SyncResult
from config import GITHUB_ORG_NAME, GITHUB_TOKEN
from database import get_session
from members.models import Stage, User

MANAGED_TEAM_SLUGS = (
    "mantis-cartographers",
    "mantis-developers",
    "mantis-engineers",
)


def desired_github_teams(stage: Stage) -> set[str]:
    if stage in {Stage.PREBOARDING, Stage.ONBOARDING}:
        return set()
    teams = {MANAGED_TEAM_SLUGS[0]}
    if stage in {Stage.DEVELOPER, Stage.ENGINEER, Stage.ARCHITECT}:
        teams.add(MANAGED_TEAM_SLUGS[1])
    if stage in {Stage.ENGINEER, Stage.ARCHITECT}:
        teams.add(MANAGED_TEAM_SLUGS[2])
    return teams


@dataclass(frozen=True)
class GitHubAccount:
    id: int
    login: str


@dataclass(frozen=True)
class ListedGitHubAccount:
    id: int | None
    login: str


T = TypeVar("T")


class GitHubClient:
    """Typed GitHubKit adapter with access-sync-specific error policy."""

    def __init__(
        self,
        token: str | None = GITHUB_TOKEN,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.github = GitHub(
            token,
            timeout=30,
            async_transport=transport,
            auto_retry=False,
            http_cache=False,
        )

    async def close(self) -> None:
        # GitHubKit opens and closes its async client around each request.
        return None

    async def _call(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        missing_ok: bool = False,
    ) -> T | None:
        try:
            return await operation()
        except RateLimitExceeded as error:
            raise AccessSyncError(
                self._failed_message(error),
                retryable=True,
                retry_after=max(0, error.retry_after.total_seconds()),
            ) from error
        except RequestFailed as error:
            response = error.response.raw_response
            if response.status_code == 404 and missing_ok:
                return None
            raise self._request_failed_error(error) from error
        except RequestError as error:
            retryable = isinstance(
                error.exc, (httpx.TimeoutException, httpx.NetworkError)
            )
            raise AccessSyncError(str(error.exc), retryable=retryable) from error
        except GitHubException as error:
            raise AccessSyncError(str(error), retryable=False) from error

    async def account_by_login(self, login: str) -> GitHubAccount:
        response = await self._call(
            lambda: self.github.rest.users.async_get_by_username(login)
        )
        assert response is not None
        user = response.parsed_data
        return GitHubAccount(id=user.id, login=user.login)

    async def account_by_id(self, account_id: int) -> GitHubAccount:
        response = await self._call(
            lambda: self.github.rest.users.async_get_by_id(account_id)
        )
        assert response is not None
        user = response.parsed_data
        return GitHubAccount(id=user.id, login=user.login)

    async def organization_membership(self, org: str, login: str) -> bool:
        response = await self._call(
            lambda: self.github.rest.orgs.async_get_membership_for_user(org, login),
            missing_ok=True,
        )
        return response is not None

    async def team_id(self, org: str, slug: str) -> int:
        response = await self._call(
            lambda: self.github.rest.teams.async_get_by_name(org, slug)
        )
        assert response is not None
        return response.parsed_data.id

    async def team_membership(self, org: str, slug: str, login: str) -> str | None:
        response = await self._call(
            lambda: self.github.rest.teams.async_get_membership_for_user_in_org(
                org, slug, login
            ),
            missing_ok=True,
        )
        return None if response is None else response.parsed_data.role

    async def create_invitation(
        self, org: str, account_id: int, team_ids: list[int]
    ) -> None:
        await self._call(
            lambda: self.github.rest.orgs.async_create_invitation(
                org,
                data={
                    "invitee_id": account_id,
                    "role": "direct_member",
                    "team_ids": team_ids,
                },
            )
        )

    async def add_team_membership(self, org: str, slug: str, login: str) -> None:
        await self._call(
            lambda: (
                self.github.rest.teams.async_add_or_update_membership_for_user_in_org(
                    org, slug, login, data={"role": "member"}
                )
            )
        )

    async def remove_team_membership(self, org: str, slug: str, login: str) -> None:
        await self._call(
            lambda: self.github.rest.teams.async_remove_membership_for_user_in_org(
                org, slug, login
            ),
            missing_ok=True,
        )

    async def list_team_accounts(
        self, org: str, slug: str
    ) -> list[ListedGitHubAccount]:
        try:
            members = [
                ListedGitHubAccount(id=member.id, login=member.login)
                async for member in self.github.rest.paginate(
                    self.github.rest.teams.async_list_members_in_org,
                    org,
                    slug,
                    role="all",
                    per_page=100,
                )
            ]
            invitations = [
                ListedGitHubAccount(id=None, login=invitation.login)
                async for invitation in self.github.rest.paginate(
                    self.github.rest.teams.async_list_pending_invitations_in_org,
                    org,
                    slug,
                    per_page=100,
                )
                if invitation.login
            ]
            return [*members, *invitations]
        except RateLimitExceeded as error:
            raise AccessSyncError(
                self._failed_message(error),
                retryable=True,
                retry_after=max(0, error.retry_after.total_seconds()),
            ) from error
        except RequestFailed as error:
            raise self._request_failed_error(error) from error
        except RequestError as error:
            retryable = isinstance(
                error.exc, (httpx.TimeoutException, httpx.NetworkError)
            )
            raise AccessSyncError(str(error.exc), retryable=retryable) from error
        except GitHubException as error:
            raise AccessSyncError(str(error), retryable=False) from error

    @staticmethod
    def _message(response: httpx.Response) -> str:
        try:
            return str(response.json().get("message", response.text))
        except ValueError:
            return response.text[:500]

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        value = response.headers.get("retry-after")
        if value:
            try:
                return max(0, float(value))
            except ValueError:
                pass
        reset = response.headers.get("x-ratelimit-reset")
        if reset:
            try:
                return max(0, float(reset) - time.time())
            except ValueError:
                pass
        return None

    @classmethod
    def _failed_message(cls, error: RequestFailed) -> str:
        response = error.response.raw_response
        return (
            f"GitHub {error.request.method} {error.request.url.path} returned "
            f"{response.status_code}: {cls._message(response)}"
        )

    @classmethod
    def _request_failed_error(cls, error: RequestFailed) -> AccessSyncError:
        response = error.response.raw_response
        retry_after = cls._retry_after(response)
        rate_limited = response.status_code == 429 or (
            response.status_code == 403
            and (
                response.headers.get("x-ratelimit-remaining") == "0"
                or "rate limit" in response.text.casefold()
                or "retry-after" in response.headers
            )
        )
        retryable = (
            rate_limited
            or response.status_code in {408, 409}
            or response.status_code >= 500
        )
        return AccessSyncError(
            cls._failed_message(error),
            retryable=retryable,
            retry_after=retry_after if retryable else None,
        )


class GitHubAccessProvider:
    name = "github"

    def __init__(
        self,
        client: GitHubClient | None = None,
        *,
        organization: str = GITHUB_ORG_NAME,
    ) -> None:
        self.client = client or GitHubClient()
        self.organization = organization
        self._team_ids: dict[str, int] = {}

    async def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close is not None:
            await close()

    async def reconcile(
        self, member_uuid: UUID, *, dry_run: bool = False
    ) -> SyncResult:
        # The job contains only the UUID. Canonical state is deliberately read now.
        user, identity = await asyncio.to_thread(self._load_state, member_uuid)
        if user is None:
            return SyncResult()

        actions: list[SyncAction] = []
        current_account: GitHubAccount | None = None
        if user.github_username:
            current_account = await self._account_by_login(user.github_username)

        if identity is not None and (
            current_account is None or str(current_account.id) != identity.external_id
        ):
            old_account = await self._account_by_id(identity.external_id)
            actions.extend(
                await self._remove_all_managed(
                    old_account, member=user.email, dry_run=dry_run
                )
            )

        if current_account is None:
            if not dry_run:
                await asyncio.to_thread(self._delete_identity, member_uuid)
            return SyncResult(actions)

        desired = desired_github_teams(user.stage)
        current = await self._current_managed_memberships(current_account.login)
        missing = desired - current.keys()

        if missing:
            org_membership = await self.client.organization_membership(
                self.organization, current_account.login
            )
            if not org_membership:
                team_ids = [await self._team_id(slug) for slug in sorted(desired)]
                actions.append(
                    SyncAction(
                        self.name,
                        user.email,
                        "invite",
                        self.organization,
                        ",".join(sorted(desired)),
                    )
                )
                if not dry_run:
                    await self.client.create_invitation(
                        self.organization, current_account.id, team_ids
                    )
                # The invitation already contains every desired team.
                missing = set()

        for slug in sorted(missing):
            actions.append(SyncAction(self.name, user.email, "add", slug))
            if not dry_run:
                await self.client.add_team_membership(
                    self.organization, slug, current_account.login
                )

        for slug in sorted(current.keys() - desired):
            actions.append(SyncAction(self.name, user.email, "remove", slug))
            if not dry_run:
                await self.client.remove_team_membership(
                    self.organization, slug, current_account.login
                )

        if not dry_run:
            await asyncio.to_thread(self._save_identity, member_uuid, current_account)
        return SyncResult(actions)

    async def bulk_reconcile(self, *, dry_run: bool) -> SyncResult:
        users = await asyncio.to_thread(self._load_all_users)
        actions: list[SyncAction] = []
        for user in users:
            try:
                result = await self.reconcile(user.id, dry_run=dry_run)
                actions.extend(result.actions)
            except AccessSyncError as error:
                actions.append(
                    SyncAction(self.name, user.email, "error", "github", str(error))
                )

        # Reverse sweep catches legacy/unmatched GitHub members that no member
        # row change could enqueue. Active members and pending invitees count.
        identities, usernames = await asyncio.to_thread(self._member_lookup)
        seen: set[tuple[str, str, str]] = {
            (action.member.casefold(), action.action, action.target)
            for action in actions
        }
        for slug in MANAGED_TEAM_SLUGS:
            github_accounts = await self.client.list_team_accounts(
                self.organization, slug
            )
            for account in github_accounts:
                login = account.login
                account_id = account.id
                user = identities.get(str(account_id)) or usernames.get(
                    login.casefold()
                )
                authorized = user is not None and slug in desired_github_teams(
                    user.stage
                )
                key = ((user.email if user else login).casefold(), "remove", slug)
                if authorized or key in seen:
                    continue
                seen.add(key)
                actions.append(
                    SyncAction(
                        self.name,
                        user.email if user else login,
                        "remove",
                        slug,
                        "reverse sweep",
                    )
                )
                if not dry_run:
                    await self.client.remove_team_membership(
                        self.organization, slug, login
                    )
        return SyncResult(actions)

    async def validate_configuration(self) -> None:
        for slug in MANAGED_TEAM_SLUGS:
            await self._team_id(slug)

    async def _account_by_login(self, login: str) -> GitHubAccount:
        return await self.client.account_by_login(login)

    async def _account_by_id(self, account_id: str) -> GitHubAccount:
        return await self.client.account_by_id(int(account_id))

    async def _team_id(self, slug: str) -> int:
        if slug not in self._team_ids:
            self._team_ids[slug] = await self.client.team_id(self.organization, slug)
        return self._team_ids[slug]

    async def _current_managed_memberships(self, login: str) -> dict[str, str]:
        memberships: dict[str, str] = {}
        for slug in MANAGED_TEAM_SLUGS:
            role = await self.client.team_membership(self.organization, slug, login)
            if role is not None:
                memberships[slug] = role
        return memberships

    async def _remove_all_managed(
        self, account: GitHubAccount, *, member: str, dry_run: bool
    ) -> list[SyncAction]:
        current = await self._current_managed_memberships(account.login)
        actions: list[SyncAction] = []
        for slug in sorted(current):
            actions.append(
                SyncAction(self.name, member, "remove", slug, "old identity")
            )
            if not dry_run:
                await self.client.remove_team_membership(
                    self.organization, slug, account.login
                )
        return actions

    @staticmethod
    def _load_state(member_uuid: UUID) -> tuple[User | None, AccessSyncIdentity | None]:
        with get_session() as session:
            user = session.get(User, member_uuid)
            identity = session.get(AccessSyncIdentity, (member_uuid, "github"))
            for value in (user, identity):
                if value is not None:
                    session.expunge(value)
            return user, identity

    @staticmethod
    def _load_all_users() -> list[User]:
        with get_session() as session:
            users = list(session.exec(select(User)).all())
            for user in users:
                session.expunge(user)
            return users

    @staticmethod
    def _member_lookup() -> tuple[dict[str, User], dict[str, User]]:
        with get_session() as session:
            users = list(session.exec(select(User)).all())
            identities = list(
                session.exec(
                    select(AccessSyncIdentity).where(
                        AccessSyncIdentity.provider == "github"
                    )
                ).all()
            )
            users_by_id = {user.id: user for user in users}
            by_external_id = {
                identity.external_id: users_by_id[identity.member_uuid]
                for identity in identities
                if identity.member_uuid in users_by_id
            }
            by_username = {
                user.github_username.casefold(): user
                for user in users
                if user.github_username
            }
            for user in users:
                session.expunge(user)
            return by_external_id, by_username

    @staticmethod
    def _save_identity(member_uuid: UUID, account: GitHubAccount) -> None:
        with get_session() as session:
            identity = session.get(AccessSyncIdentity, (member_uuid, "github"))
            if identity is None:
                identity = AccessSyncIdentity(
                    member_uuid=member_uuid,
                    provider="github",
                    external_id=str(account.id),
                )
            identity.external_id = str(account.id)
            identity.external_login = account.login
            session.add(identity)
            session.commit()

    @staticmethod
    def _delete_identity(member_uuid: UUID) -> None:
        with get_session() as session:
            identity = session.get(AccessSyncIdentity, (member_uuid, "github"))
            if identity is not None:
                session.delete(identity)
                session.commit()
