"""Agents, templates, workflows and schedules scoped by project membership
(Phase 2, Task 5)."""

import asyncio
import datetime
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import auth
from app.database import SessionLocal
from app.main import app
from app.models import Schedule, Task, User
from conftest import login_as


def _uid():
    return uuid.uuid4().hex[:8]


def _mk_project(client):
    r = client.post("/projects", json={"name": f"rs-{_uid()}"})
    assert r.status_code == 201, r.text
    return r.json()


def _grant(client, pid, user_id, role):
    keep = [
        {k: m[k] for k in ("subject_type", "subject_id", "role")}
        for m in client.get(f"/projects/{pid}/members").json()
        if m["subject_id"] != user_id
    ]
    keep.append({"subject_type": "user", "subject_id": user_id, "role": role})
    r = client.put(f"/projects/{pid}/members", json=keep)
    assert r.status_code == 200, r.text


def _user(client, grants=(), tag="u"):
    u = login_as(f"{tag}-{_uid()}@test.local")
    for pid, role in grants:
        _grant(client, pid, u.user_id, role)
    return u


def _agent(client, project_id=None, **kw):
    body = {"name": f"rs-agent-{_uid()}", "system_prompt": "x", "default_prompt": "go",
            "requires_approval": True, **kw}
    if project_id:
        body["project_id"] = project_id
    r = client.post("/agents", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _workflow(client, agents):
    r = client.post("/workflows", json={
        "name": f"rs-wf-{_uid()}",
        "stages": [{"name": "S", "items": [{"agent": a, "prompt": "do {{input}}"} for a in agents]}],
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_agent_template_workflow_writes_admin_only(client):
    u = _user(client)
    a = _agent(client)
    tpl = client.post("/templates", json={"name": "rs-tpl", "prompt": "p"}).json()
    wf = _workflow(client, [a["id"]])

    assert u.post("/agents", json={"name": f"x-{_uid()}", "system_prompt": "x"}).status_code == 403
    assert u.patch(f"/agents/{a['id']}", json={"description": "d"}).status_code == 403
    assert u.delete(f"/agents/{a['id']}").status_code == 403
    assert u.post("/templates", json={"name": "t", "prompt": "p"}).status_code == 403
    assert u.delete(f"/templates/{tpl['id']}").status_code == 403
    stages = [{"name": "S", "items": [{"agent": a["id"], "prompt": "p"}]}]
    assert u.post("/workflows", json={"name": "w", "stages": stages}).status_code == 403
    assert u.patch(f"/workflows/{wf['id']}", json={"description": "d"}).status_code == 403
    assert u.delete(f"/workflows/{wf['id']}").status_code == 403

    # Lists stay visible to everyone.
    assert a["id"] in {x["id"] for x in u.get("/agents").json()}
    assert tpl["id"] in {x["id"] for x in u.get("/templates").json()}
    assert wf["id"] in {x["id"] for x in u.get("/workflows").json()}

    assert client.patch(f"/agents/{a['id']}", json={"description": "d"}).status_code == 200


def test_run_agent_needs_contributor_on_target(client):
    p, hidden = _mk_project(client), _mk_project(client)
    u = _user(client, [(p["id"], "contributor")])
    on_p = _agent(client, p["id"])
    on_hidden = _agent(client, hidden["id"])
    adhoc = _agent(client)

    assert u.post(f"/agents/{on_p['id']}/run", json={}).status_code == 201
    assert u.post(f"/agents/{on_hidden['id']}/run", json={}).status_code == 404
    # Override wins: an ad-hoc agent pointed at a hidden project is refused...
    assert u.post(f"/agents/{adhoc['id']}/run", json={"project_id": hidden["id"]}).status_code == 404
    # ...and pointed at a project the user contributes to is allowed.
    r = u.post(f"/agents/{on_hidden['id']}/run", json={"project_id": p["id"]})
    assert r.status_code == 201 and r.json()["project_id"] == p["id"]
    # No project at all: an ad-hoc task, allowed.
    assert u.post(f"/agents/{adhoc['id']}/run", json={}).status_code == 201

    assert u.post(f"/agents/{on_hidden['id']}/bulk", json={"prompts": ["a"]}).status_code == 404
    assert u.post(f"/agents/{on_p['id']}/bulk", json={"prompts": ["a", "b"]}).status_code == 201

    # Templates target their own project; none = ad-hoc.
    tpl_hidden = client.post("/templates", json={"name": "t", "prompt": "p", "project_id": hidden["id"]}).json()
    tpl_p = client.post("/templates", json={"name": "t", "prompt": "p", "project_id": p["id"]}).json()
    tpl_none = client.post("/templates", json={"name": "t", "prompt": "p"}).json()
    assert u.post(f"/templates/{tpl_hidden['id']}/run").status_code == 404
    assert u.post(f"/templates/{tpl_p['id']}/run").status_code == 201
    assert u.post(f"/templates/{tpl_none['id']}/run").status_code == 201


def _mint(user_id: str, task_id: str) -> str:
    async def go():
        async with SessionLocal() as s:
            raw = await auth.mint_token(s, user_id, "agent-task", task_id, 600)
            await s.commit()
            return raw

    return asyncio.run(go())


def test_dispatch_refused_message(client):
    p, hidden = _mk_project(client), _mk_project(client)
    email = f"disp-{_uid()}@test.local"
    u = login_as(email)
    _grant(client, p["id"], u.user_id, "contributor")
    worker_hidden = _agent(client, hidden["id"])
    worker_p = _agent(client, p["id"])
    worker_none = _agent(client)
    parent = u.post("/tasks", json={"prompt": "coord", "requires_approval": True,
                                    "project_id": p["id"]}).json()

    t = TestClient(app)
    t.headers["Authorization"] = f"Bearer {_mint(u.user_id, parent['id'])}"
    body = {"parent_task_id": parent["id"]}

    r = t.post("/agents/dispatch", json={**body, "items": [{"agent": worker_hidden["name"], "prompt": "x"}]})
    assert r.status_code == 403
    assert r.json()["detail"] == f"{email} is not a contributor on project {hidden['name']}"
    # The payload's project override is checked too (for a no-project agent).
    r = t.post("/agents/dispatch", json={**body, "project_id": hidden["id"],
                                         "items": [{"agent": worker_none["id"], "prompt": "x"}]})
    assert r.status_code == 403
    assert r.json()["detail"] == f"{email} is not a contributor on project {hidden['name']}"
    # Mixed batch: nothing is created when any child is refused.
    r = t.post("/agents/dispatch", json={**body, "items": [
        {"agent": worker_p["id"], "prompt": "ok"}, {"agent": worker_hidden["id"], "prompt": "no"}]})
    assert r.status_code == 403

    async def kids():
        async with SessionLocal() as s:
            return (await s.execute(select(Task).where(Task.parent_task_id == parent["id"]))).scalars().all()

    assert asyncio.run(kids()) == []

    r = t.post("/agents/dispatch", json={**body, "items": [
        {"agent": worker_p["id"], "prompt": "ok"}, {"agent": worker_none["id"], "prompt": "ok"}]})
    assert r.status_code == 201, r.text

    # A logged-in user calling dispatch directly is refused too, without
    # learning the project's name (R14).
    r = u.post("/agents/dispatch", json={"items": [{"agent": worker_hidden["id"], "prompt": "x"}]})
    assert r.status_code == 404
    assert r.json()["detail"] == "Project not found" and hidden["name"] not in r.text


def test_workflow_run_needs_all_projects(client):
    p1, p2 = _mk_project(client), _mk_project(client)
    a1, a2 = _agent(client, p1["id"]), _agent(client, p2["id"])
    # Stage items reference one agent by id and the other by exact name.
    wf = _workflow(client, [a1["id"], a2["name"]])

    one = _user(client, [(p1["id"], "contributor")])
    assert one.post(f"/workflows/{wf['id']}/run", json={"input": "i"}).status_code == 404
    _grant(client, p2["id"], one.user_id, "contributor")
    r = one.post(f"/workflows/{wf['id']}/run", json={"input": "i"})
    assert r.status_code == 201, r.text
    assert sorted(r.json()["project_ids"]) == sorted([p1["id"], p2["id"]])
    got = one.get(f"/workflows/runs/{r.json()['id']}").json()["run"]
    assert sorted(got["project_ids"]) == sorted([p1["id"], p2["id"]])

    # No project touched: anyone may run it.
    free = _workflow(client, [_agent(client)["id"]])
    nobody = _user(client)
    r = nobody.post(f"/workflows/{free['id']}/run", json={"input": "i"})
    assert r.status_code == 201, r.text
    assert r.json()["project_ids"] == []


def test_workflow_run_visible_with_any_overlap(client):
    p1, p2 = _mk_project(client), _mk_project(client)
    wf = _workflow(client, [_agent(client, p1["id"])["id"], _agent(client, p2["id"])["id"]])
    runner = _user(client, [(p1["id"], "contributor"), (p2["id"], "contributor")])
    run = runner.post(f"/workflows/{wf['id']}/run", json={"input": "i"}).json()

    viewer = _user(client, [(p2["id"], "contributor")])
    maint = _user(client, [(p1["id"], "maintainer")])
    outsider = _user(client)

    assert run["id"] in {r["id"] for r in viewer.get("/workflows/runs").json()}
    assert run["id"] in {r["id"] for r in runner.get("/workflows/runs").json()}
    assert run["id"] not in {r["id"] for r in outsider.get("/workflows/runs").json()}
    assert viewer.get(f"/workflows/runs/{run['id']}").status_code == 200
    assert outsider.get(f"/workflows/runs/{run['id']}").status_code == 404

    # Cancel/retry: creator, admin, or maintainer of any touched project.
    assert outsider.post(f"/workflows/runs/{run['id']}/cancel").status_code == 404
    assert viewer.post(f"/workflows/runs/{run['id']}/cancel").status_code == 403
    assert viewer.post(f"/workflows/runs/{run['id']}/retry").status_code == 403
    assert maint.post(f"/workflows/runs/{run['id']}/cancel").status_code == 200

    # A no-project run is visible only to its creator (and admins).
    free = _workflow(client, [_agent(client)["id"]])
    mine = outsider.post(f"/workflows/{free['id']}/run", json={"input": "i"}).json()
    assert mine["id"] in {r["id"] for r in outsider.get("/workflows/runs").json()}
    assert viewer.get(f"/workflows/runs/{mine['id']}").status_code == 404
    assert mine["id"] not in {r["id"] for r in viewer.get("/workflows/runs").json()}
    assert client.get(f"/workflows/runs/{mine['id']}").status_code == 200
    assert outsider.post(f"/workflows/runs/{mine['id']}/cancel").status_code == 200

    # The non-admin list honours `limit` after filtering: a newer run the
    # outsider can't see must not crowd out their own.
    hidden = client.post(f"/workflows/{wf['id']}/run", json={"input": "later"}).json()
    got = outsider.get("/workflows/runs?limit=1").json()
    assert [r["id"] for r in got] == [mine["id"]], (hidden["id"], got)
    assert outsider.get("/workflows/runs?limit=0").status_code == 422
    assert outsider.get("/workflows/runs?limit=501").status_code == 422
    assert client.get("/workflows/runs?limit=501").status_code == 422


def test_parent_task_must_be_visible(client):
    p = _mk_project(client)
    u = _user(client, [(p["id"], "contributor")])
    a = _agent(client)
    hidden_parent = client.post("/tasks", json={"prompt": "x", "requires_approval": True}).json()
    seen_parent = client.post("/tasks", json={"prompt": "x", "requires_approval": True,
                                              "project_id": p["id"]}).json()
    item = [{"agent": a["id"], "prompt": "go"}]
    assert u.post(f"/agents/{a['id']}/run", json={"parent_task_id": hidden_parent["id"]}).status_code == 404
    assert u.post("/agents/dispatch", json={"parent_task_id": hidden_parent["id"], "items": item}).status_code == 404
    assert u.post(f"/agents/{a['id']}/run", json={"parent_task_id": "nope"}).status_code == 404
    r = u.post(f"/agents/{a['id']}/run", json={"parent_task_id": seen_parent["id"]})
    assert r.status_code == 201 and r.json()["created_by"] == u.user_id
    r = u.post("/agents/dispatch", json={"parent_task_id": seen_parent["id"], "items": item})
    assert r.status_code == 201 and r.json()[0]["created_by"] == u.user_id


def _sched(c, pid=None, **kw):
    body = {"name": f"rs-sch-{_uid()}", "cron": "0 3 * * *", "prompt": "hi",
            "requires_approval": True, **kw}
    if pid:
        body["project_id"] = pid
    return c.post("/schedules", json=body)


def test_schedule_scoping(client):
    p, hidden = _mk_project(client), _mk_project(client)
    contrib = _user(client, [(p["id"], "contributor")])
    other = _user(client, [(p["id"], "contributor")])
    maint = _user(client, [(p["id"], "maintainer")])
    outsider = _user(client)

    assert _sched(contrib, hidden["id"]).status_code == 404
    # The schedule's agent targets a hidden project: refused too.
    hidden_agent = _agent(client, hidden["id"])
    assert _sched(contrib, agent_id=hidden_agent["id"]).status_code == 404
    sch = _sched(contrib, p["id"]).json()
    adhoc = _sched(contrib).json()

    ids = lambda c: {x["id"] for x in c.get("/schedules").json()}  # noqa: E731
    assert {sch["id"], adhoc["id"]} <= ids(contrib)
    assert sch["id"] in ids(other) and adhoc["id"] not in ids(other)
    assert sch["id"] not in ids(outsider)
    assert {sch["id"], adhoc["id"]} <= ids(client)

    assert outsider.get(f"/schedules/{sch['id']}").status_code == 404
    assert other.get(f"/schedules/{sch['id']}").status_code == 200
    assert other.get(f"/schedules/{adhoc['id']}").status_code == 404
    assert outsider.get(f"/schedules/{sch['id']}/runs").status_code == 404

    # Edit/delete: creator, maintainer, admin.
    assert other.patch(f"/schedules/{sch['id']}", json={"name": "x"}).status_code == 403
    assert other.delete(f"/schedules/{sch['id']}").status_code == 403
    assert outsider.patch(f"/schedules/{sch['id']}", json={"name": "x"}).status_code == 404
    assert maint.patch(f"/schedules/{sch['id']}", json={"name": "m"}).status_code == 200
    assert contrib.patch(f"/schedules/{sch['id']}", json={"name": "c"}).status_code == 200
    # Moving it into a project the editor can't contribute to is refused.
    assert contrib.patch(f"/schedules/{sch['id']}", json={"project_id": hidden["id"]}).status_code == 404
    assert client.patch(f"/schedules/{adhoc['id']}", json={"name": "a"}).status_code == 200

    # Run-now: contributor.
    assert other.post(f"/schedules/{sch['id']}/run").status_code == 201
    assert outsider.post(f"/schedules/{sch['id']}/run").status_code == 404

    assert other.delete(f"/schedules/{adhoc['id']}").status_code == 404
    assert contrib.delete(f"/schedules/{adhoc['id']}").status_code == 204
    assert maint.delete(f"/schedules/{sch['id']}").status_code == 204


def _set_active(user_id, active):
    async def go():
        async with SessionLocal() as s:
            (await s.get(User, user_id)).active = active
            await s.commit()

    asyncio.run(go())


def test_run_now_applies_owner_checks(client):
    p = _mk_project(client)
    u = _user(client, [(p["id"], "contributor")])
    sch = _sched(u, p["id"]).json()
    _set_active(u.user_id, False)
    try:
        r = client.post(f"/schedules/{sch['id']}/run")
    finally:
        _set_active(u.user_id, True)
    assert r.status_code == 409
    assert r.json()["detail"] == "Schedule owner is disabled"
    assert client.put(f"/projects/{p['id']}/members", json=[]).status_code == 200
    r = client.post(f"/schedules/{sch['id']}/run")
    assert r.status_code == 409
    assert r.json()["detail"] == "Schedule owner lost access to project"
    assert client.post(f"/schedules/{_sched(client).json()['id']}/run").status_code == 201


class _Worker:
    async def submit(self, *a):
        pass


def test_scheduler_skips_owner_without_access(client, caplog):
    import app.scheduler as sched_mod

    p = _mk_project(client)
    u = _user(client, [(p["id"], "contributor")])
    sch = _sched(u, p["id"]).json()
    # Owner loses access to the project.
    r = client.put(f"/projects/{p['id']}/members", json=[])
    assert r.status_code == 200, r.text

    async def drive():
        async with SessionLocal() as s:
            row = await s.get(Schedule, sch["id"])
            row.next_run_at = datetime.datetime(2000, 1, 1)
            await s.commit()
        await sched_mod.Scheduler(_Worker())._tick()
        async with SessionLocal() as s:
            n = len((await s.execute(select(Task).where(Task.schedule_id == sch["id"]))).scalars().all())
            nxt = (await s.get(Schedule, sch["id"])).next_run_at
        return n, nxt

    with caplog.at_level("WARNING", logger="app.scheduler"):
        n, nxt = asyncio.run(drive())
    assert n == 0
    assert nxt.replace(tzinfo=None) > datetime.datetime(2001, 1, 1)
    assert any("owner lost access to project" in m for m in caplog.messages)


def test_scheduler_skips_missing_owner(client, caplog):
    import app.scheduler as sched_mod

    sch = _sched(client).json()

    async def drive():
        async with SessionLocal() as s:
            row = await s.get(Schedule, sch["id"])
            row.created_by = "no-such-user"
            row.next_run_at = datetime.datetime(2000, 1, 1)
            await s.commit()
        await sched_mod.Scheduler(_Worker())._tick()
        async with SessionLocal() as s:
            n = len((await s.execute(select(Task).where(Task.schedule_id == sch["id"]))).scalars().all())
            nxt = (await s.get(Schedule, sch["id"])).next_run_at
        return n, nxt

    with caplog.at_level("WARNING", logger="app.scheduler"):
        n, nxt = asyncio.run(drive())
    assert n == 0
    assert nxt.replace(tzinfo=None) > datetime.datetime(2001, 1, 1)
    assert any("owner no longer exists" in m for m in caplog.messages)
