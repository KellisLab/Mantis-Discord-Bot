"""Shared access-provider interfaces and result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class SyncAction:
    provider: str
    member: str
    action: str
    target: str
    detail: str = ""


@dataclass
class SyncResult:
    actions: list[SyncAction] = field(default_factory=list)


class AccessSyncError(RuntimeError):
    """Base provider error carrying retry policy."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


class AccessProvider(Protocol):
    name: str

    async def reconcile(
        self, member_uuid: UUID, *, dry_run: bool = False
    ) -> SyncResult:
        """Reload current canonical state and reconcile one provider."""
