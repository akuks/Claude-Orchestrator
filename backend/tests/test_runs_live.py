"""GET /workflows/runs/live: active runs + coordinator threads for the Home scene."""

import asyncio
import uuid

from app.database import SessionLocal
from app.models import Agent, Task, WorkflowRun
from conftest import login_as


def _uid():
    return uuid.uuid4().hex[:8]


def _project(client):
    r = client.post("/projects", json={"name": f"live-{_uid()}"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _add(*rows):
    async def go():
        async with SessionLocal() as s:
            s.add_all(rows)
            await s.commit()

    asyncio.run(go())


def _run(pid, status="running", current=1, n_stages=2):
    run = WorkflowRun(
        workflow_id=uuid.uuid4().hex, name=f"live-run-{_uid()}", status=status,
        current_stage=current, project_ids=[pid],
        stages=[{"name": f"S{i}", "items": []} for i in range(n_stages)],
    )
    _add(run)
    return run.id


def _stage_task(run_id, stage, status, pid):
    _add(Task(
        title=f"t-{_uid()}", prompt="p", project_id=pid, status=status,
        workflow_run_id=run_id, stage_index=stage,
    ))


def _live(c):
    r = c.get("/workflows/runs/live")
    assert r.status_code == 200, r.text
    return r.json()


def _find(items, id_):
    return next((i for i in items if i["id"] == id_), None)


def test_live_includes_active_run_with_stage_states(client):
    pid = _project(client)
    rid = _run(pid)
    _stage_task(rid, 0, "completed", pid)
    _stage_task(rid, 0, "completed", pid)
    _stage_task(rid, 1, "running", pid)
    item = _find(_live(client), rid)
    assert item["kind"] == "run" and item["project_ids"] == [pid]
    assert item["status"] == "running" and item["created_at"]
    assert [s["state"] for s in item["stages"]] == ["done", "current"]
    assert [s["task_count"] for s in item["stages"]] == [2, 1]
    assert [s["name"] for s in item["stages"]] == ["S0", "S1"]


def test_live_excludes_finished_runs(client):
    pid = _project(client)
    ids = [_run(pid, status=s) for s in ("completed", "failed")]
    live = _live(client)
    assert all(_find(live, i) is None for i in ids)


def test_live_scoped_for_outsider(client):
    pid = _project(client)
    rid = _run(pid)
    _stage_task(rid, 1, "running", pid)
    agent = client.post("/agents", json={
        "name": f"live-co-{_uid()}", "system_prompt": "x", "coordinator": True,
    }).json()
    root = Task(title="secret-thread", prompt="p", project_id=pid, status="running", agent_id=agent["id"])
    root.root_id = root.id
    _add(root)
    out = login_as(f"live-out-{_uid()}@test.local")
    items = _live(out)
    assert _find(items, rid) is None and _find(items, root.id) is None
    assert pid not in {p for i in items for p in i["project_ids"]}
    # The admin does see both.
    admin_items = _live(client)
    assert _find(admin_items, rid) and _find(admin_items, root.id)


def test_live_thread_from_coordinator_children(client):
    pid = _project(client)
    agent = client.post("/agents", json={
        "name": f"live-co-{_uid()}", "system_prompt": "x", "coordinator": True,
    }).json()
    plain = client.post("/agents", json={"name": f"live-pl-{_uid()}", "system_prompt": "x"}).json()
    root = Task(title="lead", prompt="p", project_id=pid, status="running", agent_id=agent["id"])
    root.root_id = root.id
    plain_root = Task(title="plain", prompt="p", project_id=pid, status="running", agent_id=plain["id"])
    plain_root.root_id = plain_root.id
    _add(root, plain_root)
    for title, st in [("c1", "completed"), ("c2", "running"), ("c3", "failed")]:
        _add(Task(title=title, prompt="p", project_id=pid, status=st,
                  parent_task_id=root.id, root_id=root.id))
    live = _live(client)
    item = _find(live, root.id)
    assert item["kind"] == "thread" and item["title"] == "lead"
    assert item["project_ids"] == [pid]
    assert [(s["name"], s["state"], s["task_count"]) for s in item["stages"]] == [
        ("c1", "done", 1), ("c2", "current", 1), ("c3", "failed", 1),
    ]
    assert _find(live, plain_root.id) is None


def test_live_waiting_approval_state(client):
    pid = _project(client)
    rid = _run(pid, current=0, n_stages=3)
    _stage_task(rid, 0, "awaiting_approval", pid)
    _stage_task(rid, 1, "queued", pid)
    item = _find(_live(client), rid)
    assert [s["state"] for s in item["stages"]] == ["waiting_approval", "next", "next"]


def test_stage_state_earlier_stage_without_tasks_is_done():
    from app.routers.workflows import _stage_state

    assert _stage_state([], 0, 2) == "done"
    assert _stage_state([], 2, 2) == "current"
    assert _stage_state([], 3, 2) == "next"
    # Visible tasks still decide when there are some.
    assert _stage_state(["failed"], 0, 2) == "failed"


def test_live_earlier_stage_with_no_visible_tasks_is_done(client):
    pid = _project(client)
    rid = _run(pid, current=2, n_stages=4)
    _stage_task(rid, 1, "completed", pid)
    _stage_task(rid, 2, "running", pid)
    item = _find(_live(client), rid)
    assert [s["state"] for s in item["stages"]] == ["done", "done", "current", "next"]


def test_live_caps_runs_and_batches_task_queries(client, monkeypatch):
    from sqlalchemy import event

    from app.routers import workflows

    monkeypatch.setattr(workflows, "LIVE_MAX", 3)
    pid = _project(client)
    rids = []
    for _ in range(6):
        rid = _run(pid)
        _stage_task(rid, 0, "completed", pid)
        _stage_task(rid, 1, "running", pid)
        rids.append(rid)

    # Count only the endpoint's own queries: the sessions it opens. An
    # engine-wide listener also saw the worker / scheduler loops querying
    # tasks at the same time (flaky on Postgres, where a re-used database
    # has leftover running tasks the worker re-queues at startup).
    seen: list[str] = []
    real_factory = workflows.SessionLocal

    def counted_session(*a, **k):
        s = real_factory(*a, **k)

        def count(state):
            text = str(state.statement)
            if "FROM tasks" in text:
                seen.append(text)

        event.listen(s.sync_session, "do_orm_execute", count)
        return s

    monkeypatch.setattr(workflows, "SessionLocal", counted_session)
    items = _live(client)
    monkeypatch.setattr(workflows, "SessionLocal", real_factory)

    assert len(items) <= 3
    # Newest first: the three most recent runs make the cut, older ones don't.
    assert [i["id"] for i in items if i["id"] in rids] == rids[::-1][:3]
    for i in items:
        if i["id"] in rids:
            assert [s["state"] for s in i["stages"]] == ["done", "current"]
    # Stage tasks for all runs in one query, thread roots in one, their
    # children in one — not one query per run / per thread.
    assert 1 <= len(seen) <= 3, seen  # (the counter does see the endpoint)
