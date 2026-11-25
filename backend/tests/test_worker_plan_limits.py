"""Phase 7c: the worker notices Claude plan limits in the stream and waits for the reset time.

Stream lines go through WorkerManager._handle_stream_event (unit) and through the mock claude's
[plan-limit:MODE:RESETS] seam (whole runs)."""

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import planlimits as pl
from app.config import settings
from app.database import SessionLocal
from app.models import PlanLimitStatus, Task, TaskEvent
from app.worker import WorkerManager
from conftest import _PROBES, login_as, wait_task

pytestmark = pytest.mark.usefixtures("client")


def _now():
    return datetime.now(timezone.utc)


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def _user(kind="oauth_token"):
    c = login_as(f"wpl-{uuid.uuid4().hex[:10]}@test.local")
    c.token = "t" + uuid.uuid4().hex
    r = c.put("/me/credentials", json={"claude_token": c.token, "claude_kind": kind})
    assert r.status_code == 200, r.text
    return c


def _row(owner):
    async def go():
        async with SessionLocal() as s:
            return await pl.get_row(s, owner)
    return asyncio.run(go())


def _events(tid, etype=None):
    async def go():
        async with SessionLocal() as s:
            q = select(TaskEvent).where(TaskEvent.task_id == tid).order_by(TaskEvent.seq)
            return [(e.type, e.payload) for e in (await s.execute(q)).scalars().all()
                    if etype is None or e.type == etype]
    return asyncio.run(go())


def _task(tid):
    async def go():
        async with SessionLocal() as s:
            return await s.get(Task, tid)
    return asyncio.run(go())


def _until(fn, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.2)
    return fn()


def _waiting(tid):
    t = _task(tid)
    return t if t is not None and t.status == "queued" and t.not_before is not None else None


def _runs() -> list[str]:
    f = _PROBES / "plan-runs"
    return f.read_text().split() if f.exists() else []


def _store(owner, status, resets, ref=None, api_key=False):
    asyncio.run(pl.record({"owner": owner, "api_key": api_key, "ref": ref},
                          {"status": status, "resets_at": resets, "utilization": None, "raw": {}}, None))


# ---- the stream (unit) ------------------------------------------------------------------------------------


def _drive(events, owner):
    w = WorkerManager()
    got = []

    async def emit(t, p):
        got.append((t, p))

    ctx = {"task_id": "t-" + uuid.uuid4().hex[:8], "mcp_ids": {}, "secrets": [], "model_stored": True,
           "plan": {"owner": owner, "rejected": False, "resets_at": None, "rate_limited": False,
                    "last_status": None, "warned": False}}
    result = {"is_error": False}

    async def go():
        for ev in events:
            await w._handle_stream_event(ev, emit, result, ctx)

    asyncio.run(go())
    return got, ctx, result


def _rle(status, resets=None, **more):
    info = {"status": status, **more}
    if resets is not None:
        info["resetsAt"] = resets
    return {"type": "rate_limit_event", "rate_limit_info": info, "uuid": "u", "session_id": "s"}


@pytest.mark.parametrize("unit", ["s", "ms"])
def test_rate_limit_event_is_stored_and_logged(unit):
    owner = {"owner": "u-" + uuid.uuid4().hex[:8], "api_key": False, "ref": None}
    when = (_now() + timedelta(hours=1)).replace(microsecond=0)
    value = int(when.timestamp()) * (1000 if unit == "ms" else 1)
    got, ctx, _ = _drive([_rle("rejected", value, utilization=1.0)], owner)
    row = _row(owner["owner"])
    assert row.status == "rejected" and _aware(row.resets_at) == when and row.utilization == 1.0
    assert row.raw["resetsAt"] == value and row.task_id == ctx["task_id"]
    ev = [p for t, p in got if t == "plan_limit"]
    assert ev == [{"status": "rejected", "resets_at": when.isoformat(), "utilization": 1.0,
                   "raw": {"status": "rejected", "resetsAt": value, "utilization": 1.0},
                   "credential_owner": owner["owner"]}]
    assert ctx["plan"]["rejected"] is True and ctx["plan"]["resets_at"] == when


@pytest.mark.parametrize("value", [None, 4102444800, 10 ** 18, "soon"])
def test_absurd_or_missing_reset_is_stored_as_none(value):
    owner = {"owner": "u-" + uuid.uuid4().hex[:8], "api_key": False, "ref": None}
    got, ctx, _ = _drive([_rle("rejected", value)], owner)
    row = _row(owner["owner"])
    assert row.status == "rejected" and row.resets_at is None
    assert not pl.is_holding(row)  # no reset time: never holds work
    assert ctx["plan"]["resets_at"] is None


def test_warning_is_one_event_and_no_pause():
    owner = {"owner": "u-" + uuid.uuid4().hex[:8], "api_key": False, "ref": None}
    when = (_now() + timedelta(hours=2)).replace(microsecond=0)
    got, ctx, _ = _drive([_rle("allowed_warning", int(when.timestamp()), utilization=0.8),
                          _rle("allowed_warning", int(when.timestamp()), utilization=0.9),
                          _rle("allowed")], owner)
    ev = [p for t, p in got if t == "plan_limit"]
    warn = [p for p in ev if p.get("warning")]
    assert len(warn) == 1
    assert warn[0]["text"] == f"Your Claude plan is close to its limit. It resets at {when:%H:%M}."
    assert [p["status"] for p in ev] == ["allowed_warning", "allowed"]
    assert ctx["plan"]["rejected"] is False
    assert _row(owner["owner"]).status == "allowed"  # latest wins


def test_api_retry_keeps_every_documented_field_and_marks_rate_limit():
    ev = {"type": "system", "subtype": "api_retry", "attempt": 2, "max_retries": 10, "retry_delay_ms": 4000,
          "error_status": 429, "error": "rate_limit", "session_id": "s1", "uuid": "x"}
    got, ctx, _ = _drive([ev], None)
    assert got == [("system", {"subtype": "api_retry", "session_id": "s1", "attempt": 2, "max_retries": 10,
                               "retry_delay_ms": 4000, "error_status": 429, "error": "rate_limit"})]
    assert ctx["plan"]["rate_limited"] is True
    # other system events keep their old shape
    got, _, _ = _drive([{"type": "system", "subtype": "init", "session_id": "s2", "tools": ["x"]}], None)
    assert got == [("system", {"subtype": "init", "session_id": "s2"})]


def test_assistant_error_rate_limit_is_noticed():
    got, ctx, _ = _drive([{"type": "assistant", "error": "rate_limit",
                           "message": {"content": [{"type": "text", "text": "x"}]}}], None)
    assert ctx["plan"]["rate_limited"] is True


def test_what_counts_as_a_limit_hit():
    hit = WorkerManager._plan_limit_hit
    assert hit({"plan": {"rejected": True}}, {}, "")
    assert hit({"plan": {"rate_limited": True}}, {}, "")
    assert hit({"plan": {}}, {"is_error": True, "result_text": "You've hit your session limit"}, "")
    assert not hit({"plan": {}}, {"is_error": False, "result_text": "You've hit your session limit"}, "")
    assert hit({"plan": {}}, {}, "API Error: 429 rate limit")
    assert not hit({"plan": {}}, {"is_error": True, "result_text": "tests failed"}, "boom")


def test_api_key_and_ownerless_runs_store_nothing_without_an_event():
    got, _, _ = _drive([{"type": "result", "result": "ok", "is_error": False}], None)
    assert [t for t, _ in got if t == "plan_limit"] == []


# ---- whole runs ------------------------------------------------------------------------------------------


def test_rejected_run_waits_for_the_reset_not_a_retry(client):
    u = _user()
    resets = (_now() + timedelta(hours=1)).replace(microsecond=0)
    tid = u.post("/tasks", json={"prompt": f"go [plan-limit:rejected:{int(resets.timestamp())}]"}).json()["id"]
    t = _until(lambda: _waiting(tid))
    assert t is not None, _task(tid).status
    assert _aware(t.not_before) == resets + timedelta(minutes=1)
    assert t.wait_reason == f"Paused — your Claude plan limit was reached. Starts again at " \
                            f"{resets + timedelta(minutes=1):%H:%M}."
    assert t.attempt == 0  # not one of the transient retries
    assert not [e for e in _events(tid) if e[0] == "retry"]
    paused = [p for _, p in _events(tid, "plan_limit") if p.get("paused")]
    assert paused and paused[-1]["not_before"] == (resets + timedelta(minutes=1)).isoformat()
    out = u.get(f"/tasks/{tid}").json()
    assert out["wait_reason"] == t.wait_reason and out["not_before"] and out["status"] == "queued"
    row = _row(u.user_id)
    assert row.status == "rejected" and row.task_id == tid and row.credential_ref

    # the owner's next task does not start; it waits with the same sentence
    before = len(_runs())
    tid2 = u.post("/tasks", json={"prompt": "next [plan-limit:allowed:none]"}).json()["id"]
    t2 = _until(lambda: _waiting(tid2))
    assert t2 is not None and t2.wait_reason == t.wait_reason
    time.sleep(0.5)
    assert len(_runs()) == before  # it never ran
    assert _task(tid2).started_at is None

    # another person's work is never held by this status
    other = _user()
    done = wait_task(other, other.post("/tasks", json={"prompt": "free [plan-limit:allowed:none]"}).json()["id"])
    assert done["status"] == "completed"

    # cancel ends a waiting task at once
    assert u.post(f"/tasks/{tid}/cancel").status_code == 200
    assert _until(lambda: _task(tid).status == "cancelled")

    # "Try now" (admin): the owner's status goes and its waiting tasks start now
    r = client.delete(f"/plan/status/{u.user_id}")
    assert r.status_code == 200 and r.json()["removed"] is True and tid2 in r.json()["tasks"]
    assert wait_task(u, tid2)["status"] == "completed"
    t2 = _task(tid2)
    assert t2.not_before is None and t2.wait_reason is None


def test_limit_seen_only_in_the_result_text_is_requeued(client):
    """Review Focus 2: nothing on stderr, no rejected event; the result says the limit was hit."""
    u = _user()
    resets = (_now() + timedelta(minutes=90)).replace(microsecond=0)
    ms = int(resets.timestamp()) * 1000
    tid = u.post("/tasks", json={"prompt": f"go [plan-limit:textonly:{ms}]"}).json()["id"]
    t = _until(lambda: _waiting(tid))
    assert t is not None, _task(tid).status
    assert _aware(t.not_before) == resets + timedelta(minutes=1) and t.attempt == 0
    row = _row(u.user_id)
    assert row.status == "rejected" and _aware(row.resets_at) == resets  # other tasks wait too
    u.post(f"/tasks/{tid}/cancel")
    assert _until(lambda: _task(tid).status == "cancelled")
    client.delete(f"/plan/status/{u.user_id}")


def test_no_reset_time_takes_the_old_backoff(client, monkeypatch):
    monkeypatch.setattr(settings, "retry_max_attempts", 1)
    monkeypatch.setattr(settings, "retry_backoff_seconds", 0)
    u = _user()
    tid = u.post("/tasks", json={"prompt": "go [plan-limit:rejected:none]"}).json()["id"]
    done = wait_task(u, tid)
    assert done["status"] == "failed" and done["attempt"] == 1
    retry = [p for t, p in _events(tid) if t == "retry"]
    assert len(retry) == 1 and retry[0]["backoff_seconds"] == 0
    assert _task(tid).not_before is None


def test_stale_rejected_status_holds_nothing(client):
    """Review Focus 3: a rejected status whose reset time passed no longer holds work."""
    u = _user()
    _store(u.user_id, "rejected", _now() - timedelta(minutes=2))
    done = wait_task(u, u.post("/tasks", json={"prompt": "go [plan-limit:allowed:none]"}).json()["id"])
    assert done["status"] == "completed" and done["wait_reason"] is None


def test_start_check_respects_not_before(client):
    """A queued task whose not_before is ahead waits (a timer queues it again); one whose not_before passed
    (e.g. after a restart) starts and loses the wait."""
    u = _user()

    async def go():
        ahead, past = uuid.uuid4().hex, uuid.uuid4().hex
        async with SessionLocal() as s:
            for tid, nb in ((ahead, _now() + timedelta(hours=1)), (past, _now() - timedelta(minutes=1))):
                s.add(Task(id=tid, title="t", prompt="p", root_id=tid, status="queued", created_by=u.user_id,
                           not_before=nb, wait_reason="Paused — x"))
            await s.commit()
        w = WorkerManager()
        got = []

        async def emit(t, p):
            got.append(t)

        waits = await w._plan_wait(ahead, emit)
        timer = w._waits.get(ahead)
        runs = await w._plan_wait(past, emit)
        if timer is not None:
            timer.cancel()
        async with SessionLocal() as s:
            p = await s.get(Task, past)
            return waits, timer is not None, runs, p.not_before, p.wait_reason, got

    waits, timed, runs, nb, why, got = asyncio.run(go())
    assert waits is True and timed is True and got == []  # no new sentence: no second event
    assert runs is False and nb is None and why is None


def test_warning_run_completes_with_one_warning(client):
    u = _user()
    resets = (_now() + timedelta(hours=3)).replace(microsecond=0)
    done = wait_task(u, u.post("/tasks", json={
        "prompt": f"go [plan-limit:warning:{int(resets.timestamp())}]"}).json()["id"])
    assert done["status"] == "completed"
    warn = [p for _, p in _events(done["id"], "plan_limit") if p.get("warning")]
    assert len(warn) == 1 and warn[0]["text"] == f"Your Claude plan is close to its limit. It resets at {resets:%H:%M}."
    me = u.get("/plan/status").json()
    assert me["plan_applies"] is True and me["status"]["status"] == "allowed_warning"
    assert me["status"]["banner"]["kind"] == "warning"


def test_api_key_user_is_not_held_by_someone_elses_status(client):
    """Review Focus 5."""
    plan_user = _user()
    _store(plan_user.user_id, "rejected", _now() + timedelta(hours=1))
    key_user = _user("api_key")
    done = wait_task(key_user, key_user.post("/tasks", json={"prompt": "go [plan-limit:allowed:none]"}).json()["id"])
    assert done["status"] == "completed" and done["wait_reason"] is None
    me = key_user.get("/plan/status").json()
    assert me["api_key"] is True and me["plan_applies"] is False
    client.delete(f"/plan/status/{plan_user.user_id}")


def test_stored_rows_never_hold_a_secret(client):
    u = _user()
    resets = int((_now() + timedelta(hours=1)).timestamp())
    tid = u.post("/tasks", json={"prompt": f"go [plan-limit:rejected:{resets}]"}).json()["id"]
    assert _until(lambda: _waiting(tid))
    u.post(f"/tasks/{tid}/cancel")
    _until(lambda: _task(tid).status == "cancelled")

    async def rows():
        async with SessionLocal() as s:
            return (await s.execute(select(PlanLimitStatus).where(
                PlanLimitStatus.credential_owner == u.user_id))).scalars().all()

    (row,) = asyncio.run(rows())
    text = repr({c.name: getattr(row, c.name) for c in PlanLimitStatus.__table__.columns})
    assert u.token not in text and "sk-test-fallback" not in text
    assert all(u.token not in repr(p) for _, p in _events(tid))
    client.delete(f"/plan/status/{u.user_id}")
