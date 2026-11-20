from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from .. import mcp_manager
from ..access import ALL, can_see_task, maintained_project_ids, project_role
from ..config import settings
from ..constants import Status
from ..database import SessionLocal
from ..models import McpToolPolicy, Task
from ..schemas import ApprovalOut, TaskOut

router = APIRouter(prefix="/approvals", tags=["approvals"])


async def _blast_radius(task: Task) -> dict:
    """What the task could touch — MCP servers, write/merge capability, target."""
    servers = await mcp_manager.servers_for_task(task.project)
    ctx: dict = {
        "project": task.project,
        "permission_mode": settings.claude_permission_mode,
        "mcp_servers": [s.name for s in servers],
        "can_write": False,
        "can_merge": False,
        "writable_tools": [],
        "blocked_tools": [],
    }
    if servers:
        async with SessionLocal() as s:
            policies = (
                await s.execute(
                    select(McpToolPolicy).where(
                        McpToolPolicy.server_id.in_([sv.id for sv in servers])
                    )
                )
            ).scalars().all()
        writable = [
            p.tool_name
            for p in policies
            if p.classification in ("write", "dangerous") and p.action != "block"
        ]
        ctx["writable_tools"] = sorted(writable)[:20]
        ctx["blocked_tools"] = sorted(p.tool_name for p in policies if p.action == "block")[:20]
        ctx["can_write"] = bool(writable)
        ctx["can_merge"] = any(
            "merge" in p.tool_name.lower() and p.action != "block" for p in policies
        )
    return ctx


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _may_decide(s, p, task: Task) -> bool:
    """Admin; or a maintainer of the task's project, except on their own critical
    request. Ad-hoc (no-project) tasks are admin-only."""
    if not p.user.active:
        return False
    if p.is_admin:
        return True
    if task.project_id is None:
        return False
    if await project_role(s, p, task.project_id) != "maintainer":
        return False
    return not (task.risk == "critical" and task.created_by == p.user.id)


async def _require_decidable(s, p, task_id: str) -> Task:
    """404 if invisible or not pending; 403 if visible but not decidable."""
    task = await s.get(Task, task_id)
    if task is None or not await can_see_task(s, p, task):
        raise HTTPException(404, "No pending approval for this task")
    if task.status != Status.AWAITING_APPROVAL:
        raise HTTPException(404, "No pending approval for this task")
    if not await _may_decide(s, p, task):
        raise HTTPException(403, "Not allowed to decide this approval")
    return task


class RejectBody(BaseModel):
    reason: str | None = None


class BulkBody(BaseModel):
    task_ids: list[str]


@router.get("", response_model=list[ApprovalOut])
async def list_pending(request: Request):
    """Tasks waiting for approval that the viewer may decide, most recent first,
    with blast-radius context."""
    p = request.state.principal
    async with SessionLocal() as s:
        stmt = (
            select(Task)
            .where(Task.status == Status.AWAITING_APPROVAL)
            .order_by(Task.created_at.desc())
        )
        maintained = await maintained_project_ids(s, p)
        if maintained is not ALL:
            stmt = stmt.where(
                Task.project_id.in_(maintained),
                # No self-approval of one's own critical request (NULL-safe).
                or_(
                    func.coalesce(Task.risk, "") != "critical",
                    func.coalesce(Task.created_by, "") != p.user.id,
                ),
            )
        rows = (await s.execute(stmt)).scalars().all()
    out = []
    for t in rows:
        card = ApprovalOut.model_validate(t)
        card.context = await _blast_radius(t)
        out.append(card)
    return out


def _approve(task: Task, decider: str) -> Task:
    task.status = Status.QUEUED
    task.decided_at = _now()
    task.decided_by = decider
    return task


@router.post("/{task_id}/approve", response_model=TaskOut)
async def approve(task_id: str, request: Request):
    p = request.state.principal
    async with SessionLocal() as s:
        task = _approve(await _require_decidable(s, p, task_id), p.user.id)
        await s.commit()
        await s.refresh(task)
        out = TaskOut.model_validate(task)
    await request.app.state.worker.submit(out.id, out.priority, out.created_at)
    return out


@router.post("/{task_id}/reject", response_model=TaskOut)
async def reject(task_id: str, body: RejectBody, request: Request):
    async with SessionLocal() as s:
        task = await _require_decidable(s, request.state.principal, task_id)
        task.status = Status.CANCELLED
        task.decided_at = _now()
        task.decided_by = request.state.principal.user.id
        task.decision_reason = (body.reason or "Rejected")[:2000]
        task.completed_at = _now()
        await s.commit()
        await s.refresh(task)
        return TaskOut.model_validate(task)


@router.post("/approve", response_model=list[TaskOut])
async def bulk_approve(body: BulkBody, request: Request):
    p = request.state.principal
    approved = []
    async with SessionLocal() as s:
        for tid in body.task_ids:
            task = await s.get(Task, tid)
            # Silently skip missing, not-pending and undecidable ids.
            if (
                task is not None
                and task.status == Status.AWAITING_APPROVAL
                and await _may_decide(s, p, task)
            ):
                approved.append(_approve(task, p.user.id))
        await s.commit()
        out = [TaskOut.model_validate(t) for t in approved]
    for o in out:
        await request.app.state.worker.submit(o.id, o.priority, o.created_at)
    return out
