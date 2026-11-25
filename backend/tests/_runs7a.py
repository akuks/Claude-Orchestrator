"""Shared helpers for the Phase 7a run tests (approve ahead, project check lock, run again,
per-step usage). Built on test_gates_runner's fakes: no real command, no real CLI."""

import asyncio

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import WorkflowRun
from app import workflow_runner as wr
from test_gates_runner import (  # noqa: F401 - fixtures and helpers re-exported
    ANN, FAIL, FakeWorker, Fakes, _finish_tasks, _project, _rows, _run, _tasks, agents, fakes, gate,
    has, oc, settle, texts, wait_for,
)

NAME = "r7a"


async def make_run(stages, pids=(), budget=None, status="pending", current=0, state=None) -> str:
    async with SessionLocal() as s:
        run = WorkflowRun(workflow_id="w", name=NAME, input="the input", stages=stages,
                          project_ids=list(pids), status=status, current_stage=current,
                          budget_usd=budget, stage_state=state or {})
        s.add(run)
        await s.commit()
        return run.id


def approval(name="Approve plan", ahead=True):
    """An approval step; ahead=True (the default here) opts in to approving ahead, None leaves
    the key out (refused, like False)."""
    st = {"name": name, "kind": "approval", "message": "ok?", "before": []}
    if ahead is not None:
        st["ahead"] = ahead
    return st


def agent(name, a, prompt="do {{input}}"):
    return {"name": name, "items": [{"agent": a, "prompt": prompt}]}


def lock_owners(run_id) -> list:
    return [(p, o) for p, o in wr._PROJECT_LOCKS.items() if o[0] == run_id]


@pytest.fixture(autouse=True)
def _end_runs(client, monkeypatch):
    """Keep the app's background runner off this file's runs while a test runs, and end the runs
    a test left active so it never picks them up later; forget their project locks."""
    async def _no_tick():
        return None

    monkeypatch.setattr(app.state.workflow_runner, "_tick", _no_tick)
    yield

    async def end():
        async with SessionLocal() as s:
            rows = (await s.execute(select(WorkflowRun).where(
                WorkflowRun.name == NAME, WorkflowRun.status.in_(list(wr.ACTIVE_RUN))))).scalars().all()
            for run in rows:
                run.status = "failed"
                wr.release_project_locks(run.id)
            await s.commit()

    asyncio.run(end())


def use_runner(monkeypatch, runner):
    """Serve the routes from `runner` (setitem on the backing dict, never setattr on app.state)."""
    monkeypatch.setitem(app.state._state, "workflow_runner", runner)
