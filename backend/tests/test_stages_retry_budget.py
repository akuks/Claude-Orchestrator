"""Phase 5c Task 3: auto-retry once and the per-run budget cap."""

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.constants import Status
from app.database import SessionLocal
from app.main import app
from app.models import Agent, Project, Task, WorkflowRun
from app.workflow_runner import WorkflowRunner
from conftest import login_as

ANN = SimpleNamespace(name="Ann", email="ann@test.local")


class FakeWorker:
    def __init__(self):
        self.submitted, self.cancelled = [], []

    async def submit(self, tid, prio, created):
        self.submitted.append(tid)

    async def cancel(self, tid):
        self.cancelled.append(tid)


@pytest.fixture
def no_tick(client, monkeypatch):
    async def _no_tick():
        return None

    monkeypatch.setattr(app.state.workflow_runner, "_tick", _no_tick)


async def _agent(cap=None, pid=None) -> str:
    async with SessionLocal() as s:
        a = Agent(name=f"rb-{uuid.uuid4().hex[:8]}", system_prompt="x", default_prompt="x",
                  max_budget_usd=cap, project_id=pid)
        s.add(a)
        await s.commit()
        return a.name


async def _make_run(stages, budget=None, pids=()) -> str:
    async with SessionLocal() as s:
        run = WorkflowRun(workflow_id="w", name="rb", input="the input", stages=stages,
                          project_ids=list(pids), status="pending", current_stage=0, budget_usd=budget)
        s.add(run)
        await s.commit()
        return run.id


async def _run(rid) -> WorkflowRun:
    async with SessionLocal() as s:
        return await s.get(WorkflowRun, rid)


async def _tasks(rid, stage=None) -> list:
    async with SessionLocal() as s:
        q = select(Task).where(Task.workflow_run_id == rid)
        if stage is not None:
            q = q.where(Task.stage_index == stage)
        return (await s.execute(q.order_by(Task.created_at))).scalars().all()


async def _set(rid, stage, status, cost=None, attempt=None, text="out"):
    """Set the status (and cost) of the stage's tasks of `attempt` (default: all)."""
    async with SessionLocal() as s:
        q = select(Task).where(Task.workflow_run_id == rid, Task.stage_index == stage)
        if attempt is not None:
            q = q.where(Task.stage_attempt == attempt)
        for t in (await s.execute(q)).scalars().all():
            t.status = status
            t.result_text = text
            if cost is not None:
                t.total_cost_usd = cost
        await s.commit()


def texts(run):
    return [x["text"] for x in run.log or []]


def stage(agent, name="Dev", auto=False, n=1, **kw):
    st = {"name": name, "items": [{"agent": agent, "prompt": "do {{input}}"} for _ in range(n)], **kw}
    if auto:
        st["auto_retry"] = True
    return st


# ---------------------------------------------------------------------------
# Auto-retry
# ---------------------------------------------------------------------------


def test_auto_retry_once_then_fails(no_tick):
    async def go():
        a = await _agent()
        rid = await _make_run([stage(a, auto=True), stage(a, name="Next")])
        w = FakeWorker()
        r = WorkflowRunner(w, 999)
        await r.start_run(rid)
        await _set(rid, 0, Status.FAILED, attempt=0)
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "running"
        assert "Step Dev retried automatically" in texts(run)
        assert run.stage_state["0"]["attempt"] == 1 and run.stage_state["0"]["auto_retried"] is True
        tasks = await _tasks(rid, 0)
        assert sorted(t.stage_attempt for t in tasks) == [0, 1]
        new = next(t for t in tasks if t.stage_attempt == 1)
        assert new.id in w.submitted
        # a second failure fails the run as in 5a
        await _set(rid, 0, Status.FAILED, attempt=1)
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "failed"
        assert texts(run).count("Step Dev retried automatically") == 1
        assert "Step Dev failed: 1 task failed" in texts(run)
        assert len(await _tasks(rid, 0)) == 2
        # a person's Retry is still allowed and does not re-arm the auto-retry
        assert await r.retry_run(rid)
        run = await _run(rid)
        assert run.stage_state["0"]["attempt"] == 2 and run.stage_state["0"]["auto_retried"] is True
        await _set(rid, 0, Status.FAILED, attempt=2)
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "failed" and texts(run).count("Step Dev retried automatically") == 1

    asyncio.run(go())


def test_auto_retry_success_advances(no_tick):
    async def go():
        a = await _agent()
        rid = await _make_run([stage(a, auto=True), stage(a, name="Next")])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _set(rid, 0, Status.FAILED, attempt=0)
        await r._advance(rid)
        await _set(rid, 0, Status.COMPLETED, attempt=1)
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "running" and run.current_stage == 1
        assert len(await _tasks(rid, 1)) == 1

    asyncio.run(go())


def test_cancelled_task_not_auto_retried(no_tick):
    async def go():
        a = await _agent()
        rid = await _make_run([stage(a, auto=True)])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _set(rid, 0, Status.CANCELLED)
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "failed"
        assert "Step Dev retried automatically" not in texts(run)
        assert len(await _tasks(rid, 0)) == 1

    asyncio.run(go())


def test_failed_plus_cancelled_not_auto_retried(no_tick):
    async def go():
        a = await _agent()
        rid = await _make_run([stage(a, auto=True, n=2)])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        t1, t2 = await _tasks(rid, 0)
        async with SessionLocal() as s:
            (await s.get(Task, t1.id)).status = Status.FAILED
            (await s.get(Task, t2.id)).status = Status.CANCELLED
            await s.commit()
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "failed" and len(await _tasks(rid, 0)) == 2

    asyncio.run(go())


def test_auto_retry_waits_for_siblings(no_tick):
    async def go():
        a = await _agent()
        rid = await _make_run([stage(a, auto=True, n=2)])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        t1, t2 = await _tasks(rid, 0)
        async with SessionLocal() as s:
            (await s.get(Task, t1.id)).status = Status.FAILED
            (await s.get(Task, t2.id)).status = Status.RUNNING
            await s.commit()
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "running" and len(await _tasks(rid, 0)) == 2
        async with SessionLocal() as s:
            (await s.get(Task, t2.id)).status = Status.COMPLETED
            await s.commit()
        await r._advance(rid)
        run = await _run(rid)
        assert "Step Dev retried automatically" in texts(run)
        assert len(await _tasks(rid, 0)) == 4

    asyncio.run(go())


def test_without_auto_retry_unchanged(no_tick):
    async def go():
        a = await _agent()
        rid = await _make_run([stage(a)])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        run = await _run(rid)
        assert "auto_retried" not in run.stage_state["0"]
        await _set(rid, 0, Status.FAILED)
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "failed"
        assert texts(run) == ["Step Dev failed: 1 task failed"]
        assert len(await _tasks(rid, 0)) == 1

    asyncio.run(go())


def test_outputs_invalid_counts_as_failure(no_tick, tmp_path):
    async def go():
        async with SessionLocal() as s:
            p = Project(name="rb-" + uuid.uuid4().hex[:8], slug=uuid.uuid4().hex, directory=str(tmp_path))
            s.add(p)
            await s.commit()
            pid = p.id
        a = await _agent(pid=pid)
        st = {"name": "Plan", "auto_retry": True,
              "items": [{"agent": a, "prompt": "plan", "outputs": ["summary"]}]}
        rid = await _make_run([st], pids=[pid])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _set(rid, 0, Status.COMPLETED, attempt=0, text="no block here")
        await r._advance(rid)
        run = await _run(rid)
        assert "Step Plan retried automatically" in texts(run)
        tasks = await _tasks(rid, 0)
        old = next(t for t in tasks if t.stage_attempt == 0)
        assert old.status == Status.FAILED and old.error.startswith("outputs invalid:")
        assert any(t.stage_attempt == 1 for t in tasks)

    asyncio.run(go())


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


def test_budget_reached_before_stage_pauses(no_tick):
    async def go():
        a = await _agent()
        rid = await _make_run([stage(a), stage(a, name="Next")], budget=1.0)
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _set(rid, 0, Status.COMPLETED, cost=1.25)
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "paused" and run.current_stage == 1
        assert run.stage_state["1"]["status"] == "budget"
        assert "Step Next held: budget reached ($1.25 of $1.00)" in texts(run)
        assert await _tasks(rid, 1) == []
        await r._advance(rid)  # still held, no repeated line
        run = await _run(rid)
        assert texts(run).count("Step Next held: budget reached ($1.25 of $1.00)") == 1

    asyncio.run(go())


def test_task_cap_is_min_of_agent_and_remaining(no_tick):
    async def go():
        a = await _agent(cap=5.0)
        b = await _agent(cap=0.2)
        rid = await _make_run([stage(a), {"name": "Two", "items": [
            {"agent": a, "prompt": "x"}, {"agent": b, "prompt": "y"}]}], budget=2.0)
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        (t0,) = await _tasks(rid, 0)
        assert t0.max_budget_usd == pytest.approx(2.0)
        await _set(rid, 0, Status.COMPLETED, cost=1.5)
        await r._advance(rid)
        caps = sorted(t.max_budget_usd for t in await _tasks(rid, 1))
        assert caps == [pytest.approx(0.2), pytest.approx(0.5)]

    asyncio.run(go())


def test_uncapped_run_keeps_agent_cap(no_tick):
    async def go():
        a = await _agent(cap=3.0)
        rid = await _make_run([stage(a)])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        (t0,) = await _tasks(rid, 0)
        assert t0.max_budget_usd == pytest.approx(3.0)

    asyncio.run(go())


def test_remainder_under_a_cent_pauses(no_tick):
    async def go():
        a = await _agent()
        rid = await _make_run([stage(a), stage(a, name="Next")], budget=1.0)
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _set(rid, 0, Status.COMPLETED, cost=0.995)
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "paused" and run.stage_state["1"]["status"] == "budget"
        assert await _tasks(rid, 1) == []

    asyncio.run(go())


def test_budget_checked_before_auto_retry(no_tick):
    async def go():
        a = await _agent()
        rid = await _make_run([stage(a, auto=True)], budget=1.0)
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _set(rid, 0, Status.FAILED, cost=1.0)
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "paused" and run.stage_state["0"]["status"] == "budget"
        assert run.stage_state["0"]["auto_retried"] is True and run.stage_state["0"]["attempt"] == 1
        assert "Step Dev held: budget reached ($1.00 of $1.00)" in texts(run)
        assert len(await _tasks(rid, 0)) == 1
        await r.raise_budget(rid, 3.0, ANN)
        run = await _run(rid)
        assert run.status == "running"
        tasks = await _tasks(rid, 0)
        new = next(t for t in tasks if t.stage_attempt == 1)
        assert new.max_budget_usd == pytest.approx(2.0)

    asyncio.run(go())


def test_raise_resumes(no_tick):
    async def go():
        a = await _agent()
        rid = await _make_run([stage(a), stage(a, name="Next")], budget=1.0)
        w = FakeWorker()
        r = WorkflowRunner(w, 999)
        await r.start_run(rid)
        await _set(rid, 0, Status.COMPLETED, cost=1.0)
        await r._advance(rid)
        assert (await _run(rid)).status == "paused"
        with pytest.raises(ValueError, match="more than"):
            await r.raise_budget(rid, 1.0, ANN)
        await r.raise_budget(rid, 4.5, ANN)
        run = await _run(rid)
        assert run.budget_usd == 4.5 and run.status == "running"
        assert "Budget raised to $4.50 by Ann" in texts(run)
        (t1,) = await _tasks(rid, 1)
        assert t1.id in w.submitted and t1.max_budget_usd == pytest.approx(2.0)  # default cap 2.0
        assert run.stage_state["1"]["status"] == "running"

    asyncio.run(go())


def test_raise_leaving_under_a_cent_refused(no_tick):
    async def go():
        a = await _agent()
        rid = await _make_run([stage(a), stage(a, name="Next")], budget=1.0)
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _set(rid, 0, Status.COMPLETED, cost=1.0)
        await r._advance(rid)
        with pytest.raises(ValueError, match="at least \\$0.01 to spend"):
            await r.raise_budget(rid, 1.005, ANN)
        run = await _run(rid)
        assert run.budget_usd == 1.0 and run.status == "paused"

    asyncio.run(go())


def test_raise_refused_on_inactive_run(no_tick):
    async def go():
        a = await _agent()
        rid = await _make_run([stage(a)], budget=1.0)
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await r.cancel_run(rid)
        with pytest.raises(ValueError, match="not active"):
            await r.raise_budget(rid, 5.0, ANN)

    asyncio.run(go())


@pytest.mark.parametrize("raise_first", [True, False])
def test_raise_racing_cancel_cancel_wins(no_tick, raise_first):
    async def go():
        a = await _agent()
        rid = await _make_run([stage(a), stage(a, name="Next")], budget=1.0)
        w = FakeWorker()
        r = WorkflowRunner(w, 999)
        await r.start_run(rid)
        await _set(rid, 0, Status.COMPLETED, cost=1.0)
        await r._advance(rid)
        assert (await _run(rid)).status == "paused"

        async def do_raise():
            try:
                await r.raise_budget(rid, 5.0, ANN)
            except ValueError:
                pass

        coros = [do_raise(), r.cancel_run(rid)]
        if not raise_first:
            coros.reverse()
        await asyncio.gather(*coros)
        run = await _run(rid)
        assert run.status == "failed"
        for t in await _tasks(rid, 1):  # any task the raise started was cancelled
            assert t.id in w.cancelled

    asyncio.run(go())


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class FakeRunner:
    def __init__(self):
        self.calls, self.err = [], None

    def live_gate(self, run_id):
        return None

    async def raise_budget(self, run_id, budget_usd, user):
        self.calls.append((run_id, budget_usd, getattr(user, "id", None)))
        if self.err:
            raise ValueError(self.err)


def _project(client):
    r = client.post("/projects", json={"name": f"rb-{uuid.uuid4().hex[:8]}"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _grant(client, pid, user_id, role):
    keep = [
        {k: m[k] for k in ("subject_type", "subject_id", "role")}
        for m in client.get(f"/projects/{pid}/members").json()
        if m["subject_id"] != user_id
    ]
    keep.append({"subject_type": "user", "subject_id": user_id, "role": role})
    assert client.put(f"/projects/{pid}/members", json=keep).status_code == 200


def _api_run(pid, created_by=None, budget=2.0):
    async def go():
        async with SessionLocal() as s:
            run = WorkflowRun(workflow_id=uuid.uuid4().hex, name="rb-api", status="paused", current_stage=0,
                              project_ids=[pid], stages=[{"name": "S", "items": []}],
                              stage_state={"0": {"status": "budget"}}, created_by=created_by, budget_usd=budget)
            s.add(run)
            await s.flush()
            s.add(Task(prompt="x", title="x", workflow_run_id=run.id, stage_index=0, stage_attempt=0,
                       status=Status.COMPLETED, total_cost_usd=1.5, project_id=pid))
            await s.commit()
            return run.id

    return asyncio.run(go())


def test_budget_endpoint_permissions(client, no_tick, monkeypatch):
    pid = _project(client)
    creator = login_as(f"rb-{uuid.uuid4().hex[:8]}@test.local")
    viewer = login_as(f"rb-{uuid.uuid4().hex[:8]}@test.local")
    _grant(client, pid, creator.user_id, "contributor")
    _grant(client, pid, viewer.user_id, "contributor")
    rid = _api_run(pid, created_by=creator.user_id)
    fr = FakeRunner()
    monkeypatch.setitem(app.state._state, "workflow_runner", fr)
    assert viewer.post(f"/workflows/runs/{rid}/budget", json={"budget_usd": 5.0}).status_code == 403
    r = creator.post(f"/workflows/runs/{rid}/budget", json={"budget_usd": 5.0})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == rid and "log" not in r.json() and "stage_state" not in r.json()
    assert fr.calls == [(rid, 5.0, creator.user_id)]
    for bad in (0, 10001, "5", None):
        assert creator.post(f"/workflows/runs/{rid}/budget", json={"budget_usd": bad}).status_code == 422
    fr.err = "the budget must be more than the $1.50 already spent"
    r = creator.post(f"/workflows/runs/{rid}/budget", json={"budget_usd": 1.0})
    assert r.status_code == 409 and "already spent" in r.json()["detail"]
    assert client.post("/workflows/runs/nope/budget", json={"budget_usd": 5.0}).status_code == 404


def test_budget_endpoint_real_runner_409(client, no_tick):
    pid = _project(client)
    rid = _api_run(pid)
    r = client.post(f"/workflows/runs/{rid}/budget", json={"budget_usd": 1.5})
    assert r.status_code == 409 and "already spent" in r.json()["detail"]
    r = client.post(f"/workflows/runs/{rid}/budget", json={"budget_usd": 3.0})
    assert r.status_code == 200 and r.json()["budget_usd"] == 3.0
    d = client.get(f"/workflows/runs/{rid}").json()
    assert d["budget"] == {"spent": 1.5, "budget_usd": 3.0}
    assert any(x["text"].startswith("Budget raised to $3.00 by ") for x in d["log"])


def test_detail_budget_without_cap(client, no_tick):
    pid = _project(client)
    rid = _api_run(pid, budget=None)
    d = client.get(f"/workflows/runs/{rid}").json()
    assert d["budget"] == {"spent": 1.5, "budget_usd": None}
