"""Phase 3c Task 3: the ask routes, limits and the background run (mock CLI)."""

import asyncio
import time
import uuid

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import auth
from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models import RunAsk, Task, TaskEvent, TaskUsage
from conftest import login_as


def _run(coro):
    return asyncio.run(coro)


def _user(client, tag="ask"):
    c = login_as(f"{tag}-{uuid.uuid4().hex[:8]}@test.local")
    r = c.put("/me/credentials", json={"claude_token": "c" + uuid.uuid4().hex, "claude_kind": "api_key"})
    assert r.status_code == 200, r.text
    c.portal = client.portal  # background asks need the server's long-lived loop, not one per request
    return c


def _task(created_by, **kw):
    tid = uuid.uuid4().hex

    async def go():
        async with SessionLocal() as s:
            s.add(Task(id=tid, title="t", prompt="p", root_id=tid, status="completed", created_by=created_by, **kw))
            await s.commit()

    _run(go())
    return tid


def _wait(c, tid, status="answered", timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        rows = c.get(f"/tasks/{tid}/asks").json()
        if rows and rows[0]["status"] == status:
            return rows[0]
        time.sleep(0.1)
    raise AssertionError(c.get(f"/tasks/{tid}/asks").json())


def _count(tid):
    async def go():
        async with SessionLocal() as s:
            return (await s.execute(select(func.count()).select_from(RunAsk).where(RunAsk.task_id == tid))).scalar_one()

    return _run(go())


def test_ask_happy_path(client):
    u = _user(client)
    tid = _task(u.user_id)
    r = u.post(f"/tasks/{tid}/asks", json={"question": "why? [ask-probe:happy1]"})
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "pending" and r.json()["answer"] is None
    row = _wait(u, tid)
    assert row["answer"] == "answer for happy1" and row["cost_usd"] == 0.0123
    assert row["model"] == settings.ask_model and row["answered_at"]


def test_ask_private_to_asker(client):
    u = _user(client)
    tid = _task(u.user_id)
    assert u.post(f"/tasks/{tid}/asks", json={"question": "q"}).status_code == 202
    _wait(u, tid)
    assert client.get(f"/tasks/{tid}/asks").json() == []  # admin sees the task, not the ask


def test_ask_invisible_task_404(client):
    u = _user(client)
    tid = _task(u.user_id)
    other = _user(client)
    assert other.post(f"/tasks/{tid}/asks", json={"question": "q"}).status_code == 404
    assert other.get(f"/tasks/{tid}/asks").status_code == 404


def test_ask_agent_token_forbidden(client):
    u = _user(client)
    tid = _task(u.user_id)

    async def go():
        async with SessionLocal() as s:
            raw = await auth.mint_token(s, u.user_id, "agent-task", tid, 3600)
            await s.commit()
            return raw

    a = TestClient(app)
    a.headers["Authorization"] = f"Bearer {_run(go())}"
    assert a.post(f"/tasks/{tid}/asks", json={"question": "q"}).status_code in (401, 403)
    assert a.get(f"/tasks/{tid}/asks").status_code in (401, 403)


def test_ask_question_length_422(client):
    u = _user(client)
    tid = _task(u.user_id)
    for q in ("", "   ", "x" * 2001):
        assert u.post(f"/tasks/{tid}/asks", json={"question": q}).status_code == 422
    assert u.post(f"/tasks/{tid}/asks", json={"question": "x" * 2000}).status_code == 202


def test_ask_no_credential_422_message(client):
    r = client.put("/admin/org-fallback", json={"api_key": None})  # may be unsupported; fall back to delete
    if r.status_code >= 400:
        r = client.delete("/admin/org-fallback")
    try:
        c = login_as(f"nocred-{uuid.uuid4().hex[:8]}@test.local")
        tid = _task(c.user_id)
        r = c.post(f"/tasks/{tid}/asks", json={"question": "q"})
        assert r.status_code == 422
        assert r.json()["detail"] == "Add your Claude credential to ask about runs"
    finally:
        assert client.put("/admin/org-fallback", json={"api_key": "sk-test-fallback"}).status_code == 200


def test_ask_pending_conflict_409(client):
    u = _user(client)
    tid = _task(u.user_id)
    a = u.post(f"/tasks/{tid}/asks", json={"question": "one [ask-sleep]"})
    b = u.post(f"/tasks/{tid}/asks", json={"question": "two"})
    assert (a.status_code, b.status_code) == (202, 409)
    assert _count(tid) == 1


def test_ask_concurrent_files_one(client):
    u = _user(client)
    tid = _task(u.user_id)
    from concurrent.futures import ThreadPoolExecutor

    def post(_):
        return u.post(f"/tasks/{tid}/asks", json={"question": "q [ask-sleep]"})

    with ThreadPoolExecutor(3) as ex:
        rs = list(ex.map(post, range(3)))
    assert sorted(r.status_code for r in rs) == [202, 409, 409]
    assert _count(tid) == 1


def test_ask_hourly_limit_429(client, monkeypatch):
    monkeypatch.setattr(settings, "ask_hourly_limit", 2)
    u = _user(client)
    tid = _task(u.user_id)
    for _ in range(2):
        assert u.post(f"/tasks/{tid}/asks", json={"question": "q"}).status_code == 202
        _wait(u, tid)
    assert u.post(f"/tasks/{tid}/asks", json={"question": "q"}).status_code == 429


def test_ask_disabled_503(client, monkeypatch):
    monkeypatch.setattr(settings, "ask_enabled", False)
    u = _user(client)
    tid = _task(u.user_id)
    assert u.post(f"/tasks/{tid}/asks", json={"question": "q"}).status_code == 503


def test_ask_failure_stored(client):
    u = _user(client)
    tid = _task(u.user_id)
    assert u.post(f"/tasks/{tid}/asks", json={"question": "q [ask-fail]"}).status_code == 202
    row = _wait(u, tid, "failed")
    assert row["error"] == "no answer" and row["answer"] is None


def test_ask_never_touches_task(client):
    u = _user(client)
    tid = _task(u.user_id, total_cost_usd=1.5)

    async def seed():
        async with SessionLocal() as s:
            s.add(TaskEvent(task_id=tid, seq=1, type="text_output", payload={"text": "hello"}))
            s.add(TaskUsage(task_id=tid, root_id=tid, est_cost_usd=0.5))
            await s.commit()

    _run(seed())

    async def snap():
        async with SessionLocal() as s:
            t = await s.get(Task, tid)
            ev = (await s.execute(select(func.count()).select_from(TaskEvent).where(TaskEvent.task_id == tid))).scalar_one()
            us = await s.get(TaskUsage, tid)
            return t.total_cost_usd, t.status, ev, us.est_cost_usd

    before = _run(snap())
    assert u.post(f"/tasks/{tid}/asks", json={"question": "q [ask-probe:notouch]"}).status_code == 202
    _wait(u, tid)
    assert _run(snap()) == before == (1.5, "completed", 1, 0.5)


def test_ask_answer_redacted(client, monkeypatch):
    u = _user(client)
    tid = _task(u.user_id)
    from app.asks import service
    from app.asks.runner import AskResult

    async def fake(prompt, env):
        return AskResult(answer="the key is sk-ant-api03-" + "A" * 40 + "\nline two", cost_usd=0.01)

    monkeypatch.setattr(service, "run_ask", fake)
    assert u.post(f"/tasks/{tid}/asks", json={"question": "q"}).status_code == 202
    row = _wait(u, tid)
    assert "sk-ant-api03" not in row["answer"] and "\nline two" in row["answer"]


def test_second_pending_row_violates_unique_index(client):
    u = _user(client)
    tid = _task(u.user_id)

    async def add(status):
        async with SessionLocal() as s:
            s.add(RunAsk(task_id=tid, user_id=u.user_id, question="q", status=status))
            await s.commit()

    _run(add("pending"))
    with pytest.raises(IntegrityError):
        _run(add("pending"))
    _run(add("answered"))  # only pending rows are limited
    _run(add("answered"))
