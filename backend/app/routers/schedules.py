from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from ..constants import VALID_MODELS
from ..database import SessionLocal
from ..models import Schedule, Task
from ..schemas import ScheduleCreate, ScheduleOut, ScheduleUpdate, TaskOut
from ..scheduler import is_valid_cron, next_run

router = APIRouter(prefix="/schedules", tags=["schedules"])

_VALID_NOTIFY = {"never", "on_failure", "always"}


@router.get("/preview")
async def preview_cron(cron: str, count: int = 5):
    """Return the next N run times for a cron expression (for the UI builder)."""
    if not is_valid_cron(cron):
        raise HTTPException(400, "Invalid cron expression")
    from croniter import croniter
    from datetime import datetime, timezone

    it = croniter(cron, datetime.now(timezone.utc))
    return {"cron": cron, "next_runs": [it.get_next(datetime).isoformat() for _ in range(count)]}


@router.post("", response_model=ScheduleOut, status_code=201)
async def create_schedule(payload: ScheduleCreate):
    if not is_valid_cron(payload.cron):
        raise HTTPException(400, "Invalid cron expression")
    if payload.model and payload.model not in VALID_MODELS:
        raise HTTPException(400, f"Invalid model. Use one of {sorted(VALID_MODELS)}")
    if payload.notify not in _VALID_NOTIFY:
        raise HTTPException(400, f"notify must be one of {sorted(_VALID_NOTIFY)}")
    async with SessionLocal() as s:
        sch = Schedule(
            name=payload.name,
            cron=payload.cron,
            prompt=payload.prompt,
            project_id=payload.project_id,
            model=payload.model or "",
            max_turns=payload.max_turns,
            priority=payload.priority,
            tags=payload.tags,
            enabled=payload.enabled,
            notify=payload.notify,
            notify_webhook=payload.notify_webhook,
            next_run_at=next_run(payload.cron) if payload.enabled else None,
        )
        s.add(sch)
        await s.commit()
        await s.refresh(sch)
        return ScheduleOut.model_validate(sch)


@router.get("", response_model=list[ScheduleOut])
async def list_schedules():
    async with SessionLocal() as s:
        rows = (
            await s.execute(select(Schedule).order_by(Schedule.created_at.desc()))
        ).scalars().all()
    return [ScheduleOut.model_validate(r) for r in rows]


async def _get_or_404(s, schedule_id: str) -> Schedule:
    sch = await s.get(Schedule, schedule_id)
    if sch is None:
        raise HTTPException(404, "Schedule not found")
    return sch


@router.get("/{schedule_id}", response_model=ScheduleOut)
async def get_schedule(schedule_id: str):
    async with SessionLocal() as s:
        return ScheduleOut.model_validate(await _get_or_404(s, schedule_id))


@router.patch("/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(schedule_id: str, payload: ScheduleUpdate):
    data = payload.model_dump(exclude_unset=True)
    if "cron" in data and not is_valid_cron(data["cron"]):
        raise HTTPException(400, "Invalid cron expression")
    if data.get("model") and data["model"] not in VALID_MODELS:
        raise HTTPException(400, "Invalid model")
    if "notify" in data and data["notify"] not in _VALID_NOTIFY:
        raise HTTPException(400, f"notify must be one of {sorted(_VALID_NOTIFY)}")
    async with SessionLocal() as s:
        sch = await _get_or_404(s, schedule_id)
        for k, v in data.items():
            setattr(sch, k, v)
        # Recompute the next run when cadence or enabled state changes.
        if sch.enabled:
            if "cron" in data or "enabled" in data or sch.next_run_at is None:
                sch.next_run_at = next_run(sch.cron)
        else:
            sch.next_run_at = None
        await s.commit()
        await s.refresh(sch)
        return ScheduleOut.model_validate(sch)


@router.delete("/{schedule_id}", status_code=204)
async def delete_schedule(schedule_id: str):
    async with SessionLocal() as s:
        sch = await _get_or_404(s, schedule_id)
        await s.delete(sch)
        await s.commit()


@router.post("/{schedule_id}/run", response_model=TaskOut, status_code=201)
async def run_schedule_now(schedule_id: str, request: Request):
    task_id = await request.app.state.scheduler.run_now(schedule_id)
    if task_id is None:
        raise HTTPException(404, "Schedule not found")
    async with SessionLocal() as s:
        task = await s.get(Task, task_id)
        return TaskOut.model_validate(task)


@router.get("/{schedule_id}/runs", response_model=list[TaskOut])
async def schedule_runs(schedule_id: str, limit: int = 50):
    async with SessionLocal() as s:
        await _get_or_404(s, schedule_id)
        rows = (
            await s.execute(
                select(Task)
                .where(Task.schedule_id == schedule_id)
                .order_by(Task.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
    return [TaskOut.model_validate(t) for t in rows]
