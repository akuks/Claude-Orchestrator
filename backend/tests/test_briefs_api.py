"""Phase 4c task 3: briefs through the API - create, steps inherit, run/dispatch, nesting - and read_only /
system_prompt inheritance by thread steps."""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.constants import Status
from app.database import SessionLocal
from app.guard import grants
from app.main import app
from app.models import Agent, Task, TaskQuestion, WorkflowRun

BRIEF = {"goal": "Add the export", "can_change": ["src/**"], "cannot_change": ["migrations"],
         "done": "tests pass", "stop": "when blocked"}


def _tag():
    return uuid.uuid4().hex[:8]


def _run(coro):
    return asyncio.run(coro)


def _agent(client, **kw):
    r = client.post("/agents", json={"name": f"b-{_tag()}", "system_prompt": "sys", "default_prompt": "go", **kw})
    assert r.status_code == 201, r.text
    return r.json()


def _row(tid):
    async def go():
        async with SessionLocal() as s:
            return await s.get(Task, tid)

    return _run(go())


def _finish(tid, **fields):
    async def go():
        async with SessionLocal() as s:
            t = await s.get(Task, tid)
            t.status = Status.COMPLETED
            t.session_id = t.session_id or "sess-b"
            for k, v in fields.items():
                setattr(t, k, v)
            await s.commit()

    _run(go())


@pytest.fixture
def admin_id(client):
    return client.get("/auth/me").json()["id"]


@pytest.fixture
def submitted(monkeypatch):
    got = []

    async def submit(task_id, priority, created_at):
        got.append(task_id)

    monkeypatch.setattr(app.state.worker, "submit", submit)
    yield got
    async def cancel():
        async with SessionLocal() as s:
            for tid in got:
                t = await s.get(Task, tid)
                if t is not None and t.status in Status.ACTIVE:
                    t.status = Status.CANCELLED
            await s.commit()

    _run(cancel())


def test_create_task_with_brief(client, submitted):
    r = client.post("/tasks", json={"prompt": "do it", "brief": {**BRIEF, "can_change": ["./src//**"]}})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["brief"]["can_change"] == ["src/**"] and out["brief"]["goal"] == "Add the export"
    assert out["brief_bounds"] == []
    plain = client.post("/tasks", json={"prompt": "no brief"}).json()
    assert plain["brief"] is None and plain["brief_bounds"] == []
    nul = client.post("/tasks", json={"prompt": "null brief", "brief": None}).json()
    assert nul["brief"] is None


def test_create_task_bad_brief_422(client, submitted):
    r = client.post("/tasks", json={"prompt": "x", "brief": {**BRIEF, "stop": "never"}})
    assert r.status_code == 422 and r.json()["detail"] == 'stop must say when to stop, not "never"'
    r = client.post("/tasks", json={"prompt": "x", "brief": {}})
    assert r.status_code == 422 and r.json()["detail"] == "the brief needs goal"
    r = client.post("/tasks", json={"prompt": "x", "brief": "text"})
    assert r.status_code == 422 and r.json()["detail"] == "the brief must be an object"


def _briefed_parent(client, bounds=None):
    out = client.post("/tasks", json={"prompt": "parent", "brief": BRIEF}).json()
    _finish(out["id"], brief_bounds=bounds or [{"task_id": "G", "can_change": ["src"], "cannot_change": []}])
    return out["id"]


def test_steps_inherit_brief(client, admin_id, submitted):
    tid = _briefed_parent(client)
    want = _row(tid)
    assert want.brief and want.brief_bounds

    def same(child):
        assert child["brief"] == want.brief and child["brief_bounds"] == want.brief_bounds, child

    r = client.post(f"/tasks/{tid}/followup", json={"prompt": "more"})
    assert r.status_code == 201, r.text
    same(r.json())
    _finish(r.json()["id"])
    r = client.post(f"/tasks/{tid}/continue", json={"mode": "fresh"})
    assert r.status_code == 201, r.text
    same(r.json())
    _finish(r.json()["id"])
    r = client.post(f"/tasks/{tid}/duplicate")
    assert r.status_code == 201, r.text
    same(r.json())

    # answer follow-up
    async def mkq():
        async with SessionLocal() as s:
            q = TaskQuestion(task_id=tid, root_id=want.root_id or tid, question="Which database?",
                             options=[], source="agent", status="open")
            s.add(q)
            await s.commit()
            return q.id

    qid = _run(mkq())
    r = client.post(f"/questions/{qid}/answer", json={"answer": "SQLite"})
    assert r.status_code == 201, r.text
    same(r.json()["task"])

    # grants._spawn called directly
    async def spawn():
        async with SessionLocal() as s:
            root = await s.get(Task, tid)
            child = await grants._spawn(s, [_Appr()], root, None)
            await s.rollback()
            return child.brief, child.brief_bounds

    brief, bounds = _run(spawn())
    assert brief == want.brief and bounds == want.brief_bounds


class _Appr:
    id = "appr1"
    decided_by = None
    tool = "Bash"
    summary = "x"

    def __getattr__(self, name):
        return None


def test_agent_run_and_dispatch_brief(client, admin_id, submitted):
    lead = _agent(client, coordinator=True)
    worker = _agent(client)
    own = {**BRIEF, "can_change": ["src/a/**"], "goal": "narrower"}
    parent = client.post(f"/agents/{lead['id']}/run", json={"brief": BRIEF})
    assert parent.status_code == 201, parent.text
    parent = parent.json()
    assert parent["brief"]["goal"] == "Add the export" and parent["brief_bounds"] == []
    kids = client.post("/agents/dispatch", json={"parent_task_id": parent["id"], "items": [
        {"agent": worker["id"], "prompt": "plain"},
        {"agent": worker["id"], "prompt": "own", "brief": own}]})
    assert kids.status_code == 201, kids.text
    plain, mine = kids.json()
    assert plain["brief"] == parent["brief"] and plain["brief_bounds"] == []
    assert mine["brief"]["goal"] == "narrower" and mine["brief"]["can_change"] == ["src/a/**"]
    assert mine["brief_bounds"] == [{"task_id": parent["id"], "can_change": ["src/**"],
                                     "cannot_change": ["migrations"]}]
    bad = client.post("/agents/dispatch", json={"parent_task_id": parent["id"], "items": [
        {"agent": worker["id"], "prompt": "x", "brief": {"goal": "g"}}]})
    assert bad.status_code == 422 and bad.json()["detail"] == "the brief needs can_change"

    # an agent token dispatches under its own task the same way
    async def mint():
        async with SessionLocal() as s:
            raw = await auth.mint_token(s, admin_id, "agent-task", parent["id"], 3600)
            await s.commit()
            return raw

    tok = TestClient(app)
    tok.headers["Authorization"] = f"Bearer {_run(mint())}"
    tok.headers["X-Requested-With"] = "co"
    r = tok.post("/agents/dispatch", json={"parent_task_id": parent["id"], "items": [
        {"agent": worker["id"], "prompt": "tok", "brief": own}]})
    assert r.status_code == 201, r.text
    assert len(r.json()[0]["brief_bounds"]) == 1


def test_nesting_limit(client, admin_id, submitted):
    lead = _agent(client, coordinator=True)
    worker = _agent(client)

    def brief(n):
        return {**BRIEF, "can_change": [f"src/l{n}/**"]}

    cur = client.post(f"/agents/{lead['id']}/run", json={"brief": brief(1)}).json()
    for level in range(2, 6):
        r = client.post("/agents/dispatch", json={"parent_task_id": cur["id"], "items": [
            {"agent": worker["id"], "prompt": "x", "brief": brief(level)}]})
        assert r.status_code == 201, (level, r.text)
        cur = r.json()[0]
    assert len(cur["brief_bounds"]) == 4
    r = client.post("/agents/dispatch", json={"parent_task_id": cur["id"], "items": [
        {"agent": worker["id"], "prompt": "x", "brief": brief(6)}]})
    assert r.status_code == 422 and r.json()["detail"] == "briefs nest at most 5 deep"


def test_followup_of_read_only_step_is_read_only(client, submitted):
    a = _agent(client, read_only=True)
    t = client.post(f"/agents/{a['id']}/run", json={}).json()
    assert t["read_only"] is True
    _finish(t["id"])
    f = client.post(f"/tasks/{t['id']}/followup", json={"prompt": "more"})
    assert f.status_code == 201, f.text
    assert f.json()["read_only"] is True
    assert _row(f.json()["id"]).system_prompt == "sys"
    _finish(f.json()["id"])
    c = client.post(f"/tasks/{t['id']}/continue", json={"mode": "fresh"})
    assert c.json()["read_only"] is True
    d = client.post(f"/tasks/{t['id']}/duplicate")
    assert d.json()["read_only"] is True

    async def spawn():
        async with SessionLocal() as s:
            child = await grants._spawn(s, [_Appr()], await s.get(Task, t["id"]), None)
            await s.rollback()
            return child.read_only

    assert _run(spawn()) is True
    # a writable step stays writable
    w = client.post("/tasks", json={"prompt": "w"}).json()
    _finish(w["id"])
    assert client.post(f"/tasks/{w['id']}/followup", json={"prompt": "m"}).json()["read_only"] is False


def test_workflow_step_from_read_only_agent_is_read_only(client, submitted):
    a = _agent(client, read_only=True)
    wf = client.post("/workflows", json={"name": f"wf-{_tag()}", "stages": [
        {"name": "s0", "items": [{"agent": a["name"], "prompt": "a"}]}]}).json()

    async def scenario():
        async with SessionLocal() as s:
            run = WorkflowRun(workflow_id=wf["id"], name=wf["name"], input="i",
                              stages=wf["stages"], status="pending", current_stage=0)
            s.add(run)
            await s.commit()
            run_id = run.id
        await app.state.workflow_runner.start_run(run_id)
        async with SessionLocal() as s:
            from sqlalchemy import select
            rows = (await s.execute(select(Task.read_only).where(Task.workflow_run_id == run_id))).scalars().all()
            return rows

    assert _run(scenario()) == [True]
