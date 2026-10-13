from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from ..database import SessionLocal
from ..models import Agent, Task, Workflow, WorkflowRun
from ..schemas import (
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


@router.post("", response_model=WorkflowOut, status_code=201)
async def create_workflow(payload: WorkflowCreate):
    stages = [st.model_dump() for st in payload.stages]
    async with SessionLocal() as s:
        await _validate_agents(s, stages)
        wf = Workflow(name=payload.name, description=payload.description, stages=stages)
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
async def update_workflow(workflow_id: str, payload: WorkflowUpdate):
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
async def delete_workflow(workflow_id: str):
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
        run = WorkflowRun(
            workflow_id=wf.id,
            name=wf.name,
            input=payload.input,
            stages=wf.stages,
            status="pending",
            current_stage=0,
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
async def list_runs(limit: int = 50):
    async with SessionLocal() as s:
        rows = (
            await s.execute(select(WorkflowRun).order_by(WorkflowRun.created_at.desc()).limit(limit))
        ).scalars().all()
    return [WorkflowRunOut.model_validate(r) for r in rows]


@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    """Run detail + its tasks grouped by stage (the board)."""
    async with SessionLocal() as s:
        run = await s.get(WorkflowRun, run_id)
        if run is None:
            raise HTTPException(404, "Run not found")
        tasks = (
            await s.execute(
                select(Task).where(Task.workflow_run_id == run.id).order_by(Task.created_at)
            )
        ).scalars().all()
        run_out = WorkflowRunOut.model_validate(run)
        task_outs = [TaskOut.model_validate(t) for t in tasks]
    return {"run": run_out.model_dump(), "tasks": [t.model_dump() for t in task_outs]}


@router.post("/runs/{run_id}/cancel", response_model=WorkflowRunOut)
async def cancel_run(run_id: str):
    async with SessionLocal() as s:
        run = await s.get(WorkflowRun, run_id)
        if run is None:
            raise HTTPException(404, "Run not found")
        if run.status in ("pending", "running", "paused"):
            run.status = "failed"  # halt
            await s.commit()
            await s.refresh(run)
        return WorkflowRunOut.model_validate(run)
