"""Phase 2 final-review fixes: unique project names (R14), cross-project thread
delete (R15), schedule ownership on edit (R16), memory regenerate role (R13),
project duplicates/follow-ups (R6), approvals by effective project (R17) and
smaller hardening."""

import asyncio
import inspect
import uuid
from pathlib import Path

from sqlalchemy import func, select

from app.config import settings
from app.database import SessionLocal
from app.models import Artifact, ProjectMember, Schedule, Task, TaskEvent
from conftest import login_as


def _uid():
    return uuid.uuid4().hex[:8]


def _mk_project(client, **kw):
    r = client.post("/projects", json={"name": f"fw-{_uid()}", **kw})
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


def _user(client, pid=None, role=None, tag="fw"):
    u = login_as(f"{tag}-{_uid()}@test.local")
    if pid:
        _grant(client, pid, u.user_id, role)
    return u


def _mcp(client, project):
    r = client.post(
        "/mcp/servers",
        json={"name": f"fw-mcp-{_uid()}", "command": "true", "scope": "project", "project": project},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _db(fn):
    async def go():
        async with SessionLocal() as s:
            out = await fn(s)
            await s.commit()
            return out

    return asyncio.run(go())


def _add_task(**fields):
    async def go(s):
        t = Task(title="fw", prompt="p", status="cancelled", **fields)
        s.add(t)
        await s.flush()
        if t.root_id is None:
            t.root_id = t.id
        return t.id

    return _db(go)


def _set(task_id, **fields):
    async def go(s):
        t = await s.get(Task, task_id)
        for k, v in fields.items():
            setattr(t, k, v)

    _db(go)


def _get(task_id):
    async def go(s):
        return await s.get(Task, task_id)

    return _db(go)


# ---- 1. unique project names (R14) -----------------------------------------
def test_rename_onto_existing_name_409_and_servers_unchanged(client):
    p, q = _mk_project(client), _mk_project(client)
    srv_p, srv_q = _mcp(client, p["name"]), _mcp(client, q["name"])
    r = client.patch(f"/projects/{q['id']}", json={"name": p["name"]})
    assert r.status_code == 409, r.text
    assert client.get(f"/projects/{q['id']}").json()["name"] == q["name"]
    assert client.get(f"/mcp/servers/{srv_p}").json()["project"] == p["name"]
    assert client.get(f"/mcp/servers/{srv_q}").json()["project"] == q["name"]
    # Archived projects keep their name too.
    assert client.patch(f"/projects/{p['id']}", json={"archived": True}).status_code == 200
    assert client.patch(f"/projects/{q['id']}", json={"name": p["name"]}).status_code == 409
    # A maintainer hits the same wall; renaming to its own name is a no-op.
    m = _user(client, q["id"], "maintainer")
    assert m.patch(f"/projects/{q['id']}", json={"name": p["name"]}).status_code == 409
    assert m.patch(f"/projects/{q['id']}", json={"name": q["name"]}).status_code == 200


def test_create_and_import_duplicate_name_409(client, tmp_path):
    p = _mk_project(client)
    assert client.post("/projects", json={"name": p["name"]}).status_code == 409
    d = tmp_path / p["name"]
    d.mkdir()
    r = client.post("/projects/import", json={"directories": [str(d)]})
    assert r.status_code == 409, r.text
    names = [x["name"] for x in client.get("/projects?include_archived=true").json()]
    assert names.count(p["name"]) == 1


def test_human_dispatch_to_invisible_project_404(client):
    hidden = _mk_project(client)
    r = client.post("/agents", json={"name": f"fw-agent-{_uid()}", "system_prompt": "x",
                                     "default_prompt": "go", "requires_approval": True,
                                     "project_id": hidden["id"]})
    assert r.status_code == 201, r.text
    u = _user(client)
    r = u.post("/agents/dispatch", json={"items": [{"agent": r.json()["id"], "prompt": "x"}]})
    assert r.status_code == 404
    assert r.json()["detail"] == "Project not found"
    assert hidden["name"] not in r.text and hidden["id"] not in r.text


# ---- 2. deleting a root spares other projects' steps (R15) ------------------
def test_root_delete_detaches_unmanageable_steps(client):
    p, q = _mk_project(client), _mk_project(client)
    m = _user(client, p["id"], "maintainer")
    admin_id = client.get("/auth/me").json()["id"]
    root = _add_task(project_id=p["id"], created_by=admin_id)
    q_child = _add_task(project_id=q["id"], root_id=root, parent_task_id=root, created_by=admin_id)
    adhoc_child = _add_task(project_id=None, root_id=root, parent_task_id=root, created_by=admin_id)

    async def extras(s):
        s.add(TaskEvent(task_id=q_child, seq=1, type="log", payload={"x": 1}))
        s.add(Artifact(task_id=q_child, filename="a.txt", rel_path="a.txt"))

    _db(extras)
    assert m.get(f"/tasks/{q_child}").status_code == 404
    assert m.delete(f"/tasks/{root}").status_code == 204

    assert client.get(f"/tasks/{root}").status_code == 404
    assert client.get(f"/tasks/{adhoc_child}").status_code == 404  # managed via P: deleted
    kept = _get(q_child)
    assert kept is not None and kept.root_id == q_child and kept.parent_task_id is None
    assert len(client.get(f"/tasks/{q_child}/events").json()) == 1
    assert [a["filename"] for a in client.get(f"/tasks/{q_child}/artifacts").json()] == ["a.txt"]


def _owner(schedule_id):
    async def go(s):
        return (await s.get(Schedule, schedule_id)).created_by

    return _db(go)


# ---- 3. editing a schedule's content takes ownership (R16) ------------------
def test_schedule_content_edit_takes_ownership(client):
    p = _mk_project(client)
    c = _user(client, p["id"], "contributor")
    m = _user(client, p["id"], "maintainer")
    m2 = _user(client, p["id"], "maintainer")
    sch = c.post("/schedules", json={"name": f"fw-{_uid()}", "cron": "0 3 * * *",
                                     "prompt": "hi", "project_id": p["id"]}).json()
    # A cosmetic edit doesn't change the owner.
    assert m.patch(f"/schedules/{sch['id']}", json={"name": "renamed"}).status_code == 200
    assert _owner(sch["id"]) == c.user_id
    assert m.patch(f"/schedules/{sch['id']}", json={"prompt": "deploy to production"}).status_code == 200
    assert _owner(sch["id"]) == m.user_id

    t = m.post(f"/schedules/{sch['id']}/run")
    assert t.status_code == 201, t.text
    t = t.json()
    assert t["created_by"] == m.user_id and t["status"] == "awaiting_approval"
    assert m.post(f"/approvals/{t['id']}/approve").status_code == 403
    assert m2.post(f"/approvals/{t['id']}/reject", json={}).status_code == 200
    t2 = m.post(f"/schedules/{sch['id']}/run").json()
    assert client.post(f"/approvals/{t2['id']}/reject", json={}).status_code == 200


# ---- 5. duplicates and follow-ups (R6 re-ruled) ----------------------------
def test_project_duplicate_runs_in_project_directory(client, tmp_path):
    pdir = tmp_path / "repo"
    p = _mk_project(client, directory=str(pdir))
    c = _user(client, p["id"], "contributor")
    t = c.post("/tasks", json={"prompt": "hi", "project_id": p["id"], "requires_approval": True}).json()
    d = c.post(f"/tasks/{t['id']}/duplicate")
    assert d.status_code == 201, d.text
    dup = _get(d.json()["id"])
    assert dup.project_id == p["id"]
    assert Path(dup.workspace_dir).resolve() == pdir.resolve()
    # An ad-hoc duplicate keeps its own sandbox.
    a = c.post("/tasks", json={"prompt": "hi", "requires_approval": True}).json()
    ad = c.post(f"/tasks/{a['id']}/duplicate").json()
    adup = _get(ad["id"])
    assert adup.project_id is None
    assert Path(adup.workspace_dir).resolve().parent == settings.workspaces_dir.resolve()


def test_delete_cleans_sandbox_only(client, tmp_path):
    pdir = tmp_path / "repo2"
    p = _mk_project(client, directory=str(pdir))
    admin_id = client.get("/auth/me").json()["id"]
    # A project task in the repo: deleting it never removes the repo.
    t = _add_task(project_id=p["id"], workspace_dir=str(pdir), created_by=admin_id)
    assert client.delete(f"/tasks/{t}").status_code == 204
    assert pdir.is_dir()
    # A project task left in a sandbox (pre-fix duplicate): the sandbox goes.
    sb = settings.workspaces_dir / f"fw-{_uid()}"
    sb.mkdir()
    t = _add_task(project_id=p["id"], workspace_dir=str(sb), created_by=admin_id)
    assert client.delete(f"/tasks/{t}").status_code == 204
    assert not sb.exists()


# ---- 6. approvals use the effective project (R17) ---------------------------
def test_maintainer_decides_adhoc_step_in_project_thread(client):
    p = _mk_project(client)
    m = _user(client, p["id"], "maintainer")
    c = _user(client, p["id"], "contributor")
    admin_id = client.get("/auth/me").json()["id"]
    root = _add_task(project_id=p["id"], created_by=admin_id)
    step = _add_task(project_id=None, root_id=root, parent_task_id=root, created_by=c.user_id,
                     risk="info", requires_approval=True)
    _set(step, status="awaiting_approval")
    own = _add_task(project_id=None, root_id=root, parent_task_id=root, created_by=m.user_id,
                    risk="critical", requires_approval=True)
    _set(own, status="awaiting_approval")

    listed = {a["id"] for a in m.get("/approvals").json()}
    assert step in listed and own not in listed
    assert c.post(f"/approvals/{step}/reject", json={}).status_code == 403
    assert m.post(f"/approvals/{own}/reject", json={}).status_code == 403
    assert m.post(f"/approvals/{step}/reject", json={}).status_code == 200
    assert client.post(f"/approvals/{own}/reject", json={}).status_code == 200


# ---- 7. project delete drops its grants ------------------------------------
def test_delete_project_removes_members(client):
    p = _mk_project(client)
    u = _user(client, p["id"], "contributor")
    assert client.delete(f"/projects/{p['id']}").status_code == 204

    async def count(s):
        return (
            await s.execute(select(func.count()).where(ProjectMember.project_id == p["id"]))
        ).scalar_one()

    assert _db(count) == 0
    assert u.get(f"/projects/{p['id']}").status_code == 404


# ---- 8. schedule runs limit validated --------------------------------------
def test_schedule_runs_limit_validated(client):
    sch = client.post("/schedules", json={"name": f"fw-{_uid()}", "cron": "0 3 * * *",
                                          "prompt": "hi"}).json()
    for bad in (0, 501):
        assert client.get(f"/schedules/{sch['id']}/runs?limit={bad}").status_code == 422
    assert client.get(f"/schedules/{sch['id']}/runs?limit=500").status_code == 200


# ---- 9. project PATCH hardening --------------------------------------------
def test_project_field_sets_cover_update_model():
    from app.routers.projects import ADMIN_FIELDS, MAINTAINER_FIELDS, _to_out
    from app.schemas import ProjectUpdate

    assert MAINTAINER_FIELDS | ADMIN_FIELDS == set(ProjectUpdate.model_fields)
    assert not (MAINTAINER_FIELDS & ADMIN_FIELDS)
    assert inspect.signature(_to_out).parameters["admin"].default is inspect.Parameter.empty


def test_maintainer_patch_outside_allowlist_403(client):
    p = _mk_project(client)
    m = _user(client, p["id"], "maintainer")
    r = m.patch(f"/projects/{p['id']}", json={"instructions": "x", "archived": True})
    assert r.status_code == 403
    got = client.get(f"/projects/{p['id']}").json()
    assert got["instructions"] != "x" and got["archived"] is False
