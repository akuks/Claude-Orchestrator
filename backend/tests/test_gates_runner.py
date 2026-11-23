"""Phase 5a task 6: the runner runs gate and approval stages, keeps attempts, holds on
pre-flight and names flaky tests. Most tests fake the executor; one runs `true`/`false`."""

import asyncio
import dataclasses
import logging
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.constants import Status
from app.database import SessionLocal
from app.gates import codestate, executor, preflight, totals
from app.gates.test_results import TestResult
from app.main import app
from app.models import Project, Task, WorkflowGateRun, WorkflowRun
from app.workflow_runner import FLAKY_WORDS, WorkflowRunner, gate_status_text

ANN = SimpleNamespace(name="Ann", email="ann@test.local")


class FakeWorker:
    def __init__(self):
        self.submitted, self.cancelled = [], []

    async def submit(self, tid, prio, created):
        self.submitted.append(tid)

    async def cancel(self, tid):
        self.cancelled.append(tid)


def oc(outcome="passed", code=0, failed=None, failing=(), runner=None, passed=None):
    now = datetime.now(timezone.utc)
    if outcome == "timed_out":
        code = None
    return executor.Outcome(outcome, code, now, now, 1,
                            TestResult(runner, passed, failed, None, None, tuple(failing)))


FAIL = lambda *names: oc("failed", 1, failed=len(names) or None, failing=names, runner="pytest" if names else None, passed=0)  # noqa: E731


class Fakes:
    """Scripted executor: `script` maps a command to a list of outcomes (or "block")."""

    def __init__(self):
        self.script: dict[str, list] = {}
        self.calls: list[str] = []
        self.guard: dict[str, tuple] = {}
        self.pre: list = []  # pre-flight answers, last one sticky; None = met
        self.states: list = ["X"]  # code states, last one sticky

    async def run_command(self, cmd, *, cwd, env, log_path, timeout_s, on_spawn=None):
        self.calls.append(cmd)
        queue = self.script.get(cmd) or [oc()]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if item == "block":
            await asyncio.Event().wait()
        if isinstance(item, Exception):
            raise item
        now = datetime.now(timezone.utc)
        return dataclasses.replace(item, started_at=now, finished_at=now)

    async def guard_verdict(self, s, project, cmd):
        d, hits = self.guard.get(cmd, ("allow", []))
        return SimpleNamespace(decision=d, hits=[SimpleNamespace(reason=h) for h in hits])

    async def check(self, lines):
        if not self.pre:
            return None
        v = self.pre.pop(0) if len(self.pre) > 1 else self.pre[0]
        if isinstance(v, Exception):
            raise v
        return v

    async def code_state(self, directory):
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]


@pytest.fixture
def fakes(client, monkeypatch):
    # The app's own background runner must not advance these runs.
    async def _no_tick():
        return None

    monkeypatch.setattr(app.state.workflow_runner, "_tick", _no_tick)
    f = Fakes()
    monkeypatch.setattr(executor, "run_command", f.run_command)
    monkeypatch.setattr(executor, "guard_verdict", f.guard_verdict)
    monkeypatch.setattr(preflight, "check", f.check)
    monkeypatch.setattr(codestate, "code_state", f.code_state)
    return f


@pytest.fixture(scope="module")
def agents(client):
    tag = uuid.uuid4().hex[:8]
    names = []
    for n in ("A", "B"):
        name = f"gr-{n}-{tag}"
        r = client.post("/agents", json={"name": name, "system_prompt": "x", "default_prompt": "x"})
        assert r.status_code == 201, r.text
        names.append(name)
    return names


async def _project() -> str:
    async with SessionLocal() as s:
        p = Project(name="gr-" + uuid.uuid4().hex[:8], slug=uuid.uuid4().hex,
                    directory=tempfile.mkdtemp(prefix="gr-proj-"))
        s.add(p)
        await s.commit()
        return p.id


def gate(pid, run=("pytest -q",), retry=None, before=None, name="Test"):
    return {"name": name, "kind": "gate", "project": pid, "run": list(run), "retry": retry,
            "before": list(before or []), "timeout_min": 1}


async def _make_run(stages, pids=()) -> str:
    async with SessionLocal() as s:
        run = WorkflowRun(workflow_id="w", name="gr", input="the input", stages=stages,
                          project_ids=list(pids), status="pending", current_stage=0)
        s.add(run)
        await s.commit()
        return run.id


async def _run(rid) -> WorkflowRun:
    async with SessionLocal() as s:
        return await s.get(WorkflowRun, rid)


async def _rows(rid) -> list:
    async with SessionLocal() as s:
        return (await s.execute(select(WorkflowGateRun).where(WorkflowGateRun.run_id == rid)
                                .order_by(WorkflowGateRun.started_at, WorkflowGateRun.id))).scalars().all()


async def _tasks(rid, stage=None) -> list:
    async with SessionLocal() as s:
        q = select(Task).where(Task.workflow_run_id == rid)
        if stage is not None:
            q = q.where(Task.stage_index == stage)
        return (await s.execute(q.order_by(Task.created_at))).scalars().all()


async def _finish_tasks(rid, stage, status=Status.COMPLETED, text="out"):
    async with SessionLocal() as s:
        for t in (await s.execute(select(Task).where(Task.workflow_run_id == rid, Task.stage_index == stage,
                                                     Task.status != Status.FAILED))).scalars().all():
            t.status = status
            t.result_text = text
        await s.commit()


async def settle(runner, timeout=10.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while True:
        live = [t for t in runner._gates.values() if not t.done()]
        if not live:
            await asyncio.sleep(0)
            if not [t for t in runner._gates.values() if not t.done()]:
                return
            continue
        left = end - loop.time()
        assert left > 0, "gate did not settle"
        await asyncio.wait(live, timeout=left)


async def wait_for(pred, timeout=5.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        assert loop.time() < end, "condition not met in time"
        await asyncio.sleep(0.01)


def texts(run):
    return [x["text"] for x in run.log or []]


def has(run, s):
    return any(s in t for t in texts(run))


# ---------------------------------------------------------------------------


def test_agent_gate_agent_passes_through(fakes, agents):
    a, b = agents

    async def go():
        pid = await _project()
        rid = await _make_run([
            {"name": "Dev", "items": [{"agent": a, "prompt": "do {{input}}"}]},
            gate(pid),
            {"name": "Review", "items": [{"agent": b, "prompt": "review: {{prev}}"}]},
        ], [pid])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        assert len(await _tasks(rid, 0)) == 1
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        await settle(r)
        rows = await _rows(rid)
        assert [(x.command, x.kind, x.outcome) for x in rows] == [("pytest -q", "full", "passed")]
        assert rows[0].log_name == "stage1-1.log" and rows[0].attempt == 0
        run = await _run(rid)
        assert run.current_stage == 2 and run.status == "running"
        assert has(run, "Step Test (gate) passed") and has(run, "So far:")
        assert run.stage_state["1"]["status"] == "passed" and run.stage_state["1"]["finished_at"]
        t2 = await _tasks(rid, 2)
        assert len(t2) == 1 and "Gate Test passed" in t2[0].prompt
        await _finish_tasks(rid, 2)
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "completed"
        assert run.stage_state["0"]["status"] == "done" and run.stage_state["2"]["status"] == "done"

    asyncio.run(go())


def test_failing_gate_fails_run(fakes):
    fakes.script["pytest -q"] = [FAIL("test_a", "test_b")]

    async def go():
        pid = await _project()
        rid = await _make_run([gate(pid)], [pid])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await settle(r)
        run = await _run(rid)
        assert run.status == "failed"
        assert has(run, "Step Test (gate) failed: pytest -q, exit 1, 2 failed")
        assert not has(run, "test_a")
        st = run.stage_state["0"]
        assert st["status"] == "failed" and "exit 1" in st["summary"]
        rows = await _rows(rid)
        assert rows[0].failing == ["test_a", "test_b"] and rows[0].exit_code == 1

    asyncio.run(go())


@pytest.mark.parametrize("seq,want,kinds", [
    (["F", "P", "P"], "passed", ["full", "retry", "full"]),
    (["F", "F"], "failed", ["full", "retry"]),
    (["F", "P", "F"], "failed", ["full", "retry", "full"]),
])
def test_retry_round(fakes, seq, want, kinds):
    full, retry = "pytest -q", "pytest -q --lf"
    fakes.states = [None]
    outs = [oc() if x == "P" else FAIL() for x in seq]
    fakes.script[full] = [o for o, k in zip(outs, kinds) if k == "full"] + [FAIL()]
    fakes.script[retry] = [o for o, k in zip(outs, kinds) if k == "retry"] + [FAIL()]

    async def go():
        pid = await _project()
        rid = await _make_run([gate(pid, retry=retry)], [pid])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await settle(r)
        run = await _run(rid)
        assert run.stage_state["0"]["status"] == want
        assert run.status == ("completed" if want == "passed" else "failed")
        assert [x.kind for x in await _rows(rid)] == kinds

    asyncio.run(go())


def test_multi_command_stops_at_first_failure(fakes):
    fakes.script["c2"] = [FAIL()]

    async def go():
        pid = await _project()
        rid = await _make_run([gate(pid, run=("c1", "c2", "c3"))], [pid])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await settle(r)
        assert fakes.calls == ["c1", "c2"]
        run = await _run(rid)
        assert run.status == "failed" and has(run, "Step Test (gate) failed: c2, exit 1")

    asyncio.run(go())


def test_restart_resumes_without_rerunning_passed(fakes):
    fakes.script["c2"] = ["block", oc()]

    async def go():
        pid = await _project()
        rid = await _make_run([gate(pid, run=("c1", "c2", "c3"))], [pid])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        key = (rid, 0)
        await wait_for(lambda: r._procs.get(key, {}).get("cmd") == "c2")
        assert r.live_gate(rid) == {"stage_index": 0, "command": "c2", "k": 1, "n": 3,
                                    "log_name": "stage0-2.log"}
        run = await _run(rid)
        assert gate_status_text(run.stages[0], run.stage_state["0"], r.live_gate(rid)) == \
            "running `c2` (1 of 3 commands passed)"
        # Simulate the server dying: the process and the live table are gone.
        task = r._gates[key]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        r._gates.clear()
        r._procs.clear()
        run = await _run(rid)
        assert run.stage_state["0"]["status"] == "running"
        assert run.stage_state["0"]["passed_cmds"] == [0]
        assert gate_status_text(run.stages[0], run.stage_state["0"], None) == \
            "not yet passed (1 of 3 commands passed, none running)"
        await r._advance(rid)
        await settle(r)
        assert fakes.calls == ["c1", "c2", "c2", "c3"]
        run = await _run(rid)
        assert run.status == "completed"
        assert has(run, "resumed after a server restart")

    asyncio.run(go())


def test_stale_handout_result_is_dropped(fakes):
    fakes.script["pytest -q"] = [FAIL(), "block"]

    async def go():
        pid = await _project()
        rid = await _make_run([gate(pid)], [pid])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await settle(r)
        run = await _run(rid)
        assert run.status == "failed"
        old = run.stage_state["0"]["handout"]
        assert await r.retry_run(rid)
        key = (rid, 0)
        await wait_for(lambda: key in r._procs)
        run = await _run(rid)
        new = run.stage_state["0"]
        assert new["handout"] != old and new["attempt"] == 1 and new["status"] == "running"
        n = len(await _rows(rid))
        step = {**r._procs[key], "handout": old, "attempt": 0}
        assert await r._gate_record(rid, 0, old, step, oc()) is None
        assert len(await _rows(rid)) == n
        assert (await _run(rid)).stage_state["0"]["status"] == "running"
        assert await r.cancel_run(rid)
        rows = await _rows(rid)
        assert len(rows) == n + 1 and rows[-1].outcome == "cancelled" and rows[-1].attempt == 1
        # Retry keeps the earlier row.
        assert rows[0].attempt == 0 and rows[0].outcome == "failed"

    asyncio.run(go())


def test_guard_deny_fails_stage(fakes):
    fakes.guard["rm -rf x"] = ("deny", ["deletes files"])

    async def go():
        pid = await _project()
        rid = await _make_run([gate(pid, run=("rm -rf x",))], [pid])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await settle(r)
        assert fakes.calls == []
        run = await _run(rid)
        assert run.status == "failed"
        assert run.stage_state["0"]["reason"] == "blocked by the guard: deletes files"
        assert has(run, "Step Test (gate) failed: ") and not has(run, "deletes files")
        assert [x.outcome for x in await _rows(rid)] == ["refused"]

    asyncio.run(go())


def test_guard_approve_needs_allowance_then_runs_but_deny_still_refused(fakes):
    fakes.guard["x"] = ("approve", ["touches the network"])
    fakes.guard["y"] = ("deny", ["no"])

    async def go():
        pid = await _project()
        rid = await _make_run([gate(pid, run=("x", "y"))], [pid])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await settle(r)
        run = await _run(rid)
        st = run.stage_state["0"]
        assert st["status"] == "needs_approval" and st["hits"] == ["touches the network"]
        assert run.status == "running" and fakes.calls == []
        with pytest.raises(ValueError):
            await r.approve_stage(rid, 0, ANN)
        await r.allow_gate(rid, 0, ANN)
        await settle(r)
        assert fakes.calls == ["x"]
        run = await _run(rid)
        assert run.status == "failed" and "blocked by the guard: no" in run.stage_state["0"]["reason"]
        assert run.stage_state["0"]["allowed"] is True and run.stage_state["0"]["decided_by"] == "Ann"
        with pytest.raises(ValueError):
            await r.allow_gate(rid, 0, ANN)

    asyncio.run(go())


def test_allow_gate_wrong_state_raises(fakes):
    async def go():
        pid = await _project()
        rid = await _make_run([gate(pid, run=("x",)), gate(pid, run=("z",), name="Two")], [pid])
        r = WorkflowRunner(FakeWorker(), 999)
        fakes.script["z"] = ["block"]
        await r.start_run(rid)
        await wait_for(lambda: (rid, 1) in r._procs)
        with pytest.raises(ValueError, match="not waiting"):
            await r.allow_gate(rid, 1, ANN)
        with pytest.raises(ValueError, match="current"):
            await r.allow_gate(rid, 0, ANN)
        await r.cancel_run(rid)

    asyncio.run(go())


def test_preflight_holds_then_starts(fakes):
    fakes.pre = ["free memory is 1.0 GB, needs 4 GB"]

    async def go():
        pid = await _project()
        rid = await _make_run([gate(pid, before=["free memory 4 GB"])], [pid])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await settle(r)
        run = await _run(rid)
        assert run.status == "running" and run.stage_state["0"]["status"] == "held"
        assert has(run, "Step Test held: waiting for pre-flight") and not has(run, "1.0 GB")
        await r._advance(rid)  # still not met: one log line, not one per tick
        run = await _run(rid)
        assert sum("held:" in t for t in texts(run)) == 1 and fakes.calls == []
        fakes.pre = [ValueError("pre-flight 'bogus' is not one of: …")]
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "running" and run.stage_state["0"]["status"] == "held"
        assert "bogus" in run.stage_state["0"]["reason"]
        fakes.pre = [None]
        await r._advance(rid)
        await settle(r)
        run = await _run(rid)
        assert run.status == "completed" and fakes.calls == ["pytest -q"]

    asyncio.run(go())


def test_preflight_holds_agent_stage(fakes, agents):
    fakes.pre = ["port 5432 is not open"]

    async def go():
        rid = await _make_run([{"name": "Dev", "before": ["port 5432 open"],
                                "items": [{"agent": agents[0], "prompt": "x"}]}])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        assert await _tasks(rid) == []
        run = await _run(rid)
        assert run.status == "running" and has(run, "Step Dev held: waiting for pre-flight") and not has(run, "5432")
        fakes.pre = [None]
        await r._advance(rid)
        assert len(await _tasks(rid)) == 1

    asyncio.run(go())


@pytest.mark.parametrize("states,flaky", [
    (["X"], ["a", "b"]),
    (["X", "Y"], None),
    ([None], None),
])
def test_flaky(fakes, states, flaky):
    full, retry = "pytest -q", "pytest -q --lf"
    fakes.states = list(states)
    fakes.script[full] = [FAIL("a", "b"), oc(runner="pytest", passed=2, failed=0)]
    fakes.script[retry] = [oc()]

    async def go():
        pid = await _project()
        rid = await _make_run([gate(pid, retry=retry)], [pid])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await settle(r)
        run = await _run(rid)
        assert run.status == "completed"
        rows = await _rows(rid)
        assert rows[-1].kind == "full" and rows[-1].outcome == "passed"
        assert rows[-1].flaky == flaky
        summary = run.stage_state["0"]["summary"]
        if flaky:
            assert f"flaky ({FLAKY_WORDS}): a, b" in summary
            assert has(run, FLAKY_WORDS)
        else:
            assert "flaky" not in summary

    asyncio.run(go())


def test_approval_approve_and_reject(fakes, agents):
    async def go():
        rid = await _make_run([{"name": "Sign", "kind": "approval", "message": "ok?", "before": []},
                               {"name": "After", "items": [{"agent": agents[0], "prompt": "go {{prev}}"}]}])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        run = await _run(rid)
        assert run.status == "paused" and run.stage_state["0"]["status"] == "waiting_approval"
        await r._advance(rid)
        assert (await _run(rid)).status == "paused"
        with pytest.raises(ValueError):
            await r.allow_gate(rid, 0, ANN)
        await r.approve_stage(rid, 0, ANN)
        run = await _run(rid)
        assert run.status == "running" and run.current_stage == 1
        assert has(run, "Step Sign (approval) approved by Ann")
        assert run.stage_state["0"]["decided_by"] == "Ann" and run.stage_state["0"]["decided_at"]
        t = await _tasks(rid, 1)
        assert len(t) == 1 and "approved by Ann" in t[0].prompt
        with pytest.raises(ValueError):
            await r.approve_stage(rid, 0, ANN)

        rid2 = await _make_run([{"name": "Sign", "kind": "approval", "message": "ok?", "before": []}])
        await r.start_run(rid2)
        with pytest.raises(ValueError):
            await r.reject_stage(rid2, 0, ANN, "")
        await r.reject_stage(rid2, 0, ANN, "not ready")
        run = await _run(rid2)
        assert run.status == "failed" and run.stage_state["0"]["status"] == "rejected"
        assert has(run, "Step Sign (approval) rejected by Ann: not ready")

    asyncio.run(go())


def test_retry_run_keeps_agent_attempts(fakes, agents):
    async def go():
        rid = await _make_run([{"name": "Dev", "items": [{"agent": agents[0], "prompt": "x"}]}])
        w = FakeWorker()
        r = WorkflowRunner(w, 999)
        await r.start_run(rid)
        await _finish_tasks(rid, 0, Status.FAILED, "boom")
        await r._advance(rid)
        assert (await _run(rid)).status == "failed"
        assert await r.retry_run(rid)
        tasks = await _tasks(rid, 0)
        assert sorted(t.stage_attempt for t in tasks) == [0, 1]
        old = next(t for t in tasks if t.stage_attempt == 0)
        new = next(t for t in tasks if t.stage_attempt == 1)
        assert old.status == Status.FAILED and new.id in w.submitted
        run = await _run(rid)
        assert run.stage_state["0"]["attempt"] == 1
        await r._advance(rid)  # the attempt-0 failure is ignored
        assert (await _run(rid)).status == "running"
        await _finish_tasks(rid, 0, Status.COMPLETED, "fine")
        await r._advance(rid)
        assert (await _run(rid)).status == "completed"
        assert (await _tasks(rid, 0))[0].status == Status.FAILED  # evidence kept

    asyncio.run(go())


def test_cancel_kills_running_gate(fakes):
    fakes.script["slow"] = ["block"]

    async def go():
        pid = await _project()
        rid = await _make_run([gate(pid, run=("slow",))], [pid])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await wait_for(lambda: (rid, 0) in r._procs)
        task = r._gates[(rid, 0)]
        assert await r.cancel_run(rid)
        assert task.done() and r._gates == {} and r._procs == {}
        rows = await _rows(rid)
        assert [x.outcome for x in rows] == ["cancelled"] and rows[0].exit_code is None
        run = await _run(rid)
        assert run.status == "failed" and has(run, "Step Test (gate) still needs: slow")

    asyncio.run(go())


def test_real_true_and_false_gate(client, monkeypatch):
    async def _no_tick():
        return None

    monkeypatch.setattr(app.state.workflow_runner, "_tick", _no_tick)

    async def go():
        pid = await _project()
        ok = await _make_run([gate(pid, run=("true",), name="Ok")], [pid])
        bad = await _make_run([gate(pid, run=("false",), name="Bad")], [pid])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(ok)
        await r.start_run(bad)
        await settle(r, timeout=30)
        run = await _run(ok)
        assert run.status == "completed", texts(run)
        assert has(run, "Step Ok (gate) passed")
        run = await _run(bad)
        assert run.status == "failed", texts(run)
        assert has(run, "Step Bad (gate) failed: false, exit 1")
        rows = await _rows(bad)
        assert rows[0].outcome == "failed" and rows[0].exit_code == 1 and rows[0].code_state is None
        assert (executor.log_file(bad, 0, 1)).exists()

    asyncio.run(go())


# ---------------------------------------------------------------------------
# Fix round 1: per-run serialization, tick isolation, gate coroutine errors.


def test_approve_vs_cancel_cancel_wins(fakes, agents, monkeypatch):
    real = totals.compute
    entered, release = None, None

    async def slow(s, run):
        if not entered.is_set():
            entered.set()
            await release.wait()
        return await real(s, run)

    monkeypatch.setattr(totals, "compute", slow)

    async def go():
        nonlocal entered, release
        entered, release = asyncio.Event(), asyncio.Event()
        rid = await _make_run([{"name": "Sign", "kind": "approval", "message": "", "before": []},
                               {"name": "After", "items": [{"agent": agents[0], "prompt": "x"}]}])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        approve = asyncio.create_task(r.approve_stage(rid, 0, ANN))
        await wait_for(entered.is_set)
        cancel = asyncio.create_task(r.cancel_run(rid))
        await asyncio.sleep(0.05)
        assert not cancel.done()  # waits for the run lock
        release.set()
        await asyncio.gather(approve, cancel)
        run = await _run(rid)
        assert run.status == "failed" and run.current_stage == 0
        assert await _tasks(rid, 1) == []

    asyncio.run(go())


def test_held_tick_vs_cancel_creates_no_tasks(fakes, agents, monkeypatch):
    fakes.pre = ["port 5432 is not open"]

    async def go():
        rid = await _make_run([{"name": "Dev", "before": ["port 5432 open"],
                                "items": [{"agent": agents[0], "prompt": "x"}]}])
        w = FakeWorker()
        r = WorkflowRunner(w, 999)
        await r.start_run(rid)
        assert (await _run(rid)).stage_state["0"]["status"] == "held"
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow(lines):
            entered.set()
            await release.wait()
            return None  # now met

        monkeypatch.setattr(preflight, "check", slow)
        adv = asyncio.create_task(r._advance(rid))
        await wait_for(entered.is_set)
        assert await r.cancel_run(rid)  # not blocked: pre-flight runs outside the lock
        release.set()
        await adv
        run = await _run(rid)
        assert run.status == "failed"
        assert await _tasks(rid) == [] and w.submitted == []

    asyncio.run(go())


def test_gate_record_vs_cancel_is_marked_cancelled(fakes, monkeypatch):
    started, go_on = threading.Event(), threading.Event()

    def slow_prune(*a, **k):
        started.set()
        go_on.wait(5)

    monkeypatch.setattr(executor, "prune_logs", slow_prune)

    async def go():
        pid = await _project()
        rid = await _make_run([gate(pid)], [pid])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await wait_for(started.is_set)  # the command finished; its result is not recorded yet
        assert await r.cancel_run(rid)
        go_on.set()
        await settle(r)
        rows = await _rows(rid)
        assert [x.outcome for x in rows] == ["cancelled"]
        run = await _run(rid)
        assert run.status == "failed"
        assert run.stage_state["0"]["status"] == "failed" and run.stage_state["0"]["reason"] == "cancelled"
        assert has(run, "Step Test (gate) still needs: pytest -q")

    try:
        asyncio.run(go())
    finally:
        go_on.set()


def test_tick_isolates_run_errors(fakes, agents, caplog):
    async def go():
        bad = await _make_run([{"name": "Dev", "items": [{"agent": agents[0], "prompt": "x"}]}])
        good = await _make_run([{"name": "Dev", "items": [{"agent": agents[0], "prompt": "x"}]}])
        r = WorkflowRunner(FakeWorker(), 999)
        orig = r._advance

        async def adv(rid, first=False):
            if rid == bad:
                raise RuntimeError("boom")
            if rid == good:
                return await orig(rid, first)

        r._advance = adv
        with caplog.at_level(logging.ERROR, logger="app.workflow_runner"):
            await r._tick()
        assert len(await _tasks(good)) == 1 and (await _run(good)).status == "running"
        assert any(bad in rec.getMessage() for rec in caplog.records)

    asyncio.run(go())


def test_gate_coroutine_error_fails_stage(fakes):
    fakes.script["explode"] = [RuntimeError("boom")]

    async def go():
        pid = await _project()
        rid = await _make_run([gate(pid, run=("explode",))], [pid])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await settle(r)
        assert r._gates == {} and r._procs == {}
        run = await _run(rid)
        assert run.status == "failed"
        assert run.stage_state["0"]["status"] == "failed"
        assert run.stage_state["0"]["reason"] == "the gate could not run (internal error)"
        assert has(run, "Step Test (gate) failed: the gate could not run (internal error)")
        await r._advance(rid)  # nothing is resumed
        assert fakes.calls == ["explode"]

    asyncio.run(go())


# Fix round 2: the record writer holds the run lock; an in-process loss is not a "restart".


def test_cancel_waits_for_gate_record_lock(fakes, monkeypatch):
    real = totals.compute
    entered, release = None, None

    async def slow(s, run):
        if not entered.is_set():
            entered.set()
            await release.wait()
        return await real(s, run)

    monkeypatch.setattr(totals, "compute", slow)

    async def go():
        nonlocal entered, release
        entered, release = asyncio.Event(), asyncio.Event()
        pid = await _project()
        rid = await _make_run([gate(pid)], [pid])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await wait_for(entered.is_set)  # inside _gate_record_locked, holding the run lock
        cancel = asyncio.create_task(r.cancel_run(rid))
        await asyncio.sleep(0.05)
        assert not cancel.done()
        release.set()
        assert await cancel
        await settle(r)
        assert [x.outcome for x in await _rows(rid)] == ["passed"]  # recorded, no extra cancelled row
        run = await _run(rid)
        assert run.status == "failed" and run.stage_state["0"]["status"] == "passed"

    asyncio.run(go())


def test_in_process_loss_is_not_called_a_restart(fakes, monkeypatch):
    fakes.script["c1"] = [RuntimeError("boom"), oc()]

    async def go():
        pid = await _project()
        rid = await _make_run([gate(pid, run=("c1",))], [pid])
        r = WorkflowRunner(FakeWorker(), 999)

        async def broken_error(*a, **k):
            return None  # the failure could not be written

        monkeypatch.setattr(r, "_gate_error", broken_error)
        await r.start_run(rid)
        await settle(r)
        run = await _run(rid)
        assert run.stage_state["0"]["status"] == "running" and r._gates == {}
        await r._advance(rid)
        await settle(r)
        run = await _run(rid)
        assert run.status == "completed" and fakes.calls == ["c1", "c1"]
        assert not has(run, "resumed after a server restart")

    asyncio.run(go())
