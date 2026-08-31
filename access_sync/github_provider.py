"""One-way synchronization of Mantis stages into managed GitHub teams."""

from __future__ import annotations

import asyncio
import logging
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

LOGGER = logging.getLogger(__name__)

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


class _NonClosingAsyncTransport(httpx.AsyncBaseTransport):
    """Let short-lived GitHubKit clients share a transport without owning it."""

    def __init__(self, transport: httpx.AsyncBaseTransport) -> None:
        self.transport = transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self.transport.handle_async_request(request)

    async def aclose(self) -> None:
        # GitHubClient owns and closes the underlying transport. GitHubKit creates
        # a temporary AsyncClient for each request, and each of those clients
        # otherwise closes the shared connection pool when its request completes.
        return None


class GitHubClient:
    """Typed GitHubKit adapter with access-sync-specific error policy."""

    def __init__(
        self,
        token: str | None = GITHUB_TOKEN,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._transport = transport or httpx.AsyncHTTPTransport(retries=0)
        self.github = GitHub(
            token,
            timeout=30,
            async_transport=_NonClosingAsyncTransport(self._transport),
            auto_retry=False,
            http_cache=False,
        )

    async def close(self) -> None:
        await self._transport.aclose()

    async def _call(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        context: str,
        missing_ok: bool = False,
    ) -> T | None:
        try:
            return await operation()
        except RateLimitExceeded as error:
            raise AccessSyncError(
                self._contextual_message(context, self._failed_message(error)),
                retryable=True,
                retry_after=max(0, error.retry_after.total_seconds()),
            ) from error
        except RequestFailed as error:
            response = error.response.raw_response
            if response.status_code == 404 and missing_ok:
                return None
            raise self._request_failed_error(error, context=context) from error
        except RequestError as error:
            retryable = isinstance(
                error.exc, (httpx.TimeoutException, httpx.NetworkError)
            )
            raise AccessSyncError(
                self._contextual_message(context, self._exception_message(error.exc)),
                retryable=retryable,
            ) from error
        except GitHubException as error:
            raise AccessSyncError(
                self._contextual_message(context, self._exception_message(error)),
                retryable=False,
            ) from error

    async def account_by_login(self, login: str) -> GitHubAccount:
        response = await self._call(
            lambda: self.github.rest.users.async_get_by_username(login),
            context=f"GitHub user lookup for {login!r}",
        )
        assert response is not None
        user = response.parsed_data
        return GitHubAccount(id=user.id, login=user.login)

    async def account_by_id(self, account_id: int) -> GitHubAccount:
        response = await self._call(
            lambda: self.github.rest.users.async_get_by_id(account_id),
            context=f"GitHub user lookup for account ID {account_id}",
        )
        assert response is not None
        user = response.parsed_data
        return GitHubAccount(id=user.id, login=user.login)

    async def organization_membership(self, org: str, login: str) -> bool:
        response = await self._call(
            lambda: self.github.rest.orgs.async_get_membership_for_user(org, login),
            context=f"GitHub organization membership lookup for {login!r} in {org!r}",
            missing_ok=True,
        )
        return response is not None

    async def team_id(self, org: str, slug: str) -> int:
        response = await self._call(
            lambda: self.github.rest.teams.async_get_by_name(org, slug),
            context=f"GitHub team lookup for {org!r}/{slug!r}",
        )
        assert response is not None
        return response.parsed_data.id

    async def team_membership(self, org: str, slug: str, login: str) -> str | None:
        response = await self._call(
            lambda: self.github.rest.teams.async_get_membership_for_user_in_org(
                org, slug, login
            ),
            context=(
                f"GitHub team membership lookup for {login!r} "
                f"in {org!r}/{slug!r}"
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
            ),
            context=(
                f"GitHub organization invitation for account ID {account_id} "
                f"in {org!r}"
            ),
        )

    async def add_team_membership(self, org: str, slug: str, login: str) -> None:
        await self._call(
            lambda: (
                self.github.rest.teams.async_add_or_update_membership_for_user_in_org(
                    org, slug, login, data={"role": "member"}
                )
            ),
            context=(
                f"adding {login!r} to GitHub team {org!r}/{slug!r}"
            ),
        )

    async def remove_team_membership(self, org: str, slug: str, login: str) -> None:
        await self._call(
            lambda: self.github.rest.teams.async_remove_membership_for_user_in_org(
                org, slug, login
            ),
            context=(
                f"removing {login!r} from GitHub team {org!r}/{slug!r}"
            ),
            missing_ok=True,
        )

    async def list_team_accounts(
        self, org: str, slug: str
    ) -> list[ListedGitHubAccount]:
        context = f"listing members of GitHub team {org!r}/{slug!r}"
        try:
            members = [
                ListedGitHubAccount(id=member.id, login=member.login)
                async for member in self.github.rest.paginate(
                    self.github.rest.teams.async_list_members_in_org,
                    org=org,
                    team_slug=slug,
                    role="all",
                    per_page=100,
                )
            ]
            context = f"listing pending invitations for GitHub team {org!r}/{slug!r}"
            invitations = [
                ListedGitHubAccount(id=None, login=invitation.login)
                async for invitation in self.github.rest.paginate(
                    self.github.rest.teams.async_list_pending_invitations_in_org,
                    org=org,
                    team_slug=slug,
                    per_page=100,
                )
                if invitation.login
            ]
            return [*members, *invitations]
        except RateLimitExceeded as error:
            raise AccessSyncError(
                self._contextual_message(context, self._failed_message(error)),
                retryable=True,
                retry_after=max(0, error.retry_after.total_seconds()),
            ) from error
        except RequestFailed as error:
            raise self._request_failed_error(error, context=context) from error
        except RequestError as error:
            retryable = isinstance(
                error.exc, (httpx.TimeoutException, httpx.NetworkError)
            )
            raise AccessSyncError(
                self._contextual_message(context, self._exception_message(error.exc)),
                retryable=retryable,
            ) from error
        except GitHubException as error:
            raise AccessSyncError(
                self._contextual_message(context, self._exception_message(error)),
                retryable=False,
            ) from error

    @staticmethod
    def _exception_message(error: BaseException) -> str:
        message = str(error).strip()
        return f"{type(error).__name__}: {message}" if message else type(error).__name__

    @staticmethod
    def _contextual_message(context: str, message: str) -> str:
        return f"{context} failed: {message}"

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
    def _request_failed_error(
        cls, error: RequestFailed, *, context: str
    ) -> AccessSyncError:
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
            cls._contextual_message(context, cls._failed_message(error)),
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

    async def bulk_reconcile(
        self,
        *,
        dry_run: bool,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> SyncResult:
        users = await asyncio.to_thread(self._load_all_users)
        identities, usernames = await asyncio.to_thread(self._member_lookup)
        identities_by_member = await asyncio.to_thread(self._identities_by_member)

        # Fetch each managed team's roster once and reuse it both to skip
        # members that already match their desired state (no per-member
        # team-membership calls needed) and to drive the reverse sweep below.
        rosters: dict[str, list[ListedGitHubAccount]] = {}
        roster_by_login: dict[str, dict[str, str]] = {}
        for slug in MANAGED_TEAM_SLUGS:
            accounts = await self.client.list_team_accounts(self.organization, slug)
            rosters[slug] = accounts
            for account in accounts:
                roster_by_login.setdefault(account.login.casefold(), {})[slug] = slug

        actions: list[SyncAction] = []
        total = len(users)
        actionable: list[User] = []
        for user in users:
            identity = identities_by_member.get(user.id)
            if self._needs_reconcile(user, identity, roster_by_login):
                actionable.append(user)
        skipped = total - len(actionable)
        if on_progress is not None:
            on_progress(skipped, total)

        semaphore = asyncio.Semaphore(8)
        completed = skipped

        async def _run(user: User) -> list[SyncAction]:
            nonlocal completed
            try:
                async with semaphore:
                    result = await self.reconcile(user.id, dry_run=dry_run)
                return result.actions
            except AccessSyncError as error:
                return [
                    SyncAction(self.name, user.email, "error", "github", str(error))
                ]
            except Exception as error:
                LOGGER.exception(
                    "Unexpected GitHub access-sync failure for %s", user.email
                )
                message = str(error).strip()
                detail = f"Unexpected {type(error).__name__}"
                if message:
                    detail = f"{detail}: {message}"
                return [
                    SyncAction(self.name, user.email, "error", "github", detail)
                ]
            finally:
                completed += 1
                if on_progress is not None:
                    on_progress(completed, total)

        for result_actions in await asyncio.gather(*(_run(user) for user in actionable)):
            actions.extend(result_actions)

        # Reverse sweep catches legacy/unmatched GitHub members that no member
        # row change could enqueue. Active members and pending invitees count.
        seen: set[tuple[str, str, str]] = {
            (action.member.casefold(), action.action, action.target)
            for action in actions
        }
        for slug in MANAGED_TEAM_SLUGS:
            for account in rosters[slug]:
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

    def _needs_reconcile(
        self,
        user: User,
        identity: AccessSyncIdentity | None,
        roster_by_login: dict[str, dict[str, str]],
    ) -> bool:
        """Cheap, roster-only check: can this member's reconcile call be skipped?

        Conservative by design — any ambiguity falls through to the real
        ``reconcile()`` call, which re-reads canonical state itself.
        """
        if not user.github_username:
            # No linked GitHub account: reconcile() only has cleanup work to do
            # (deleting a stale identity) if one exists.
            return identity is not None
        if identity is not None and (
            identity.external_login is None
            or identity.external_login.casefold() != user.github_username.casefold()
        ):
            # Username changed since the last successful reconcile; only a
            # real pass can resolve the old-account cleanup.
            return True
        desired = desired_github_teams(user.stage)
        current = set(roster_by_login.get(user.github_username.casefold(), {}))
        return current != desired

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
    def _identities_by_member() -> dict[UUID, AccessSyncIdentity]:
        with get_session() as session:
            identities = list(
                session.exec(
                    select(AccessSyncIdentity).where(
                        AccessSyncIdentity.provider == "github"
                    )
                ).all()
            )
            for identity in identities:
                session.expunge(identity)
            return {identity.member_uuid: identity for identity in identities}

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
