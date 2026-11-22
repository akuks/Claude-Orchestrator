"""Phase 3b task 3: the automatic fresh Continue after a completed step that crossed the project's
fresh-session threshold (a ``fresh`` band), when the handoff has an open todo or the notes changed after
the band; at most 3 in a row per thread (Review Focus 3)."""

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import Task, TaskEvent, TaskHandoff, TaskUsage
from app.resume import logfmt
from app.resume.texts import AUTO_CONTINUE_LINE, CONTINUE_PROMPT
from conftest import login_as, wait_task

BAND_TS = 1790000003.0
OPEN = {"step": {"n": 2, "m": 3, "text": "Write the parser", "source": "todo"}, "next": "Test it"}
DONE = {"step": {"n": 3, "m": 3, "text": "all steps done", "source": "todo"}, "next": None}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def submitted(monkeypatch):
    got = []

    async def submit(task_id, priority, created_at):
        got.append(task_id)

    monkeypatch.setattr(app.state.worker, "submit", submit)
    return got


@pytest.fixture(scope="module")
def owner(client):
    return login_as(f"ac-owner-{uuid.uuid4().hex[:8]}@test.local")


_clock = [datetime.now(timezone.utc) - timedelta(hours=1)]


def _step(owner, *, root=None, parent=None, origin="user", status="completed", auto=False,
          bands=(("fresh", BAND_TS),), content=OPEN, notes_at=None, peak=152345):
    """A thread step row (created_at strictly increasing), its usage row and the thread's handoff."""
    tid = uuid.uuid4().hex
    _clock[0] += timedelta(seconds=1)

    async def go():
        async with SessionLocal() as s:
            s.add(Task(id=tid, title="t", prompt="the work", root_id=root or tid, parent_task_id=parent,
                       origin=origin, status=status, auto_continued=auto, created_by=owner.user_id,
                       session_id=f"s-{tid[:6]}", created_at=_clock[0],
                       notes="Step: 2 of 3" if notes_at else None, notes_updated_at=notes_at))
            if bands is not None:
                s.add(TaskUsage(task_id=tid, root_id=root or tid, peak_context=peak, context_tokens=peak,
                                bands=[{"band": b, "ts": ts} for b, ts in bands]))
            if content is not None:
                r = root or tid
                row = (await s.execute(select(TaskHandoff).where(TaskHandoff.root_id == r))).scalar_one_or_none()
                if row is None:
                    row = TaskHandoff(root_id=r, version=1)
                    s.add(row)
                row.task_id, row.content = tid, dict(content, step_task_id=tid)
            await s.commit()

    run(go())
    return tid


def _auto(tid):
    run(app.state.worker._maybe_auto_continue(tid))


def _children(tid):
    async def go():
        async with SessionLocal() as s:
            return (await s.execute(select(Task).where(Task.parent_task_id == tid))).scalars().all()

    return run(go())


def _events(tid, etype):
    async def go():
        async with SessionLocal() as s:
            return (await s.execute(select(TaskEvent).where(TaskEvent.task_id == tid, TaskEvent.type == etype)
                                    .order_by(TaskEvent.seq))).scalars().all()

    return run(go())


def test_fresh_band_and_open_todo_queue_one_fresh_continue(client, owner, submitted):
    tid = _step(owner)
    _auto(tid)
    kids = _children(tid)
    assert len(kids) == 1
    k = kids[0]
    assert (k.origin, k.prompt, k.status) == ("continue", CONTINUE_PROMPT, "queued")
    assert k.resume_session_id is None  # fresh, even though the owner could resume
    assert k.auto_continued is True and k.created_by == owner.user_id and k.root_id == tid
    assert submitted == [k.id]
    evs = _events(k.id, "auto_continue")
    assert len(evs) == 1 and evs[0].payload["text"] == AUTO_CONTINUE_LINE.format(n=152)
    ln = logfmt.line({"type": "auto_continue", "payload": evs[0].payload, "created_at": None})
    assert ln == "--:--:-- AUTO continued automatically: context reached 152k"
    # the thread now has an active step: a second call queues nothing
    _auto(tid)
    assert len(_children(tid)) == 1 and submitted == [k.id]


def test_no_fresh_band_none(client, owner, submitted):
    for bands in (None, (), (("budget80", BAND_TS), ("budget95", BAND_TS))):
        tid = _step(owner, bands=bands)
        _auto(tid)
        assert _children(tid) == []
    assert submitted == []


def test_failed_cancelled_agent_none(client, owner, submitted):
    for status in ("failed", "cancelled"):
        tid = _step(owner, status=status)
        _auto(tid)
        assert _children(tid) == []
    root = _step(owner)
    kid = _step(owner, root=root, parent=root, origin="agent")
    _auto(kid)
    assert _children(kid) == []
    assert submitted == []


def test_band_without_open_todo_or_newer_notes_none(client, owner, submitted):
    before = datetime.fromtimestamp(BAND_TS - 60, timezone.utc)
    for kw in ({"content": DONE}, {"content": None}, {"content": DONE, "notes_at": before},
               {"content": {"step": {"n": None, "m": None, "text": "the work", "source": "prompt"}}}):
        tid = _step(owner, **kw)
        _auto(tid)
        assert _children(tid) == [], kw
    assert submitted == []


def test_notes_changed_after_the_band_continue(client, owner, submitted):
    after = datetime.fromtimestamp(BAND_TS + 60, timezone.utc)
    tid = _step(owner, content=DONE, notes_at=after)
    _auto(tid)
    assert len(_children(tid)) == 1 and len(submitted) == 1


def test_handoff_of_another_step_does_not_count(client, owner, submitted):
    """A stale handoff (the finish snapshot failed) describes an earlier step: its todo does not count."""
    root = _step(owner, bands=None)
    tid = _step(owner, root=root, parent=root, content=None)
    _auto(tid)
    assert _children(tid) == []


def test_cap_three_in_a_row_and_a_user_followup_resets(client, owner, submitted):
    # U -> A1 -> A2 -> A3: the 4th completion (A3's) does not continue
    root = _step(owner)
    prev = root
    for _ in range(3):
        prev = _step(owner, root=root, parent=prev, origin="continue", auto=True)
    _auto(prev)
    assert _children(prev) == []
    # a user follow-up after A3 resets the count; then A1' continues again
    fu = _step(owner, root=root, parent=prev, origin="user")
    _auto(fu)
    assert len(_children(fu)) == 1
    # two in a row still continue (count 2 < 3)
    root = _step(owner)
    a1 = _step(owner, root=root, parent=root, origin="continue", auto=True)
    a2 = _step(owner, root=root, parent=a1, origin="continue", auto=True)
    _auto(a2)
    assert len(_children(a2)) == 1
    # a manual Continue in between is not automatic: it resets too
    root = _step(owner)
    a1 = _step(owner, root=root, parent=root, origin="continue", auto=True)
    a2 = _step(owner, root=root, parent=a1, origin="continue", auto=True)
    m = _step(owner, root=root, parent=a2, origin="continue")
    a3 = _step(owner, root=root, parent=m, origin="continue", auto=True)
    _auto(a3)
    assert len(_children(a3)) == 1
    # agent children are not main-line steps: they neither count nor reset
    root = _step(owner)
    prev = root
    for _ in range(3):
        prev = _step(owner, root=root, parent=prev, origin="continue", auto=True)
        _step(owner, root=root, parent=prev, origin="agent", bands=None, content=None)
    _auto(prev)
    assert _children(prev) and all(c.origin == "agent" for c in _children(prev))


def test_never_raises_into_finish(client, owner, monkeypatch, submitted):
    from app.routers import tasks as tasks_router

    async def boom(*a, **kw):
        raise RuntimeError("db gone")

    monkeypatch.setattr(tasks_router, "queue_continue", boom)
    tid = _step(owner)
    _auto(tid)  # logged, not raised
    _auto(uuid.uuid4().hex)  # a missing task
    assert submitted == []


def test_end_to_end_run_crosses_fresh_band_and_continues(client):
    r = client.post("/tasks", json={"prompt": "go [stream-fixture:fresh_todo]"})
    assert r.status_code == 201, r.text
    done = wait_task(client, r.json()["id"])
    assert done["status"] == "completed", done.get("error")
    deadline, kids = time.time() + 20, []
    while time.time() < deadline and not kids:
        kids = _children(done["id"])
        time.sleep(0.2)
    assert len(kids) == 1, "no automatic continue"
    k = kids[0]
    assert k.auto_continued and k.origin == "continue" and k.resume_session_id is None
    assert k.created_by == done["created_by"]
    finished = wait_task(client, k.id)
    assert finished["status"] in ("completed", "failed", "cancelled")
    assert _events(k.id, "auto_continue")[0].payload["text"] == AUTO_CONTINUE_LINE.format(n=152)
