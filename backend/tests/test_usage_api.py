"""Phase 3b Task 4: GET /tasks/{id}/usage: step figures, thread totals so far, tips."""

import asyncio
import uuid

import pytest

from app.database import SessionLocal
from app.models import Task, TaskUsage
from app.usage.report import TIP_CACHE, TIP_FRESH, TIP_NARROW
from conftest import login_as

B0 = {"messages": 0, "input": 0, "cache_write": 0, "cache_read": 0, "output": 0}


def _bd(main=None, results=(), messages=2):
    return {"main": {**B0, **(main or {})}, "helpers": {}, "models": {},
            "largest_results": list(results), "injected_chars": 0, "messages": messages}


def _mk(tid=None, root=None, usage=None, **kw):
    tid = tid or uuid.uuid4().hex

    async def go():
        async with SessionLocal() as s:
            s.add(Task(id=tid, title=kw.pop("title", "t"), prompt="p", root_id=root or tid,
                       status=kw.pop("status", "completed"), **kw))
            await s.commit()
            if usage is not None:
                await s.merge(TaskUsage(task_id=tid, root_id=root or tid, bands=[], **usage))
                await s.commit()

    asyncio.run(go())
    return tid


def _project(client, **patch):
    p = client.post("/projects", json={"name": f"uapi-{uuid.uuid4().hex[:8]}"}).json()
    if patch:
        p = client.patch(f"/projects/{p['id']}", json=patch).json()
    return p


def _get(client, tid):
    r = client.get(f"/tasks/{tid}/usage")
    assert r.status_code == 200, r.text
    return r.json()


def test_thread_totals_with_running_step_and_budget(client):
    root = _mk(title="one", total_cost_usd=1.0, duration_ms=60_000, input_tokens=100, output_tokens=50,
               usage={"est_cost_usd": 1.0, "peak_context": 50000, "context_tokens": 900, "breakdown": _bd()})
    _mk(root=root, title="two", total_cost_usd=0.5, duration_ms=30_000, input_tokens=10, output_tokens=5)
    run = _mk(root=root, title="three", status="running", max_budget_usd=2.0,
              usage={"est_cost_usd": 0.4, "peak_context": 5000, "context_tokens": 4000,
                     "breakdown": _bd({"input": 20, "output": 30, "cache_read": 999})})
    body = _get(client, run)
    step = body["step"]
    assert step["context_tokens"] == 4000 and step["peak_context"] == 5000
    assert step["est_cost_usd"] == pytest.approx(0.4) and step["cost_is_estimate"] is True
    assert step["max_budget_usd"] == 2.0 and step["budget_pct"] == 20.0
    assert step["fresh_session_tokens"] is None and step["breakdown"]["main"]["output"] == 30
    th = body["thread"]
    assert th["steps"] == 3 and th["duration_ms"] == 90_000
    assert th["cost_usd"] == pytest.approx(1.9) and th["tokens"] == 150 + 15 + 50
    assert th["last"] == {"title": "three", "duration_ms": 0, "cost_usd": pytest.approx(0.4)}
    assert body["tips"] == []


def test_finished_step_cost_flag(client):
    a = _mk(total_cost_usd=0.7, usage={"est_cost_usd": 0.7, "breakdown": _bd()})
    b = _mk(usage={"est_cost_usd": 0.3, "breakdown": _bd()})  # finished, the result gave no cost
    assert _get(client, a)["step"]["cost_is_estimate"] is False
    sb = _get(client, b)["step"]
    assert sb["cost_is_estimate"] is True and sb["est_cost_usd"] == pytest.approx(0.3)


def test_step_without_usage_row_gives_nulls(client):
    tid = _mk(title="bare")
    body = _get(client, tid)
    st = body["step"]
    assert st["context_tokens"] is None and st["peak_context"] is None and st["est_cost_usd"] is None
    assert st["breakdown"] is None and st["budget_pct"] is None
    assert body["thread"]["steps"] == 1 and body["thread"]["cost_usd"] == 0 and body["tips"] == []


def test_tips_trigger_and_not(client):
    big = _mk(usage={"peak_context": 10, "breakdown": _bd(results=[{"tool": "Read", "chars": 20_001, "id": "x"}])})
    edge = _mk(usage={"peak_context": 150_000, "breakdown": _bd(results=[{"tool": "Read", "chars": 20_000, "id": "x"}])})
    assert _get(client, big)["tips"] == [TIP_NARROW]
    assert _get(client, edge)["tips"] == []
    ctx = _mk(usage={"peak_context": 150_001, "breakdown": _bd()})
    assert _get(client, ctx)["tips"] == [TIP_FRESH]
    # the project's threshold replaces the 150 000 default
    p = _project(client, fresh_session_tokens=50000)
    low = _mk(project_id=p["id"], usage={"peak_context": 50001, "breakdown": _bd()})
    ok = _mk(project_id=p["id"], usage={"peak_context": 50000, "breakdown": _bd()})
    assert _get(client, low)["tips"] == [TIP_FRESH] and _get(client, ok)["tips"] == []
    poor = _mk(usage={"peak_context": 1, "breakdown": _bd({"input": 10, "cache_write": 50, "cache_read": 40}, messages=20)})
    assert _get(client, poor)["tips"] == [TIP_CACHE]
    good = _mk(usage={"peak_context": 1, "breakdown": _bd({"input": 10, "cache_write": 40, "cache_read": 50}, messages=20)})
    few = _mk(usage={"peak_context": 1, "breakdown": _bd({"input": 10, "cache_write": 50, "cache_read": 40}, messages=19)})
    assert _get(client, good)["tips"] == [] and _get(client, few)["tips"] == []
    allthree = _mk(usage={"peak_context": 200_000, "breakdown": _bd(
        {"input": 10, "cache_write": 50, "cache_read": 40}, [{"tool": "Bash", "chars": 30_000, "id": "y"}], 25)})
    assert _get(client, allthree)["tips"] == [TIP_NARROW, TIP_FRESH, TIP_CACHE]


def test_outsider_gets_404(client):
    tag = uuid.uuid4().hex[:8]
    p = _project(client)
    owner = login_as(f"ua-own-{tag}@test.local")
    outsider = login_as(f"ua-out-{tag}@test.local")
    r = client.put(f"/projects/{p['id']}/members", json=[
        {"subject_type": "user", "subject_id": owner.user_id, "role": "contributor"}])
    assert r.status_code == 200, r.text
    tid = _mk(project_id=p["id"], usage={"breakdown": _bd()})
    assert owner.get(f"/tasks/{tid}/usage").status_code == 200
    assert outsider.get(f"/tasks/{tid}/usage").status_code == 404


def test_finished_step_stored_cost_wins_over_estimate(client):
    tid = _mk(total_cost_usd=1.0, max_budget_usd=4.0, usage={"est_cost_usd": 0.6, "breakdown": _bd()})
    st = _get(client, tid)["step"]
    assert st["est_cost_usd"] == pytest.approx(1.0) and st["cost_is_estimate"] is False
    assert st["budget_pct"] == 25.0


def test_tips_come_from_the_asked_step_only(client):
    root = _mk(usage={"peak_context": 200_000, "breakdown": _bd(results=[{"tool": "Read", "chars": 30_000, "id": "z"}])})
    later = _mk(root=root, usage={"peak_context": 10, "breakdown": _bd()})
    assert _get(client, root)["tips"] == [TIP_NARROW, TIP_FRESH]
    assert _get(client, later)["tips"] == []


def test_awaiting_approval_counts_as_live(client):
    tid = _mk(status="awaiting_approval", usage={"est_cost_usd": 0.2, "breakdown": _bd({"input": 5, "output": 6})})
    body = _get(client, tid)
    assert body["step"]["cost_is_estimate"] is True and body["step"]["budget_pct"] is None
    assert body["thread"]["tokens"] == 11


def test_thread_fallbacks(client):
    root = _mk(usage={"est_cost_usd": 0.3, "breakdown": _bd()}, input_tokens=1, output_tokens=2)  # finished, no result cost
    _mk(root=root, status="running")  # running, no TaskUsage row
    body = _get(client, root)
    assert body["thread"]["cost_usd"] == pytest.approx(0.3) and body["thread"]["tokens"] == 3
    assert body["thread"]["steps"] == 2
