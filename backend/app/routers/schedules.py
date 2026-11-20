from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import and_, false, or_, select, true

from ..access import (
    ALL,
    project_role,
    require_project_role,
    task_visibility_clause,
    visible_project_ids,
)
from ..constants import VALID_MODELS
from ..database import SessionLocal
from ..models import Agent, Schedule, Task
from ..schemas import ScheduleCreate, ScheduleOut, ScheduleUpdate, TaskOut
from ..scheduler import is_valid_cron, next_run, owner_block_reason

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
async def create_schedule(payload: ScheduleCreate, request: Request):
    if not is_valid_cron(payload.cron):
        raise HTTPException(400, "Invalid cron expression")
    if payload.model and payload.model not in VALID_MODELS:
        raise HTTPException(400, f"Invalid model. Use one of {sorted(VALID_MODELS)}")
    if payload.notify not in _VALID_NOTIFY:
        raise HTTPException(400, f"notify must be one of {sorted(_VALID_NOTIFY)}")
    if not payload.agent_id and not (payload.prompt or "").strip():
        raise HTTPException(400, "Provide a prompt or an agent_id")
    async with SessionLocal() as s:
        await _require_targets(s, request.state.principal, payload.project_id, payload.agent_id)
        sch = Schedule(
            name=payload.name,
            cron=payload.cron,
            prompt=payload.prompt or "",
            agent_id=payload.agent_id,
            project_id=payload.project_id,
            model=payload.model or "",
            max_turns=payload.max_turns,
            priority=payload.priority,
            tags=payload.tags,
            enabled=payload.enabled,
            notify=payload.notify,
            notify_webhook=payload.notify_webhook,
            next_run_at=next_run(payload.cron) if payload.enabled else None,
            created_by=request.state.principal.user.id,
        )
        s.add(sch)
        await s.commit()
        await s.refresh(sch)
        return ScheduleOut.model_validate(sch)


async def _require_targets(
    s, p, project_id: str | None, agent_id: str | None, strict: bool = True
) -> None:
    """Contributor on every project the schedule's runs would land in: its own
    project and its agent's (the agent's wins when firing). None = ad-hoc.
    `strict`: a missing agent is a 404 (create/edit); firing falls back to the
    inline prompt, so run-now tolerates it."""
    agent = None
    if agent_id:
        agent = await s.get(Agent, agent_id)
        if agent is None and strict:
            raise HTTPException(404, "Agent not found")
    for pid in {project_id, agent and agent.project_id} - {None}:
        await require_project_role(s, p, pid)


async def _visibility_clause(s, p):
    if not p.user.active:
        return false()
    visible = await visible_project_ids(s, p)
    if visible is ALL:
        return true()
    return or_(
        Schedule.project_id.in_(visible),
        and_(Schedule.project_id.is_(None), Schedule.created_by == p.user.id),
    )


@router.get("", response_model=list[ScheduleOut])
async def list_schedules(request: Request):
    async with SessionLocal() as s:
        clause = await _visibility_clause(s, request.state.principal)
        rows = (
            await s.execute(select(Schedule).where(clause).order_by(Schedule.created_at.desc()))
        ).scalars().all()
    return [ScheduleOut.model_validate(r) for r in rows]


async def _get_or_404(s, p, schedule_id: str, manage: bool = False) -> Schedule:
    """404 unless visible (project: any role; none: creator/admin). With
    `manage` (edit/delete), 403 unless creator, maintainer or admin."""
    sch = (
        await s.execute(
            select(Schedule).where(Schedule.id == schedule_id, await _visibility_clause(s, p))
        )
    ).scalar_one_or_none()
    if sch is None:
        raise HTTPException(404, "Schedule not found")
    if manage and sch.created_by != p.user.id and not p.is_admin:
        if sch.project_id is None or await project_role(s, p, sch.project_id) != "maintainer":
            raise HTTPException(403, "Insufficient role")
    return sch


@router.get("/{schedule_id}", response_model=ScheduleOut)
async def get_schedule(schedule_id: str, request: Request):
    async with SessionLocal() as s:
        return ScheduleOut.model_validate(
            await _get_or_404(s, request.state.principal, schedule_id)
        )


@router.patch("/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(schedule_id: str, payload: ScheduleUpdate, request: Request):
    p = request.state.principal
    data = payload.model_dump(exclude_unset=True)
    if "cron" in data and not is_valid_cron(data["cron"]):
        raise HTTPException(400, "Invalid cron expression")
    if data.get("model") and data["model"] not in VALID_MODELS:
        raise HTTPException(400, "Invalid model")
    if "notify" in data and data["notify"] not in _VALID_NOTIFY:
        raise HTTPException(400, f"notify must be one of {sorted(_VALID_NOTIFY)}")
    async with SessionLocal() as s:
        sch = await _get_or_404(s, p, schedule_id, manage=True)
        if "project_id" in data or "agent_id" in data:
            await _require_targets(
                s, p, data.get("project_id", sch.project_id), data.get("agent_id", sch.agent_id)
            )
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
async def delete_schedule(schedule_id: str, request: Request):
    async with SessionLocal() as s:
        sch = await _get_or_404(s, request.state.principal, schedule_id, manage=True)
        await s.delete(sch)
        await s.commit()


@router.post("/{schedule_id}/run", response_model=TaskOut, status_code=201)
async def run_schedule_now(schedule_id: str, request: Request):
    p = request.state.principal
    async with SessionLocal() as s:
        sch = await _get_or_404(s, p, schedule_id)
        await _require_targets(s, p, sch.project_id, sch.agent_id, strict=False)
        # Same owner rules as the scheduler: the run is created as the owner.
        reason = await owner_block_reason(s, sch)
        if reason is not None:
            raise HTTPException(409, f"Schedule {reason}")
    task_id = await request.app.state.scheduler.run_now(schedule_id)
    if task_id is None:
        raise HTTPException(404, "Schedule not found")
    async with SessionLocal() as s:
        task = await s.get(Task, task_id)
        return TaskOut.model_validate(task)


@router.get("/{schedule_id}/runs", response_model=list[TaskOut])
async def schedule_runs(schedule_id: str, request: Request, limit: int = 50):
    p = request.state.principal
    async with SessionLocal() as s:
        await _get_or_404(s, p, schedule_id)
        clause = await task_visibility_clause(s, p)
        rows = (
            await s.execute(
                select(Task)
                .where(Task.schedule_id == schedule_id, clause)
                .order_by(Task.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
    return [TaskOut.model_validate(t) for t in rows]
