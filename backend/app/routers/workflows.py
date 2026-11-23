import asyncio
import re
import time

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ValidationError
from sqlalchemy import and_, or_, select

from ..access import (
    ALL,
    maintained_project_ids,
    require_project_role,
    task_visibility_clause,
    visible_project_ids,
)
from ..auth import ensure_admin
from ..database import SessionLocal
from ..constants import Status
from ..gates import executor, spec, test_results, totals
from ..models import Agent, Project, Task, Workflow, WorkflowGateRun, WorkflowRun
from ..redaction import clean_text
from .. import workflow_templates as wft
from ..workflow_runner import ACTIVE_RUN, gate_status_text
from ..schemas import (
    LiveItemOut,
    LiveStageOut,
    StageReject,
    TaskOut,
    WorkflowCreate,
    WorkflowOut,
    WorkflowRunCreate,
    WorkflowRunOut,
    WorkflowRunSummaryOut,
    WorkflowUpdate,
)

router = APIRouter(prefix="/workflows", tags=["workflows"])


# A starter pipeline built around the dev-team / Canonizer agents. Stage items
# reference agents by name; {{input}} is replaced with the run's input (PR/issue).
PRESETS = [
    {
        "name": "Feature (AI-planned): plan → develop → review → test → deploy",
        "description": "Tech Lead analyses & plans first, then the dev stages build from that plan. Use for vaguer inputs.",
        "stages": [
            {
                "name": "Plan",
                "items": [
                    {"agent": "Canonizer Tech Lead", "prompt": "Analyse this work and produce a precise implementation plan, split into a FRONTEND section and a BACKEND/API section with numbered, specific tasks. Do NOT write code or delegate — output only the plan.\n\nWork: {{input}}"},
                ],
            },
            {
                "name": "Develop",
                "items": [
                    {"agent": "Canonizer Frontend Dev", "prompt": "Implement the FRONTEND section of this plan. Branch off UI-UX-Overhaul and open a PR.\n\nPLAN:\n{{prev}}\n\n(Original request: {{input}})"},
                    {"agent": "Canonizer API Dev", "prompt": "Implement the BACKEND/API section of this plan. Branch off UI-UX-Overhaul and open a PR.\n\nPLAN:\n{{prev}}\n\n(Original request: {{input}})"},
                ],
            },
            {
                "name": "Review",
                "items": [{"agent": "Canonizer Code Reviewer", "prompt": "Review the open PR(s) for: {{input}}. Report findings."}],
            },
            {
                "name": "Test",
                "items": [{"agent": "Canonizer Server Test", "prompt": "Verify the change for: {{input}} on the dev server; run tests and smoke-test."}],
            },
            {
                "name": "Deploy",
                "items": [{"agent": "Canonizer Deploy", "prompt": "Deploy the change for: {{input}} to canonizer-development."}],
            },
        ],
    },
    {
        "name": "Feature: develop → review → test → deploy",
        "description": "Full cycle: build in parallel, review, verify on server, then gated deploy. You control each stage (no AI planning).",
        "stages": [
            {
                "name": "Develop",
                "items": [
                    {"agent": "Canonizer Frontend Dev", "prompt": "Implement the frontend part of: {{input}}. Branch off UI-UX-Overhaul and open a PR."},
                    {"agent": "Canonizer API Dev", "prompt": "Implement the backend/API part of: {{input}}. Branch off UI-UX-Overhaul and open a PR."},
                ],
            },
            {
                "name": "Review",
                "items": [
                    {"agent": "Canonizer Code Reviewer", "prompt": "Review the open PR(s) for: {{input}}. Report findings."},
                ],
            },
            {
                "name": "Test",
                "items": [
                    {"agent": "Canonizer Server Test", "prompt": "Verify the change for: {{input}} on the dev server; run tests and smoke-test."},
                ],
            },
            {
                "name": "Deploy",
                "items": [
                    {"agent": "Canonizer Deploy", "prompt": "Deploy the change for: {{input}} to canonizer-development."},
                ],
            },
        ],
    },
]


@router.get("/presets")
async def list_presets():
    return PRESETS


@router.get("/templates")
async def list_templates():
    return wft.templates_out()


@router.get("/repo-files")
async def repo_files(request: Request, project_id: str = Query(...)):
    async with SessionLocal() as s:
        p = request.state.principal
        if not p.user.active:
            raise HTTPException(404, "Project not found")
        visible = await visible_project_ids(s, p)
        project = await s.get(Project, project_id)
        if project is None or (visible is not ALL and project_id not in visible):
            raise HTTPException(404, "Project not found")
    return await wft.list_repo_files(project)


class ImportBody(BaseModel):
    text: Optional[str] = None
    project_id: Optional[str] = None
    file: Optional[str] = None


@router.post("/import", response_model=WorkflowOut, status_code=201)
async def import_workflow(payload: ImportBody, request: Request):
    ensure_admin(request.state.principal)
    try:
        if payload.text is not None:
            text = payload.text
        elif payload.project_id and payload.file:
            async with SessionLocal() as s:
                project = await s.get(Project, payload.project_id)
            if project is None:
                raise ValueError("Project not found")
            text = await wft.read_repo_file(project, payload.file)
        else:
            raise ValueError("Send text, or project_id and file")
        try:
            data = WorkflowCreate.model_validate(wft.parse_import(text))
        except ValidationError as e:
            err = e.errors()[0]
            raise ValueError(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}") from None
    except ValueError as e:
        raise HTTPException(422, str(e))
    try:
        return await create_workflow(data, request)
    except HTTPException as e:
        if e.status_code == 400:
            raise HTTPException(422, e.detail)
        raise


@router.get("/{workflow_id}/export")
async def export_workflow(workflow_id: str):
    async with SessionLocal() as s:
        wf = await s.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(404, "Workflow not found")
    return Response(wft.export_markdown(wf), media_type="text/markdown; charset=utf-8")


async def _validate_agents(s, stages: list) -> None:
    agents = (await s.execute(select(Agent))).scalars().all()
    known = {a.id for a in agents} | {a.name for a in agents}
    for st in stages:
        if spec.stage_kind(st) != "agent":
            continue
        for item in st.get("items") or []:
            if item.get("agent") not in known:
                raise HTTPException(400, f"Unknown agent in stage '{st.get('name')}': {item.get('agent')}")


async def workflow_projects(s, wf: Workflow) -> list[str]:
    """Distinct non-null project ids of the agents its stages reference
    (each stage item's `agent` is an agent id or exact name)."""
    stages = wf.stages or []
    refs = {
        item.get("agent")
        for st in stages if spec.stage_kind(st) == "agent"
        for item in st.get("items") or []
    }
    refs.discard(None)
    ids = {st.get("project") for st in stages if spec.stage_kind(st) != "agent" and st.get("project")}
    if refs:
        rows = await s.execute(
            select(Agent.id, Agent.name, Agent.project_id).where(Agent.project_id.is_not(None))
        )
        ids |= {r.project_id for r in rows if r.id in refs or r.name in refs}
    return sorted(ids)


async def _normalize(s, stages: list[dict]) -> list[dict]:
    try:
        return await spec.validate_stages(s, stages)
    except ValueError as e:
        raise HTTPException(422, str(e))


def _run_visible(p, run: WorkflowRun, visible) -> bool:
    if visible is ALL or run.created_by == p.user.id:
        return p.user.active
    return bool(set(run.project_ids or []) & visible)


async def _require_run(s, p, run_id: str, manage: bool = False) -> WorkflowRun:
    """404 unless the run is visible; with `manage` (cancel/retry), 403 unless
    the caller is its creator, an admin, or maintainer of any touched project."""
    run = await s.get(WorkflowRun, run_id)
    if run is None or not _run_visible(p, run, await visible_project_ids(s, p)):
        raise HTTPException(404, "Run not found")
    if manage and run.created_by != p.user.id:
        maintained = await maintained_project_ids(s, p)
        if maintained is not ALL and not set(run.project_ids or []) & maintained:
            raise HTTPException(403, "Insufficient role")
    return run


@router.post("", response_model=WorkflowOut, status_code=201)
async def create_workflow(payload: WorkflowCreate, request: Request):
    ensure_admin(request.state.principal)
    stages = [st.model_dump(exclude_unset=True) for st in payload.stages]
    async with SessionLocal() as s:
        stages = await _normalize(s, stages)
        await _validate_agents(s, stages)
        wf = Workflow(name=payload.name, description=payload.description, stages=stages, created_by=request.state.principal.user.id)
        s.add(wf)
        await s.commit()
        await s.refresh(wf)
        return WorkflowOut.model_validate(wf)


@router.get("", response_model=list[WorkflowOut])
async def list_workflows():
    async with SessionLocal() as s:
        rows = (await s.execute(select(Workflow).order_by(Workflow.created_at.desc()))).scalars().all()
    return [WorkflowOut.model_validate(w) for w in rows]


@router.patch("/{workflow_id}", response_model=WorkflowOut)
async def update_workflow(workflow_id: str, payload: WorkflowUpdate, request: Request):
    ensure_admin(request.state.principal)
    data = payload.model_dump(exclude_unset=True)
    async with SessionLocal() as s:
        wf = await s.get(Workflow, workflow_id)
        if wf is None:
            raise HTTPException(404, "Workflow not found")
        if "stages" in data:
            data["stages"] = await _normalize(s, data["stages"])
            await _validate_agents(s, data["stages"])
        for k, v in data.items():
            setattr(wf, k, v)
        await s.commit()
        await s.refresh(wf)
        return WorkflowOut.model_validate(wf)


@router.delete("/{workflow_id}", status_code=204)
async def delete_workflow(workflow_id: str, request: Request):
    ensure_admin(request.state.principal)
    async with SessionLocal() as s:
        wf = await s.get(Workflow, workflow_id)
        if wf is None:
            raise HTTPException(404, "Workflow not found")
        await s.delete(wf)
        await s.commit()


@router.post("/{workflow_id}/run", response_model=WorkflowRunSummaryOut, status_code=201)
async def run_workflow(workflow_id: str, payload: WorkflowRunCreate, request: Request):
    async with SessionLocal() as s:
        wf = await s.get(Workflow, workflow_id)
        if wf is None:
            raise HTTPException(404, "Workflow not found")
        # Contributor on every project the stages touch (no project = anyone).
        project_ids = await workflow_projects(s, wf)
        for pid in project_ids:
            await require_project_role(s, request.state.principal, pid)
        run = WorkflowRun(
            workflow_id=wf.id,
            name=wf.name,
            input=payload.input,
            stages=wf.stages,
            status="pending",
            current_stage=0,
            created_by=request.state.principal.user.id,
            project_ids=project_ids,
        )
        s.add(run)
        await s.commit()
        await s.refresh(run)
        run_id = run.id
        out = WorkflowRunSummaryOut.model_validate(run)
    # Start stage 0 immediately via the runner.
    await request.app.state.workflow_runner.start_run(run_id)
    async with SessionLocal() as s:
        run = await s.get(WorkflowRun, run_id)
        return WorkflowRunSummaryOut.model_validate(run)


@router.get("/runs", response_model=list[WorkflowRunSummaryOut])
async def list_runs(request: Request, limit: int = Query(50, ge=1, le=500)):
    p = request.state.principal
    query = select(WorkflowRun).order_by(WorkflowRun.created_at.desc(), WorkflowRun.id.desc())
    async with SessionLocal() as s:
        visible = await visible_project_ids(s, p)
        if visible is ALL:
            rows = (await s.execute(query.limit(limit))).scalars().all()
            return [WorkflowRunSummaryOut.model_validate(r) for r in rows]
        # Visibility is an overlap test on the JSON `project_ids` column, which
        # has no portable SQL form (SQLite vs Postgres JSON). So walk the runs
        # newest-first in keyset batches on (created_at, id) and keep visible
        # ones until `limit` is reached — never filter after a LIMIT, which
        # would drop rows.
        out, batch, page = [], max(limit, 200), query
        while len(out) < limit:
            rows = (await s.execute(page.limit(batch))).scalars().all()
            out += [r for r in rows if _run_visible(p, r, visible)]
            if len(rows) < batch:
                break
            last = rows[-1]
            page = query.where(
                or_(
                    WorkflowRun.created_at < last.created_at,
                    and_(WorkflowRun.created_at == last.created_at, WorkflowRun.id < last.id),
                )
            )
    return [WorkflowRunSummaryOut.model_validate(r) for r in out[:limit]]


LIVE_MAX = 50
_THREAD_STATE = {
    Status.COMPLETED: "done",
    Status.RUNNING: "current",
    Status.QUEUED: "current",
    Status.AWAITING_APPROVAL: "waiting_approval",
    Status.FAILED: "failed",
    Status.CANCELLED: "failed",
}


_GATE_STATE = {
    "passed": "done", "approved": "done", "failed": "failed", "rejected": "failed",
    "waiting_approval": "waiting_approval", "needs_approval": "waiting_approval",
}


def _gate_stage_state(status, index: int, current: int) -> str:
    """Live state of a gate/approval stage from its stage_state status."""
    if status in _GATE_STATE:
        return _GATE_STATE[status]
    if index < current:
        return "done"
    return "current" if index == current else "next"


def _stage_state(statuses: list[str], index: int, current: int) -> str:
    if statuses:
        if all(st == Status.COMPLETED for st in statuses):
            return "done"
        if any(st in (Status.FAILED, Status.CANCELLED) for st in statuses):
            return "failed"
        if Status.AWAITING_APPROVAL in statuses:
            return "waiting_approval"
    elif index < current:
        # The run has moved past it; the viewer just can't see its tasks.
        return "done"
    return "current" if index == current else "next"


@router.get("/runs/live", response_model=list[LiveItemOut])
async def live_runs(request: Request):
    """Active workflow runs and running coordinator threads the viewer can see,
    newest first, max 50 — the data behind the Home comet scene. Declared before
    `/runs/{run_id}` so "live" isn't taken as a run id."""
    p = request.state.principal
    items: list[LiveItemOut] = []
    async with SessionLocal() as s:
        visible = await visible_project_ids(s, p)
        clause = await task_visibility_clause(s, p)
        # Run visibility is an overlap test on a JSON column (no portable SQL),
        # so filter in Python. That's cheap here: only pending/running/paused
        # runs are loaded, and the number of active runs is small.
        runs = (
            await s.execute(
                select(WorkflowRun)
                .where(WorkflowRun.status.in_(("pending", "running", "paused")))
                .order_by(WorkflowRun.created_at.desc(), WorkflowRun.id.desc())
            )
        ).scalars().all()
        runs = [r for r in runs if _run_visible(p, r, visible)][:LIVE_MAX]
        # Stage task statuses for every run in one grouped query.
        by_run: dict[str, dict[int, list[str]]] = {}
        if runs:
            rows = (
                await s.execute(
                    select(Task.workflow_run_id, Task.stage_index, Task.status, Task.stage_attempt).where(
                        and_(Task.workflow_run_id.in_([r.id for r in runs]), clause)
                    )
                )
            ).all()
            cur = {
                r.id: (r.stage_state or {}) for r in runs
            }
            for run_id, idx, st, att in rows:
                # only the stage's current attempt (a retried stage keeps its old tasks)
                want = int(((cur.get(run_id) or {}).get(str(idx)) or {}).get("attempt") or 0)
                if (att or 0) != want:
                    continue
                by_run.setdefault(run_id, {}).setdefault(idx, []).append(st)
        for run in runs:
            by_stage = by_run.get(run.id, {})
            stages = []
            for i, st in enumerate(run.stages or []):
                sts = by_stage.get(i, [])
                if spec.stage_kind(st) != "agent":
                    state = _gate_stage_state(
                        ((run.stage_state or {}).get(str(i)) or {}).get("status"), i, run.current_stage
                    )
                else:
                    state = _stage_state(sts, i, run.current_stage)
                stages.append(
                    LiveStageOut(
                        name=(st.get("name") if isinstance(st, dict) else None) or f"Stage {i + 1}",
                        state=state,
                        task_count=len(sts),
                    )
                )
            items.append(
                LiveItemOut(
                    kind="run", id=run.id, title=run.name, project_ids=list(run.project_ids or []),
                    status=run.status, created_at=run.created_at, stages=stages,
                )
            )
        # Threads: running/queued root tasks of a coordinator agent (not stage tasks).
        roots = (
            await s.execute(
                select(Task)
                .join(Agent, Agent.id == Task.agent_id)
                .where(
                    Agent.coordinator.is_(True),
                    Task.parent_task_id.is_(None),
                    Task.workflow_run_id.is_(None),
                    Task.status.in_((Status.RUNNING, Status.QUEUED)),
                    clause,
                )
                .order_by(Task.created_at.desc(), Task.id.desc())
                .limit(LIVE_MAX)
            )
        ).scalars().all()
        # All visible children of those roots in one query.
        kids_of: dict[str, list[Task]] = {}
        if roots:
            kids = (
                await s.execute(
                    select(Task)
                    .where(and_(Task.parent_task_id.in_([r.id for r in roots]), clause))
                    .order_by(Task.created_at, Task.id)
                )
            ).scalars().all()
            for k in kids:
                kids_of.setdefault(k.parent_task_id, []).append(k)
        for root in roots:
            kids = kids_of.get(root.id, [])
            pids = list(dict.fromkeys(t.project_id for t in [root, *kids] if t.project_id))
            items.append(
                LiveItemOut(
                    kind="thread", id=root.id, title=root.title, project_ids=pids,
                    status=root.status, created_at=root.created_at,
                    stages=[
                        LiveStageOut(name=k.title, state=_THREAD_STATE.get(k.status, "next"), task_count=1)
                        for k in kids
                    ],
                )
            )
    items.sort(key=lambda i: i.created_at, reverse=True)
    return items[:LIVE_MAX]


_PROGRESS_TTL = 2.0
_PROGRESS_TAIL = 256 * 1024
_LOG_TAIL = 64 * 1024
_LOG_LINES = 200
_OTHERS_MAX = 5
_progress_cache: dict[str, tuple[float, str | None]] = {}


def _read_tail(path, size: int) -> tuple[str, bool]:
    """The last `size` bytes of a file as text, and whether anything was cut."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            end = f.tell()
            start = max(0, end - size)
            f.seek(start)
            data = f.read(size)
    except OSError:
        return "", False
    return data.decode("utf-8", errors="replace"), start > 0


def _log_path(run_id: str, log_name):
    if not log_name or not isinstance(log_name, str) or log_name != re.sub(r"[^A-Za-z0-9._-]", "", log_name):
        return None
    if log_name in (".", ".."):
        return None
    try:
        return executor._run_dir(run_id) / log_name
    except ValueError:
        return None


async def _live_with_progress(runner, run_id: str) -> dict | None:
    live = runner.live_gate(run_id)
    if not live:
        _progress_cache.pop(run_id, None)
        return None
    live = dict(live)
    log_name = live.pop("log_name", None)
    now = time.monotonic()
    hit = _progress_cache.get(run_id)
    if hit and now - hit[0] < _PROGRESS_TTL:
        line = hit[1]
    else:
        line = None
        path = _log_path(run_id, log_name)
        if path is not None:
            def work():
                text, _ = _read_tail(path, _PROGRESS_TAIL)
                prog = test_results.progress(text) if text else None
                return test_results.progress_line(prog) if prog else None

            try:
                line = await asyncio.to_thread(work)
            except Exception:  # noqa: BLE001 - progress is best effort
                line = None
        _progress_cache[run_id] = (now, line)
        if len(_progress_cache) > 200:
            for k in [k for k, v in _progress_cache.items() if now - v[0] > 60]:
                _progress_cache.pop(k, None)
    live["progress_line"] = line
    return live


async def _held_others(s, p, run: WorkflowRun, visible) -> tuple[list[str], int]:
    """Names (max 5) of other active runs sharing a project that the viewer can see,
    and how many such runs are not named."""
    mine = set(run.project_ids or [])
    if not mine:
        return [], 0
    rows = (
        await s.execute(
            select(WorkflowRun)
            .where(WorkflowRun.status.in_(list(ACTIVE_RUN)), WorkflowRun.id != run.id)
            .order_by(WorkflowRun.created_at.desc(), WorkflowRun.id.desc())
        )
    ).scalars().all()
    seen = [r.name for r in rows if mine & set(r.project_ids or []) and _run_visible(p, r, visible)]
    return seen[:_OTHERS_MAX], max(0, len(seen) - _OTHERS_MAX)


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request):
    """Run detail + its tasks grouped by stage (the board). Only the stage tasks
    the viewer can see are listed."""
    p = request.state.principal
    runner = request.app.state.workflow_runner
    async with SessionLocal() as s:
        run = await _require_run(s, p, run_id)
        visible = await visible_project_ids(s, p)
        clause = await task_visibility_clause(s, p)
        tasks = (
            await s.execute(
                select(Task)
                .where(and_(Task.workflow_run_id == run.id, clause))
                .order_by(Task.created_at)
            )
        ).scalars().all()
        run_out = WorkflowRunOut.model_validate(run)
        task_outs = [TaskOut.model_validate(t) for t in tasks]
        grows = (
            await s.execute(
                select(WorkflowGateRun)
                .where(WorkflowGateRun.run_id == run.id)
                .order_by(WorkflowGateRun.started_at.desc(), WorkflowGateRun.id.desc())
                .limit(200)
            )
        ).scalars().all()
        hidden = {
            i for i, st in enumerate(run.stages or [])
            if spec.stage_kind(st) == "gate" and _gate_hidden(st, visible)
        }
        gate_runs = [_gate_run_out(g) for g in grows if g.stage_index not in hidden]
        live = await _live_with_progress(runner, run.id)
        if live and live.get("stage_index") in hidden:
            live = None
        stage_state = {k: dict(v) if isinstance(v, dict) else {} for k, v in (run.stage_state or {}).items()}
        held = [
            k for k, v in stage_state.items()
            if v.get("status") == "held"
        ]
        others: tuple[list[str], int] | None = None
        if held:
            others = await _held_others(s, p, run, visible)
        for i, st in enumerate(run.stages or []):
            kind = spec.stage_kind(st)
            if kind == "agent":
                continue
            entry = stage_state.setdefault(str(i), {})
            if kind == "gate":
                lv = live if live and live.get("stage_index") == i else None
                entry["status_text"] = gate_status_text(st, entry, lv)
            if i in hidden:
                for k in ("summary", "hits", "reason", "others", "others_more", "flaky", "decided_by"):
                    entry.pop(k, None)
                entry["hidden"] = True
            elif entry.get("status") == "held" and others is not None:
                entry["others"], entry["others_more"] = others
        tot = await totals.compute(s, run)
        tot["line"] = totals.line(tot)
    out = run_out.model_dump()
    out["stage_state"] = stage_state
    return {
        "run": out,
        "tasks": [t.model_dump() for t in task_outs],
        "stage_state": stage_state,
        "gate_runs": gate_runs,
        "live": live,
        "totals": tot,
        "log": out["log"],
    }


def _gate_hidden(stage: dict, visible) -> bool:
    """True when the gate's project is one the viewer cannot see."""
    pid = stage.get("project") if isinstance(stage, dict) else None
    return bool(pid) and visible is not ALL and pid not in visible


def _iso(v):
    return v.isoformat() if v else None


def _gate_run_out(g: WorkflowGateRun) -> dict:
    return {
        "id": g.id, "run_id": g.run_id, "stage_index": g.stage_index, "attempt": g.attempt,
        "handout": g.handout, "command": g.command, "kind": g.kind,
        "started_at": _iso(g.started_at), "finished_at": _iso(g.finished_at),
        "duration_ms": g.duration_ms, "exit_code": g.exit_code, "outcome": g.outcome,
        "runner": g.runner, "passed": g.passed, "failed": g.failed, "skipped": g.skipped,
        "failing": g.failing or [], "flaky": g.flaky, "code_state": g.code_state,
        "has_log": bool(g.log_name),
    }


def _stage_at(run: WorkflowRun, i: int) -> dict:
    stages = run.stages or []
    if not 0 <= i < len(stages):
        raise HTTPException(404, "Step not found")
    return stages[i] if isinstance(stages[i], dict) else {}


@router.post("/runs/{run_id}/stages/{i}/approve")
async def approve_stage(run_id: str, i: int, request: Request):
    p = request.state.principal
    async with SessionLocal() as s:
        _stage_at(await _require_run(s, p, run_id, manage=True), i)
    try:
        await request.app.state.workflow_runner.approve_stage(run_id, i, p.user)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    return await _run_after(run_id)


@router.post("/runs/{run_id}/stages/{i}/reject")
async def reject_stage(run_id: str, i: int, payload: StageReject, request: Request):
    p = request.state.principal
    async with SessionLocal() as s:
        _stage_at(await _require_run(s, p, run_id, manage=True), i)
    try:
        await request.app.state.workflow_runner.reject_stage(run_id, i, p.user, payload.reason)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    return await _run_after(run_id)


@router.post("/runs/{run_id}/stages/{i}/allow")
async def allow_gate(run_id: str, i: int, request: Request):
    p = request.state.principal
    async with SessionLocal() as s:
        run = await _require_run(s, p, run_id)
        stage = _stage_at(run, i)
        maintained = await maintained_project_ids(s, p)
        if maintained is not ALL and (stage.get("project") not in maintained):
            raise HTTPException(403, "Insufficient role")
    try:
        await request.app.state.workflow_runner.allow_gate(run_id, i, p.user)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    return await _run_after(run_id)


async def _run_after(run_id: str) -> WorkflowRunSummaryOut:
    async with SessionLocal() as s:
        return WorkflowRunSummaryOut.model_validate(await s.get(WorkflowRun, run_id))


@router.get("/runs/{run_id}/gate-runs/{rid}/log")
async def gate_run_log(run_id: str, rid: str, request: Request):
    """The last 200 lines (max 64 KB) of one gate command's log, secrets redacted."""
    async with SessionLocal() as s:
        run = await _require_run(s, request.state.principal, run_id)
        g = await s.get(WorkflowGateRun, rid)
        if g is None or g.run_id != run.id:
            raise HTTPException(404, "Gate run not found")
        stages = run.stages or []
        if 0 <= g.stage_index < len(stages) and _gate_hidden(stages[g.stage_index], await visible_project_ids(s, request.state.principal)):
            raise HTTPException(404, "Gate run not found")
        pids = {
            st.get("project") for st in (run.stages or [])
            if isinstance(st, dict) and spec.stage_kind(st) == "gate" and st.get("project")
        }
        secrets: set[str] = set()
        if pids:
            envs = (await s.execute(select(Project.env).where(Project.id.in_(pids)))).scalars().all()
            for env in envs:
                for v in (env or {}).values() if isinstance(env, dict) else []:
                    if isinstance(v, str) and len(v) >= 6:
                        secrets.add(v)
        path = _log_path(run.id, g.log_name)
    if path is None:
        return {"lines": [], "truncated": False}
    ordered = sorted(secrets, key=len, reverse=True)

    def work():
        text, cut = _read_tail(path, _LOG_TAIL)
        if not text:
            return {"lines": [], "truncated": False}
        for v in ordered:
            text = text.replace(v, "[redacted]")
        text = clean_text(text)
        lines = text.splitlines()
        if cut and lines:
            lines = lines[1:]  # the first line is probably cut mid-way
        over = len(lines) > _LOG_LINES
        return {"lines": lines[-_LOG_LINES:], "truncated": cut or over}

    return await asyncio.to_thread(work)


@router.post("/runs/{run_id}/cancel", response_model=WorkflowRunSummaryOut)
async def cancel_run(run_id: str, request: Request):
    async with SessionLocal() as s:
        await _require_run(s, request.state.principal, run_id, manage=True)
    # Halt the run AND stop its in-flight stage tasks.
    await request.app.state.workflow_runner.cancel_run(run_id)
    async with SessionLocal() as s:
        return WorkflowRunSummaryOut.model_validate(await s.get(WorkflowRun, run_id))


@router.post("/runs/{run_id}/retry", response_model=WorkflowRunSummaryOut)
async def retry_run(run_id: str, request: Request):
    """Re-run the failed stage and continue the pipeline."""
    async with SessionLocal() as s:
        run = await _require_run(s, request.state.principal, run_id, manage=True)
        if run.status != "failed":
            raise HTTPException(400, "Only a failed run can be retried")
    await request.app.state.workflow_runner.retry_run(run_id)
    async with SessionLocal() as s:
        return WorkflowRunSummaryOut.model_validate(await s.get(WorkflowRun, run_id))
