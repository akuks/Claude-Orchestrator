"""Phase 7d task 1 (spec §1): a closed handoff stays closed until a new run of its thread starts, and a
thread whose step completed with nothing left open is closed automatically (closed_by "system")."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Task, TaskEvent, TaskHandoff, TaskQuestion
from app.resume import handoff
from conftest import login_as, wait_task


@pytest.fixture(autouse=True)
def _no_lessons_job(monkeypatch):
    from app.decisions import after_run

    async def noop(root_id, closer_id):
        return None

    monkeypatch.setattr(after_run, "lessons_after_close", noop)


def run(coro):
    return asyncio.run(coro)


def _now():
    return datetime.now(timezone.utc)


def mk_task(**kw):
    tid = kw.pop("id", None) or uuid.uuid4().hex

    async def go():
        async with SessionLocal() as s:
            s.add(Task(id=tid, title=kw.pop("title", "t"), prompt=kw.pop("prompt", "do it"),
                       root_id=kw.pop("root_id", tid), status=kw.pop("status", "completed"), **kw))
            await s.commit()

    run(go())
    return tid


def add_event(task_id, etype, payload, seq):
    async def go():
        async with SessionLocal() as s:
            s.add(TaskEvent(task_id=task_id, seq=seq, type=etype, payload=payload))
            await s.commit()

    run(go())


def row_of(root):
    async def go():
        async with SessionLocal() as s:
            return (await s.execute(select(TaskHandoff).where(TaskHandoff.root_id == root))).scalar_one_or_none()

    return run(go())


def close(root, by="someone", at=None):
    async def go():
        async with SessionLocal() as s:
            row = (await s.execute(select(TaskHandoff).where(TaskHandoff.root_id == root))).scalar_one()
            row.status, row.closed_at, row.closed_by = "closed", at or _now(), by
            await s.commit()

    run(go())


def set_task(tid, **kw):
    async def go():
        async with SessionLocal() as s:
            t = await s.get(Task, tid)
            for k, v in kw.items():
                setattr(t, k, v)
            await s.commit()

    run(go())


# ---- snapshot keeps a closed handoff closed --------------------------------------------------------


def test_heartbeat_notes_and_finish_of_the_same_run_keep_it_closed(client, tmp_path):
    """Review focus 2: the run started before the close; its later snapshots update the content only."""
    tid = mk_task(workspace_dir=str(tmp_path), status="running", started_at=_now() - timedelta(minutes=5))
    run(handoff.snapshot(tid, "working"))
    close(tid)
    add_event(tid, "text_output", {"text": "more progress"}, 1)
    for reason in ("working", "notes", "manual", "run ended"):
        row = run(handoff.snapshot(tid, reason))
        assert row.status == "closed" and row.closed_by == "someone" and row.closed_at is not None, reason
    stored = row_of(tid)
    assert stored.status == "closed" and stored.content["last_reply"] == "more progress"
    assert stored.reason == "run ended"


def test_a_new_run_of_the_thread_reopens_it(client, tmp_path):
    root = mk_task(workspace_dir=str(tmp_path), started_at=_now() - timedelta(hours=2),
                   completed_at=_now() - timedelta(hours=1))
    run(handoff.snapshot(root, "run ended"))
    close(root, at=_now() - timedelta(minutes=30))
    # a follow-up / Continue step of the thread that started after the close
    step = mk_task(workspace_dir=str(tmp_path), root_id=root, parent_task_id=root, origin="continue",
                   status="running", started_at=_now(), created_at=_now())
    row = run(handoff.snapshot(step, "working"))
    assert row.status == "open" and row.closed_at is None and row.closed_by is None


def test_retry_reopens_it(client, tmp_path):
    tid = mk_task(workspace_dir=str(tmp_path), started_at=_now() - timedelta(hours=2),
                  completed_at=_now() - timedelta(hours=1))
    run(handoff.snapshot(tid, "run ended"))
    close(tid, at=_now() - timedelta(minutes=30))
    assert run(handoff.snapshot(tid, "manual")).status == "closed"
    set_task(tid, status="running", started_at=_now(), completed_at=None)  # what Retry + the worker do
    assert run(handoff.snapshot(tid, "working")).status == "open"


def test_the_continue_snapshot_alone_keeps_it_closed(client, tmp_path):
    """The Continue route snapshots before its step is queued; only the queued step reopens it (reopen)."""
    tid = mk_task(workspace_dir=str(tmp_path), started_at=_now() - timedelta(hours=2),
                  completed_at=_now() - timedelta(hours=1))
    run(handoff.snapshot(tid, "run ended"))
    close(tid)
    assert run(handoff.snapshot(tid, "continue")).status == "closed"
    assert run(handoff.reopen(tid)) is True
    row = row_of(tid)
    assert row.status == "open" and row.closed_at is None and row.closed_by is None
    assert run(handoff.reopen(tid)) is False  # already open


def test_continue_route_reopens_a_closed_handoff(client):
    me = login_as(f"hc-cont-{uuid.uuid4().hex[:6]}@test.local")
    r = me.post("/tasks", json={"prompt": "close then continue"})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    assert wait_task(me, tid)["status"] == "completed"
    assert me.post(f"/tasks/{tid}/handoff/close").status_code == 200
    assert me.post(f"/tasks/{tid}/handoff/refresh").json()["status"] == "closed"  # a person's Refresh
    closed_at = row_of(tid).closed_at
    r = me.post(f"/tasks/{tid}/continue", json={"mode": "fresh"})
    assert r.status_code == 201, r.text
    row = row_of(tid)  # right after the POST: reopened (unless the step already ran and closed it again)
    assert row.status == "open" or (row.closed_by == "system" and row.closed_at > closed_at)
    assert row.closed_by != me.user_id
    assert wait_task(me, r.json()["id"])["status"] == "completed"


def test_a_refused_continue_leaves_it_closed(client, monkeypatch):
    from fastapi import HTTPException

    from app.routers import tasks as tasks_router

    me = login_as(f"hc-409-{uuid.uuid4().hex[:6]}@test.local")
    tid = me.post("/tasks", json={"prompt": "refused continue"}).json()["id"]
    assert wait_task(me, tid)["status"] == "completed"
    assert me.post(f"/tasks/{tid}/handoff/close").status_code == 200

    async def busy(*a, **kw):  # a step of the thread became active under the lock
        raise HTTPException(409, "A step of this thread is still active")

    monkeypatch.setattr(tasks_router, "queue_continue", busy)
    assert me.post(f"/tasks/{tid}/continue", json={"mode": "fresh"}).status_code == 409
    row = row_of(tid)
    assert row.status == "closed" and row.closed_by == me.user_id


# ---- closed automatically when a step completes with nothing open ----------------------------------


def test_a_completed_run_closes_its_handoff(client):
    me = login_as(f"hc-auto-{uuid.uuid4().hex[:6]}@test.local")
    r = me.post("/tasks", json={"prompt": "finish cleanly"})
    tid = r.json()["id"]
    assert wait_task(me, tid)["status"] == "completed"
    import time

    deadline = time.time() + 10
    while time.time() < deadline and (row_of(tid) is None or row_of(tid).status != "closed"):
        time.sleep(0.1)
    row = row_of(tid)
    assert row.status == "closed" and row.closed_by == "system" and row.closed_at is not None
    # notes are kept on close
    assert row.text and row.content


def _finished(tmp_path, status="completed", **kw):
    tid = mk_task(workspace_dir=str(tmp_path), status=status, started_at=_now() - timedelta(minutes=3),
                  completed_at=_now(), **kw)
    run(handoff.snapshot(tid, "run ended"))
    return tid


@pytest.mark.parametrize("status", ["failed", "cancelled", "queued"])
def test_auto_close_only_on_success(client, tmp_path, status):
    tid = _finished(tmp_path, status=status)
    assert run(handoff.auto_close(tid)) is False
    assert row_of(tid).status == "open"


def test_auto_close_skips_a_thread_with_an_open_question(client, tmp_path):
    tid = _finished(tmp_path)

    async def ask(state):
        async with SessionLocal() as s:
            s.add(TaskQuestion(task_id=tid, root_id=tid, question="Which one?", status=state))
            await s.commit()

    run(ask("open"))
    assert run(handoff.auto_close(tid)) is False and row_of(tid).status == "open"
    other = _finished(tmp_path)

    async def answered():
        async with SessionLocal() as s:
            s.add(TaskQuestion(task_id=other, root_id=other, question="Done?", status="answered"))
            await s.commit()

    run(answered())
    assert run(handoff.auto_close(other)) is True and row_of(other).status == "closed"


def test_auto_close_skips_an_open_todo_an_active_step_and_a_child(client, tmp_path):
    todo = _finished(tmp_path)

    async def put_todo():
        async with SessionLocal() as s:
            row = (await s.execute(select(TaskHandoff).where(TaskHandoff.root_id == todo))).scalar_one()
            row.content = {**row.content, "step_task_id": todo,
                           "step": {"source": "notes", "n": 1, "m": 3, "text": "middle"}}
            await s.commit()

    run(put_todo())
    assert run(handoff.auto_close(todo)) is False

    busy = _finished(tmp_path)
    mk_task(root_id=busy, parent_task_id=busy, status="queued", origin="continue")
    assert run(handoff.auto_close(busy)) is False

    parent = _finished(tmp_path)
    kid = mk_task(root_id=parent, parent_task_id=parent, origin="agent", status="completed", completed_at=_now())
    assert run(handoff.auto_close(kid)) is False and row_of(parent).status == "open"


def test_auto_close_racing_a_persons_close_keeps_the_person(client, tmp_path, monkeypatch):
    """The person closes between auto_close's read (open) and its write: the close stays theirs."""
    import threading

    tid = _finished(tmp_path)
    real = handoff.open_todo

    def close_meanwhile(content, task_id):  # the last check before the write
        t = threading.Thread(target=close, args=(tid,), kwargs={"by": "person-2"})
        t.start()
        t.join()
        return real(content, task_id)

    monkeypatch.setattr(handoff, "open_todo", close_meanwhile)
    assert run(handoff.auto_close(tid)) is False
    row = row_of(tid)
    assert row.status == "closed" and row.closed_by == "person-2"


def test_auto_close_keeps_a_persons_close(client, tmp_path):
    tid = _finished(tmp_path)
    close(tid, by="person-1")
    assert run(handoff.auto_close(tid)) is False
    assert row_of(tid).closed_by == "person-1"
