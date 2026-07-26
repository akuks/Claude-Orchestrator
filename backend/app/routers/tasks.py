import base64
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from ..config import settings
from ..constants import VALID_MODELS, Priority, Status
from ..database import SessionLocal
from ..models import Artifact, Project, Task, TaskEvent
from ..schemas import (
    ArtifactOut,
    EventOut,
    FollowupCreate,
    StatsOut,
    TaskCreate,
    TaskOut,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _default_title(prompt: str) -> str:
    line = prompt.strip().splitlines()[0] if prompt.strip() else "Untitled task"
    return line[:80]


def _write_workspace(task_id: str, payload: TaskCreate) -> Path:
    ws = settings.workspaces_dir / task_id
    ws.mkdir(parents=True, exist_ok=True)
    if payload.claude_md:
        (ws / "CLAUDE.md").write_text(payload.claude_md)
    for f in payload.input_files:
        safe = os.path.basename(f.name)
        if not safe:
            continue
        try:
            (ws / safe).write_bytes(base64.b64decode(f.content_base64))
        except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
            raise HTTPException(400, f"Invalid base64 for input file {f.name}: {exc}")
    return ws


@router.post("", response_model=TaskOut, status_code=201)
async def create_task(payload: TaskCreate, request: Request):
    if payload.priority not in Priority.ALL:
        raise HTTPException(400, f"Invalid priority. Use one of {sorted(Priority.ALL)}")

    async with SessionLocal() as s:
        # Resolve project: task runs in the project's directory and inherits its
        # default model unless the request overrides it.
        project_obj = None
        if payload.project_id:
            project_obj = await s.get(Project, payload.project_id)
            if project_obj is None:
                raise HTTPException(404, "Project not found")

        model = payload.model or (project_obj.default_model if project_obj else "sonnet")
        if model not in VALID_MODELS:
            raise HTTPException(400, f"Invalid model. Use one of {sorted(VALID_MODELS)}")

        task = Task(
            title=payload.title or _default_title(payload.prompt),
            prompt=payload.prompt,
            project=(project_obj.name if project_obj else payload.project),
            project_id=payload.project_id,
            priority=payload.priority,
            tags=payload.tags,
            model=model,
            max_turns=payload.max_turns,
            status=Status.QUEUED,
        )
        s.add(task)
        await s.flush()  # assign id
        task.root_id = task.id  # a fresh task starts its own thread
        if project_obj:
            # Repo-backed task: run in the project directory (no sandbox copy).
            task.workspace_dir = project_obj.directory
        else:
            ws = _write_workspace(task.id, payload)
            task.workspace_dir = str(ws)
        await s.commit()
        await s.refresh(task)
        out = TaskOut.model_validate(task)

    await request.app.state.worker.submit(task.id, task.priority, task.created_at)
    return out


async def _thread_counts(s, root_ids: list[str]) -> dict[str, int]:
    if not root_ids:
        return {}
    rows = (
        await s.execute(
            select(Task.root_id, func.count())
            .where(Task.root_id.in_(root_ids))
            .group_by(Task.root_id)
        )
    ).all()
    return {rid: n for rid, n in rows}


@router.get("", response_model=list[TaskOut])
async def list_tasks(
    project: str | None = None,
    project_id: str | None = None,
    status: str | None = None,
    roots_only: bool = True,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    stmt = select(Task).order_by(Task.created_at.desc())
    if roots_only:
        # A thread's root has root_id == its own id; hide follow-up steps.
        stmt = stmt.where(Task.root_id == Task.id)
    if project_id:
        stmt = stmt.where(Task.project_id == project_id)
    if project:
        stmt = stmt.where(Task.project == project)
    if status:
        stmt = stmt.where(Task.status == status)
    stmt = stmt.limit(limit).offset(offset)
    async with SessionLocal() as s:
        rows = (await s.execute(stmt)).scalars().all()
        counts = await _thread_counts(s, [t.root_id or t.id for t in rows])
    out = []
    for t in rows:
        o = TaskOut.model_validate(t)
        o.thread_count = counts.get(t.root_id or t.id, 1)
        out.append(o)
    return out


@router.get("/stats", response_model=StatsOut)
async def stats():
    start_of_day = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    async with SessionLocal() as s:
        by_status_rows = (
            await s.execute(select(Task.status, func.count()).group_by(Task.status))
        ).all()
        by_status = {k: v for k, v in by_status_rows}

        tasks_today = (
            await s.execute(
                select(func.count()).where(Task.created_at >= start_of_day)
            )
        ).scalar_one()

        completed_today = (
            await s.execute(
                select(func.count()).where(
                    Task.completed_at >= start_of_day,
                    Task.status == Status.COMPLETED,
                )
            )
        ).scalar_one()

        finished_today = (
            await s.execute(
                select(func.count()).where(
                    Task.completed_at >= start_of_day,
                    Task.status.in_([Status.COMPLETED, Status.FAILED]),
                )
            )
        ).scalar_one()

        avg_duration = (
            await s.execute(
                select(func.avg(Task.duration_ms)).where(
                    Task.status == Status.COMPLETED
                )
            )
        ).scalar_one()

    running = by_status.get(Status.RUNNING, 0)
    queued = by_status.get(Status.QUEUED, 0)
    success_rate = (completed_today / finished_today) if finished_today else 0.0
    return StatsOut(
        tasks_today=tasks_today,
        running=running,
        queued=queued,
        completed_today=completed_today,
        success_rate=round(success_rate, 3),
        avg_duration_ms=float(avg_duration) if avg_duration is not None else None,
        by_status=by_status,
    )


async def _get_task_or_404(s, task_id: str) -> Task:
    task = await s.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    return task


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(task_id: str):
    async with SessionLocal() as s:
        task = await _get_task_or_404(s, task_id)
        counts = await _thread_counts(s, [task.root_id or task.id])
        out = TaskOut.model_validate(task)
        out.thread_count = counts.get(task.root_id or task.id, 1)
        return out


@router.get("/{task_id}/thread", response_model=list[TaskOut])
async def get_thread(task_id: str):
    """All steps in this task's thread (root + follow-ups), oldest first."""
    async with SessionLocal() as s:
        task = await _get_task_or_404(s, task_id)
        root = task.root_id or task.id
        rows = (
            await s.execute(
                select(Task)
                .where(Task.root_id == root)
                .order_by(Task.created_at.asc())
            )
        ).scalars().all()
    return [TaskOut.model_validate(t) for t in rows]


@router.get("/{task_id}/events", response_model=list[EventOut])
async def get_events(task_id: str, after_seq: int = 0):
    async with SessionLocal() as s:
        await _get_task_or_404(s, task_id)
        rows = (
            await s.execute(
                select(TaskEvent)
                .where(TaskEvent.task_id == task_id, TaskEvent.seq > after_seq)
                .order_by(TaskEvent.seq)
            )
        ).scalars().all()
    return [EventOut.model_validate(e) for e in rows]


@router.post("/{task_id}/cancel", response_model=TaskOut)
async def cancel_task(task_id: str, request: Request):
    async with SessionLocal() as s:
        task = await _get_task_or_404(s, task_id)
        if task.status in Status.TERMINAL:
            raise HTTPException(409, f"Task already {task.status}")
    await request.app.state.worker.cancel(task_id)
    async with SessionLocal() as s:
        task = await _get_task_or_404(s, task_id)
        return TaskOut.model_validate(task)


@router.post("/{task_id}/retry", response_model=TaskOut)
async def retry_task(task_id: str, request: Request):
    async with SessionLocal() as s:
        task = await _get_task_or_404(s, task_id)
        if task.status not in Status.TERMINAL:
            raise HTTPException(409, "Only finished tasks can be retried")
        task.status = Status.QUEUED
        task.exit_code = None
        task.error = None
        task.result_text = None
        task.num_turns = None
        task.total_cost_usd = None
        task.duration_ms = None
        task.started_at = None
        task.completed_at = None
        await s.commit()
        await s.refresh(task)
        out = TaskOut.model_validate(task)
    await request.app.state.worker.submit(out.id, out.priority, out.created_at)
    return out


@router.post("/{task_id}/duplicate", response_model=TaskOut, status_code=201)
async def duplicate_task(task_id: str, request: Request):
    async with SessionLocal() as s:
        src = await _get_task_or_404(s, task_id)
        new = Task(
            title=f"{src.title} (copy)",
            prompt=src.prompt,
            project=src.project,
            priority=src.priority,
            tags=list(src.tags or []),
            model=src.model,
            max_turns=src.max_turns,
            parent_task_id=src.id,
            status=Status.QUEUED,
        )
        s.add(new)
        await s.flush()
        new.root_id = new.id  # a duplicate is an independent new thread
        ws = settings.workspaces_dir / new.id
        ws.mkdir(parents=True, exist_ok=True)
        # Carry over CLAUDE.md and input files from the source workspace.
        if src.workspace_dir:
            src_ws = Path(src.workspace_dir)
            src_claude = src_ws / "CLAUDE.md"
            if src_claude.exists():
                (ws / "CLAUDE.md").write_text(src_claude.read_text(errors="replace"))
        new.workspace_dir = str(ws)
        await s.commit()
        await s.refresh(new)
        out = TaskOut.model_validate(new)
    await request.app.state.worker.submit(out.id, out.priority, out.created_at)
    return out


@router.post("/{task_id}/followup", response_model=TaskOut, status_code=201)
async def followup_task(task_id: str, payload: FollowupCreate, request: Request):
    """Continue a task's Claude session with a new prompt (a resumable thread)."""
    async with SessionLocal() as s:
        parent = await _get_task_or_404(s, task_id)
        if not parent.session_id:
            raise HTTPException(
                409, "Parent has no resumable session yet (must finish a run first)"
            )
        child = Task(
            title=_default_title(payload.prompt),
            prompt=payload.prompt,
            project=parent.project,
            priority=parent.priority,
            tags=list(parent.tags or []),
            model=parent.model,
            max_turns=parent.max_turns,
            parent_task_id=parent.id,
            root_id=parent.root_id or parent.id,  # join the parent's thread
            resume_session_id=parent.session_id,
            workspace_dir=parent.workspace_dir,  # same workspace = continues context
            status=Status.QUEUED,
        )
        s.add(child)
        await s.commit()
        await s.refresh(child)
        out = TaskOut.model_validate(child)
    await request.app.state.worker.submit(out.id, out.priority, out.created_at)
    return out


@router.get("/{task_id}/artifacts", response_model=list[ArtifactOut])
async def list_artifacts(task_id: str):
    async with SessionLocal() as s:
        await _get_task_or_404(s, task_id)
        rows = (
            await s.execute(select(Artifact).where(Artifact.task_id == task_id))
        ).scalars().all()
    return [ArtifactOut.model_validate(a) for a in rows]


@router.get("/{task_id}/artifacts/{filename:path}")
async def download_artifact(task_id: str, filename: str):
    async with SessionLocal() as s:
        task = await _get_task_or_404(s, task_id)
        workspace = task.workspace_dir
    if not workspace:
        raise HTTPException(404, "No workspace for task")
    root = Path(workspace).resolve()
    target = (root / filename).resolve()
    # Path-traversal guard: target must stay inside the workspace.
    if not str(target).startswith(str(root) + os.sep) and target != root:
        raise HTTPException(400, "Invalid path")
    if not target.is_file():
        raise HTTPException(404, "Artifact not found")
    return FileResponse(str(target), filename=target.name)
