"""Phase 4b Task 2: suggestion review REST (list, person POST, accept, reject) and the agent route."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import auth
from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models import DecisionProposal, Project, ProjectDecision, Task
from conftest import login_as

REVIEWED = "this suggestion was already reviewed"


def _run(coro):
    return asyncio.run(coro)


def _uid():
    return uuid.uuid4().hex[:8]


@pytest.fixture()
def world(client):
    pid = client.post("/projects", json={"name": f"prop-{_uid()}"}).json()["id"]
    maint = login_as(f"pm-{_uid()}@test.local")
    contrib = login_as(f"pc-{_uid()}@test.local")
    out = login_as(f"po-{_uid()}@test.local")
    r = client.put(f"/projects/{pid}/members", json=[
        {"subject_type": "user", "subject_id": maint.user_id, "role": "maintainer"},
        {"subject_type": "user", "subject_id": contrib.user_id, "role": "contributor"}])
    assert r.status_code == 200, r.text
    return {"pid": pid, "base": f"/projects/{pid}/decisions", "maint": maint, "contrib": contrib, "out": out,
            "admin": client}


def _seed(pid, n=1, *, status="pending", evidence="commits a1b2c3d", source="run", **kw):
    ids = []

    async def go():
        async with SessionLocal() as s:
            for i in range(n):
                row = DecisionProposal(
                    project_id=pid, source=source, title=kw.get("title", f"idea {i}"), why="because",
                    touches=kw.get("touches", ["src/a.py"]), evidence=evidence, status=status,
                    created_at=datetime(2026, 11, 20, tzinfo=timezone.utc) + timedelta(minutes=i))
                s.add(row)
                await s.flush()
                ids.append(row.id)
            await s.commit()

    _run(go())
    return ids


def _body(**kw):
    return {"kind": "decision", "title": "Keep cents", "why": "w", "touches": ["src/a.py"], **kw}


def _seq(pid):
    async def go():
        async with SessionLocal() as s:
            return (await s.get(Project, pid)).decision_seq

    return _run(go())


def _count_decisions(pid):
    async def go():
        async with SessionLocal() as s:
            return (await s.execute(select(func.count()).select_from(ProjectDecision).where(
                ProjectDecision.project_id == pid))).scalar_one()

    return _run(go())


def test_list_pending_and_all(world):
    w = world
    _seed(w["pid"], 101)
    r = w["contrib"].get(f"{w['base']}/proposals")
    assert r.status_code == 200
    j = r.json()
    assert len(j["items"]) == 100 and j["pending"] == 101
    assert j["items"][0]["title"] == "idea 100"  # newest first
    _seed(w["pid"], 1, status="accepted", title="done")
    _seed(w["pid"], 1, status="rejected", title="no")
    j = w["contrib"].get(f"{w['base']}/proposals").json()
    assert j["pending"] == 101 and all(i["status"] == "pending" for i in j["items"])
    assert len(w["contrib"].get(f"{w['base']}/proposals", params={"status": "all"}).json()["items"]) == 100
    # a small project: all shows accepted and rejected
    pid2 = w["admin"].post("/projects", json={"name": f"prop2-{_uid()}"}).json()["id"]
    _seed(pid2, 1)
    _seed(pid2, 1, status="accepted", title="done")
    _seed(pid2, 1, status="rejected", title="no")
    j = w["admin"].get(f"/projects/{pid2}/decisions/proposals", params={"status": "all"}).json()
    assert {i["status"] for i in j["items"]} == {"pending", "accepted", "rejected"} and j["pending"] == 1


def test_person_proposal_roles(world):
    w = world
    r = w["contrib"].post(f"{w['base']}/proposals", json={"title": "Use cents", "why": "x", "touches": ["a/**"]})
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["source"] == "person" and j["proposed_by"] == w["contrib"].user_id and j["status"] == "pending"
    assert [i["id"] for i in w["contrib"].get(f"{w['base']}/proposals").json()["items"]] == [j["id"]]
    assert w["out"].get(f"{w['base']}/proposals").status_code == 404
    assert w["out"].post(f"{w['base']}/proposals", json={"title": "x"}).status_code == 404
    assert w["contrib"].post(f"{w['base']}/proposals/{j['id']}/accept", json=_body()).status_code == 403
    assert w["contrib"].post(f"{w['base']}/proposals/{j['id']}/reject").status_code == 403
    assert w["contrib"].post(f"{w['base']}/proposals", json={"title": "x", "touches": ["/etc/passwd"]}).status_code == 422
    assert w["contrib"].post(f"{w['base']}/proposals", json={"title": "  "}).status_code == 422


def test_accept_empty_evidence_source(world):
    w = world
    pid_ = _seed(w["pid"], 1, evidence="")[0]
    r = w["maint"].post(f"{w['base']}/proposals/{pid_}/accept", json=_body())
    assert r.status_code == 201, r.text
    assert r.json()["source"] == "suggestion"


def test_person_route_needs_project_role(world):
    w = world
    root = _task(w["maint"].user_id, w["pid"])
    tid = uuid.uuid4().hex

    async def go():  # an ad-hoc step the outsider created in a thread rooted in the project
        async with SessionLocal() as s:
            s.add(Task(id=tid, title="adhoc", prompt="p", root_id=root, status="completed",
                       created_by=w["out"].user_id, project_id=None))
            await s.commit()

    _run(go())
    r = w["out"].post(f"/tasks/{tid}/decision-proposals", json={"title": "x"})
    assert r.status_code == 403, r.text


def test_accept_creates_decision(world):
    w = world
    pid_ = _seed(w["pid"], 1, evidence="commits a1b2c3d")[0]
    r = w["maint"].post(f"{w['base']}/proposals/{pid_}/accept", json=_body(title="Edited title", why="edited"))
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["title"] == "Edited title" and d["why"] == "edited" and d["number"] == 1
    assert d["source"] == "suggestion: commits a1b2c3d" and d["decided_by"] == w["maint"].user_id
    p = w["maint"].get(f"{w['base']}/proposals", params={"status": "all"}).json()["items"][0]
    assert p["status"] == "accepted" and p["decision_number"] == d["number"]
    assert p["reviewed_by"] == w["maint"].user_id and p["reviewed_at"]


def test_accept_twice_409(world):
    w = world
    pid_ = _seed(w["pid"], 1)[0]
    assert w["maint"].post(f"{w['base']}/proposals/{pid_}/accept", json=_body()).status_code == 201
    seq, n = _seq(w["pid"]), _count_decisions(w["pid"])
    r = w["maint"].post(f"{w['base']}/proposals/{pid_}/accept", json=_body())
    assert r.status_code == 409 and r.json()["detail"] == REVIEWED
    assert _seq(w["pid"]) == seq and _count_decisions(w["pid"]) == n == 1


def test_accept_after_reject_409(world):
    w = world
    pid_ = _seed(w["pid"], 1)[0]
    r = w["maint"].post(f"{w['base']}/proposals/{pid_}/reject")
    assert r.status_code == 200 and r.json()["status"] == "rejected" and r.json()["reviewed_by"] == w["maint"].user_id
    seq = _seq(w["pid"])
    r = w["maint"].post(f"{w['base']}/proposals/{pid_}/accept", json=_body())
    assert r.status_code == 409 and r.json()["detail"] == REVIEWED
    assert _seq(w["pid"]) == seq and _count_decisions(w["pid"]) == 0
    assert w["maint"].post(f"{w['base']}/proposals/{pid_}/reject").status_code == 409


def test_unknown_or_other_project_404(world):
    w = world
    other = w["admin"].post("/projects", json={"name": f"prop-o-{_uid()}"}).json()["id"]
    oid = _seed(other, 1)[0]
    assert w["maint"].post(f"{w['base']}/proposals/{oid}/accept", json=_body()).status_code == 404
    assert w["maint"].post(f"{w['base']}/proposals/{oid}/reject").status_code == 404
    assert w["maint"].post(f"{w['base']}/proposals/nope/reject").status_code == 404


def test_accept_validation_422_leaves_pending(world):
    w = world
    pid_ = _seed(w["pid"], 1)[0]
    seq = _seq(w["pid"])
    r = w["maint"].post(f"{w['base']}/proposals/{pid_}/accept", json=_body(kind="reference"))
    assert r.status_code == 422
    r = w["maint"].post(f"{w['base']}/proposals/{pid_}/accept", json=_body(touches=["../x"]))
    assert r.status_code == 422
    assert _seq(w["pid"]) == seq
    j = w["maint"].get(f"{w['base']}/proposals").json()
    assert j["pending"] == 1 and j["items"][0]["status"] == "pending"


def test_queue_full_429(world, monkeypatch):
    w = world
    monkeypatch.setattr(settings, "proposals_pending_cap", 1)
    assert w["contrib"].post(f"{w['base']}/proposals", json={"title": "one"}).status_code == 201
    r = w["contrib"].post(f"{w['base']}/proposals", json={"title": "two"})
    assert r.status_code == 429 and r.json()["detail"] == "the suggestion queue is full"


# ---- the agent route -----------------------------------------------------------------------------


def _task(created_by, project_id=None):
    tid = uuid.uuid4().hex

    async def go():
        async with SessionLocal() as s:
            s.add(Task(id=tid, title=f"t-{tid[:6]}", prompt="p", root_id=tid, status="running",
                       created_by=created_by, project_id=project_id))
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


@pytest.fixture()
def admin_id(client):
    return client.get("/auth/me").json()["id"]


def test_agent_route_own_task_only(world, admin_id):
    w = world
    mine, other = _task(admin_id, w["pid"]), _task(admin_id, w["pid"])
    a = _agent(admin_id, mine)
    url = f"/tasks/{mine}/decision-proposals"
    assert _agent(admin_id, other).post(url, json={"title": "x"}).status_code == 403
    r = a.post(url, json={"title": "Use cents", "why": "w", "touches": ["src/**"]})
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["source"] == "agent" and j["proposed_by"] is None and j["task_id"] == mine and j["status"] == "pending"
    # a person who manages the task
    r = w["admin"].post(url, json={"title": "person's"})
    assert r.status_code == 201 and r.json()["source"] == "person" and r.json()["proposed_by"] == admin_id
    assert w["out"].post(url, json={"title": "x"}).status_code in (403, 404)


def test_agent_limit_3(world, admin_id):
    w = world
    t = _task(admin_id, w["pid"])
    a = _agent(admin_id, t)
    for i in range(3):
        assert a.post(f"/tasks/{t}/decision-proposals", json={"title": f"s{i}"}).status_code == 201
    r = a.post(f"/tasks/{t}/decision-proposals", json={"title": "s4"})
    assert r.status_code == 429 and r.json()["detail"] == "at most 3 suggestions per task"


def test_agent_route_needs_project(admin_id):
    t = _task(admin_id, None)
    r = _agent(admin_id, t).post(f"/tasks/{t}/decision-proposals", json={"title": "x"})
    assert r.status_code == 422 and "no project" in r.json()["detail"]


def test_agent_bad_touches_422(world, admin_id):
    t = _task(admin_id, world["pid"])
    a = _agent(admin_id, t)
    for bad in (["/etc/passwd"], ["../x"], [f"a{i}" for i in range(25)]):
        assert a.post(f"/tasks/{t}/decision-proposals", json={"title": "x", "touches": bad}).status_code == 422
