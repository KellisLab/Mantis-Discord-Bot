"""Durable dispatcher and workers for external access providers."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import timedelta
from uuid import UUID

import psycopg
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from access_sync.models import (
    AccessSyncEvent,
    AccessSyncJob,
    AccessSyncJobStatus,
    utc_now,
)
from access_sync.types import AccessProvider, AccessSyncError
from database import DATABASE_URL, get_session
from members.models import User

LOGGER = logging.getLogger(__name__)
ACCESS_SYNC_CHANNEL = "access_sync"


def _psycopg_url() -> str:
    return DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)


class AccessSyncEngine:
    """Fan durable member-change events out to isolated provider jobs."""

    def __init__(
        self,
        providers: list[AccessProvider],
        *,
        worker_count: int = 2,
        max_attempts: int = 5,
    ) -> None:
        self.providers = {provider.name: provider for provider in providers}
        self.worker_count = worker_count
        self.max_attempts = max_attempts
        self._tasks: list[asyncio.Task[None]] = []
        self._wake = asyncio.Event()
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._tasks.append(
            asyncio.create_task(self._dispatcher(), name="access-sync-dispatcher")
        )
        self._tasks.append(
            asyncio.create_task(self._listen(), name="access-sync-listener")
        )
        for index in range(self.worker_count):
            self._tasks.append(
                asyncio.create_task(self._worker(), name=f"access-sync-worker-{index}")
            )
        self._wake.set()

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        self._started = False
        for provider in self.providers.values():
            close = getattr(provider, "close", None)
            if close is not None:
                await close()

    def enqueue_member(self, member_uuid: UUID) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._create_event(member_uuid)
            self._wake.set()
        else:
            task = loop.create_task(asyncio.to_thread(self._create_event, member_uuid))
            task.add_done_callback(self._event_created)

    def enqueue(self, discord_id: str | int) -> None:
        """Compatibility adapter for existing Discord command call sites."""

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._enqueue_discord_id(str(discord_id))
            self._wake.set()
        else:
            task = loop.create_task(
                asyncio.to_thread(self._enqueue_discord_id, str(discord_id))
            )
            task.add_done_callback(self._event_created)

    def _enqueue_discord_id(self, discord_id: str) -> None:
        with get_session() as session:
            member_uuid = session.exec(
                select(User.id).where(User.discord_id == discord_id)
            ).one_or_none()
        if member_uuid is not None:
            self._create_event(member_uuid)

    def _event_created(self, task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except Exception:
            LOGGER.exception("Could not persist an explicit access-sync event")
        else:
            self._wake.set()

    async def enqueue_all(self) -> None:
        with get_session() as session:
            member_uuids = list(session.exec(select(User.id)).all())
        for member_uuid in member_uuids:
            self.enqueue_member(member_uuid)

    async def on_member_update(self, before, after) -> None:
        provider = self.providers.get("discord")
        if provider is not None and hasattr(provider, "on_member_update"):
            try:
                await provider.on_member_update(before, after)
            except Exception:
                LOGGER.exception("Failed to repair manual Discord access drift")

    def on_member_join(self, member) -> None:
        provider = self.providers.get("discord")
        if provider is not None and hasattr(provider, "on_member_join"):
            asyncio.create_task(provider.on_member_join(member))

    async def reconcile_discord_startup(self) -> None:
        provider = self.providers.get("discord")
        if provider is not None and hasattr(provider, "reconcile_startup"):
            await provider.reconcile_startup()

    def retry_failed(self) -> int:
        with get_session() as session:
            failed = list(
                session.exec(
                    select(AccessSyncJob).where(
                        AccessSyncJob.status == AccessSyncJobStatus.FAILED
                    )
                ).all()
            )
            retried = 0
            for job in failed:
                pending = session.exec(
                    select(AccessSyncJob.uuid).where(
                        AccessSyncJob.member_uuid == job.member_uuid,
                        AccessSyncJob.provider == job.provider,
                        AccessSyncJob.status == AccessSyncJobStatus.PENDING,
                    )
                ).first()
                if pending is not None:
                    session.delete(job)
                    continue
                job.status = AccessSyncJobStatus.PENDING
                job.attempts = 0
                job.available_at = utc_now()
                job.last_error = None
                session.add(job)
                retried += 1
            session.commit()
        self._wake.set()
        return retried

    @staticmethod
    def failed_jobs() -> list[AccessSyncJob]:
        with get_session() as session:
            jobs = list(
                session.exec(
                    select(AccessSyncJob)
                    .where(AccessSyncJob.status == AccessSyncJobStatus.FAILED)
                    .order_by(AccessSyncJob.updated_at.desc())
                ).all()
            )
            for job in jobs:
                session.expunge(job)
            return jobs

    @staticmethod
    def _create_event(member_uuid: UUID) -> None:
        with get_session() as session:
            session.add(AccessSyncEvent(member_uuid=member_uuid))
            session.commit()

    async def _dispatcher(self) -> None:
        await asyncio.to_thread(self._recover_running_jobs)
        while True:
            dispatched = await asyncio.to_thread(self._dispatch_events)
            if dispatched:
                self._wake.set()
                continue
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=1)
            except TimeoutError:
                pass

    def _dispatch_events(self) -> int:
        with get_session() as session:
            events = list(
                session.exec(
                    select(AccessSyncEvent)
                    .order_by(AccessSyncEvent.id)
                    .limit(100)
                    .with_for_update(skip_locked=True)
                ).all()
            )
            for event in events:
                for provider_name in self.providers:
                    pending = session.exec(
                        select(AccessSyncJob.uuid).where(
                            AccessSyncJob.member_uuid == event.member_uuid,
                            AccessSyncJob.provider == provider_name,
                            AccessSyncJob.status == AccessSyncJobStatus.PENDING,
                        )
                    ).first()
                    # Only pending work is collapsed. A running row never blocks
                    # this insert, so a mid-run change creates a follow-up job.
                    if pending is None:
                        session.add(
                            AccessSyncJob(
                                member_uuid=event.member_uuid,
                                provider=provider_name,
                            )
                        )
                session.delete(event)
            try:
                session.commit()
            except IntegrityError:
                # Another dispatcher inserted the same pending work. Re-run the
                # batch; the partial unique index is the concurrency backstop.
                session.rollback()
                return 0
            return len(events)

    def _recover_running_jobs(self) -> None:
        with get_session() as session:
            running = list(
                session.exec(
                    select(AccessSyncJob).where(
                        AccessSyncJob.status == AccessSyncJobStatus.RUNNING
                    )
                ).all()
            )
            for job in running:
                pending = session.exec(
                    select(AccessSyncJob.uuid).where(
                        AccessSyncJob.member_uuid == job.member_uuid,
                        AccessSyncJob.provider == job.provider,
                        AccessSyncJob.status == AccessSyncJobStatus.PENDING,
                    )
                ).first()
                if pending is not None:
                    session.delete(job)
                else:
                    job.status = AccessSyncJobStatus.PENDING
                    job.available_at = utc_now()
                    session.add(job)
            session.commit()

    async def _worker(self) -> None:
        while True:
            job = await asyncio.to_thread(self._claim_job)
            if job is None:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=1)
                except TimeoutError:
                    pass
                continue

            provider = self.providers.get(job.provider)
            if provider is None:
                await asyncio.to_thread(
                    self._finish_job,
                    job.uuid,
                    False,
                    False,
                    "Provider is not registered",
                )
                continue
            try:
                # Provider reloads the member now, never from notification data.
                await provider.reconcile(job.member_uuid)
            except AccessSyncError as error:
                await asyncio.to_thread(
                    self._finish_job,
                    job.uuid,
                    False,
                    error.retryable,
                    str(error),
                    error.retry_after,
                )
            except Exception as error:
                LOGGER.exception("Unexpected %s access sync failure", job.provider)
                await asyncio.to_thread(
                    self._finish_job, job.uuid, False, True, str(error)
                )
            else:
                await asyncio.to_thread(self._finish_job, job.uuid, True, False, None)

    @staticmethod
    def _claim_job() -> AccessSyncJob | None:
        with get_session() as session:
            job = session.exec(
                select(AccessSyncJob)
                .where(
                    AccessSyncJob.status == AccessSyncJobStatus.PENDING,
                    AccessSyncJob.available_at <= utc_now(),
                )
                .order_by(AccessSyncJob.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).first()
            if job is None:
                return None
            running = session.exec(
                select(AccessSyncJob.uuid).where(
                    AccessSyncJob.member_uuid == job.member_uuid,
                    AccessSyncJob.provider == job.provider,
                    AccessSyncJob.status == AccessSyncJobStatus.RUNNING,
                )
            ).first()
            # A follow-up may exist while its predecessor is running, but it
            # must not execute concurrently and finish out of order.
            if running is not None:
                return None
            job.status = AccessSyncJobStatus.RUNNING
            job.attempts += 1
            session.add(job)
            session.commit()
            session.refresh(job)
            session.expunge(job)
            return job

    def _finish_job(
        self,
        job_uuid: UUID,
        success: bool,
        retryable: bool,
        error: str | None,
        retry_after: float | None = None,
    ) -> None:
        with get_session() as session:
            job = session.get(AccessSyncJob, job_uuid)
            if job is None:
                return
            if success:
                session.delete(job)
            else:
                newer_pending = session.exec(
                    select(AccessSyncJob.uuid).where(
                        AccessSyncJob.member_uuid == job.member_uuid,
                        AccessSyncJob.provider == job.provider,
                        AccessSyncJob.status == AccessSyncJobStatus.PENDING,
                    )
                ).first()
                if newer_pending is not None:
                    session.delete(job)
                elif retryable and job.attempts < self.max_attempts:
                    job.status = AccessSyncJobStatus.PENDING
                    job.available_at = utc_now() + timedelta(
                        seconds=(
                            min(3600, retry_after)
                            if retry_after is not None
                            else min(300, 2**job.attempts)
                        )
                    )
                    job.last_error = error
                    session.add(job)
                else:
                    job.status = AccessSyncJobStatus.FAILED
                    job.last_error = error
                    session.add(job)
            session.commit()
        self._wake.set()

    async def _listen(self) -> None:
        while True:
            try:
                connection = await psycopg.AsyncConnection.connect(
                    _psycopg_url(), autocommit=True
                )
                async with connection:
                    await connection.execute(f"LISTEN {ACCESS_SYNC_CHANNEL}")
                    async for _notification in connection.notifies():
                        self._wake.set()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Access sync listener disconnected; retrying")
                await asyncio.sleep(5)
