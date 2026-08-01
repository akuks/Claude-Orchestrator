"""Cron scheduler: fires due schedules by creating tasks.

An in-process asyncio loop (no external scheduler needed for Phase 4). Each tick
finds enabled schedules whose next_run_at is due, spawns a task via the shared
task_service, and advances next_run_at with croniter.
"""

import asyncio
from datetime import datetime, timezone

from croniter import croniter
from sqlalchemy import select

from .config import settings
from .constants import Status
from .database import SessionLocal
from .models import Schedule
from .task_service import build_task


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_valid_cron(expr: str) -> bool:
    return croniter.is_valid(expr)


def next_run(expr: str, after: datetime | None = None) -> datetime:
    base = after or _now()
    return croniter(expr, base).get_next(datetime)


class Scheduler:
    def __init__(self, worker) -> None:
        self._worker = worker
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        # Ensure every enabled schedule has a next_run_at computed.
        async with SessionLocal() as s:
            for sch in (await s.execute(select(Schedule))).scalars().all():
                if sch.enabled and sch.next_run_at is None and is_valid_cron(sch.cron):
                    sch.next_run_at = next_run(sch.cron)
            await s.commit()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(settings.scheduler_interval_seconds)
            try:
                await self._tick()
            except Exception:  # noqa: BLE001 - the loop must never die
                continue

    async def _tick(self) -> None:
        now = _now()
        async with SessionLocal() as s:
            due = (
                await s.execute(
                    select(Schedule).where(
                        Schedule.enabled.is_(True), Schedule.next_run_at <= now
                    )
                )
            ).scalars().all()
            fired = []
            for sch in due:
                if not is_valid_cron(sch.cron):
                    continue
                task = await build_task(
                    s,
                    prompt=sch.prompt,
                    title=f"{sch.name} (scheduled)",
                    project_id=sch.project_id,
                    model=sch.model or None,
                    max_turns=sch.max_turns,
                    priority=sch.priority,
                    tags=sch.tags,
                    schedule_id=sch.id,
                    requires_approval=sch.requires_approval,
                )
                sch.last_run_at = now
                sch.next_run_at = next_run(sch.cron, now)
                # Approval-gated runs (schedule flag OR auto-gated critical) wait
                # in the inbox; everything else is dispatched.
                if task.status != Status.AWAITING_APPROVAL:
                    fired.append((task.id, task.priority, task.created_at))
            await s.commit()

        for task_id, priority, created_at in fired:
            await self._worker.submit(task_id, priority, created_at)

    async def run_now(self, schedule_id: str) -> str | None:
        """Fire a schedule immediately (does not change its cron cadence)."""
        async with SessionLocal() as s:
            sch = await s.get(Schedule, schedule_id)
            if sch is None:
                return None
            task = await build_task(
                s,
                prompt=sch.prompt,
                title=f"{sch.name} (manual run)",
                project_id=sch.project_id,
                model=sch.model or None,
                max_turns=sch.max_turns,
                priority=sch.priority,
                tags=sch.tags,
                schedule_id=sch.id,
            )
            info = (task.id, task.priority, task.created_at)
            await s.commit()
        await self._worker.submit(*info)
        return info[0]
