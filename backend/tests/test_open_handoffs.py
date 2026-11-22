"""Phase 3b task 7: ``GET /handoffs/open`` (Home "Open handoffs"). Threads the caller started or may manage
whose latest main-line step ended in the last 14 days, is not running, and whose handoff is open with an open
todo (the same helper as the automatic Continue) or a non-completed status; at most 10, newest first. People
only: an agent token never gets past the allowlist."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import auth
from app.main import app
from app.database import SessionLocal
from app.models import Task, TaskHandoff
from app.resume import handoff
from conftest import login_as


def _run(coro):
    return asyncio.run(coro)


def _now():
    return datetime.now(timezone.utc)


def _thread(owner, *, status="failed", ended_days=1.0, handoff_status="open", content=None, steps=1,
            active_step=False, origin_last="user", title=None, project_id=None):
    """A thread of ``steps`` finished steps (the last one ``status``) with a stored handoff; returns
    (root_id, last_step_id)."""
    root = uuid.uuid4().hex
    ids = [root] + [uuid.uuid4().hex for _ in range(steps - 1)]
    ended = _now() - timedelta(days=ended_days)

    async def go():
        async with SessionLocal() as s:
            for i, tid in enumerate(ids):
                last = i == len(ids) - 1
                s.add(Task(id=tid, title=(title or f"oh-{root[:6]}") if i == 0 else f"step-{i}", prompt="p",
                           root_id=root, status=status if last else "completed", created_by=owner,
                           project_id=project_id, origin=origin_last if last and i else "user",
                           created_at=ended - timedelta(minutes=10 * (len(ids) - i)),
                           completed_at=ended if last else ended - timedelta(minutes=5)))
            if active_step:
                s.add(Task(id=uuid.uuid4().hex, title="live", prompt="p", root_id=root, status="running",
                           created_by=owner, project_id=project_id, created_at=ended + timedelta(minutes=1)))
            c = content if content is not None else {"step_task_id": ids[-1], "step": {"source": "prompt"}}
            s.add(TaskHandoff(root_id=root, task_id=ids[-1], status=handoff_status, saved_at=ended,
                              content=c, text="t"))
            await s.commit()

    _run(go())
    return root, ids[-1]


def _todo(step_id, n=1, m=3):
    return {"step_task_id": step_id, "step": {"source": "notes", "n": n, "m": m, "text": "the middle bit"}}


def _roots(c):
    r = c.get("/handoffs/open")
    assert r.status_code == 200, r.text
    return [row["root_id"] for row in r.json()]


def test_open_todo_helper_is_the_worker_one():
    from app.worker import WorkerManager

    assert WorkerManager._open_todo is handoff.open_todo
    assert handoff.open_todo(_todo("a"), "a") is True
    assert handoff.open_todo(_todo("a", n=3, m=3), "a") is False
    assert handoff.open_todo(_todo("a"), "b") is False  # describes another step


def test_rule(client):
    me = login_as(f"oh-rule-{uuid.uuid4().hex[:6]}@test.local")
    failed, _ = _thread(me.user_id, status="failed")
    cancelled, _ = _thread(me.user_id, status="cancelled", ended_days=2)
    # completed, its handoff has an open todo for the latest step
    root_todo, last = _thread(me.user_id, status="completed", steps=2, ended_days=3)

    async def todo():
        async with SessionLocal() as s:
            from sqlalchemy import select

            row = (await s.execute(select(TaskHandoff).where(TaskHandoff.root_id == root_todo))).scalar_one()
            row.content = _todo(last)
            await s.commit()

    _run(todo())
    done_clean, _ = _thread(me.user_id, status="completed")  # completed, nothing open
    closed, _ = _thread(me.user_id, status="failed", handoff_status="closed")
    old, _ = _thread(me.user_id, status="failed", ended_days=15)
    busy, _ = _thread(me.user_id, status="failed", active_step=True)

    got = _roots(me)
    assert got == [failed, cancelled, root_todo]  # newest first
    for gone in (done_clean, closed, old, busy):
        assert gone not in got

    rows = {r["root_id"]: r for r in me.get("/handoffs/open").json()}
    row = rows[root_todo]
    assert row["task"]["id"] == last and row["task"]["can_manage"] is True
    assert row["open_todo"] is True and row["step"]["n"] == 1 and row["step"]["m"] == 3
    assert row["title"] == f"oh-{root_todo[:6]}"
    assert rows[failed]["open_todo"] is False and rows[failed]["ended_at"]


def test_latest_step_is_the_main_line_one(client):
    """A dispatched child finishing later does not stand for the thread (the Continue target is main-line)."""
    me = login_as(f"oh-agent-{uuid.uuid4().hex[:6]}@test.local")
    root, _ = _thread(me.user_id, status="failed", steps=2, origin_last="agent")
    # the failed step is a dispatched child; the main-line root completed with nothing open

    assert root not in _roots(me)


def test_visibility_and_manage(client):
    owner = login_as(f"oh-own-{uuid.uuid4().hex[:6]}@test.local")
    other = login_as(f"oh-oth-{uuid.uuid4().hex[:6]}@test.local")
    mine, _ = _thread(owner.user_id)
    assert mine in _roots(owner)
    assert mine not in _roots(other)  # an ad-hoc thread of someone else: not visible

    # a project thread: a contributor can see it but not manage it (someone else started it), so it is not
    # listed for them; a maintainer of the project may manage it, so it is. (Fresh users, not the admin: the
    # admin's list is the whole suite's newest 10.)
    boss = login_as(f"oh-boss-{uuid.uuid4().hex[:6]}@test.local")
    pid = client.post("/projects", json={"name": f"oh-p-{uuid.uuid4().hex[:6]}"}).json()["id"]
    keep = [{"subject_type": m["subject_type"], "subject_id": m["subject_id"], "role": m["role"]}
            for m in client.get(f"/projects/{pid}/members").json()]
    r = client.put(f"/projects/{pid}/members", json=keep + [
        {"subject_type": "user", "subject_id": other.user_id, "role": "contributor"},
        {"subject_type": "user", "subject_id": boss.user_id, "role": "maintainer"}])
    assert r.status_code == 200, r.text
    admin_id = client.get("/auth/me").json()["id"]
    proj_thread, _ = _thread(admin_id, project_id=pid)
    assert other.get(f"/tasks/{proj_thread}").status_code == 200  # visible...
    assert proj_thread not in _roots(other)  # ...but not managed
    assert proj_thread in _roots(boss)
    assert mine not in _roots(boss)


def test_at_most_ten_newest_first(client):
    me = login_as(f"oh-ten-{uuid.uuid4().hex[:6]}@test.local")
    made = [_thread(me.user_id, ended_days=0.1 * (i + 1))[0] for i in range(12)]
    assert _roots(me) == made[:10]


def test_agent_token_is_refused(client):
    me = login_as(f"oh-tok-{uuid.uuid4().hex[:6]}@test.local")
    _, last = _thread(me.user_id, status="running")

    async def mint():
        async with SessionLocal() as s:
            raw = await auth.mint_token(s, me.user_id, "agent-task", last, 3600)
            await s.commit()
            return raw

    t = TestClient(app)
    t.headers["Authorization"] = f"Bearer {_run(mint())}"
    r = t.get("/handoffs/open")
    assert r.status_code == 403


def test_open_handoffs_pages_past_scan_max(client, monkeypatch):
    from app.routers import handoffs as h
    monkeypatch.setattr(h, "SCAN_MAX", 3)
    me = login_as(f"oh-pg-{uuid.uuid4().hex[:6]}@test.local")
    pid = client.post("/projects", json={"name": f"oh-pg-{uuid.uuid4().hex[:6]}"}).json()["id"]
    keep = [{"subject_type": m["subject_type"], "subject_id": m["subject_id"], "role": m["role"]}
            for m in client.get(f"/projects/{pid}/members").json()]
    assert client.put(f"/projects/{pid}/members", json=keep + [
        {"subject_type": "user", "subject_id": me.user_id, "role": "contributor"}]).status_code == 200
    admin_id = client.get("/auth/me").json()["id"]
    mine, _ = _thread(me.user_id, ended_days=2.0)
    for i in range(4):  # newer, visible to me but not mine to manage: they fill the first page
        _thread(admin_id, ended_days=0.01 * (i + 1), project_id=pid)
    assert mine in _roots(me)
