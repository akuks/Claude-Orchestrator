"""Phase 3b task 2: the worker's ingest of co-guard's band records (`usage_band` run-log events,
TaskUsage.bands once per band) and of the `injected` chars nudges spool (each spool line counted once,
live or at the final ingest)."""

import asyncio
import json
import uuid

from sqlalchemy import select

from app.database import SessionLocal
from app.guard import runtime as guard_runtime
from app.models import Task, TaskEvent, TaskUsage
from app.resume import logfmt
from conftest import wait_task


def _events(task_id):
    async def go():
        async with SessionLocal() as s:
            return (await s.execute(select(TaskEvent).where(TaskEvent.task_id == task_id)
                                    .order_by(TaskEvent.seq))).scalars().all()

    return asyncio.run(go())


def _usage(task_id):
    async def go():
        async with SessionLocal() as s:
            return await s.get(TaskUsage, task_id)

    return asyncio.run(go())


def test_run_ingests_bands_once_and_injected_chars(client):
    r = client.post("/tasks", json={"prompt": "go [stream-fixture:usage_bands]"})
    assert r.status_code == 201, r.text
    done = wait_task(client, r.json()["id"])
    assert done["status"] == "completed", done.get("error")
    evs = _events(done["id"])
    bands = [e.payload for e in evs if e.type == "usage_band"]
    assert bands == [{"band": "budget80", "value": 4.0}, {"band": "budget95", "value": 4.8},
                     {"band": "fresh", "value": 152345}]
    # band / nudge records are bookkeeping, never `guard` events
    assert not [e for e in evs if e.type == "guard" and e.payload.get("event") == "PostToolUse"]
    row = _usage(done["id"])
    assert row.bands == [{"band": "budget80", "ts": 1790000001.0}, {"band": "budget95", "ts": 1790000001.5},
                         {"band": "fresh", "ts": 1790000003.0}]
    assert row.breakdown["injected_chars"] == 500 + 0 + 160 + 80 + 200 + 100  # the bogus band is not counted
    lines = [logfmt.line({"type": e.type, "payload": e.payload, "created_at": e.created_at})
             for e in evs if e.type == "usage_band"]
    assert [ln.split(" ", 1)[1] for ln in lines] == ["BAND budget80 $4.00", "BAND budget95 $4.80",
                                                     "BAND fresh 152k"]


def test_logfmt_usage_band_lines():
    def ln(p):
        return logfmt.line({"type": "usage_band", "payload": p, "created_at": None})

    assert ln({"band": "budget95", "value": 4.756}) == "--:--:-- BAND budget95 $4.76"
    assert ln({"band": "fresh", "value": 152999}) == "--:--:-- BAND fresh 152k"
    assert ln({"band": "fresh", "value": None}) == "--:--:-- BAND fresh"
    assert ln({"band": "budget80", "value": "x"}) == "--:--:-- BAND budget80"
    assert ln({"band": "fresh", "value": float("inf")}) == "--:--:-- BAND fresh"


def _task_row():
    tid = uuid.uuid4().hex

    async def go():
        async with SessionLocal() as s:
            s.add(Task(id=tid, title="t", prompt="p", root_id=tid, status="running"))
            await s.commit()

    asyncio.run(go())
    return tid


def test_live_then_final_ingest_counts_each_line_once(client):
    """The heartbeat reads the spool live, the final ingest reads it again: every line counts once,
    every band once (DB-deduped), a half-written last line waits for its newline."""
    from app import worker as worker_mod
    from app.usage.tracker import UsageTracker

    tid = _task_row()
    w = worker_mod.WorkerManager()
    tracker = w._usage[tid] = UsageTracker()
    w._usage_spool[tid] = w._new_usage_spool()
    emitted = []

    async def emit(etype, payload):
        emitted.append((etype, payload))

    d = guard_runtime.make_task_dir(tid)
    spool = d / guard_runtime.SPOOL_NAME

    def add(*recs, tail=""):
        with open(spool, "a", encoding="utf-8") as f:
            f.write("".join(json.dumps(r) + "\n" for r in recs) + tail)

    add({"event": "PostToolUse", "tool_use_id": "x", "done": True, "ts": 1.0},
        {"event": "PostToolUse", "band": "budget80", "value": 4.0, "injected": 80, "ts": 2.0},
        tail='{"event": "PostToolUse", "nudge": "notes", "inj')
    asyncio.run(w._ingest_usage_spool(tid, emit))
    assert tracker.injected_chars == 80
    asyncio.run(w._ingest_usage_spool(tid, emit))  # nothing new
    assert tracker.injected_chars == 80
    with open(spool, "a", encoding="utf-8") as f:
        f.write('ected": 30, "ts": 3.0}\n')
    add({"event": "PostToolUse", "band": "fresh", "value": 160000, "injected": 200, "ts": 4.0})
    asyncio.run(w._ingest_usage_spool(tid, emit))
    assert tracker.injected_chars == 80 + 30 + 200
    # the final ingest (it reads the whole spool as guard events, then wipes the task dir)
    asyncio.run(w._ingest_guard_spool(tid, emit))
    assert tracker.injected_chars == 310
    assert [p for t, p in emitted if t == "usage_band"] == [{"band": "budget80", "value": 4.0},
                                                            {"band": "fresh", "value": 160000}]
    assert not [p for t, p in emitted if t == "guard"]
    assert _usage(tid).bands == [{"band": "budget80", "ts": 2.0}, {"band": "fresh", "ts": 4.0}]
    assert not d.exists()


def test_recovery_style_ingest_without_live_state_dedupes_bands_by_the_row(client):
    """An ingest with no live state (a crashed run's recovery) still records bands, never twice."""
    from app import worker as worker_mod

    tid = _task_row()
    w = worker_mod.WorkerManager()
    emitted = []

    async def emit(etype, payload):
        emitted.append((etype, payload))

    d = guard_runtime.make_task_dir(tid)
    (d / guard_runtime.SPOOL_NAME).write_text(
        json.dumps({"event": "PostToolUse", "band": "budget95", "value": 4.9, "injected": 160, "ts": 5.0}) + "\n"
        + json.dumps({"event": "PostToolUse", "band": "budget95", "value": 5.0, "injected": 160, "ts": 6.0})
        + "\n" + '{"event": "PostToolUse", "band": "fresh", "value": Infinity, "ts": NaN}\n')
    asyncio.run(w._ingest_usage_spool(tid, emit))
    asyncio.run(w._ingest_guard_spool(tid, emit))
    assert [p for t, p in emitted if t == "usage_band"] == [{"band": "budget95", "value": 4.9},
                                                            {"band": "fresh", "value": None}]
    assert _usage(tid).bands == [{"band": "budget95", "ts": 5.0}, {"band": "fresh", "ts": None}]


def test_crash_path_finish_ingests_bands_and_injected(client):
    """_run_guarded's crash handler goes straight to _finish: the spool's bands and injected chars are
    still ingested (once: a line the heartbeat already read is not counted again)."""
    from app import worker as worker_mod
    from app.usage.tracker import UsageTracker

    tid = _task_row()
    w = worker_mod.WorkerManager()
    tracker = w._usage[tid] = UsageTracker()
    w._usage_spool[tid] = w._new_usage_spool()
    d = guard_runtime.make_task_dir(tid)
    spool = d / guard_runtime.SPOOL_NAME
    spool.write_text(json.dumps({"event": "PostToolUse", "nudge": "notes", "injected": 40, "ts": 1.0}) + "\n")

    async def noop(*a):
        pass

    asyncio.run(w._ingest_usage_spool(tid, noop))  # a heartbeat read before the crash
    with open(spool, "a", encoding="utf-8") as f:
        f.write(json.dumps({"event": "PostToolUse", "band": "fresh", "value": 160000, "injected": 200,
                            "ts": 2.0}) + "\n")

    async def boom(task_id):
        raise RuntimeError("kaboom")

    w._run_job = boom
    w._sem = asyncio.Semaphore(1)

    async def crash():
        await w._sem.acquire()
        await w._run_guarded(tid)

    asyncio.run(crash())
    assert not d.exists()
    row = _usage(tid)
    assert row.bands == [{"band": "fresh", "ts": 2.0}]
    assert row.breakdown["injected_chars"] == 240
    assert tracker.injected_chars == 240
    assert [e.payload for e in _events(tid) if e.type == "usage_band"] == [{"band": "fresh", "value": 160000}]


def test_rerun_clears_the_earlier_attempts_bands(client):
    """A step run again starts with empty bands and figures: the new attempt's crossing is recorded,
    the earlier attempt's fresh band is gone."""
    r = client.post("/tasks", json={"prompt": "go [stream-fixture:usage_band80]"})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    done = wait_task(client, tid)
    assert done["status"] == "completed", done.get("error")

    async def stale():
        async with SessionLocal() as s:
            row = await s.get(TaskUsage, tid)
            row.bands = [{"band": "fresh", "ts": 1.0}, {"band": "budget80", "ts": 1.0}]
            row.context_tokens, row.peak_context = 999999, 999999
            await s.commit()

    asyncio.run(stale())
    r = client.post(f"/tasks/{tid}/retry")
    assert r.status_code in (200, 201, 202), r.text
    rerun = wait_task(client, r.json().get("id", tid))
    assert rerun["status"] == "completed", rerun.get("error")
    row = _usage(rerun["id"])
    assert row.bands == [{"band": "budget80", "ts": 1790000001.0}]
    assert row.peak_context != 999999
