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
    out = run(retention.tidy(days=60, only=[old]))
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
    out = run(retention.tidy(days=60, only=list(ids.values())))
    assert out["tasks"] == 0
    for name, tid in ids.items():
        assert len(events_of(tid)) == 30, name
        assert handoff_of(tid).prev_text == "before", name


def test_an_open_handoff_keeps_its_previous_version(client):
    tid = mk(handoff_state="open")
    run(retention.tidy(days=60, only=[tid]))
    assert events_of(tid)[0].type == "summary"
    assert handoff_of(tid).prev_text == "before"


def test_idempotent_and_a_later_run_is_folded_in(client):
    tid = mk(events=6)
    run(retention.tidy(days=60, only=[tid]))
    first = events_of(tid)
    assert run(retention.tidy(days=60, only=[tid]))["tasks"] == 0  # already a summary: skipped
    assert [e.id for e in events_of(tid)] == [e.id for e in first]

    async def more():  # a Retry long ago added events after the summary
        async with SessionLocal() as s:
            s.add(TaskEvent(task_id=tid, seq=7, type="error", payload={"message": "boom"},
                            created_at=_now() - timedelta(days=300)))
            await s.commit()

    run(more())
    assert run(retention.tidy(days=60, only=[tid]))["tasks"] == 1
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
    out = run(retention.tidy(days=60, only=ids))
    assert out["tasks"] == 5 and out["batches"] == 3
    assert run(retention.preview(days=60, only=ids))["tasks"] == 0


def test_never_deletes_decisions(client):
    tid = mk()

    async def n():
        async with SessionLocal() as s:
            return (await s.execute(select(func.count()).select_from(ProjectDecision))).scalar()

    before = run(n())
    run(retention.tidy(days=60, only=[tid]))
    assert run(n()) == before


# ---- readers cope with a summary-only task --------------------------------------------------------


def test_readers_cope_with_a_summary_only_task(client, tmp_path):
    tid = mk()
    run(retention.tidy(days=60, only=[tid]))
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
