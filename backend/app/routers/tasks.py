import base64
import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import InvalidRequestError

from ..access import (
    can_see_task,
    effective_project_id,
    project_role,
    require_project_role,
    require_task,
    task_role,
    task_visibility_clause,
)
from ..config import settings
from ..constants import VALID_MODELS, Priority, Status
from ..database import SessionLocal
from ..guard import runtime as guard_runtime
from .. import reports
from ..models import ActionGrant, Artifact, Project, RunAsk, Task, TaskEvent, TaskHandoff, TaskQuestion, TaskUsage
from ..resume import handoff as resume_handoff
from ..resume.texts import CONTINUE_PROMPT
from ..task_service import build_task
from ..usage import report as usage_report
from ..schemas import (
    ArtifactOut,
    ContinueCreate,
    EventOut,
    FollowupCreate,
    QuestionCreate,
    QuestionOut,
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

    if payload.model and payload.model not in VALID_MODELS:
        raise HTTPException(400, f"Invalid model. Use one of {sorted(VALID_MODELS)}")

    async with SessionLocal() as s:
        if payload.project_id:
            await require_project_role(s, request.state.principal, payload.project_id)
            if (await s.get(Project, payload.project_id)) is None:
                raise HTTPException(404, "Project not found")
        task = await build_task(
            s,
            prompt=payload.prompt,
            title=payload.title,
            project_id=payload.project_id,
            model=payload.model,
            max_turns=payload.max_turns,
            max_budget_usd=payload.max_budget_usd,
            priority=payload.priority,
            tags=payload.tags,
            claude_md=payload.claude_md,
            input_files=[f.model_dump() for f in payload.input_files],
            requires_approval=payload.requires_approval,
            created_by=request.state.principal.user.id,
        )
        await s.commit()
        await s.refresh(task)
        out = TaskOut.model_validate(task)

    # Approval-gated tasks wait in the inbox; others run immediately.
    if not out.requires_approval:
        await request.app.state.worker.submit(out.id, out.priority, out.created_at)
    return out


async def _thread_counts(s, root_ids: list[str], visible) -> dict[str, int]:
    if not root_ids:
        return {}
    rows = (
        await s.execute(
            select(Task.root_id, func.count())
            .where(Task.root_id.in_(root_ids), visible)
            .group_by(Task.root_id)
        )
    ).all()
    return {rid: n for rid, n in rows}


@router.get("", response_model=list[TaskOut])
async def list_tasks(
    request: Request,
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
    async with SessionLocal() as s:
        visible = await task_visibility_clause(s, request.state.principal)
        stmt = stmt.where(visible).limit(limit).offset(offset)
        rows = (await s.execute(stmt)).scalars().all()
        counts = await _thread_counts(s, [t.root_id or t.id for t in rows], visible)
    out = []
    for t in rows:
        o = TaskOut.model_validate(t)
        o.thread_count = counts.get(t.root_id or t.id, 1)
        out.append(o)
    return out


@router.get("/stats", response_model=StatsOut)
async def stats(request: Request):
    start_of_day = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    async with SessionLocal() as s:
        visible = await task_visibility_clause(s, request.state.principal)
        by_status_rows = (
            await s.execute(
                select(Task.status, func.count()).where(visible).group_by(Task.status)
            )
        ).all()
        by_status = {k: v for k, v in by_status_rows}

        tasks_today = (
            await s.execute(
                select(func.count()).where(visible, Task.created_at >= start_of_day)
            )
        ).scalar_one()

        completed_today = (
            await s.execute(
                select(func.count()).where(
                    visible,
                    Task.completed_at >= start_of_day,
                    Task.status == Status.COMPLETED,
                )
            )
        ).scalar_one()

        finished_today = (
            await s.execute(
                select(func.count()).where(
                    visible,
                    Task.completed_at >= start_of_day,
                    Task.status.in_([Status.COMPLETED, Status.FAILED]),
                )
            )
        ).scalar_one()

        avg_duration = (
            await s.execute(
                select(func.avg(Task.duration_ms)).where(
                    visible, Task.status == Status.COMPLETED
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


async def _may_manage(s, p, task: Task) -> bool:
    """Creator, maintainer on the task, or admin (visibility checked separately)."""
    return (
        p.is_admin
        or (task.created_by is not None and task.created_by == p.user.id)
        or await task_role(s, p, task) == "maintainer"
    )


async def _manage_allowed(s, p, task: Task, *, need_contributor: bool = False) -> tuple[bool, str | None]:
    """(allowed, why not): the one rule behind ``_require_manage`` and the per-viewer
    ``can_manage`` flag on a task."""
    if not await _may_manage(s, p, task):
        return False, "Not allowed to modify this task"
    pid = await effective_project_id(s, task)
    if need_contributor and pid is not None and await project_role(s, p, pid) is None:
        return False, "Contributor role required on the task's project"
    return True, None


async def _require_manage(s, p, task: Task, *, need_contributor: bool = False) -> None:
    """Creator, maintainer on the task, or admin may act on it (else 403).

    Follow-up/duplicate also need contributor on the effective project (the
    task's own, or its thread root's for an ad-hoc step) since they run in that
    project's repo/session. The task is already visible, so a missing role is 403."""
    ok, why = await _manage_allowed(s, p, task, need_contributor=need_contributor)
    if not ok:
        raise HTTPException(403, why)


async def _viewer_flags(s, p, out: TaskOut, task: Task) -> TaskOut:
    """Per-viewer ``can_manage`` (what ``POST /continue`` and follow-ups allow) and ``can_resume``
    (the viewer owns the step's session)."""
    out.can_manage = (await _manage_allowed(s, p, task, need_contributor=True))[0]
    out.can_resume = bool(out.can_manage and task.session_id and task.created_by == p.user.id)
    return out


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(task_id: str, request: Request):
    async with SessionLocal() as s:
        p = request.state.principal
        task = await require_task(s, p, task_id)
        visible = await task_visibility_clause(s, p)
        counts = await _thread_counts(s, [task.root_id or task.id], visible)
        out = TaskOut.model_validate(task)
        out.thread_count = counts.get(task.root_id or task.id, 1)
        return await _viewer_flags(s, p, out, task)


@router.get("/{task_id}/thread", response_model=list[TaskOut])
async def get_thread(task_id: str, request: Request):
    """All steps in this task's thread (root + follow-ups), oldest first."""
    async with SessionLocal() as s:
        p = request.state.principal
        task = await require_task(s, p, task_id)
        root = task.root_id or task.id
        rows = (
            await s.execute(
                select(Task)
                .where(Task.root_id == root, await task_visibility_clause(s, p))
                .order_by(Task.created_at.asc())
            )
        ).scalars().all()
        return [await _viewer_flags(s, p, TaskOut.model_validate(t), t) for t in rows]


@router.get("/{task_id}/usage")
async def get_usage(task_id: str, request: Request):
    """Token care: this step's figures, the thread's totals so far and up to three tips."""
    async with SessionLocal() as s:
        p = request.state.principal
        task = await require_task(s, p, task_id)
        root = task.root_id or task.id
        steps = (
            await s.execute(
                select(Task)
                .where(Task.root_id == root, await task_visibility_clause(s, p))
                .order_by(Task.created_at.asc())
            )
        ).scalars().all()
        ids = [t.id for t in steps]
        usages = list((await s.execute(select(TaskUsage).where(TaskUsage.task_id.in_(ids)))).scalars().all())
        pid = await effective_project_id(s, task)
        proj = await s.get(Project, pid) if pid else None
        usage = next((u for u in usages if u.task_id == task.id), None)
        return usage_report.build(task, usage, steps, usages, proj.fresh_session_tokens if proj else None)


@router.get("/{task_id}/events", response_model=list[EventOut])
async def get_events(task_id: str, request: Request, after_seq: int = 0):
    async with SessionLocal() as s:
        await require_task(s, request.state.principal, task_id)
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
    p = request.state.principal
    async with SessionLocal() as s:
        task = await require_task(s, p, task_id)
        await _require_manage(s, p, task)
        if task.status in Status.TERMINAL:
            raise HTTPException(409, f"Task already {task.status}")
    await request.app.state.worker.cancel(task_id)
    try:  # co-guard (Ruling P24): a step that is already cancelled drops the thread's deferred follow-ups
        from ..guard import grants as guard_grants

        await guard_grants.after_task_finished(task_id, submit=request.app.state.worker.submit)
    except Exception:  # noqa: BLE001
        pass
    async with SessionLocal() as s:
        task = await require_task(s, p, task_id)
        return TaskOut.model_validate(task)


@router.post("/{task_id}/retry", response_model=TaskOut)
async def retry_task(task_id: str, request: Request):
    async with SessionLocal() as s:
        p = request.state.principal
        task = await require_task(s, p, task_id)
        await _require_manage(s, p, task)
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
        if isinstance(task.recovery, dict):
            # the record belongs to the automatic re-run of the crashed attempt (a compose failure keeps
            # it pending). A person's Retry starts the step over without it, and never resumes the
            # conversation cut mid-tool: the resume id goes back to what the step had before recovery.
            if "resume_before" in task.recovery:
                task.resume_session_id = task.recovery["resume_before"]
            if task.recovery.get("pending"):
                task.recovery = {**task.recovery, "pending": False}
        await s.commit()
        await s.refresh(task)
        out = TaskOut.model_validate(task)
    await request.app.state.worker.submit(out.id, out.priority, out.created_at)
    return out


@router.post("/{task_id}/duplicate", response_model=TaskOut, status_code=201)
async def duplicate_task(task_id: str, request: Request):
    async with SessionLocal() as s:
        p = request.state.principal
        src = await require_task(s, p, task_id)
        await _require_manage(s, p, src, need_contributor=True)
        project = None
        if src.project_id is not None:
            project = await s.get(Project, src.project_id)
            if project is None:
                raise HTTPException(404, "Project not found")
        new = Task(
            title=f"{src.title} (copy)",
            prompt=src.prompt,
            project=src.project,
            project_id=src.project_id,
            priority=src.priority,
            tags=list(src.tags or []),
            model=src.model,
            max_turns=src.max_turns,
            # the source's budget, else the default a new task gets (build_task)
            max_budget_usd=(src.max_budget_usd if src.max_budget_usd is not None
                            else settings.default_task_budget_usd),
            parent_task_id=src.id,
            origin="user",
            status=Status.QUEUED,
            created_by=request.state.principal.user.id,
        )
        s.add(new)
        await s.flush()
        new.root_id = new.id  # a duplicate is an independent new thread
        if project is not None:
            new.workspace_dir = project.directory  # run in the repo, like build_task
        else:
            ws = settings.workspaces_dir / new.id
            ws.mkdir(parents=True, exist_ok=True)
            # Carry over CLAUDE.md from the source sandbox.
            if src.workspace_dir:
                src_claude = Path(src.workspace_dir) / "CLAUDE.md"
                if src_claude.exists():
                    (ws / "CLAUDE.md").write_text(src_claude.read_text(errors="replace"))
            new.workspace_dir = str(ws)
        await s.commit()
        await s.refresh(new)
        out = TaskOut.model_validate(new)
    await request.app.state.worker.submit(out.id, out.priority, out.created_at)
    return out


def _step_from(parent: Task, *, prompt: str, origin: str, resume: str | None, created_by: str | None) -> Task:
    """The one builder of a thread step (a follow-up or a Continue): the
    parent's thread, workspace and run settings (model, max turns, max budget), queued."""
    return Task(
        title=_default_title(prompt),
        prompt=prompt,
        project=parent.project,
        project_id=parent.project_id,  # an ad-hoc step stays ad-hoc (root rule)
        priority=parent.priority,
        tags=list(parent.tags or []),
        model=parent.model,
        max_turns=parent.max_turns,
        max_budget_usd=parent.max_budget_usd,  # the hard cut and the 80/95 % bands carry on
        parent_task_id=parent.id,
        origin=origin,
        root_id=parent.root_id or parent.id,  # join the parent's thread
        resume_session_id=resume,
        workspace_dir=parent.workspace_dir,  # same workspace = continues context
        status=Status.QUEUED,
        created_by=created_by,
    )


@router.post("/{task_id}/followup", response_model=TaskOut, status_code=201)
async def followup_task(task_id: str, payload: FollowupCreate, request: Request):
    """Continue a task's Claude session with a new prompt (a resumable thread)."""
    async with SessionLocal() as s:
        p = request.state.principal
        parent = await require_task(s, p, task_id)
        await _require_manage(s, p, parent, need_contributor=True)
        if not parent.session_id:
            raise HTTPException(
                409, "Parent has no resumable session yet (must finish a run first)"
            )
        # Claude sessions belong to an account: only the parent's creator
        # resumes it. Anyone else starts a fresh session on their own
        # credential (same thread and workspace).
        sender = p.user.id
        resume = parent.session_id if sender == parent.created_by else None
        child = _step_from(parent, prompt=payload.prompt, origin="user", resume=resume, created_by=sender)
        s.add(child)
        await s.commit()
        await s.refresh(child)
        out = TaskOut.model_validate(child)
    await request.app.state.worker.submit(out.id, out.priority, out.created_at)
    return out


CONTINUE_MODES = ("auto", "resume", "fresh")


async def queue_continue(s, parent: Task, *, mode: str, sender: str | None, auto: bool) -> Task:
    """Add a queued ``continue`` step to ``parent``'s thread (committed; the caller submits it to the
    worker). Under the global follow-up lock (serialised with deny-and-resume's join / defer / spawn
    choice) it re-checks for an active step of the thread (409). ``resume`` needs ``sender`` to own the
    parent's session (409); ``fresh`` never resumes; ``auto`` resumes when it can. ``auto=True``: the
    automatic fresh Continue (marked ``auto_continued``)."""
    from ..guard import grants as guard_grants

    async with guard_grants.followup_lock():
        try:
            await s.refresh(parent)
        except InvalidRequestError:  # deleted while waiting for the lock
            raise HTTPException(404, "Task not found") from None
        root = parent.root_id or parent.id
        active = (await s.execute(select(Task.id).where(
            or_(Task.root_id == root, Task.id == root), Task.status.in_(Status.ACTIVE)).limit(1))).first()
        if active is not None:
            raise HTTPException(409, "A step of this thread is still active")
        can_resume = bool(parent.session_id) and sender == parent.created_by
        if mode == "resume" and not can_resume:
            raise HTTPException(
                409, "Only the session's owner can resume it, and the step needs a session")
        resume = parent.session_id if can_resume and mode != "fresh" else None
        child = _step_from(parent, prompt=CONTINUE_PROMPT, origin="continue", resume=resume,
                           created_by=sender)
        child.auto_continued = bool(auto)
        s.add(child)
        await s.commit()
        await s.refresh(child)
        return child


@router.post("/{task_id}/continue", response_model=TaskOut, status_code=201)
async def continue_task(task_id: str, request: Request, payload: ContinueCreate | None = None):
    """Continue a stopped main-line step: a queued ``continue`` step of its
    thread with the fixed Continue text. ``resume`` needs the sender to own
    the step's session (409 otherwise); ``auto`` resumes when it can, else
    starts fresh with the handoff; ``fresh`` never resumes. Same permission
    as a follow-up."""
    mode = (payload.mode if payload is not None else None) or "auto"
    if mode not in CONTINUE_MODES:
        raise HTTPException(400, "mode must be auto, resume or fresh")
    p = request.state.principal
    async with SessionLocal() as s:
        parent = await require_task(s, p, task_id)
        await _require_manage(s, p, parent, need_contributor=True)
        if parent.origin == "agent":
            raise HTTPException(409, "A dispatched child cannot be continued; continue its thread")
        if parent.status not in Status.TERMINAL:
            raise HTTPException(409, f"Task is still {parent.status}")
        # a live step: 409 before the snapshot (it would describe that step without its secrets);
        # the check under the lock below is the real serialisation
        root = parent.root_id or parent.id
        active = (await s.execute(select(Task.id).where(
            or_(Task.root_id == root, Task.id == root), Task.status.in_(Status.ACTIVE)).limit(1))).first()
        if active is not None:
            raise HTTPException(409, "A step of this thread is still active")
    worker = request.app.state.worker
    # Where the thread stands, saved before the step is queued and outside the
    # global follow-up lock (it runs git). The step is terminal, so it has no
    # live run secrets or outside-commit base to pass.
    try:
        await resume_handoff.snapshot(task_id, "continue")
    except Exception:  # noqa: BLE001 - the Continue still runs (its prompt uses the stored handoff)
        logging.getLogger(__name__).exception("handoff snapshot before continue failed for %s", task_id)
    async with SessionLocal() as s:
        parent = await require_task(s, p, task_id)
        child = await queue_continue(s, parent, mode=mode, sender=p.user.id, auto=False)
        out = TaskOut.model_validate(child)
    await worker.submit(out.id, out.priority, out.created_at)
    return out


@router.delete("/{task_id}", status_code=204)
async def delete_task(task_id: str, request: Request):
    """Delete a task (and its events/artifacts). Deleting a thread root removes
    the thread steps the caller may manage; the others (e.g. steps in projects
    the caller can't see) are detached into threads of their own. Sandbox
    workspaces are removed; project repos are not."""
    async with SessionLocal() as s:
        p = request.state.principal
        task = await require_task(s, p, task_id)
        await _require_manage(s, p, task)
        if task.status in Status.ACTIVE:
            raise HTTPException(409, "Cancel the task before deleting it")
        members, kept = [task], []
        if task.root_id and task.root_id == task.id:
            others = (
                await s.execute(select(Task).where(Task.root_id == task.id, Task.id != task.id))
            ).scalars().all()
            for m in others:
                if await can_see_task(s, p, m) and await _may_manage(s, p, m):
                    members.append(m)
                else:
                    kept.append(m)

        # A finished coordinator root can still have dispatched steps running;
        # deleting them would leave live workers writing to vanished rows.
        if any(m.status in Status.ACTIVE for m in members):
            raise HTTPException(409, "A step in this thread is still running; cancel it first")

        deleted = {m.id for m in members}
        for k in kept:
            k.root_id = k.id
            if k.parent_task_id in deleted:
                k.parent_task_id = None

        ws_root = str(settings.workspaces_dir.resolve())
        pids = {m.project_id for m in members} - {None}
        repos = set()
        if pids:
            rows = await s.execute(select(Project.directory).where(Project.id.in_(pids)))
            repos = {str(Path(d).resolve()) for d in rows.scalars() if d}
        for m in members:
            # Remove the isolated sandbox workspace only (a path inside
            # workspaces_dir) — never a project repo.
            if m.workspace_dir:
                try:
                    wsp = Path(m.workspace_dir).resolve()
                    if str(wsp).startswith(ws_root + os.sep) and str(wsp) not in repos:
                        shutil.rmtree(wsp, ignore_errors=True)
                except OSError:
                    pass
            await s.delete(m)
        # the deleted threads' handoffs (no FK either)
        await s.execute(delete(TaskHandoff).where(TaskHandoff.root_id.in_(deleted)))
        # their usage rows, questions and asks (FK cascade on Postgres; SQLite does not enforce it)
        await s.execute(delete(TaskUsage).where(TaskUsage.task_id.in_(deleted)))
        await s.execute(delete(TaskQuestion).where(TaskQuestion.task_id.in_(deleted)))
        await s.execute(delete(RunAsk).where(RunAsk.task_id.in_(deleted)))
        # co-guard grants of the deleted thread / steps (no FK: they would be orphans)
        await s.execute(delete(ActionGrant).where(
            or_(ActionGrant.root_id.in_(deleted),
                and_(ActionGrant.scope == "once", ActionGrant.task_id.in_(deleted)))))
        await s.commit()


# ---- handoff (phase 3a): where the thread stands. People only (agent tokens are
# outside the allowlist); any viewer reads it, a manager refreshes or closes it.


def _handoff_out(row: TaskHandoff) -> dict:
    return {
        "content": row.content or {},
        "text": row.text or "",
        "version": row.version,
        "status": row.status,
        "reason": row.reason,
        "saved_at": row.saved_at.isoformat() if row.saved_at else None,
        "prev_available": row.prev_text is not None,
    }


async def _thread_handoff(s, task: Task) -> TaskHandoff | None:
    return (
        await s.execute(select(TaskHandoff).where(TaskHandoff.root_id == (task.root_id or task.id)))
    ).scalar_one_or_none()


# ---- notes (phase 3a): what the agent says it is doing. The agent's own token may PUT its own
# task's notes (auth._AGENT_NOTES); people read them, a manager may write them.


def _notes_out(task: Task) -> dict:
    return {"notes": task.notes, "notes_updated_at": task.notes_updated_at.isoformat() if task.notes_updated_at else None}


@router.get("/{task_id}/notes")
async def get_notes(task_id: str, request: Request):
    async with SessionLocal() as s:
        task = await require_task(s, request.state.principal, task_id)
        return _notes_out(task)


@router.put("/{task_id}/notes")
async def put_notes(task_id: str, request: Request):
    p = request.state.principal
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared > resume_handoff.NOTES_BODY_MAX:
        raise HTTPException(413, "Notes are too large")
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > resume_handoff.NOTES_BODY_MAX:
            raise HTTPException(413, "Notes are too large")
    async with SessionLocal() as s:
        if p.is_agent:
            if p.token.task_id != task_id:
                raise HTTPException(403, "Token not allowed for this task")
            task = await s.get(Task, task_id)
            if task is None:
                raise HTTPException(404, "Task not found")
        else:
            task = await require_task(s, p, task_id)
            await _require_manage(s, p, task)
        if "json" in (request.headers.get("content-type") or "").lower():
            try:
                data = json.loads(body.decode("utf-8"))
            except ValueError:
                raise HTTPException(400, "Body is not valid JSON") from None
            text = data.get("text") if isinstance(data, dict) else None
            if not isinstance(text, str):
                raise HTTPException(422, "Body needs a text string")
        else:
            text = body.decode("utf-8", errors="replace")
        worker = request.app.state.worker
        task.notes = resume_handoff.clean_notes(text, worker._run_secrets.get(task_id))
        task.notes_updated_at = datetime.now(timezone.utc)
        await s.commit()
        out = _notes_out(task)
    tdir = guard_runtime.task_dir(task_id)
    if tdir.is_dir():
        try:
            (tdir / "notes.stamp").touch()
        except OSError:
            pass
    try:  # the notes are saved: a failed snapshot must not turn the save into a 500 the agent retries
        await resume_handoff.snapshot(task_id, "notes", secrets=worker._run_secrets.get(task_id),
                                      since=worker._run_since.get(task_id))
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("handoff snapshot after notes failed for %s", task_id)
    return out


# ---- questions (phase 3b): the agent's own token may file and read its own task's questions
# (auth._AGENT_QUESTIONS); a manager may file one; people read the thread's. Answer and dismiss live in
# routers/questions.py (people only).


async def _question_task(s, p, task_id: str) -> Task:
    """The task a question call is about: the agent token's own task only (403 otherwise), or a task the
    person can see (404 otherwise)."""
    if p.is_agent:
        if p.token.task_id != task_id:
            raise HTTPException(403, "Token not allowed for this task")
        task = await s.get(Task, task_id)
        if task is None:
            raise HTTPException(404, "Task not found")
        return task
    return await require_task(s, p, task_id)


@router.post("/{task_id}/questions", response_model=QuestionOut, status_code=201)
async def file_question(task_id: str, payload: QuestionCreate, request: Request):
    from ..questions import service as questions

    p = request.state.principal
    async with SessionLocal() as s:
        task = await _question_task(s, p, task_id)
        if not p.is_agent:
            await _require_manage(s, p, task)
        fields = questions.clean_fields(payload, request.app.state.worker._run_secrets.get(task_id))
        q = await questions.file(s, task, fields, source="agent" if p.is_agent else "person")
        out = questions.out(q, task_title=task.title,
                            can_answer=not p.is_agent and (await _manage_allowed(s, p, task, need_contributor=True))[0])
    await questions.notify(out.id)
    return out


@router.get("/{task_id}/questions", response_model=list[QuestionOut])
async def list_task_questions(task_id: str, request: Request):
    """The agent token: its own task's questions. A person: the questions of the task's thread, on the steps
    they can see, oldest first."""
    from ..questions import service as questions

    p = request.state.principal
    async with SessionLocal() as s:
        task = await _question_task(s, p, task_id)
        if p.is_agent:
            rows = (await s.execute(select(TaskQuestion, Task.title).join(Task, Task.id == TaskQuestion.task_id)
                                    .where(TaskQuestion.task_id == task_id)
                                    .order_by(TaskQuestion.created_at.asc()))).all()
            return [questions.out(q, task_title=title) for q, title in rows]
        rows = (await s.execute(
            select(TaskQuestion, Task).join(Task, Task.id == TaskQuestion.task_id)
            .where(TaskQuestion.root_id == (task.root_id or task.id), await task_visibility_clause(s, p))
            .order_by(TaskQuestion.created_at.asc()))).all()
        res = []
        for q, t in rows:
            can = q.status == "open" and (await _manage_allowed(s, p, t, need_contributor=True))[0]
            res.append(questions.out(q, task_title=t.title, can_answer=can))
        return res


@router.get("/{task_id}/handoff")
async def get_handoff(task_id: str, request: Request):
    async with SessionLocal() as s:
        task = await require_task(s, request.state.principal, task_id)
        row = await _thread_handoff(s, task)
    if row is None:
        raise HTTPException(404, "No handoff for this thread yet")
    return _handoff_out(row)


@router.post("/{task_id}/handoff/refresh")
async def refresh_handoff(task_id: str, request: Request):
    p = request.state.principal
    async with SessionLocal() as s:
        task = await require_task(s, p, task_id)
        await _require_manage(s, p, task)
    # a live run's outside-commit base (F9), so a refresh keeps its outside commits
    worker = request.app.state.worker
    row = await resume_handoff.snapshot(task_id, "manual", secrets=worker._run_secrets.get(task_id),
                                        since=worker._run_since.get(task_id))
    if row is None:
        raise HTTPException(404, "Task not found")
    return _handoff_out(row)


@router.post("/{task_id}/handoff/close")
async def close_handoff(task_id: str, request: Request):
    """Mark the thread's handoff done; the next snapshot reopens it."""
    p = request.state.principal
    async with SessionLocal() as s:
        task = await require_task(s, p, task_id)
        await _require_manage(s, p, task)
        row = await _thread_handoff(s, task)
        if row is None:
            raise HTTPException(404, "No handoff for this thread yet")
        row.status = "closed"
        row.closed_at = datetime.now(timezone.utc)
        row.closed_by = p.user.id
        await s.commit()
        return _handoff_out(row)


def _safe_filename(task: Task, ext: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", (task.title or "report")).strip("-")[:60]
    return f"{base or 'report'}.{ext}"


@router.get("/{task_id}/report")
async def download_report(task_id: str, request: Request, format: str = "pdf"):
    """Download the task's result as a PDF or DOCX report."""
    async with SessionLocal() as s:
        task = await require_task(s, request.state.principal, task_id)
    if format == "pdf":
        data = reports.build_pdf(task)
        media = "application/pdf"
        ext = "pdf"
    elif format == "docx":
        data = reports.build_docx(task)
        media = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        ext = "docx"
    else:
        raise HTTPException(400, "format must be 'pdf' or 'docx'")
    return Response(
        content=data,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{_safe_filename(task, ext)}"'
        },
    )


@router.get("/{task_id}/artifacts", response_model=list[ArtifactOut])
async def list_artifacts(task_id: str, request: Request):
    async with SessionLocal() as s:
        await require_task(s, request.state.principal, task_id)
        rows = (
            await s.execute(select(Artifact).where(Artifact.task_id == task_id))
        ).scalars().all()
    return [ArtifactOut.model_validate(a) for a in rows]


@router.get("/{task_id}/artifacts/{filename:path}")
async def download_artifact(task_id: str, filename: str, request: Request):
    async with SessionLocal() as s:
        task = await require_task(s, request.state.principal, task_id)
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
