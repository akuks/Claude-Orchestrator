"""Phase 8a: the decision track record - showings, outcomes, score, backfill."""

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.guard import runtime as guard_runtime
from app.constants import Status
from app.database import SessionLocal
from app.decisions import track
from app.models import DecisionShowing, Project, Task, TaskEvent, WorkflowRun
from conftest import login_as

pytestmark = pytest.mark.usefixtures("fail_after_60s")


def _run(coro):
    return asyncio.run(coro)


async def _project() -> str:
    async with SessionLocal() as s:
        p = Project(name="tr-" + uuid.uuid4().hex[:8], slug=uuid.uuid4().hex, directory="/tmp/none", env={})
        s.add(p)
        await s.commit()
        return p.id


async def _task(pid, status="running", **kw) -> str:
    tid = uuid.uuid4().hex
    async with SessionLocal() as s:
        s.add(Task(id=tid, title="t", prompt="p", root_id=tid, status=status, project_id=pid, **kw))
        await s.commit()
    return tid


async def _set(tid, **kw):
    async with SessionLocal() as s:
        t = await s.get(Task, tid)
        for k, v in kw.items():
            setattr(t, k, v)
        await s.commit()


async def _rows(tid=None, pid=None) -> list:
    async with SessionLocal() as s:
        q = select(DecisionShowing)
        if tid:
            q = q.where(DecisionShowing.task_id == tid)
        if pid:
            q = q.where(DecisionShowing.project_id == pid)
        return (await s.execute(q.order_by(DecisionShowing.decision_number))).scalars().all()


async def _outcome(tid):
    rows = await _rows(tid)
    return [(r.decision_number, r.outcome, r.retried) for r in rows]


# ---- recording --------------------------------------------------------------------------------------------


def test_same_decision_shown_50_times_is_one_row(client):
    """Review focus 1: the spool says D-7 fifty times (and more across reads): one row, the first time kept."""
    from app import worker as worker_mod
    from app.usage.tracker import UsageTracker

    async def go():
        pid = await _project()
        return pid, await _task(pid)

    pid, tid = _run(go())
    w = worker_mod.WorkerManager()
    w._usage[tid] = UsageTracker()
    w._usage_spool[tid] = w._new_usage_spool()
    emitted = []

    async def emit(etype, payload):
        emitted.append((etype, payload))

    d = guard_runtime.make_task_dir(tid)
    spool = d / guard_runtime.SPOOL_NAME
    with open(spool, "w", encoding="utf-8") as f:
        for k in range(50):
            f.write(json.dumps({"event": "PreToolUse", "nudge": "decisions", "ids": [7], "path": f"a{k}.py",
                                "ts": float(k)}) + "\n")
    _run(w._ingest_usage_spool(tid, emit))
    first = _run(_rows(tid))
    assert [(r.decision_number, r.outcome) for r in first] == [(7, None)]
    with open(spool, "a", encoding="utf-8") as f:
        f.write(json.dumps({"event": "PreToolUse", "nudge": "decisions", "ids": [7, 9], "path": "b.py"}) + "\n")
    _run(w._ingest_usage_spool(tid, emit))
    rows = _run(_rows(tid))
    assert [r.decision_number for r in rows] == [7, 9]
    assert rows[0].shown_at == first[0].shown_at and rows[0].project_id == pid
    assert len([1 for t, _ in emitted if t == "decision_note"]) == 51
    guard_runtime.remove_task_dir(tid)


def test_note_keeps_up_to_20_ids(client):
    from app.worker import WorkerManager

    note = WorkerManager._decision_note({"nudge": "decisions", "ids": list(range(1, 30)), "path": "x"})
    assert note["ids"] == list(range(1, 21))
    assert track.clean_ids([3, 3, True, 0, -1, "4", 5]) == [3, 5]


def test_task_without_project_records_nothing(client):
    async def go():
        tid = uuid.uuid4().hex
        async with SessionLocal() as s:
            s.add(Task(id=tid, title="t", prompt="p", root_id=tid, status="running"))
            await s.commit()
        await track.record_shown(tid, [1, 2])
        return await _rows(tid)

    assert _run(go()) == []


# ---- outcomes ---------------------------------------------------------------------------------------------


def test_plain_task_outcomes(client):
    """Review focus 2: completed, failed, cancelled (not counted), retried."""

    async def go():
        pid = await _project()
        ok, bad, gone, again = [await _task(pid) for _ in range(4)]
        for t in (ok, bad, gone, again):
            await track.record_shown(t, [1])
        await _set(ok, status=Status.COMPLETED)
        await _set(bad, status=Status.FAILED)
        await _set(gone, status=Status.CANCELLED)
        await _set(again, status=Status.COMPLETED, attempt=2)
        await track.settle_tasks([ok, bad, gone, again])
        return [await _outcome(t) for t in (ok, bad, gone, again)]

    assert _run(go()) == [[(1, "completed", False)], [(1, "failed", False)], [(1, None, False)],
                          [(1, "completed", True)]]


def test_finish_settles_failed_task_in_background(client):
    """The finish path settles every finished task, failed included, without waiting for it."""
    from app.worker import WorkerManager

    async def go():
        pid = await _project()
        tid = await _task(pid)
        await track.record_shown(tid, [4])
        await WorkerManager()._finish(tid, Status.FAILED, error="boom")
        await track.drain()
        return await _outcome(tid)

    assert _run(go()) == [(4, "failed", False)]


def _gate(pid):
    return {"name": "Test", "kind": "gate", "project": pid, "run": ["pytest -q"], "retry": None, "before": [],
            "timeout_min": 1}


async def _wf(pid, gate_state: dict, extra_stages=0) -> tuple[str, list[str]]:
    """A run [agent (two tasks), gate] with the gate in `gate_state`; both agent tasks completed and shown D-1."""
    stages = [{"name": "Dev", "items": [{"agent": "a", "prompt": "x"}, {"agent": "b", "prompt": "y"}]}, _gate(pid)]
    async with SessionLocal() as s:
        run = WorkflowRun(workflow_id="w", name="tr", input="", stages=stages, project_ids=[pid], status="running",
                          current_stage=1, stage_state={"0": {"status": "done"}, "1": gate_state})
        s.add(run)
        await s.commit()
        rid = run.id
    tids = [await _task(pid, status=Status.COMPLETED, workflow_run_id=rid, stage_index=0) for _ in range(2)]
    for t in tids:
        await track.record_shown(t, [1])
    return rid, tids


@pytest.mark.parametrize("gate_state,want", [
    ({"status": "passed"}, "passed"),
    ({"status": "passed", "fix_round": 1}, "fixed"),
    ({"status": "fixing", "fix_round": 1}, "fixed"),
    ({"status": "failed", "reason": "exit 1"}, "failed"),
    ({"status": "failed", "reason": "cancelled"}, None),
    ({"status": "running"}, None),
])
def test_multi_task_stage_takes_its_gate_outcome(client, gate_state, want):
    """Review focus 2: every task of a multi-task step gets the outcome of the gate after it."""

    async def go():
        pid = await _project()
        rid, tids = await _wf(pid, gate_state)
        await track.settle_run(rid)
        return [await _outcome(t) for t in tids]

    assert _run(go()) == [[(1, want, False)]] * 2


def test_fix_tagged_task_of_same_attempt_means_fixed(client):
    async def go():
        pid = await _project()
        rid, tids = await _wf(pid, {"status": "passed", "attempt": 1})
        await _task(pid, status=Status.COMPLETED, workflow_run_id=rid, stage_index=1, stage_attempt=1,
                    tags=["workflow", "fix"])
        await track.settle_run(rid)
        fixed = await _outcome(tids[0])
        # a fix task of an older attempt does not count
        rid2, tids2 = await _wf(pid, {"status": "passed", "attempt": 1})
        await _task(pid, status=Status.COMPLETED, workflow_run_id=rid2, stage_index=1, stage_attempt=0,
                    tags=["workflow", "fix"])
        await track.settle_tasks([tids2[0]])
        return fixed, await _outcome(tids2[1])

    assert _run(go()) == ([(1, "fixed", False)], [(1, "passed", False)])


def test_settled_outcome_not_cleared_and_later_failure_wins(client):
    async def go():
        pid = await _project()
        rid, tids = await _wf(pid, {"status": "fixing", "fix_round": 1})
        await track.settle_run(rid)
        out = [await _outcome(tids[0])]
        async with SessionLocal() as s:  # the run is cancelled: the known outcome stays
            run = await s.get(WorkflowRun, rid)
            run.stage_state = {"0": {"status": "done"}, "1": {"status": "failed", "reason": "cancelled"}}
            await s.commit()
        await track.settle_run(rid)
        out.append(await _outcome(tids[0]))
        async with SessionLocal() as s:
            run = await s.get(WorkflowRun, rid)
            run.stage_state = {"0": {"status": "done"}, "1": {"status": "failed", "reason": "exit 1"}}
            await s.commit()
        await track.settle_run(rid)
        out.append(await _outcome(tids[0]))
        return out

    assert _run(go()) == [[(1, "fixed", False)], [(1, "fixed", False)], [(1, "failed", False)]]


def test_step_without_gate_completed(client):
    async def go():
        pid = await _project()
        stages = [{"name": "Dev", "items": [{"agent": "a", "prompt": "x"}]}]
        async with SessionLocal() as s:
            run = WorkflowRun(workflow_id="w", name="tr", input="", stages=stages, project_ids=[pid],
                              status="completed", current_stage=0, stage_state={"0": {"status": "done"}})
            s.add(run)
            await s.commit()
            rid = run.id
        tid = await _task(pid, status=Status.COMPLETED, workflow_run_id=rid, stage_index=0)
        await track.record_shown(tid, [2])
        await track.settle_task(tid)
        return await _outcome(tid)

    assert _run(go()) == [(2, "completed", False)]


# ---- the runner settles when a gate settles ---------------------------------------------------------------


def test_gate_settling_settles_showings(client, monkeypatch):
    from app.gates import codestate, executor, preflight
    from app.main import app
    from app.workflow_runner import WorkflowRunner
    from test_gates_runner import FAIL, Fakes, FakeWorker
    from test_stages_fixloop import settle

    async def _no_tick():
        return None

    monkeypatch.setattr(app.state.workflow_runner, "_tick", _no_tick)
    f = Fakes()
    monkeypatch.setattr(executor, "run_command", f.run_command)
    monkeypatch.setattr(executor, "guard_verdict", f.guard_verdict)
    monkeypatch.setattr(preflight, "check", f.check)
    monkeypatch.setattr(codestate, "code_state", f.code_state)
    f.script["pytest -q"] = [FAIL("test_a")]

    async def go():
        pid = await _project()
        async with SessionLocal() as s:
            run = WorkflowRun(workflow_id="w", name="tr", input="", stages=[_gate(pid)], project_ids=[pid],
                              status="pending", current_stage=0)
            s.add(run)
            await s.commit()
            rid = run.id
        tid = await _task(pid, status=Status.COMPLETED, workflow_run_id=rid, stage_index=0)
        await track.record_shown(tid, [3])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await settle(r)
        await track.drain()
        return (await (_run_status(rid))), await _outcome(tid)

    assert _run(go()) == ("failed", [(3, "failed", False)])


async def _run_status(rid):
    async with SessionLocal() as s:
        return (await s.get(WorkflowRun, rid)).status


# ---- score ------------------------------------------------------------------------------------------------


async def _seed(pid, number, outcomes, days_ago=1):
    at = datetime.now(timezone.utc) - timedelta(days=days_ago)
    async with SessionLocal() as s:
        for o in outcomes:
            tid = uuid.uuid4().hex
            s.add(Task(id=tid, title="t", prompt="p", root_id=tid, status="completed", project_id=pid))
            await s.flush()
            s.add(DecisionShowing(project_id=pid, decision_number=number, task_id=tid, shown_at=at, outcome=o,
                                  retried=False))
        await s.commit()


def test_everything_fails_flags_nothing(client):
    """Review focus 4: the baseline rule - a project where every task fails flags no decision."""

    async def go():
        pid = await _project()
        await _seed(pid, 1, ["failed"] * 6)
        await _seed(pid, 2, ["fixed"] * 3 + ["failed"] * 5)
        async with SessionLocal() as s:
            return await track.track_for(s, pid, [1, 2, 3])

    t = _run(go())
    assert not any(x["trouble"] for x in t.values())
    assert t[1]["shown"] == 6 and t[1]["failed"] == 6 and t[2]["fixed"] == 3
    assert t[3] == {"shown": 0, "passed": 0, "completed": 0, "fixed": 0, "failed": 0, "last_shown_at": None,
                    "trouble": False}


def test_trouble_rule(client):
    async def go():
        pid = await _project()
        await _seed(pid, 1, ["fixed", "failed", "failed", "passed", "completed"])  # 3/5 = 60%
        await _seed(pid, 2, ["passed"] * 20)  # overall 3/25 = 12%
        await _seed(pid, 3, ["failed"] * 3 + [None] * 5)  # 3 with an outcome: too few
        await _seed(pid, 4, ["failed"] * 9, days_ago=120)  # outside the window
        async with SessionLocal() as s:
            return await track.track_for(s, pid, [1, 2, 3, 4])

    t = _run(go())
    assert t[1]["trouble"] and t[1]["shown"] == 5 and t[1]["last_shown_at"] is not None
    assert not t[2]["trouble"] and not t[3]["trouble"] and t[3]["shown"] == 8
    assert t[4]["shown"] == 0


# ---- list route and backfill ------------------------------------------------------------------------------


@pytest.fixture()
def world(client):
    pid = client.post("/projects", json={"name": f"trk-{uuid.uuid4().hex[:8]}"}).json()["id"]
    maint = login_as(f"tm-{uuid.uuid4().hex[:8]}@test.local")
    r = client.put(f"/projects/{pid}/members", json=[
        {"subject_type": "user", "subject_id": maint.user_id, "role": "maintainer"}])
    assert r.status_code == 200, r.text
    base = f"/projects/{pid}/decisions"
    for title in ("a", "b"):
        assert maint.post(base, json={"title": title}).status_code == 201
    return {"pid": pid, "base": base, "maint": maint}


def test_list_has_track_for_active_only(client, world):
    w = world
    _run(_seed(w["pid"], 1, ["failed", "passed"]))
    assert w["maint"].post(f"{w['base']}/2/retire").status_code == 200
    rows = {d["number"]: d for d in client.get(w["base"], params={"status": "all"}).json()}
    t = rows[1]["track"]
    assert {k: t[k] for k in ("shown", "passed", "completed", "fixed", "failed", "trouble")} == {
        "shown": 2, "passed": 1, "completed": 0, "fixed": 0, "failed": 1, "trouble": False}
    assert t["last_shown_at"]
    assert rows[2]["track"] is None
    assert client.get(f"{w['base']}/1").json()["track"] is None


def test_backfill_twice_adds_no_duplicates(client, world):
    """Review focus 3: from the decision_note events still present; the second run adds nothing."""
    w = world

    async def seed():
        tid = await _task(w["pid"], status=Status.FAILED)
        other = await _task(await _project(), status=Status.COMPLETED)  # another project's note
        async with SessionLocal() as s:
            for k, (t, ids) in enumerate([(tid, [1, 2]), (tid, [1]), (tid, "bad"), (other, [1])]):
                s.add(TaskEvent(task_id=t, seq=k + 1, type="decision_note", payload={"ids": ids, "path": "x"}))
            await s.commit()
        await track.record_shown(tid, [2])  # already recorded live
        return tid

    tid = _run(seed())
    url = f"{w['base']}/track/backfill"
    assert w["maint"].post(url).status_code == 403  # admin only
    r = client.post(url)
    assert r.status_code == 200 and r.json() == {"added": 1}
    assert client.post(url).json() == {"added": 0}
    assert _run(_outcome(tid)) == [(1, "failed", False), (2, "failed", False)]

    async def count():
        async with SessionLocal() as s:
            return (await s.execute(select(func.count()).select_from(DecisionShowing).where(
                DecisionShowing.project_id == w["pid"]))).scalar()

    assert _run(count()) == 2
    assert client.post(f"/projects/{uuid.uuid4().hex}/decisions/track/backfill").status_code == 404
