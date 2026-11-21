"""Phase 3a task 4: the agent's notes (PUT/GET /tasks/{id}/notes), the agent-token allowlist, the
policy keys and the notes command the hook tells the agent to run."""

import asyncio
import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import auth
from app.database import SessionLocal
from app.guard import co_hook, policy_inputs, rules
from app.main import app
from app.models import GuardOrgSettings, Task
from conftest import login_as


def _run(coro):
    return asyncio.run(coro)


def _task(created_by, status="running"):
    tid = uuid.uuid4().hex

    async def go():
        async with SessionLocal() as s:
            s.add(Task(id=tid, title=f"t-{tid[:6]}", prompt="p", root_id=tid, status=status, created_by=created_by))
            await s.commit()

    _run(go())
    return tid


def _agent(user_id, task_id):
    async def go():
        async with SessionLocal() as s:
            raw = await auth.mint_token(s, user_id, "agent-task", task_id, 3600)
            await s.commit()
            return raw

    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {_run(go())}"
    return c


def _row(tid):
    async def go():
        async with SessionLocal() as s:
            return await s.get(Task, tid)

    return _run(go())


@pytest.fixture
def me(client):
    return login_as(f"notes-{uuid.uuid4().hex[:8]}@test.local")


def test_agent_puts_own_notes(client, me):
    tid = _task(me.user_id)
    agent = _agent(me.user_id, tid)
    r = agent.put(f"/tasks/{tid}/notes", content=b"Task: x\nStep: 1 of 2: a\nNext: b", headers={"Content-Type": "text/plain"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["notes"] == "Task: x\nStep: 1 of 2: a\nNext: b" and out["notes_updated_at"]
    row = _row(tid)
    assert row.notes == out["notes"] and row.notes_updated_at is not None
    h = me.get(f"/tasks/{tid}/handoff")
    assert h.status_code == 200 and h.json()["reason"] == "notes"
    assert "Next: b" in h.json()["text"]
    # JSON bodies work too, and the person reads them
    r = agent.put(f"/tasks/{tid}/notes", json={"text": "Task: y"})
    assert r.status_code == 200 and me.get(f"/tasks/{tid}/notes").json()["notes"] == "Task: y"


def test_agent_token_other_task_403(client, me):
    mine, other = _task(me.user_id), _task(me.user_id)
    agent = _agent(me.user_id, mine)
    assert agent.put(f"/tasks/{other}/notes", json={"text": "x"}).status_code == 403
    assert _row(other).notes is None
    # the allowlist widened by this route only: the read side stays closed to the token
    assert agent.get(f"/tasks/{mine}/notes").status_code == 403
    assert agent.post(f"/tasks/{mine}/handoff/refresh").status_code == 403
    assert agent.put(f"/tasks/{mine}/notes", json={"text": "ok"}).status_code == 200


def test_notes_cleaned_redacted_capped(client, me):
    tid = _task(me.user_id)
    tok = "ghp_" + "a" * 36
    words = [f"w{i}" for i in range(300)]
    words[3] = tok
    words[4] = "zero​width"
    words[119] = "sk-ant-" + "b" * 40  # the last kept word
    text = "Task: " + " ".join(words)
    r = _agent(me.user_id, tid).put(f"/tasks/{tid}/notes", content=text.encode(), headers={"Content-Type": "text/plain"})
    assert r.status_code == 200
    notes = r.json()["notes"]
    assert len(notes.split()) <= 120 and len(notes) <= 1000
    assert tok not in notes and "sk-ant-" not in notes and "***" in notes and "​" not in notes
    assert "zerowidth" in notes


def test_notes_body_too_large_413(client, me):
    tid = _task(me.user_id)
    agent = _agent(me.user_id, tid)
    assert agent.put(f"/tasks/{tid}/notes", content=b"a" * 4097, headers={"Content-Type": "text/plain"}).status_code == 413
    assert agent.put(f"/tasks/{tid}/notes", content=b"a " * 2048, headers={"Content-Type": "text/plain"}).status_code == 200


def test_outsider_404_viewer_get(client, me):
    tid = _task(me.user_id)
    out = login_as(f"notes-out-{uuid.uuid4().hex[:8]}@test.local")
    assert out.get(f"/tasks/{tid}/notes").status_code == 404
    assert out.put(f"/tasks/{tid}/notes", json={"text": "x"}).status_code == 404
    assert _row(tid).notes is None
    assert me.put(f"/tasks/{tid}/notes", json={"text": "mine"}).status_code == 200  # a manager may write
    assert me.get(f"/tasks/{tid}/notes").json()["notes"] == "mine"
    assert client.get(f"/tasks/{tid}/notes").status_code == 200  # admin


def test_notes_command_is_allowed_by_rules():
    cmd = co_hook.NOTES_LINE.split("Save them with: ", 1)[1].replace("<your notes>", "Task: x\nNext: y")
    policy = {"project_dir": "/work/proj", "home": "/home/someone", "cwd": "/work/proj"}
    assert rules.evaluate("Bash", {"command": cmd}, policy, "/work/proj").decision == "allow"


def test_policy_notes_from_org_setting(client):
    async def gather(ground):
        async with SessionLocal() as s:
            row = await s.get(GuardOrgSettings, 1)
            if row is None:
                row = GuardOrgSettings(id=1)
                s.add(row)
            row.ground_rules = ground
            await s.commit()
        async with SessionLocal() as s:
            task = Task(id="x", title="t", prompt="p", root_id="x")
            return (await policy_inputs.gather(s, task=task, project=None, owner_id=None, mcp=None)).policy

    try:
        assert _run(gather({}))["notes"] is True
        got = _run(gather({"notes": False}))
        assert got["notes"] is False and got["notes_after"] == 12
        assert _run(gather({"notes": True}))["notes"] is True
    finally:
        _run(gather({}))
