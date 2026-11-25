"""Phase 7a §1: approve an approval step ahead of time (F123)."""

import asyncio
import uuid

import pytest

from app.constants import Status
from app.database import SessionLocal
from app.gates import spec
from app.models import Project, WorkflowRun
from app.workflow_runner import AHEAD_REFUSED, WorkflowRunner
from conftest import login_as
from _runs7a import (  # noqa: F401 - fixtures
    ANN, FakeWorker, _end_runs, _finish_tasks, _run, _tasks, agent, agents, approval, fakes, has,
    make_run, texts, use_runner,
)

BOB = type("U", (), {"name": "Bob", "id": "bob-id"})()


def test_approved_ahead_step_passes_when_reached(fakes, agents):
    a, b = agents

    async def go():
        rid = await make_run([agent("Plan", a), approval("Approve plan"), agent("Build", b, "build {{prev}}")])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await r.approve_ahead(rid, 1, BOB)
        run = await _run(rid)
        ahead = run.stage_state["1"]["ahead"]
        assert ahead["by_name"] == "Bob" and ahead["by"] == "bob-id" and ahead["at"]
        assert run.current_stage == 0 and run.status == "running"
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        run = await _run(rid)
        st = run.stage_state["1"]
        assert st["status"] == "approved" and st["decided_by"] == "Bob" and st["decided_at"]
        assert st["summary"] == "Approved ahead by Bob"
        assert run.current_stage == 2 and run.status == "running"
        assert texts(run).count("Approve plan: approved ahead by Bob") == 1
        t = await _tasks(rid, 2)
        assert len(t) == 1 and "Approved ahead by Bob" in t[0].prompt

    asyncio.run(go())


def test_two_approved_ahead_in_a_row_and_last_step(fakes, agents):
    async def go():
        rid = await make_run([agent("Plan", agents[0]), approval("One"), approval("Two")])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await r.approve_ahead(rid, 1, ANN)
        await r.approve_ahead(rid, 2, BOB)
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "completed"
        assert has(run, "One: approved ahead by Ann") and has(run, "Two: approved ahead by Bob")

    asyncio.run(go())


def test_refused_on_release_step_reached_step_and_non_approval(fakes, agents):
    async def go():
        rid = await make_run([approval("Approve plan"), agent("Build", agents[0]),
                              approval("Approve release", ahead=False)])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        assert (await _run(rid)).stage_state["0"]["status"] == "waiting_approval"
        with pytest.raises(ValueError, match="already been reached"):
            await r.approve_ahead(rid, 0, ANN)  # the step the run is on now
        with pytest.raises(ValueError, match=AHEAD_REFUSED):
            await r.approve_ahead(rid, 2, ANN)
        with pytest.raises(ValueError, match="not an approval step"):
            await r.approve_ahead(rid, 1, ANN)
        await r.approve_stage(rid, 0, ANN)
        with pytest.raises(ValueError, match="already been reached"):
            await r.approve_ahead(rid, 0, ANN)  # a step already passed
        run = await _run(rid)
        assert "ahead" not in run.stage_state.get("2", {})

    asyncio.run(go())


def test_idempotent_and_withdraw(fakes, agents):
    async def go():
        rid = await make_run([agent("Plan", agents[0]), approval("Approve plan")])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await r.approve_ahead(rid, 1, ANN)
        first = (await _run(rid)).stage_state["1"]["ahead"]
        await r.approve_ahead(rid, 1, BOB)  # idempotent: the first approval stands
        run = await _run(rid)
        assert run.stage_state["1"]["ahead"] == first
        assert texts(run).count("Approve plan: early approval given by Ann") == 1
        await r.withdraw_ahead(rid, 1, BOB)
        await r.withdraw_ahead(rid, 1, BOB)  # nothing left: no-op
        run = await _run(rid)
        assert "1" not in run.stage_state
        assert texts(run).count("Approve plan: early approval withdrawn by Bob") == 1
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "paused" and run.stage_state["1"]["status"] == "waiting_approval"

    asyncio.run(go())


def test_dropped_on_cancel(fakes, agents):
    async def go():
        rid = await make_run([agent("Plan", agents[0]), approval("Approve plan")])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await r.approve_ahead(rid, 1, ANN)
        assert await r.cancel_run(rid)
        run = await _run(rid)
        assert run.status == "failed" and "1" not in run.stage_state
        assert has(run, "Approve plan: early approval dropped (the run was cancelled)")

    asyncio.run(go())


def test_dropped_on_failure_never_applied(fakes, agents):
    async def go():
        rid = await make_run([agent("Plan", agents[0]), approval("Approve plan")])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await r.approve_ahead(rid, 1, ANN)
        await _finish_tasks(rid, 0, Status.FAILED, "boom")
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "failed" and "1" not in run.stage_state
        lines = texts(run)
        assert lines.index("Step Plan failed: 1 task failed") < lines.index(
            "Approve plan: early approval dropped (the run failed)")
        # Retry of the failed step: the approval is gone, so the step waits for a person.
        assert await r.retry_run(rid)
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "paused" and run.stage_state["1"]["status"] == "waiting_approval"

    asyncio.run(go())


def test_survives_a_server_restart(fakes, agents):
    async def go():
        rid = await make_run([agent("Plan", agents[0]), approval("Approve plan")])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await r.approve_ahead(rid, 1, ANN)
        await _finish_tasks(rid, 0)
        r2 = WorkflowRunner(FakeWorker(), 999)  # a new process: only the database is left
        await r2._advance(rid)
        run = await _run(rid)
        assert run.status == "completed" and run.stage_state["1"]["decided_by"] == "Ann"

    asyncio.run(go())


# --- spec and templates ------------------------------------------------------------------


def _validate(stages):
    async def go():
        async with SessionLocal() as s:
            return await spec.validate_stages(s, stages)

    return asyncio.run(go())


def test_spec_accepts_ahead_on_approval_only(client):
    out = _validate([
        {"name": "A", "kind": "approval", "ahead": False},
        {"name": "B", "kind": "approval", "ahead": True},
        {"name": "C", "kind": "approval"},
        {"name": "D", "items": [{"agent": "x", "prompt": "p"}], "ahead": False},
    ])
    assert out[0]["ahead"] is False
    assert "ahead" not in out[1] and "ahead" not in out[2] and "ahead" not in out[3]
    with pytest.raises(ValueError, match="ahead must be true or false"):
        _validate([{"name": "A", "kind": "approval", "ahead": "no"}])


# --- API -----------------------------------------------------------------------------------


def _project_row(tmp_path):
    async def go():
        async with SessionLocal() as s:
            p = Project(name=(n := f"r7a-{uuid.uuid4().hex[:8]}"), slug=n, directory=str(tmp_path))
            s.add(p)
            await s.commit()
            return p.id

    return asyncio.run(go())


def test_api_approve_ahead_and_withdraw(client, fakes, agents, monkeypatch, tmp_path):
    pid = _project_row(tmp_path)
    r = WorkflowRunner(FakeWorker(), 999)
    use_runner(monkeypatch, r)
    rid = asyncio.run(make_run([agent("Plan", agents[0]), approval("Approve plan"),
                                approval("Approve release", ahead=False)], [pid],
                               status="running", state={"0": {"attempt": 0, "status": "running"}}))
    base = f"/workflows/runs/{rid}/stages"
    stranger = login_as(f"r7a-{uuid.uuid4().hex[:8]}@test.local")
    assert stranger.post(f"{base}/1/approve-ahead").status_code == 404  # cannot even see it
    j = client.post(f"{base}/1/approve-ahead")
    assert j.status_code == 200, j.text
    assert j.json()["id"] == rid and j.json()["status"] == "running"
    assert client.post(f"{base}/1/approve-ahead").status_code == 200  # idempotent
    d = client.get(f"/workflows/runs/{rid}").json()
    ahead = d["stage_state"]["1"]["ahead"]
    assert set(ahead) == {"by_name", "at"} and ahead["by_name"]  # no user id on the wire
    assert d["run"]["stages"][2]["ahead"] is False
    rel = client.post(f"{base}/2/approve-ahead")
    assert rel.status_code == 409 and rel.json()["detail"] == AHEAD_REFUSED
    assert client.post(f"{base}/0/approve-ahead").status_code == 409  # reached
    assert client.post(f"{base}/9/approve-ahead").status_code == 404
    assert client.delete(f"{base}/1/approve-ahead").status_code == 200
    d = client.get(f"/workflows/runs/{rid}").json()
    assert "ahead" not in d["stage_state"].get("1", {})


def test_api_needs_manage_role(client, fakes, agents, monkeypatch, tmp_path):
    from test_gates_api import _grant

    pid = _project_row(tmp_path)
    r = WorkflowRunner(FakeWorker(), 999)
    use_runner(monkeypatch, r)
    rid = asyncio.run(make_run([agent("Plan", agents[0]), approval("Approve plan")], [pid],
                               status="running", state={"0": {"attempt": 0, "status": "running"}}))
    viewer = login_as(f"r7a-{uuid.uuid4().hex[:8]}@test.local")
    _grant(client, pid, viewer.user_id, "contributor")
    assert viewer.post(f"/workflows/runs/{rid}/stages/1/approve-ahead").status_code == 403
    maint = login_as(f"r7a-{uuid.uuid4().hex[:8]}@test.local")
    _grant(client, pid, maint.user_id, "maintainer")
    assert maint.post(f"/workflows/runs/{rid}/stages/1/approve-ahead").status_code == 200

    async def check():
        async with SessionLocal() as s:
            return (await s.get(WorkflowRun, rid)).stage_state["1"]["ahead"]["by"]

    assert asyncio.run(check()) == maint.user_id
