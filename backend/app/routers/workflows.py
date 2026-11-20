from fastapi import APIRouter, HTTPException, Query, Request
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
from ..models import Agent, Task, Workflow, WorkflowRun
from ..schemas import (
    LiveItemOut,
    LiveStageOut,
    TaskOut,
    WorkflowCreate,
    WorkflowOut,
    WorkflowRunCreate,
    WorkflowRunOut,
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


async def _validate_agents(s, stages: list) -> None:
    agents = (await s.execute(select(Agent))).scalars().all()
    known = {a.id for a in agents} | {a.name for a in agents}
    for st in stages:
        for item in st.get("items", []):
            if item.get("agent") not in known:
                raise HTTPException(400, f"Unknown agent in stage '{st.get('name')}': {item.get('agent')}")


async def workflow_projects(s, wf: Workflow) -> list[str]:
    """Distinct non-null project ids of the agents its stages reference
    (each stage item's `agent` is an agent id or exact name)."""
    refs = {item.get("agent") for st in (wf.stages or []) for item in st.get("items", [])}
    refs.discard(None)
    if not refs:
        return []
    rows = await s.execute(
        select(Agent.id, Agent.name, Agent.project_id).where(Agent.project_id.is_not(None))
    )
    return sorted({r.project_id for r in rows if r.id in refs or r.name in refs})


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
    stages = [st.model_dump() for st in payload.stages]
    async with SessionLocal() as s:
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
    if "stages" in data:
        data["stages"] = [st for st in data["stages"]]  # already dicts via model_dump
    async with SessionLocal() as s:
        wf = await s.get(Workflow, workflow_id)
        if wf is None:
            raise HTTPException(404, "Workflow not found")
        if "stages" in data:
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


@router.post("/{workflow_id}/run", response_model=WorkflowRunOut, status_code=201)
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
        out = WorkflowRunOut.model_validate(run)
    # Start stage 0 immediately via the runner.
    await request.app.state.workflow_runner.start_run(run_id)
    async with SessionLocal() as s:
        run = await s.get(WorkflowRun, run_id)
        return WorkflowRunOut.model_validate(run)


@router.get("/runs", response_model=list[WorkflowRunOut])
async def list_runs(request: Request, limit: int = Query(50, ge=1, le=500)):
    p = request.state.principal
    query = select(WorkflowRun).order_by(WorkflowRun.created_at.desc(), WorkflowRun.id.desc())
    async with SessionLocal() as s:
        visible = await visible_project_ids(s, p)
        if visible is ALL:
            rows = (await s.execute(query.limit(limit))).scalars().all()
            return [WorkflowRunOut.model_validate(r) for r in rows]
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
    return [WorkflowRunOut.model_validate(r) for r in out[:limit]]


LIVE_MAX = 50
_THREAD_STATE = {
    Status.COMPLETED: "done",
    Status.RUNNING: "current",
    Status.QUEUED: "current",
    Status.AWAITING_APPROVAL: "waiting_approval",
    Status.FAILED: "failed",
    Status.CANCELLED: "failed",
}


def _stage_state(statuses: list[str], index: int, current: int) -> str:
    if statuses:
        if all(st == Status.COMPLETED for st in statuses):
            return "done"
        if any(st in (Status.FAILED, Status.CANCELLED) for st in statuses):
            return "failed"
        if Status.AWAITING_APPROVAL in statuses:
            return "waiting_approval"
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
        for run in (r for r in runs if _run_visible(p, r, visible)):
            tasks = (
                await s.execute(
                    select(Task.stage_index, Task.status).where(
                        and_(Task.workflow_run_id == run.id, clause)
                    )
                )
            ).all()
            by_stage: dict[int, list[str]] = {}
            for idx, st in tasks:
                by_stage.setdefault(idx, []).append(st)
            stages = []
            for i, st in enumerate(run.stages or []):
                sts = by_stage.get(i, [])
                stages.append(
                    LiveStageOut(
                        name=(st.get("name") if isinstance(st, dict) else None) or f"Stage {i + 1}",
                        state=_stage_state(sts, i, run.current_stage),
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
        for root in roots:
            kids = (
                await s.execute(
                    select(Task)
                    .where(and_(Task.parent_task_id == root.id, clause))
                    .order_by(Task.created_at, Task.id)
                )
            ).scalars().all()
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


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request):
    """Run detail + its tasks grouped by stage (the board). Only the stage tasks
    the viewer can see are listed."""
    p = request.state.principal
    async with SessionLocal() as s:
        run = await _require_run(s, p, run_id)
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
    return {"run": run_out.model_dump(), "tasks": [t.model_dump() for t in task_outs]}


@router.post("/runs/{run_id}/cancel", response_model=WorkflowRunOut)
async def cancel_run(run_id: str, request: Request):
    async with SessionLocal() as s:
        await _require_run(s, request.state.principal, run_id, manage=True)
    # Halt the run AND stop its in-flight stage tasks.
    await request.app.state.workflow_runner.cancel_run(run_id)
    async with SessionLocal() as s:
        return WorkflowRunOut.model_validate(await s.get(WorkflowRun, run_id))


@router.post("/runs/{run_id}/retry", response_model=WorkflowRunOut)
async def retry_run(run_id: str, request: Request):
    """Re-run the failed stage and continue the pipeline."""
    async with SessionLocal() as s:
        run = await _require_run(s, request.state.principal, run_id, manage=True)
        if run.status != "failed":
            raise HTTPException(400, "Only a failed run can be retried")
    await request.app.state.workflow_runner.retry_run(run_id)
    async with SessionLocal() as s:
        return WorkflowRunOut.model_validate(await s.get(WorkflowRun, run_id))
