"""Phase 7a §3: run a finished run again from a step (F66)."""

import asyncio
import uuid

import pytest

from app.constants import Status
from app.database import SessionLocal
from app.models import Task
from app.workflow_runner import WorkflowRunner
from conftest import login_as
from _runs7a import (  # noqa: F401 - fixtures
    ANN, FAIL, FakeWorker, _end_runs, _finish_tasks, _project, _rows, _run, _tasks, agent, agents, approval,
    fakes, gate, has, make_run, oc, settle, texts, use_runner,
)


async def until_status(rid, want, timeout=5.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while (await _run(rid)).status != want:
        assert loop.time() < end, f"run did not reach {want}"
        await asyncio.sleep(0.01)


def test_completed_run_again_from_the_gate(fakes, agents):
    a, b = agents

    async def go():
        pid = await _project()
        rid = await make_run([agent("Dev", a), gate(pid),
                              agent("Review", b, "review {{prev}} of {{stage1}}")], [pid])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _finish_tasks(rid, 0, text="dev out")
        await r._advance(rid)
        await until_status(rid, "running")
        await settle(r)
        await r._advance(rid)
        await _finish_tasks(rid, 2, text="review out")
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "completed", texts(run)

        await r.rerun_from(rid, 1, ANN)
        run = await _run(rid)
        assert has(run, "Run again from Test by Ann")
        assert run.current_stage == 1
        assert run.stage_state["1"]["attempt"] == 1
        assert run.stage_state["2"] == {"attempt": 1}  # reset to not started, next attempt
        assert run.stage_state["0"]["status"] == "done"  # earlier step keeps its result
        await until_status(rid, "running")
        await settle(r)
        await r._advance(rid)
        run = await _run(rid)
        assert run.current_stage == 2 and run.stage_state["1"]["status"] == "passed"
        rows = await _rows(rid)
        assert [x.attempt for x in rows] == [0, 1]  # the old gate run stays in the history
        assert len({x.log_name for x in rows}) == 2  # its log is not overwritten
        t2 = await _tasks(rid, 2)
        assert sorted(t.stage_attempt for t in t2) == [0, 1]
        new = next(t for t in t2 if t.stage_attempt == 1)
        assert "Gate Test passed" in new.prompt and "dev out" in new.prompt  # {{prev}} and {{stage1}}
        await _finish_tasks(rid, 2, text="again")
        await r._advance(rid)
        assert (await _run(rid)).status == "completed"

    asyncio.run(go())


def test_failed_run_again_from_an_earlier_agent_step(fakes, agents):
    async def go():
        rid = await make_run([agent("Plan", agents[0]), agent("Build", agents[1])])
        w = FakeWorker()
        r = WorkflowRunner(w, 999)
        await r.start_run(rid)
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        await _finish_tasks(rid, 1, Status.FAILED, "boom")
        await r._advance(rid)
        assert (await _run(rid)).status == "failed"
        await r.rerun_from(rid, 0, ANN)
        run = await _run(rid)
        assert run.status == "running" and run.current_stage == 0
        assert run.stage_state["0"]["attempt"] == 1 and run.stage_state["0"]["status"] == "running"
        assert run.stage_state["1"] == {"attempt": 1}
        t0 = await _tasks(rid, 0)
        assert sorted(t.stage_attempt for t in t0) == [0, 1]
        assert next(t for t in t0 if t.stage_attempt == 1).id in w.submitted
        assert len(await _tasks(rid, 1)) == 1  # old tasks stay

    asyncio.run(go())


def test_refused_while_active_or_from_a_step_never_reached(fakes, agents):
    async def go():
        rid = await make_run([agent("Plan", agents[0]), agent("Build", agents[1]), approval("Sign")])
        r = WorkflowRunner(FakeWorker(), 999)
        with pytest.raises(ValueError, match="still going"):
            await r.rerun_from(rid, 0, ANN)  # pending
        await r.start_run(rid)
        with pytest.raises(ValueError, match="still going"):
            await r.rerun_from(rid, 0, ANN)  # running
        await _finish_tasks(rid, 0, Status.FAILED, "x")
        await r._advance(rid)
        assert (await _run(rid)).status == "failed"
        with pytest.raises(ValueError, match="never reached"):
            await r.rerun_from(rid, 1, ANN)
        with pytest.raises(ValueError, match="never reached"):
            await r.rerun_from(rid, 2, ANN)
        with pytest.raises(ValueError, match="does not exist"):
            await r.rerun_from(rid, 7, ANN)
        await r.rerun_from(rid, 0, ANN)  # the failed step itself: allowed
        assert (await _run(rid)).status == "running"

        p = await make_run([approval("Sign")])
        await r.start_run(p)
        assert (await _run(p)).status == "paused"
        with pytest.raises(ValueError, match="still going"):
            await r.rerun_from(p, 0, ANN)

    asyncio.run(go())


def test_run_again_drops_later_approvals_given_ahead(fakes, agents):
    async def go():
        rid = await make_run([agent("Plan", agents[0]), approval("Approve plan"), agent("Build", agents[1])])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await r.approve_ahead(rid, 1, ANN)
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        await _finish_tasks(rid, 2)
        await r._advance(rid)
        assert (await _run(rid)).status == "completed"
        await r.rerun_from(rid, 0, ANN)
        run = await _run(rid)
        assert has(run, "Approve plan: early approval dropped (run again from Plan)")
        assert run.stage_state["1"] == {"attempt": 1}
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "paused" and run.stage_state["1"]["status"] == "waiting_approval"

    asyncio.run(go())


def test_spending_limit_still_holds(fakes, agents):
    async def go():
        rid = await make_run([agent("Plan", agents[0])], budget=1.0)
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        async with SessionLocal() as s:
            for t in await _tasks(rid, 0):
                (await s.get(Task, t.id)).total_cost_usd = 1.0
            await s.commit()
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        assert (await _run(rid)).status == "completed"
        await r.rerun_from(rid, 0, ANN)
        run = await _run(rid)
        assert run.status == "paused" and run.stage_state["0"]["status"] == "budget"
        assert len(await _tasks(rid, 0)) == 1

    asyncio.run(go())


def test_failed_retry_unchanged(fakes, agents):
    """The failed-step Retry keeps working as before (no "Run again" line)."""
    async def go():
        rid = await make_run([agent("Plan", agents[0])])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _finish_tasks(rid, 0, Status.FAILED, "x")
        await r._advance(rid)
        assert await r.retry_run(rid)
        run = await _run(rid)
        assert has(run, "Step Plan retried (attempt 2)") and not has(run, "Run again")

    asyncio.run(go())


def test_api_rerun(client, fakes, agents, monkeypatch):
    from test_gates_api import _grant

    fakes.script["pytest -q"] = ["block"]
    r = WorkflowRunner(FakeWorker(), 999)
    use_runner(monkeypatch, r)

    async def setup():
        pid = await _project()
        rid = await make_run([agent("Plan", agents[0]), gate(pid)], [pid], status="failed", current=1,
                             state={"0": {"attempt": 0, "status": "done", "started_at": "2026-11-25T00:00:00+00:00",
                                          "finished_at": "2026-11-25T00:01:00+00:00"},
                                    "1": {"attempt": 0, "status": "failed", "started_at": "2026-11-25T00:01:00+00:00"}})
        active = await make_run([agent("Plan", agents[0])], [pid], status="running",
                                state={"0": {"attempt": 0, "status": "running"}})
        return pid, rid, active

    pid, rid, active = asyncio.run(setup())
    viewer = login_as(f"r7a-{uuid.uuid4().hex[:8]}@test.local")
    _grant(client, pid, viewer.user_id, "contributor")
    assert viewer.post(f"/workflows/runs/{rid}/stages/1/rerun").status_code == 403
    assert client.post(f"/workflows/runs/{rid}/stages/5/rerun").status_code == 404
    busy = client.post(f"/workflows/runs/{active}/stages/0/rerun")
    assert busy.status_code == 409 and "still going" in busy.json()["detail"]
    ok = client.post(f"/workflows/runs/{rid}/stages/1/rerun")
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "running" and ok.json()["current_stage"] == 1
    # Stop the gate it handed out (in the app's loop) before the fakes are undone.
    assert client.post(f"/workflows/runs/{rid}/cancel").status_code == 200
    assert not [t for t in r._gates.values() if not t.done()]
