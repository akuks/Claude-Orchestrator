"""Phase 5a task 4: run log, run totals and the cost line."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.database import SessionLocal
from app.gates import runlog, totals
from app.models import Task, WorkflowRun


def test_runlog_keeps_newest_200():
    run = SimpleNamespace(log=None)
    for i in range(250):
        runlog.append(run, f"line {i}")
    assert len(run.log) == 200
    assert run.log[0]["text"] == "line 50" and run.log[-1]["text"] == "line 249"
    assert datetime.fromisoformat(run.log[-1]["at"]).tzinfo is not None


def _iso(d):
    return d.isoformat()


def test_compute_two_finished_one_running(client):
    now = datetime.now(timezone.utc)
    rid = uuid.uuid4().hex
    state = {
        "0": {"started_at": _iso(now - timedelta(minutes=60)), "finished_at": _iso(now - timedelta(minutes=40))},
        "1": {"started_at": _iso(now - timedelta(minutes=40)), "finished_at": _iso(now - timedelta(minutes=30))},
        "2": {"started_at": _iso(now - timedelta(minutes=5))},
    }

    async def go():
        async with SessionLocal() as s:
            s.add(WorkflowRun(id=rid, workflow_id="w", name="n", stages=[{"name": "Plan"}, {"name": "Develop"}, {"name": "Test"}],
                              status="running", current_stage=2, stage_state=state))
            for idx, att, cost, i, o in [(0, 0, 0.5, 1000, 500), (1, 0, 1.0, 2000, 1000), (1, 1, 0.4, None, 300), (2, 0, 0.1, 10, 0)]:
                s.add(Task(title="t", prompt="p", workflow_run_id=rid, stage_index=idx, stage_attempt=att,
                           total_cost_usd=cost, input_tokens=i, output_tokens=o))
            await s.commit()
            run = await s.get(WorkflowRun, rid)
            assert run.log == []
            return await totals.compute(s, run)

    t = asyncio.run(go())
    assert t["stages_done"] == 2 and t["stages"] == 3
    assert abs(t["minutes"] - 35.0) < 0.2
    assert abs(t["cost_usd"] - 2.0) < 1e-6
    assert t["tokens"] == 1500 + 3000 + 300 + 10
    assert t["last"]["name"] == "Develop"
    assert abs(t["last"]["minutes"] - 10.0) < 0.1
    assert abs(t["last"]["cost_usd"] - 1.4) < 1e-6
    assert t["last"]["tokens"] == 3300


def test_compute_empty_run(client):
    async def go():
        async with SessionLocal() as s:
            run = WorkflowRun(id=uuid.uuid4().hex, workflow_id="w", name="n", stages=[{}], status="pending")
            return await totals.compute(s, run)

    t = asyncio.run(go())
    assert t == {"stages_done": 0, "stages": 1, "minutes": 0.0, "cost_usd": 0.0, "tokens": 0, "last": None}
    assert totals.line(t) == "So far: 0 of 1 steps done, 0.0 min, $0.00, 0 tokens"


def test_line_exact():
    t = {"stages_done": 3, "stages": 6, "minutes": 41.2, "cost_usd": 2.1, "tokens": 1_200_000,
         "last": {"name": "Develop", "minutes": 18.0, "cost_usd": 1.4, "tokens": 5}}
    assert totals.line(t) == "So far: 3 of 6 steps done, 41 min, $2.10, 1.2M tokens (last: Develop, 18 min, $1.40)"
    t2 = {"stages_done": 1, "stages": 2, "minutes": 9.96, "cost_usd": 0.005, "tokens": 900,
          "last": {"name": "A", "minutes": 4.26, "cost_usd": 0.0, "tokens": 0}}
    assert totals.line(t2) == "So far: 1 of 2 steps done, 10 min, $0.01, 900 tokens (last: A, 4.3 min, $0.00)"
    assert totals.line({**t2, "tokens": 41_400, "last": None}).endswith("$0.01, 41k tokens")
    assert totals.line({**t2, "tokens": 999_700, "last": None}).endswith("1.0M tokens")


def test_compute_counts_old_run_stages_without_state(client):
    def mk(status, current):
        return WorkflowRun(id=uuid.uuid4().hex, workflow_id="w", name="n", stages=[{}, {}, {}],
                           status=status, current_stage=current, stage_state={})

    async def go():
        async with SessionLocal() as s:
            return (await totals.compute(s, mk("completed", 2)), await totals.compute(s, mk("running", 2)))

    done, mid = asyncio.run(go())
    assert done["stages_done"] == 3
    assert mid["stages_done"] == 2
