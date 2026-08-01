from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from .. import mcp_manager
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


class RejectBody(BaseModel):
    reason: str | None = None


class BulkBody(BaseModel):
    task_ids: list[str]


@router.get("", response_model=list[ApprovalOut])
async def list_pending():
    """Tasks waiting for approval, most recent first, with blast-radius context."""
    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(Task)
                .where(Task.status == Status.AWAITING_APPROVAL)
                .order_by(Task.created_at.desc())
            )
        ).scalars().all()
    out = []
    for t in rows:
        card = ApprovalOut.model_validate(t)
        card.context = await _blast_radius(t)
        out.append(card)
    return out


async def _approve_one(s, task_id: str) -> Task | None:
    task = await s.get(Task, task_id)
    if task is None or task.status != Status.AWAITING_APPROVAL:
        return None
    task.status = Status.QUEUED
    task.decided_at = _now()
    return task


@router.post("/{task_id}/approve", response_model=TaskOut)
async def approve(task_id: str, request: Request):
    async with SessionLocal() as s:
        task = await _approve_one(s, task_id)
        if task is None:
            raise HTTPException(404, "No pending approval for this task")
        await s.commit()
        await s.refresh(task)
        out = TaskOut.model_validate(task)
    await request.app.state.worker.submit(out.id, out.priority, out.created_at)
    return out


@router.post("/{task_id}/reject", response_model=TaskOut)
async def reject(task_id: str, body: RejectBody):
    async with SessionLocal() as s:
        task = await s.get(Task, task_id)
        if task is None or task.status != Status.AWAITING_APPROVAL:
            raise HTTPException(404, "No pending approval for this task")
        task.status = Status.CANCELLED
        task.decided_at = _now()
        task.decision_reason = (body.reason or "Rejected")[:2000]
        task.completed_at = _now()
        await s.commit()
        await s.refresh(task)
        return TaskOut.model_validate(task)


@router.post("/approve", response_model=list[TaskOut])
async def bulk_approve(body: BulkBody, request: Request):
    approved = []
    async with SessionLocal() as s:
        for tid in body.task_ids:
            task = await _approve_one(s, tid)
            if task is not None:
                approved.append(task)
        await s.commit()
        out = [TaskOut.model_validate(t) for t in approved]
    for o in out:
        await request.app.state.worker.submit(o.id, o.priority, o.created_at)
    return out
