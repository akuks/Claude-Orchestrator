"""Phase 7a §4 and §5: time and spend per step in the run API (newest attempt only), the
plain status words and a step named once in log lines."""

import asyncio
from datetime import datetime, timedelta, timezone

from app.constants import Status
from app.database import SessionLocal
from app.gates import totals
from app.models import Task, WorkflowGateRun
from app.workflow_runner import WorkflowRunner, _label
from _runs7a import (  # noqa: F401 - fixtures
    ANN, FAIL, FakeWorker, _end_runs, _project, _run, agents, approval, fakes, gate, has, make_run, settle,
    texts, use_runner,
)

T = datetime(2026, 11, 25, 9, 0, 0, tzinfo=timezone.utc)


def _iso(d):
    return d.isoformat()


async def _task(rid, i, attempt, cost, tin, tout, done_at=None):
    async with SessionLocal() as s:
        s.add(Task(title="t", prompt="p", status=Status.COMPLETED, workflow_run_id=rid, stage_index=i,
                   stage_attempt=attempt, total_cost_usd=cost, input_tokens=tin, output_tokens=tout,
                   completed_at=done_at))
        await s.commit()


async def _gate_row(rid, i, attempt, ms):
    async with SessionLocal() as s:
        s.add(WorkflowGateRun(run_id=rid, stage_index=i, attempt=attempt, handout="h", command="c", kind="full",
                              started_at=T, finished_at=T, duration_ms=ms, outcome="failed", exit_code=1))
        await s.commit()


async def _all_tasks(s, rid):
    from sqlalchemy import select

    return (await s.execute(select(Task).where(Task.workflow_run_id == rid))).scalars().all()


async def _all_gates(s, rid):
    from sqlalchemy import select

    return (await s.execute(select(WorkflowGateRun).where(WorkflowGateRun.run_id == rid))).scalars().all()


def test_per_step_newest_attempt_and_seconds(client):
    async def go():
        pid = await _project()
        stages = [{"name": "Dev", "items": [{"agent": "x", "prompt": "p"}]}, gate(pid),
                  {"name": "Fix", "items": [{"agent": "x", "prompt": "p"}]}, approval("Later")]
        now = datetime.now(timezone.utc)
        rid = await make_run(stages, [pid], status="running", current=2, state={
            "0": {"attempt": 1, "status": "done", "started_at": _iso(T), "finished_at": _iso(T + timedelta(seconds=95))},
            "1": {"attempt": 0, "status": "passed", "started_at": _iso(T), "finished_at": _iso(T + timedelta(minutes=4))},
            "2": {"attempt": 0, "status": "running", "started_at": _iso(now - timedelta(seconds=30))},
        })
        await _task(rid, 0, 0, 5.0, 100, 100)  # the old attempt: not counted
        await _task(rid, 0, 1, 0.12, 1000, 200)
        await _task(rid, 0, 1, 0.03, 10, 20)
        await _task(rid, 2, 0, None, None, None)
        async with SessionLocal() as s:
            from app.models import WorkflowRun

            run = await s.get(WorkflowRun, rid)
            return await totals.per_step(s, run, await _all_tasks(s, rid), await _all_gates(s, rid))

    u = asyncio.run(go())
    assert u[0] == {"cost_usd": 0.15, "input_tokens": 1010, "output_tokens": 220, "seconds": 95}
    assert u[1] == {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "seconds": 240}
    assert u[2]["cost_usd"] == 0.0 and u[2]["input_tokens"] == 0 and 29 <= u[2]["seconds"] <= 40
    assert u[3] is None  # not started


def test_failed_steps_without_a_finish_time(client):
    async def go():
        pid = await _project()
        stages = [{"name": "Dev", "items": [{"agent": "x", "prompt": "p"}]}, gate(pid)]
        rid = await make_run(stages, [pid], status="failed", current=1, state={
            "0": {"attempt": 0, "status": "failed", "started_at": _iso(T)},
            "1": {"attempt": 2, "status": "failed", "started_at": _iso(T)},
        })
        await _task(rid, 0, 0, 0.5, 1, 2, done_at=T + timedelta(seconds=70))
        await _gate_row(rid, 1, 1, 9000)  # an older attempt
        await _gate_row(rid, 1, 2, 1500)
        await _gate_row(rid, 1, 2, 2600)
        async with SessionLocal() as s:
            from app.models import WorkflowRun

            return await totals.per_step(s, await s.get(WorkflowRun, rid), await _all_tasks(s, rid),
                                         await _all_gates(s, rid))

    u = asyncio.run(go())
    assert u[0]["seconds"] == 70 and u[0]["cost_usd"] == 0.5
    assert u[1]["seconds"] == 4  # 1.5 s + 2.6 s of this attempt's gate runs


def test_api_has_step_usage_and_status_words(client, monkeypatch):
    r = WorkflowRunner(FakeWorker(), 999)
    use_runner(monkeypatch, r)

    async def setup():
        pid = await _project()
        stages = [gate(pid), approval("Sign"), gate(pid, name="Again")]
        running = await make_run(stages, [pid], status="running", current=0, state={
            "0": {"attempt": 0, "status": "running", "handout": "h", "passed_cmds": [],
                  "started_at": _iso(T)}})
        waiting = await make_run(stages, [pid], status="paused", current=1, state={
            "0": {"attempt": 0, "status": "passed", "started_at": _iso(T), "finished_at": _iso(T)},
            "1": {"attempt": 0, "status": "waiting_approval", "started_at": _iso(T)}})
        failed = await make_run(stages, [pid], status="failed", current=0, state={
            "0": {"attempt": 0, "status": "failed", "started_at": _iso(T)}})
        return running, waiting, failed

    running, waiting, failed = asyncio.run(setup())
    d = client.get(f"/workflows/runs/{running}").json()
    assert d["stage_state"]["0"]["status_word"] == "Interrupted — will continue"
    assert len(d["step_usage"]) == 3 and d["step_usage"][1] is None
    assert set(d["step_usage"][0]) == {"cost_usd", "input_tokens", "output_tokens", "seconds"}
    d = client.get(f"/workflows/runs/{waiting}").json()
    assert d["stage_state"]["1"]["status_word"] == "Waiting for approval"
    assert "status_word" not in d["stage_state"]["0"]
    assert d["step_usage"][0]["seconds"] == 0
    d = client.get(f"/workflows/runs/{failed}").json()
    assert d["stage_state"]["0"]["status_word"] == "Failed"


# --- a step named once (F128) --------------------------------------------------------------


def test_label_names_a_step_once():
    run = type("R", (), {})()
    run.stages = [{"name": "gate", "kind": "gate"}, {"name": "Test", "kind": "gate"},
                  {"name": "Approval", "kind": "approval"}, {"name": "Build", "items": []}]
    assert [_label(run, i) for i in range(4)] == ["Step gate", "Step Test (gate)", "Step Approval",
                                                  "Step Build"]


def test_log_line_for_a_gate_named_gate(fakes):
    fakes.script["pytest -q"] = [FAIL()]

    async def go():
        pid = await _project()
        rid = await make_run([gate(pid, name="gate")], [pid])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await settle(r)
        run = await _run(rid)
        assert has(run, "Step gate failed: pytest -q, exit 1") and not has(run, "(gate)")

    asyncio.run(go())


def test_step_usage_follows_what_the_viewer_may_see(client, monkeypatch):
    """A viewer who cannot see one project gets null for its gate step and for an agent step
    whose tasks are all in it, and sums only the tasks it may see."""
    import uuid as _uuid

    from conftest import login_as
    from test_gates_api import _grant

    r = WorkflowRunner(FakeWorker(), 999)
    use_runner(monkeypatch, r)
    seen = client.post("/projects", json={"name": f"r7a-{_uuid.uuid4().hex[:8]}"}).json()["id"]
    unseen = client.post("/projects", json={"name": f"r7a-{_uuid.uuid4().hex[:8]}"}).json()["id"]

    async def setup():
        gpid = await _project()
        stages = [{"name": "Dev", "items": [{"agent": "x", "prompt": "p"}]},
                  {"name": "Secret", "items": [{"agent": "x", "prompt": "p"}]},
                  gate(gpid, name="Hidden gate")]
        state = {str(i): {"attempt": 0, "status": "done", "started_at": _iso(T),
                          "finished_at": _iso(T + timedelta(seconds=10))} for i in range(3)}
        rid = await make_run(stages, [seen, unseen, gpid], status="completed", current=2, state=state)
        async with SessionLocal() as s:
            for i, pid, cost in ((0, seen, 0.25), (0, unseen, 7.0), (1, unseen, 3.0)):
                s.add(Task(title="t", prompt="p", status=Status.COMPLETED, workflow_run_id=rid, stage_index=i,
                           stage_attempt=0, total_cost_usd=cost, input_tokens=1, output_tokens=1, project_id=pid))
            await s.commit()
        return rid

    rid = asyncio.run(setup())
    viewer = login_as(f"r7a-{_uuid.uuid4().hex[:8]}@test.local")
    _grant(client, seen, viewer.user_id, "contributor")
    u = viewer.get(f"/workflows/runs/{rid}").json()["step_usage"]
    assert u[0] == {"cost_usd": 0.25, "input_tokens": 1, "output_tokens": 1, "seconds": 10}
    assert u[1] is None and u[2] is None
    full = client.get(f"/workflows/runs/{rid}").json()["step_usage"]
    assert full[0]["cost_usd"] == 7.25 and full[1]["cost_usd"] == 3.0 and full[2] is not None


def test_status_word_between_commands_stays_running(client, monkeypatch):
    """A gate whose task is alive but has no process right now (between commands, probing the
    guard) is "Running", never "Interrupted"."""
    r = WorkflowRunner(FakeWorker(), 999)
    use_runner(monkeypatch, r)

    async def setup():
        pid = await _project()
        return await make_run([gate(pid)], [pid], status="running", state={
            "0": {"attempt": 0, "status": "running", "handout": "h", "passed_cmds": [0], "started_at": _iso(T)}})

    rid = asyncio.run(setup())
    loop = asyncio.new_event_loop()
    try:
        alive = loop.create_task(asyncio.Event().wait())
        r._gates[(rid, 0)] = alive
        assert client.get(f"/workflows/runs/{rid}").json()["stage_state"]["0"]["status_word"] == "Running"
        alive.cancel()
        loop.run_until_complete(asyncio.gather(alive, return_exceptions=True))
        assert client.get(f"/workflows/runs/{rid}").json()["stage_state"]["0"]["status_word"] == \
            "Interrupted — will continue"
    finally:
        r._gates.clear()
        loop.close()
