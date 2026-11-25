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
        # not an active run: the app's own runner (ticking in the background) leaves it alone
        run = WorkflowRun(workflow_id="w", name="tr", input="", stages=stages, project_ids=[pid], status="completed",
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


# ---- fix round 1 ------------------------------------------------------------------------------------------


async def _fix_task(pid, rid, attempt=0):
    tid = await _task(pid, status=Status.COMPLETED, workflow_run_id=rid, stage_index=1, stage_attempt=attempt,
                      tags=["workflow", "fix"])
    await track.record_shown(tid, [5])
    return tid


@pytest.mark.parametrize("gate_state,want", [
    ({"status": "passed", "fix_round": 1}, "passed"),
    ({"status": "failed", "fix_round": 2, "reason": "exit 1"}, "failed"),
    ({"status": "fixing", "fix_round": 1}, None),
])
def test_fix_task_judged_by_its_gate_final_result(client, gate_state, want):
    """I2 (design ruling, 2026-11-25): a fix task is never `fixed`: passed when its fix worked, failed when the gate failed."""

    async def go():
        pid = await _project()
        rid, tids = await _wf(pid, gate_state)
        fx = await _fix_task(pid, rid)
        await track.settle_task(fx)
        return await _outcome(fx), await _outcome(tids[0])

    fix_out, dev_out = _run(go())
    assert fix_out == [(5, want, False)]
    assert dev_out == [(1, {"passed": "fixed", "failed": "failed"}.get(want, "fixed"), False)]


@pytest.mark.parametrize("reason", [
    "the gate could not run (internal error)", "blocked by the guard: no network", "the fixer agent is not available",
    "the gate has no command to run", "the gate's project is missing or has no directory", "cancelled",
    "the fix was cancelled",
])
def test_gate_infrastructure_failure_is_not_counted(client, reason):
    async def go():
        pid = await _project()
        rid, tids = await _wf(pid, {"status": "failed", "reason": reason})
        await track.settle_run(rid)
        return await _outcome(tids[0])

    assert _run(go()) == [(1, None, False)]


def test_infra_reasons_match_the_runner():
    from app import workflow_runner as wr

    exact, prefixes = track._infra_reasons()
    assert {wr.GATE_ERROR_WORDS, wr.FIXER_GONE_WORDS, wr.FIX_CANCELLED_WORDS, wr.NO_COMMAND_WORDS,
            wr.NO_FOLDER_WORDS, "cancelled"} == exact
    assert prefixes == (wr.GUARD_BLOCKED_PREFIX,)
    for reason in exact | {f"{wr.GUARD_BLOCKED_PREFIX}: nope"}:
        assert not track._real_failure({"status": "failed", "reason": reason})
    assert track._real_failure({"status": "failed", "reason": wr.FIX_UNFINISHED_WORDS})
    assert track._real_failure({"status": "failed", "reason": "pytest -q exited with code 1"})


def test_older_step_attempt_takes_no_outcome(client):
    async def go():
        pid = await _project()
        rid, tids = await _wf(pid, {"status": "passed"})
        async with SessionLocal() as s:
            run = await s.get(WorkflowRun, rid)
            run.stage_state = {"0": {"status": "done", "attempt": 1}, "1": {"status": "passed"}}
            await s.commit()
        new = await _task(pid, status=Status.COMPLETED, workflow_run_id=rid, stage_index=0, stage_attempt=1)
        old_failed = await _task(pid, status=Status.FAILED, workflow_run_id=rid, stage_index=0, stage_attempt=0)
        for t in (new, old_failed):
            await track.record_shown(t, [1])
        await track.settle_run(rid)
        return await _outcome(tids[0]), await _outcome(new), await _outcome(old_failed)

    # the older attempt is not counted even when its task failed (the attempt check comes first)
    assert _run(go()) == ([(1, None, False)], [(1, "passed", False)], [(1, None, False)])


async def _thread_step(pid, status="running"):
    root = await _task(pid, status=Status.COMPLETED)
    tid = uuid.uuid4().hex
    async with SessionLocal() as s:
        s.add(Task(id=tid, title="t", prompt="p", root_id=root, parent_task_id=root, status=status))
        await s.commit()
    return tid


def test_ad_hoc_thread_step_counts_under_thread_project(client):
    """I1: a step with no project of its own, in a project thread, records and settles under that project."""
    from app.worker import WorkerManager

    async def go():
        pid = await _project()
        tid = await _thread_step(pid)
        await track.record_shown(tid, [6])
        await WorkerManager()._finish(tid, Status.COMPLETED)
        await track.drain()
        rows = await _rows(tid)
        return pid, [(r.project_id, r.decision_number, r.outcome) for r in rows]

    pid, rows = _run(go())
    assert rows == [(pid, 6, "completed")]


def test_backfill_counts_thread_steps(client, world):
    w = world

    async def seed():
        tid = await _thread_step(w["pid"], status=Status.FAILED)
        async with SessionLocal() as s:
            s.add(TaskEvent(task_id=tid, seq=1, type="decision_note", payload={"ids": [1], "path": "x"}))
            await s.commit()
        return tid

    tid = _run(seed())
    assert client.post(f"{w['base']}/track/backfill").json() == {"added": 1}
    assert _run(_outcome(tid)) == [(1, "failed", False)]


def test_backfill_batches_thousands_of_events(client, world):
    """I3: read by id in batches, one insert per batch; earliest showing kept; twice adds nothing."""
    from sqlalchemy import insert

    w = world
    n_tasks, per_task = 30, 100  # 3,000 events, 3 batches

    async def seed():
        tids = [await _task(w["pid"], status=Status.COMPLETED) for _ in range(n_tasks)]
        base = datetime.now(timezone.utc) - timedelta(days=2)
        rows = [{"task_id": t, "seq": k + 1, "type": "decision_note",
                 "payload": {"ids": [1 + (k % 4)], "path": "x"}, "created_at": base + timedelta(seconds=k)}
                for t in tids for k in range(per_task)]
        async with SessionLocal() as s:
            await s.execute(insert(TaskEvent), rows)
            await s.commit()
        return tids, base

    tids, base = _run(seed())
    assert track.BATCH == 1000
    url = f"{w['base']}/track/backfill"
    assert client.post(url).json() == {"added": n_tasks * 4}
    assert client.post(url).json() == {"added": 0}
    rows = _run(_rows(tids[0]))
    assert [(r.decision_number, r.outcome) for r in rows] == [(1, "completed"), (2, "completed"),
                                                              (3, "completed"), (4, "completed")]
    got = rows[0].shown_at if rows[0].shown_at.tzinfo else rows[0].shown_at.replace(tzinfo=timezone.utc)
    assert abs((got - base).total_seconds()) < 1


def test_settles_of_one_run_are_serialized_and_coalesced(client, monkeypatch):
    """M1/M2: one settle of a run in flight, at most one waiting; the rest are dropped; nothing overlaps."""
    calls, live, peak = [], [0], [0]
    real = track._settle_run_now

    mine = []

    async def counting(rid):
        if rid not in mine:  # other runs (the app's own runner) settle as usual
            return await real(rid)
        live[0] += 1
        peak[0] = max(peak[0], live[0])
        calls.append(rid)
        await asyncio.sleep(0.05)
        await real(rid)
        live[0] -= 1

    monkeypatch.setattr(track, "_settle_run_now", counting)

    async def go():
        pid = await _project()
        rid, tids = await _wf(pid, {"status": "fixing", "fix_round": 1})
        mine.append(rid)
        await asyncio.gather(*(track.settle_run(rid) for _ in range(10)))
        out = await _outcome(tids[0])
        return out, rid

    out, rid = _run(go())
    assert out == [(1, "fixed", False)]
    assert len(calls) == 2 and peak[0] == 1
    assert rid not in track._RUN_QUEUED and rid not in track._RUN_LOCKS


def test_repeat_notes_skip_the_database(client, monkeypatch):
    """M4: numbers this run already recorded are not written again; an emit that raises still records."""
    from app import worker as worker_mod
    from app.usage.tracker import UsageTracker

    seen = []
    real = track.record_shown

    async def spy(tid, nums, shown_at=None):
        seen.append(list(nums))
        return await real(tid, nums, shown_at)

    monkeypatch.setattr(track, "record_shown", spy)

    async def go():
        pid = await _project()
        return await _task(pid)

    tid = _run(go())
    w = worker_mod.WorkerManager()
    w._usage[tid] = UsageTracker()
    w._usage_spool[tid] = w._new_usage_spool()
    boom = [True]

    async def emit(etype, payload):
        if boom[0]:
            boom[0] = False
            raise RuntimeError("emit failed")

    d = guard_runtime.make_task_dir(tid)
    spool = d / guard_runtime.SPOOL_NAME
    line = json.dumps({"event": "PreToolUse", "nudge": "decisions", "ids": [8], "path": "a.py"}) + "\n"
    spool.write_text(line)
    _run(w._ingest_usage_spool(tid, emit))  # the emit raises: still recorded
    assert _run(_rows(tid)) and seen == [[8]]
    with open(spool, "a", encoding="utf-8") as f:
        f.write(line)
    _run(w._ingest_usage_spool(tid, emit))  # the note left over and a repeat: no new write
    assert seen == [[8]]
    guard_runtime.remove_task_dir(tid)


def test_record_shown_true_only_after_commit(client, monkeypatch):
    """m3: True once committed; False with no project or on a database error, and then the worker tries again."""
    from app import worker as worker_mod
    from app.usage.tracker import UsageTracker

    async def go():
        pid = await _project()
        tid = await _task(pid)
        loose = uuid.uuid4().hex
        async with SessionLocal() as s:
            s.add(Task(id=loose, title="t", prompt="p", root_id=loose, status="running"))
            await s.commit()
        return tid, await track.record_shown(tid, [1]), await track.record_shown(loose, [1]), \
            await track.record_shown(tid, [])

    tid, ok, no_project, empty = _run(go())
    assert (ok, no_project, empty) == (True, False, False)

    real_add = track._add_rows
    fail = [True]

    async def flaky_add(s, rows):
        if fail[0]:
            fail[0] = False
            raise RuntimeError("database away")
        return await real_add(s, rows)

    monkeypatch.setattr(track, "_add_rows", flaky_add)
    tid2 = _run(_task(_run(_project())))
    w = worker_mod.WorkerManager()
    w._usage[tid2] = UsageTracker()
    w._usage_spool[tid2] = w._new_usage_spool()

    async def emit(etype, payload):
        return None

    d = guard_runtime.make_task_dir(tid2)
    spool = d / guard_runtime.SPOOL_NAME
    line = json.dumps({"event": "PreToolUse", "nudge": "decisions", "ids": [3], "path": "a.py"}) + "\n"
    spool.write_text(line)
    _run(w._ingest_usage_spool(tid2, emit))  # the write fails: not marked as recorded
    assert _run(_rows(tid2)) == [] and 3 not in w._usage_spool[tid2].get("recorded", set())
    with open(spool, "a", encoding="utf-8") as f:
        f.write(line)
    _run(w._ingest_usage_spool(tid2, emit))  # shown again: written this time
    assert [r.decision_number for r in _run(_rows(tid2))] == [3]
    assert 3 in w._usage_spool[tid2]["recorded"]
    guard_runtime.remove_task_dir(tid2)


# ---- settles never hold SQLite write locks ----------------------------------------------------------------


def test_settle_decides_with_reads_and_writes_only_changes(client, monkeypatch):
    """No showings -> no write at all; a second settle with nothing new -> no write."""
    writes = []
    real = track._apply

    async def spy(changes):
        writes.append(sorted(changes))
        await real(changes)

    monkeypatch.setattr(track, "_apply", spy)

    async def go():
        pid = await _project()
        bare = await _task(pid, status=Status.COMPLETED)  # no showings
        async with SessionLocal() as s:
            run = WorkflowRun(workflow_id="w", name="tr", input="", stages=[_gate(pid)], project_ids=[pid],
                              status="completed", current_stage=0, stage_state={"0": {"status": "passed"}})
            s.add(run)
            await s.commit()
            empty_run = run.id
        await track.settle_tasks([bare])
        await track.settle_run(empty_run)
        assert writes == []
        rid, tids = await _wf(pid, {"status": "passed"})
        await track.settle_run(rid)
        await track.settle_run(rid)  # nothing changed
        return tids

    tids = _run(go())
    assert writes == [sorted(tids)]


def test_locked_database_is_retried_then_given_up(client, monkeypatch, caplog):
    import logging

    from sqlalchemy.exc import OperationalError

    monkeypatch.setattr(track, "LOCK_BACKOFF", 0.001)
    calls = [0]

    def locked():
        return OperationalError("UPDATE", {}, Exception("database is locked"))

    async def go(fail_times):
        calls[0] = 0

        async def fn():
            calls[0] += 1
            if calls[0] <= fail_times:
                raise locked()
            return "ok"

        return await track._retry_locked(fn)

    assert _run(go(3)) == "ok" and calls[0] == 4
    with pytest.raises(OperationalError):
        _run(go(9))
    assert calls[0] == 4  # 1 try + 3 retries

    async def other():
        raise ValueError("not a lock")

    with pytest.raises(ValueError):
        _run(track._retry_locked(other))

    # a settle that keeps hitting the lock logs and gives up (never raises)
    async def always_locked(changes):
        async def fn():
            raise locked()
        await track._retry_locked(fn)

    monkeypatch.setattr(track, "_apply", always_locked)

    async def settle():
        pid = await _project()
        rid, tids = await _wf(pid, {"status": "passed"})
        await track.settle_run(rid)
        return tids

    with caplog.at_level(logging.WARNING):
        tids = _run(settle())
    assert _run(_outcome(tids[0])) == [(1, None, False)]
    assert any("settling run" in r.getMessage() for r in caplog.records)


def test_smoke_settles_alongside_another_writer(client):
    """Smoke test (it does not reproduce the earlier suite failure): a writer doing read-then-write transactions
    while settles of the same run keep firing; every one of its commits succeeds."""

    async def go():
        pid = await _project()
        rid, tids = await _wf(pid, {"status": "passed"})
        states = [{"status": "fixing", "fix_round": 1}, {"status": "passed"}, {"status": "failed", "reason": "x"}]
        errors = []

        async def writer():
            for k in range(30):
                try:
                    async with SessionLocal() as s:
                        run = await s.get(WorkflowRun, rid)  # read first, then write: the risky shape
                        run.stage_state = {"0": {"status": "done"}, "1": states[k % 3]}
                        t = await s.get(Task, tids[0])
                        t.result_text = f"w{k}"
                        await s.commit()
                except Exception as e:  # noqa: BLE001
                    errors.append(repr(e))
                track.spawn_settle_run(rid)
                track.spawn_settle_task(tids[1])
                await asyncio.sleep(0)

        await asyncio.gather(writer(), *(track.settle_run(rid) for _ in range(5)))
        await track.drain()
        return errors, await _outcome(tids[0])

    errors, out = _run(go())
    assert errors == []
    assert out[0][1] in ("fixed", "failed", "passed")


def test_older_attempt_clears_a_settled_outcome(client):
    """I1: a step's task fails and settles `failed`; the step is started again -> its showing no longer counts."""

    async def go():
        pid = await _project()
        rid, tids = await _wf(pid, {"status": "passed"})
        await _set(tids[0], status=Status.FAILED)
        await track.settle_run(rid)
        before = await _outcome(tids[0])
        settled = (await _rows(tids[0]))[0].settled_at
        async with SessionLocal() as s:
            run = await s.get(WorkflowRun, rid)
            run.stage_state = {"0": {"status": "running", "attempt": 1}, "1": {"status": "passed"}}
            await s.commit()
        await track.settle_run(rid)
        row = (await _rows(tids[0]))[0]
        return before, settled, (row.outcome, row.settled_at), await _outcome(tids[1])

    before, settled, after, sibling = _run(go())
    assert before == [(1, "failed", False)] and settled is not None
    assert after == (None, None)
    assert sibling == [(1, None, False)]
