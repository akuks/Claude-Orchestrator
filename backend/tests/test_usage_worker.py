"""Phase 3b Task 1: the worker captures per-message usage into a TaskUsage row and
writes usage.json for co-guard; the project's fresh_session_tokens setting."""

import asyncio
import json
import time
import uuid

import pytest

from app.database import SessionLocal
from app.models import Task, TaskUsage
from conftest import env_probe_key, read_probe_file, wait_task


def _usage(task_id):
    async def go():
        async with SessionLocal() as s:
            return await s.get(TaskUsage, task_id)

    return asyncio.run(go())


def _project(client, **patch):
    r = client.post("/projects", json={"name": f"usage-{uuid.uuid4().hex[:8]}"})
    assert r.status_code == 201, r.text
    p = r.json()
    if patch:
        r = client.patch(f"/projects/{p['id']}", json=patch)
        assert r.status_code == 200, r.text
        p = r.json()
    return p


def test_run_stores_usage_row_and_final_cost(client):
    key = env_probe_key()
    p = _project(client, fresh_session_tokens=150000)
    r = client.post("/tasks", json={"prompt": f"go [usage-probe:{key}]", "project_id": p["id"],
                                    "max_budget_usd": 5})
    assert r.status_code == 201, r.text
    done = wait_task(client, r.json()["id"])
    assert done["status"] == "completed", done.get("error")
    row = _usage(done["id"])
    assert row is not None
    assert row.root_id == done["id"]
    assert row.context_tokens == 20 + 200 + 6000
    assert row.peak_context == 6220
    assert row.est_cost_usd == pytest.approx(0.0421)  # the result's total_cost_usd at finish
    assert row.bands == []
    b = row.breakdown
    assert b["messages"] == 2  # u1 arrived twice: counted once, with its last usage
    assert b["main"] == {"messages": 2, "input": 30, "cache_write": 1200, "cache_read": 11000, "output": 47}
    assert b["models"]["claude-opus-5-5"]["output"] == 47
    assert b["largest_results"][0] == {"tool": "Read", "chars": 3000, "id": "tuU"}  # before _short's cut
    # usage.json, as the mock found it while the run was live
    data = json.loads(read_probe_file(key, "usage"))
    assert set(data) == {"context_tokens", "est_cost_usd", "max_budget_usd", "fresh_session_tokens", "ts"}
    assert data["max_budget_usd"] == 5 and data["fresh_session_tokens"] == 150000
    assert isinstance(data["context_tokens"], int) and data["context_tokens"] >= 6010
    assert isinstance(data["est_cost_usd"], float) and data["est_cost_usd"] > 0
    assert abs(time.time() - data["ts"]) < 60
    assert read_probe_file(key, "usagemode").strip() == "600"


def test_adhoc_run_has_no_fresh_threshold(client):
    key = env_probe_key()
    r = client.post("/tasks", json={"prompt": f"go [usage-probe:{key}]"})
    done = wait_task(client, r.json()["id"])
    assert done["status"] == "completed", done.get("error")
    data = json.loads(read_probe_file(key, "usage"))
    assert data["fresh_session_tokens"] is None and data["max_budget_usd"] == done["max_budget_usd"]


def test_run_without_usage_keeps_result_totals(client):
    """The plain mock run carries no per-message usage: the row still lands, with the result's cost."""
    r = client.post("/tasks", json={"prompt": "plain usage run"})
    done = wait_task(client, r.json()["id"])
    assert done["status"] == "completed"
    row = _usage(done["id"])
    assert row is not None and row.context_tokens is None and row.est_cost_usd == pytest.approx(0.01)
    assert done["input_tokens"] == 100 and done["output_tokens"] == 50


def test_persist_usage_throttled_while_running(monkeypatch):
    """At most one TaskUsage write per 5 s and one usage.json write per 2 s from the stream handler."""
    from app import worker as worker_mod
    from app.usage.tracker import UsageTracker

    w = worker_mod.WorkerManager()
    saved, written = [], []

    async def fake_persist(task_id, tracker, final=False, total_cost_usd=None):
        saved.append(final)

    monkeypatch.setattr(w, "_persist_usage", fake_persist)
    monkeypatch.setattr(worker_mod.usage_tracker, "write_usage_file", lambda tid, data: written.append(data))
    ctx = {"task_id": "T", "mcp_ids": {}, "secrets": [], "model_stored": True, "usage": UsageTracker(),
           "usage_meta": {"max_budget_usd": 2.0, "fresh_session_tokens": None},
           "usage_saved": None, "usage_written": None}

    async def emit(*a):
        pass

    ev = {"type": "assistant", "message": {"id": "m", "model": "claude-opus-5-5", "content": [],
                                           "usage": {"input_tokens": 1000}}}

    def step(saved_ago=None, written_ago=None):
        now = time.monotonic()
        if saved_ago is not None:
            ctx["usage_saved"] = now - saved_ago
        if written_ago is not None:
            ctx["usage_written"] = now - written_ago
        asyncio.run(w._handle_stream_event(ev, emit, {}, ctx))

    step()  # the first event writes both
    assert saved == [False] and len(written) == 1
    assert written[0]["context_tokens"] == 1000 and written[0]["max_budget_usd"] == 2.0
    step()
    assert saved == [False] and len(written) == 1
    step(saved_ago=4.8, written_ago=2.1)
    assert saved == [False] and len(written) == 2
    step(saved_ago=5.1, written_ago=1.0)
    assert saved == [False, False] and len(written) == 2


@pytest.mark.parametrize("value,code", [(49999, 422), (900001, 422), (50000, 200), (900000, 200), (None, 200)])
def test_project_fresh_session_tokens_range(client, value, code):
    p = _project(client)
    r = client.patch(f"/projects/{p['id']}", json={"fresh_session_tokens": value})
    assert r.status_code == code, r.text
    if code == 200:
        assert r.json()["fresh_session_tokens"] == value


def test_project_fresh_session_tokens_null_clears(client):
    p = _project(client, fresh_session_tokens=150000)
    assert p["fresh_session_tokens"] == 150000
    r = client.patch(f"/projects/{p['id']}", json={"fresh_session_tokens": None})
    assert r.status_code == 200 and r.json()["fresh_session_tokens"] is None
    assert client.get(f"/projects/{p['id']}").json()["fresh_session_tokens"] is None


def test_task_usage_defaults_and_thread_delete_removes_it(client):
    from app.models import TaskQuestion

    done = wait_task(client, client.post("/tasks", json={"prompt": "usage delete"}).json()["id"])
    assert _usage(done["id"]) is not None

    async def add_question_and_check():
        async with SessionLocal() as s:
            t = await s.get(Task, done["id"])
            assert t.auto_continued is False
            q = TaskQuestion(task_id=t.id, root_id=t.root_id, question="which?", source="agent")
            s.add(q)
            await s.commit()
            assert q.status == "open" and q.options == [] and q.answer is None and len(q.id) == 32

    asyncio.run(add_question_and_check())
    assert client.delete(f"/tasks/{done['id']}").status_code == 204

    async def leftovers():
        from sqlalchemy import select

        async with SessionLocal() as s:
            return ((await s.execute(select(TaskQuestion).where(TaskQuestion.task_id == done["id"]))).first(),
                    await s.get(TaskUsage, done["id"]))

    assert asyncio.run(leftovers()) == (None, None)
