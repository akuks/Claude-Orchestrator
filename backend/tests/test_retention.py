"""Phase 7d task 1 (spec §2): tidying old run history - off until an admin switches it on; then finished
tasks older than N days keep one ``summary`` event and their closed handoffs drop the previous version."""

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app import retention
from app.database import SessionLocal
from app.models import ProjectDecision, Task, TaskEvent, TaskHandoff
from app.resume import handoff, logfmt


def run(coro):
    return asyncio.run(coro)


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _switch_off_after():
    yield
    run(retention.save_settings(enabled=False, days=60, by=None))


def tidy_on(only, days=60):
    """A pass with the switch on (tidy stops when it is off)."""
    run(retention.save_settings(enabled=True, days=days, by=None))
    return run(retention.tidy(days=days, only=only))


def mk(status="completed", ended_days=400.0, events=30, handoff_state="closed", notes="my notes", root=None):
    tid = uuid.uuid4().hex
    ended = _now() - timedelta(days=ended_days) if ended_days is not None else None

    async def go():
        async with SessionLocal() as s:
            s.add(Task(id=tid, title="old", prompt="p", root_id=root or tid, status=status, notes=notes,
                       created_at=(ended or _now()) - timedelta(hours=1),
                       started_at=(ended or _now()) - timedelta(minutes=50), completed_at=ended))
            for i in range(1, events + 1):
                if i % 3 == 0:
                    etype, payload = "tool_use", {"name": "Bash", "input": {"command": f"echo {i}"}}
                elif i % 3 == 1:
                    etype, payload = "text_output", {"text": f"line {i} " + "x" * 500}
                else:
                    etype, payload = "tool_result", {"content": f"ok {i}"}
                s.add(TaskEvent(task_id=tid, seq=i, type=etype, payload=payload,
                                created_at=(ended or _now()) - timedelta(minutes=40) + timedelta(seconds=i)))
            if root is None and handoff_state:
                s.add(TaskHandoff(root_id=tid, task_id=tid, status=handoff_state, content={"a": 2}, text="now",
                                  prev_content={"a": 1}, prev_text="before",
                                  saved_at=(ended or _now()), closed_at=ended if handoff_state == "closed" else None))
            await s.commit()

    run(go())
    return tid


def events_of(tid):
    async def go():
        async with SessionLocal() as s:
            return (await s.execute(select(TaskEvent).where(TaskEvent.task_id == tid).order_by(TaskEvent.seq))
                    ).scalars().all()

    return run(go())


def handoff_of(root):
    async def go():
        async with SessionLocal() as s:
            return (await s.execute(select(TaskHandoff).where(TaskHandoff.root_id == root))).scalar_one_or_none()

    return run(go())


def task_of(tid):
    async def go():
        async with SessionLocal() as s:
            return await s.get(Task, tid)

    return run(go())


# ---- the switch --------------------------------------------------------------------------------


def test_off_by_default_and_touches_nothing(client):
    got = run(retention.load_settings())
    assert got["enabled"] is False and got["days"] == 60
    tid = mk()
    assert run(retention.run_if_enabled()) is None
    assert len(events_of(tid)) == 30
    assert handoff_of(tid).prev_text == "before"


def test_settings_validate_days(client):
    with pytest.raises(ValueError):
        run(retention.save_settings(enabled=True, days=45, by=None))
    run(retention.save_settings(enabled=True, days=30, by=None))
    assert run(retention.load_settings())["days"] == 30


# ---- tidying ---------------------------------------------------------------------------------------


def test_tidies_finished_old_tasks_into_one_summary(client):
    old = mk(events=30)
    out = tidy_on(only=[old])
    assert out["tasks"] == 1
    evs = events_of(old)
    assert len(evs) == 1 and evs[0].type == "summary"
    p = evs[0].payload
    assert p["events"] == 30
    assert p["counts"] == {"text_output": 10, "tool_result": 10, "tool_use": 10}
    assert p["first_at"] < p["last_at"]
    assert len(p["lines"]) == 20 and all(isinstance(x, str) and len(x) <= 300 for x in p["lines"])
    assert any("line 28" in x for x in p["lines"]) and any("echo 30" in x for x in p["lines"])
    assert not any("line 1 " in x for x in p["lines"])  # only the last 20
    assert p["kept_days"] == 60 and p["tidied_at"]
    assert evs[0].seq == 30  # new events of a later Retry come after it
    # the closed handoff keeps its current version and drops the previous one
    h = handoff_of(old)
    assert h.content == {"a": 2} and h.text == "now" and h.prev_content is None and h.prev_text is None
    # nothing deleted
    t = task_of(old)
    assert t is not None and t.notes == "my notes" and t.status == "completed"


def test_never_touches_unfinished_or_recent_tasks(client):
    """Review focus 1."""
    ids = {
        "running": mk(status="running", ended_days=None, handoff_state="open"),
        "queued": mk(status="queued", ended_days=None, handoff_state="open"),
        "awaiting": mk(status="awaiting_approval", ended_days=None, handoff_state="open"),
        "recent": mk(ended_days=10),
        "running_with_old_end": mk(status="running", ended_days=400),  # a Retry in flight keeps an old row?
    }
    out = tidy_on(only=list(ids.values()))
    assert out["tasks"] == 0
    for name, tid in ids.items():
        assert len(events_of(tid)) == 30, name
        assert handoff_of(tid).prev_text == "before", name


def test_an_open_handoff_keeps_its_previous_version(client):
    tid = mk(handoff_state="open")
    tidy_on(only=[tid])
    assert events_of(tid)[0].type == "summary"
    assert handoff_of(tid).prev_text == "before"


def test_idempotent_and_a_later_run_is_folded_in(client):
    tid = mk(events=6)
    tidy_on(only=[tid])
    first = events_of(tid)
    assert tidy_on(only=[tid])["tasks"] == 0  # already a summary: skipped
    assert [e.id for e in events_of(tid)] == [e.id for e in first]

    async def more():  # a Retry long ago added events after the summary
        async with SessionLocal() as s:
            s.add(TaskEvent(task_id=tid, seq=7, type="error", payload={"message": "boom"},
                            created_at=_now() - timedelta(days=300)))
            await s.commit()

    run(more())
    assert tidy_on(only=[tid])["tasks"] == 1
    evs = events_of(tid)
    assert len(evs) == 1 and evs[0].type == "summary"
    assert evs[0].payload["events"] == 7 and evs[0].payload["counts"]["error"] == 1
    assert evs[0].payload["lines"][-1].endswith("boom")


def test_batches_and_preview(client, monkeypatch):
    monkeypatch.setattr(retention, "BATCH", 2)
    monkeypatch.setattr(retention, "PAUSE_SECONDS", 0)
    ids = [mk(events=3) for _ in range(5)]
    pre = run(retention.preview(days=60, only=ids))
    assert pre["tasks"] == 5 and pre["bytes"] > 0
    out = tidy_on(only=ids)
    assert out["tasks"] == 5 and out["batches"] == 3
    assert run(retention.preview(days=60, only=ids))["tasks"] == 0


def test_never_deletes_decisions(client):
    tid = mk()

    async def n():
        async with SessionLocal() as s:
            return (await s.execute(select(func.count()).select_from(ProjectDecision))).scalar()

    before = run(n())
    tidy_on(only=[tid])
    assert run(n()) == before


# ---- readers cope with a summary-only task --------------------------------------------------------


def test_readers_cope_with_a_summary_only_task(client, tmp_path):
    tid = mk()
    tidy_on(only=[tid])
    ev = {"type": "summary", "payload": events_of(tid)[0].payload, "created_at": _now()}
    line = logfmt.line(ev)
    assert line and "TIDIED" in line and "30" in line
    row = run(handoff.snapshot(tid, "manual"))
    assert row is not None and row.text

    from app.asks.record import _event_line

    assert "30" in _event_line(events_of(tid)[0], [])


# ---- routes (admin) --------------------------------------------------------------------------------


def test_routes_are_admin_only(client, member_client):
    assert member_client.get("/admin/health/retention").status_code == 403
    assert member_client.patch("/admin/health/retention", json={"enabled": True}).status_code == 403
    assert member_client.post("/admin/health/retention/run").status_code == 403


def test_routes(client):
    tid = mk(ended_days=4000)
    r = client.get("/admin/health/retention")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"enabled", "days", "choices", "tasks", "bytes", "last_run", "last_count", "running"}
    assert body["enabled"] is False and body["days"] == 60 and body["choices"] == [30, 60, 90]
    assert body["tasks"] >= 1 and body["bytes"] > 0
    # run now refuses while the switch is off
    assert client.post("/admin/health/retention/run").status_code == 409
    assert client.patch("/admin/health/retention", json={"days": 45}).status_code == 400
    r = client.patch("/admin/health/retention", json={"enabled": True, "days": 90})
    assert r.status_code == 200 and r.json()["enabled"] is True and r.json()["days"] == 90
    r = client.post("/admin/health/retention/run")
    assert r.status_code == 202, r.text
    deadline = time.time() + 15
    while time.time() < deadline and client.get("/admin/health/retention").json()["running"]:
        time.sleep(0.1)
    body = client.get("/admin/health/retention").json()
    assert body["last_run"] and body["last_count"] >= 1 and body["running"] is False
    assert [e.type for e in events_of(tid)] == ["summary"]
    r = client.patch("/admin/health/retention", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["days"] == 90


# ---- fix round 1: switch off, races, stopping, preview cost -----------------------------------------


def _counts():
    async def go():
        async with SessionLocal() as s:
            return ((await s.execute(select(func.count()).select_from(TaskEvent))).scalar(),
                    (await s.execute(select(func.count()).select_from(TaskHandoff)
                                     .where(TaskHandoff.prev_text.is_not(None)))).scalar())

    return run(go())


def test_switch_off_writes_nothing_from_loop_get_post_or_tidy(client):
    run(retention.save_settings(enabled=False, days=60, by=None))
    mk()
    mk()
    before = _counts()
    assert run(retention.run_if_due()) is None
    assert client.get("/admin/health/retention").status_code == 200
    assert client.post("/admin/health/retention/run").status_code == 409
    out = run(retention.tidy(days=60))  # even called directly, it checks the switch
    assert out["stopped"] is True and out["tasks"] == 0
    assert _counts() == before


class _HookedSession:
    """SessionLocal whose sessions call ``hook`` just before a DELETE of task events runs."""

    def __init__(self, hook):
        from app.database import SessionLocal as real

        self._real, self._hook = real, hook

    def __call__(self):
        outer = self

        class Proxy:
            def __init__(self, s):
                self._s = s

            def __getattr__(self, k):
                return getattr(self._s, k)

            async def execute(self, stmt, *a, **kw):
                from sqlalchemy.sql.dml import Delete

                if isinstance(stmt, Delete) and stmt.table.name == "task_events":
                    await outer._hook(outer._real)
                return await self._s.execute(stmt, *a, **kw)

        class CM:
            def __init__(self):
                self._cm = outer._real()

            async def __aenter__(self):
                return Proxy(await self._cm.__aenter__())

            async def __aexit__(self, *e):
                return await self._cm.__aexit__(*e)

        return CM()


def test_a_retry_racing_the_tidy_keeps_its_events(client, monkeypatch):
    """A person's Retry commits between the re-check and the delete: the queued task is left alone."""
    from app.constants import Status

    tid = mk(events=10)

    async def retry(real):
        async with real() as other:
            t = await other.get(Task, tid)
            t.status, t.completed_at, t.started_at = Status.QUEUED, None, None
            await other.commit()

    run(retention.save_settings(enabled=True, days=60, by=None))
    monkeypatch.setattr(retention, "SessionLocal", _HookedSession(retry))
    out = run(retention.tidy(days=60, only=[tid]))
    assert out["tasks"] == 0
    assert task_of(tid).status == Status.QUEUED
    assert len(events_of(tid)) == 10 and "summary" not in {e.type for e in events_of(tid)}


def test_an_event_after_the_read_is_kept_after_the_summary(client, monkeypatch):
    tid = mk(events=5)

    async def emit(real):
        async with real() as other:
            other.add(TaskEvent(task_id=tid, seq=6, type="text_output", payload={"text": "late line"}))
            await other.commit()

    run(retention.save_settings(enabled=True, days=60, by=None))
    monkeypatch.setattr(retention, "SessionLocal", _HookedSession(emit))
    run(retention.tidy(days=60, only=[tid]))
    evs = events_of(tid)
    assert [(e.seq, e.type) for e in evs] == [(5, "summary"), (6, "text_output")]
    assert evs[0].payload["events"] == 5 and not any("late line" in x for x in evs[0].payload["lines"])
    monkeypatch.undo()
    tidy_on(only=[tid])  # the late event folds in once, its line once
    p = events_of(tid)[0].payload
    assert p["events"] == 6 and sum("late line" in x for x in p["lines"]) == 1


def test_switching_off_mid_pass_stops_it(client, monkeypatch):
    monkeypatch.setattr(retention, "BATCH", 1)
    real_sleep = asyncio.sleep
    ids = [mk(events=3) for _ in range(3)]
    run(retention.save_settings(enabled=True, days=60, by=None))

    async def sleep(_):  # an admin switches it off during the first pause
        await retention.save_settings(enabled=False, by=None)
        await real_sleep(0)

    monkeypatch.setattr(retention.asyncio, "sleep", sleep)
    out = run(retention.tidy(days=60, only=ids))
    assert out["tasks"] == 1 and out["stopped"] is True
    assert sum([e.type for e in events_of(i)] == ["summary"] for i in ids) == 1
    assert all(handoff_of(i).prev_text == "before" for i in ids)  # nor the handoffs


def test_changing_the_days_mid_pass_stops_it(client, monkeypatch):
    monkeypatch.setattr(retention, "BATCH", 1)
    real_sleep = asyncio.sleep
    ids = [mk(events=3) for _ in range(2)]
    run(retention.save_settings(enabled=True, days=60, by=None))

    async def sleep(_):
        await retention.save_settings(days=90, by=None)
        await real_sleep(0)

    monkeypatch.setattr(retention.asyncio, "sleep", sleep)
    out = run(retention.tidy(days=60, only=ids))
    assert out["tasks"] == 1 and out["stopped"] is True


def test_handoff_prune_skips_a_row_saved_meanwhile(client, monkeypatch):
    """The prune is one conditional UPDATE: a snapshot between its select and its update keeps its prev."""
    tid = mk()
    real_execute_update = retention.update

    def update(model):
        async def fresh():
            async with SessionLocal() as s:
                row = (await s.execute(select(TaskHandoff).where(TaskHandoff.root_id == tid))).scalar_one()
                row.saved_at, row.prev_text = _now(), "fresh prev"
                await s.commit()

        import threading

        th = threading.Thread(target=lambda: asyncio.run(fresh()))
        th.start()
        th.join()
        return real_execute_update(model)

    monkeypatch.setattr(retention, "update", update)
    assert run(retention._prune_handoffs(_now() - timedelta(days=60), only=[tid])) == 0
    assert handoff_of(tid).prev_text == "fresh prev"


def test_preview_reads_no_payload_and_is_cached(client, monkeypatch):
    ids = [mk(events=4) for _ in range(2)]
    seen = []
    from sqlalchemy import event as sa_event

    from app.database import engine

    def spy(conn, cursor, statement, *a):
        seen.append(statement)

    sa_event.listen(engine.sync_engine, "before_cursor_execute", spy)
    try:
        pre = run(retention.preview(days=60, only=ids))
    finally:
        sa_event.remove(engine.sync_engine, "before_cursor_execute", spy)
    assert pre["tasks"] == 2 and pre["bytes"] == 8 * retention.ROW_ESTIMATE
    assert seen and not any("payload" in st for st in seen)
    assert retention.PREVIEW_CACHE_SECONDS >= 600
    seen.clear()
    sa_event.listen(engine.sync_engine, "before_cursor_execute", spy)
    try:
        assert run(retention.preview(days=60, only=ids)) == pre  # cached: no query
    finally:
        sa_event.remove(engine.sync_engine, "before_cursor_execute", spy)
    assert seen == []


def test_one_failing_task_is_skipped_and_the_run_recorded(client, monkeypatch, caplog):
    import logging

    bad, good = mk(events=3), mk(events=3)
    real = retention._summarise

    async def flaky(tid, cutoff, days):
        if tid == bad:
            raise RuntimeError("bad payload")
        return await real(tid, cutoff, days)

    monkeypatch.setattr(retention, "_summarise", flaky)
    monkeypatch.setattr(retention, "_eligible", lambda cutoff, only=None, _e=retention._eligible: _e(cutoff, [bad, good]))
    run(retention.save_settings(enabled=True, days=60, by=None))
    with caplog.at_level(logging.ERROR, logger="app.retention"):
        out = run(retention.run_if_enabled())
    assert out["tasks"] == 1 and out["skipped"] == 1
    assert [e.type for e in events_of(good)] == ["summary"] and len(events_of(bad)) == 3
    assert any(bad in r.getMessage() for r in caplog.records)
    cfg = run(retention.load_settings())
    assert cfg["last_run"] and cfg["last_count"] == 1


def test_the_loop_logs_a_failed_pass(client, monkeypatch, caplog):
    import logging

    from app import main

    calls = []

    async def boom():
        calls.append(1)
        if len(calls) > 1:
            raise asyncio.CancelledError
        raise RuntimeError("db down")

    monkeypatch.setattr(main.retention, "run_if_due", boom)
    monkeypatch.setattr(main.settings, "retention_first_delay_seconds", 0)
    monkeypatch.setattr(main.settings, "retention_check_interval_seconds", 0)
    with caplog.at_level(logging.ERROR, logger="app.main"):
        with pytest.raises(asyncio.CancelledError):
            run(main._retention_loop())
    assert any("tidying pass failed" in r.getMessage() for r in caplog.records)
