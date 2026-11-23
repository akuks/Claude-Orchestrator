"""Phase 4b task 5: lessons suggested when a thread's handoff is closed."""

import asyncio
import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app import memory
from app.database import SessionLocal
from app.decisions import after_run
from app.models import DecisionProposal, Project, Task, TaskHandoff


def _run(coro):
    return asyncio.run(coro)


def _thread(trust="own", handoff="Where it stands: X", steps=("first step", "second step")):
    pid, root = uuid.uuid4().hex, uuid.uuid4().hex

    async def go():
        async with SessionLocal() as s:
            s.add(Project(id=pid, name="p" + pid[:8], slug="p" + pid[:8], directory="/x", trust=trust))
            s.add(Task(id=root, prompt="p", title=steps[0], project_id=pid, root_id=root,
                       created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)))
            for i, t in enumerate(steps[1:], 2):
                s.add(Task(id=uuid.uuid4().hex, prompt="p", title=t, project_id=pid, root_id=root,
                           created_at=datetime(2026, 1, i, tzinfo=timezone.utc)))
            s.add(TaskHandoff(root_id=root, text=handoff, status="closed"))
            await s.commit()

    _run(go())
    return pid, root


def _rows(pid):
    async def go():
        async with SessionLocal() as s:
            return (await s.execute(select(DecisionProposal).where(DecisionProposal.project_id == pid)
                                    .order_by(DecisionProposal.dedupe_key))).scalars().all()

    return _run(go())


@pytest.fixture()
def model(monkeypatch):
    state = {"calls": [], "uids": [],
             "reply": json.dumps([{"title": f"Lesson {i}", "why": "w", "touches": ["src/**"], "kind": "rule"}
                                  for i in range(4)])}

    async def env(s, uid, *, project_env=None, purpose):
        state["uids"].append(uid)
        return {"A": "b"}

    async def run(prompt, m, cwd, e):
        state["calls"].append(prompt)
        return state["reply"]

    monkeypatch.setattr(after_run, "agent_env_or_none", env)
    monkeypatch.setattr(memory, "_run_claude", run)
    return state


def test_lessons_stored(client, model):
    pid, root = _thread(handoff="H" * 5000)
    _run(after_run.lessons_after_close(root, "closer1"))
    rows = _rows(pid)
    assert len(rows) == 3
    assert {r.kind for r in rows} == {"lesson"} and {r.source for r in rows} == {"lesson"}
    assert [r.dedupe_key for r in rows] == [f"lesson:{root}:{i}" for i in (1, 2, 3)]
    assert all(r.touches == [] and r.task_id == root and r.evidence == f"thread {root}" for r in rows)
    assert model["uids"] == ["closer1"]
    prompt = model["calls"][0]
    assert after_run.LESSONS_FRAME in prompt and "H" * 4000 in prompt and "H" * 4001 not in prompt
    assert prompt.index("first step") < prompt.index("second step")


def test_lessons_once_per_thread(client, model):
    pid, root = _thread()
    _run(after_run.lessons_after_close(root, "c"))
    _run(after_run.lessons_after_close(root, "c"))
    assert len(model["calls"]) == 1 and len(_rows(pid)) == 3


def test_lessons_conditions(client, model, monkeypatch):
    pid, root = _thread(trust="external")
    _run(after_run.lessons_after_close(root, "c"))
    assert model["calls"] == [] and _rows(pid) == []
    pid, root = _thread()

    async def off(s):
        return {"ground_rules": {"decisions": False}}

    monkeypatch.setattr(after_run.policy_store, "org_settings", off)
    _run(after_run.lessons_after_close(root, "c"))
    assert model["calls"] == [] and _rows(pid) == []


def test_lessons_never_raises(client, model):
    pid, root = _thread()

    async def boom(*a, **k):
        raise RuntimeError("x")

    import app.memory as m
    orig = m._run_claude
    m._run_claude = boom
    try:
        _run(after_run.lessons_after_close(root, "c"))
        _run(after_run.lessons_after_close(uuid.uuid4().hex, "c"))
    finally:
        m._run_claude = orig
    assert _rows(pid) == []


def test_close_route_starts_lessons(client, monkeypatch):
    calls = []

    async def rec(root_id, closer_id):
        calls.append((root_id, closer_id))

    monkeypatch.setattr(after_run, "lessons_after_close", rec)
    me = client.get("/auth/me").json()["id"]
    tid = uuid.uuid4().hex

    async def go():
        async with SessionLocal() as s:
            s.add(Task(id=tid, prompt="p", title="t", root_id=tid, created_by=me, status="completed"))
            s.add(TaskHandoff(root_id=tid, text="h"))
            await s.commit()

    _run(go())
    assert client.post(f"/tasks/{tid}/handoff/close").status_code == 200
    import time
    end = time.time() + 3
    while not calls and time.time() < end:
        time.sleep(0.05)
    assert calls == [(tid, me)]


def test_close_already_closed_does_not_start_lessons(client, monkeypatch):
    calls = []

    async def rec(root_id, closer_id):
        calls.append((root_id, closer_id))

    monkeypatch.setattr(after_run, "lessons_after_close", rec)
    me = client.get("/auth/me").json()["id"]
    tid = uuid.uuid4().hex

    async def go():
        async with SessionLocal() as s:
            s.add(Task(id=tid, prompt="p", title="t", root_id=tid, created_by=me, status="completed"))
            s.add(TaskHandoff(root_id=tid, text="h", status="closed"))
            await s.commit()

    _run(go())
    assert client.post(f"/tasks/{tid}/handoff/close").status_code == 200
    import time
    time.sleep(0.5)
    assert calls == []
