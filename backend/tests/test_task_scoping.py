"""Task, stream and approval scoping by project membership (Phase 2, Task 4)."""

import asyncio
import uuid

import pytest
from starlette.websockets import WebSocketDisconnect

from app.database import SessionLocal
from app.models import Task
from conftest import login_as


def _uid():
    return uuid.uuid4().hex[:8]


def _mk_project(client):
    r = client.post("/projects", json={"name": f"ts-{_uid()}"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _grant(client, pid, user_id, role):
    """Add (or change) one user grant, keeping the project's other members."""
    keep = [
        {k: m[k] for k in ("subject_type", "subject_id", "role")}
        for m in client.get(f"/projects/{pid}/members").json()
        if m["subject_id"] != user_id
    ]
    keep.append({"subject_type": "user", "subject_id": user_id, "role": role})
    r = client.put(f"/projects/{pid}/members", json=keep)
    assert r.status_code == 200, r.text


def _user(client, pid=None, role=None, tag="u"):
    u = login_as(f"{tag}-{_uid()}@test.local")
    if pid:
        _grant(client, pid, u.user_id, role)
    return u


def _gated(c, pid=None, prompt="hello"):
    body = {"prompt": prompt, "requires_approval": True}
    if pid:
        body["project_id"] = pid
    r = c.post("/tasks", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _ids(rows):
    return {t["id"] for t in rows}


def test_task_list_scoped(client):
    pid, other = _mk_project(client), _mk_project(client)
    u = _user(client, pid, "contributor")
    mine = _gated(client, pid)
    hidden = _gated(client, other)
    adhoc_admin = _gated(client)
    own = _gated(u)
    ids = _ids(u.get("/tasks?limit=500").json())
    assert mine["id"] in ids and own["id"] in ids
    assert hidden["id"] not in ids and adhoc_admin["id"] not in ids
    assert hidden["id"] not in _ids(u.get(f"/tasks?project_id={other}").json())
    by = u.get("/tasks/stats").json()["by_status"]
    assert sum(by.values()) == len(u.get("/tasks?limit=500&roots_only=false").json())


def test_task_get_events_artifacts_report_404_for_outsider(client, tmp_path):
    pid = _mk_project(client)
    t = _gated(client, pid)
    (tmp_path / "x.txt").write_text("artifact")
    _set(t["id"], workspace_dir=str(tmp_path))
    out = _user(client, tag="out")
    for path in ("", "/thread", "/events", "/artifacts", "/artifacts/x.txt", "/report"):
        assert out.get(f"/tasks/{t['id']}{path}").status_code == 404, path
    for path in ("cancel", "retry", "duplicate"):
        assert out.post(f"/tasks/{t['id']}/{path}").status_code == 404, path
    assert out.post(f"/tasks/{t['id']}/followup", json={"prompt": "x"}).status_code == 404
    assert out.delete(f"/tasks/{t['id']}").status_code == 404
    inside = _user(client, pid, "contributor")
    assert inside.get(f"/tasks/{t['id']}").status_code == 200
    assert inside.get(f"/tasks/{t['id']}/events").status_code == 200
    r = inside.get(f"/tasks/{t['id']}/artifacts/x.txt")
    assert r.status_code == 200 and r.content == b"artifact"


def test_create_in_project_needs_contributor(client):
    pid = _mk_project(client)
    out = _user(client, tag="cr")
    r = out.post("/tasks", json={"prompt": "hi", "project_id": pid, "requires_approval": True})
    assert r.status_code == 404
    _grant(client, pid, out.user_id, "contributor")
    r = out.post("/tasks", json={"prompt": "hi", "project_id": pid, "requires_approval": True})
    assert r.status_code == 201, r.text
    # Ad-hoc tasks need no project role.
    assert out.post("/tasks", json={"prompt": "hi", "requires_approval": True}).status_code == 201


def test_contributor_cannot_cancel_others_task(client):
    pid = _mk_project(client)
    c = _user(client, pid, "contributor")
    t = _gated(client, pid)
    assert c.post(f"/tasks/{t['id']}/cancel").status_code == 403
    assert c.post(f"/tasks/{t['id']}/duplicate").status_code == 403
    assert c.delete(f"/tasks/{t['id']}").status_code == 403
    # Creator may cancel their own.
    own = _gated(c, pid)
    assert c.post(f"/tasks/{own['id']}/cancel").status_code == 200


def test_maintainer_can_cancel(client):
    pid = _mk_project(client)
    m = _user(client, pid, "maintainer")
    t = _gated(client, pid)
    r = m.post(f"/tasks/{t['id']}/cancel")
    assert r.status_code == 200, r.text
    t2 = _gated(client, pid)
    assert m.post(f"/approvals/{t2['id']}/reject", json={}).status_code == 200
    assert m.delete(f"/tasks/{t2['id']}").status_code == 204


def _set(task_id, **fields):
    async def go():
        async with SessionLocal() as s:
            t = await s.get(Task, task_id)
            for k, v in fields.items():
                setattr(t, k, v)
            await s.commit()

    asyncio.run(go())


def _adhoc_child(root_id, created_by, status="cancelled", **fields):
    """An ad-hoc (project_id=None) step in a project task's thread."""
    async def go():
        async with SessionLocal() as s:
            child = Task(
                title="step", prompt="p", status=status, project_id=None,
                root_id=root_id, parent_task_id=root_id, created_by=created_by, **fields,
            )
            s.add(child)
            await s.commit()
            return child.id

    return asyncio.run(go())


def test_followup_duplicate_need_contributor_on_root_project(client):
    pid = _mk_project(client)
    c = _user(client, pid, "contributor")
    root = _gated(client, pid)
    cid = _adhoc_child(root["id"], c.user_id, session_id="sess-ts")
    r = c.post(f"/tasks/{cid}/followup", json={"prompt": "x"})
    assert r.status_code == 201, r.text
    assert r.json()["project_id"] == pid  # inherits the thread root's project
    # Drop the grant: the creator still sees their own ad-hoc step, but may no
    # longer follow up / duplicate into the project's repo and session.
    assert client.put(f"/projects/{pid}/members", json=[]).status_code == 200
    assert c.get(f"/tasks/{cid}").status_code == 200
    assert c.post(f"/tasks/{cid}/followup", json={"prompt": "x"}).status_code == 403
    assert c.post(f"/tasks/{cid}/duplicate").status_code == 403


def test_duplicate_and_followup_carry_project(client):
    pid = _mk_project(client)
    c1 = _user(client, pid, "contributor")
    c2 = _user(client, pid, "contributor")
    t = _gated(c1, pid)
    _set(t["id"], session_id="sess-carry")
    d = c1.post(f"/tasks/{t['id']}/duplicate")
    assert d.status_code == 201, d.text
    f = c1.post(f"/tasks/{t['id']}/followup", json={"prompt": "more"})
    assert f.status_code == 201, f.text
    for new in (d.json(), f.json()):
        assert new["project_id"] == pid
        assert c2.get(f"/tasks/{new['id']}").status_code == 200


def test_maintainer_manages_adhoc_steps_in_project_thread(client):
    pid = _mk_project(client)
    m = _user(client, pid, "maintainer")
    c = _user(client, pid, "contributor")
    other = _user(client, pid, "contributor")
    root = _gated(client, pid)
    done = _adhoc_child(root["id"], c.user_id)
    assert other.delete(f"/tasks/{done}").status_code == 403
    assert m.delete(f"/tasks/{done}").status_code == 204
    pending = _adhoc_child(root["id"], c.user_id, status="awaiting_approval")
    assert other.post(f"/tasks/{pending}/cancel").status_code == 403
    assert m.post(f"/tasks/{pending}/cancel").status_code == 200


def test_child_of_visible_root_visible(client):
    pid = _mk_project(client)
    c = _user(client, pid, "contributor")
    out = _user(client, tag="cv")
    root = _gated(client, pid)

    async def mk_child():
        async with SessionLocal() as s:
            child = Task(
                title="child", prompt="p", status="cancelled", project_id=None,
                root_id=root["id"], parent_task_id=root["id"],
                created_by=(await s.get(Task, root["id"])).created_by,
            )
            s.add(child)
            await s.commit()
            return child.id

    cid = asyncio.run(mk_child())
    assert c.get(f"/tasks/{cid}").status_code == 200
    assert cid in _ids(c.get(f"/tasks/{root['id']}/thread").json())
    assert cid in _ids(c.get("/tasks?roots_only=false&limit=500").json())
    assert out.get(f"/tasks/{cid}").status_code == 404
    assert cid not in _ids(out.get("/tasks?roots_only=false&limit=500").json())


def test_ws_invisible_task_4404(client):
    pid = _mk_project(client)
    t = _gated(client, pid)
    out = _user(client, tag="ws")
    with pytest.raises(WebSocketDisconnect) as e:
        with out.websocket_connect(f"/tasks/{t['id']}/stream") as ws:
            ws.receive_json()
    assert e.value.code == 4404
    with pytest.raises(WebSocketDisconnect) as e:
        with out.websocket_connect("/tasks/does-not-exist/stream") as ws:
            ws.receive_json()
    assert e.value.code == 4404


def test_approvals_routed_to_maintainers(client):
    pid = _mk_project(client)
    c = _user(client, pid, "contributor")
    m = _user(client, pid, "maintainer")
    t = _gated(c, pid)
    assert t["id"] not in _ids(c.get("/approvals").json())
    assert t["id"] in _ids(m.get("/approvals").json())
    assert t["id"] in _ids(client.get("/approvals").json())
    assert c.post(f"/approvals/{t['id']}/approve").status_code == 403
    assert c.post(f"/approvals/{t['id']}/reject", json={}).status_code == 403
    assert c.post("/approvals/approve", json={"task_ids": [t["id"]]}).json() == []
    out = _user(client, tag="ap")
    assert out.post(f"/approvals/{t['id']}/approve").status_code == 404
    r = m.post(f"/approvals/{t['id']}/reject", json={"reason": "no"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"


def test_cannot_self_approve_critical(client):
    pid = _mk_project(client)
    m1 = _user(client, pid, "maintainer")
    m2 = _user(client, pid, "maintainer")
    a = _gated(m1, pid, prompt="please git push the branch")
    assert a["risk"] == "critical"
    assert a["id"] not in _ids(m1.get("/approvals").json())
    assert m1.post(f"/approvals/{a['id']}/approve").status_code == 403
    assert m1.post("/approvals/approve", json={"task_ids": [a["id"]]}).json() == []
    assert m2.post(f"/approvals/{a['id']}/approve").status_code == 200
    b = _gated(m1, pid, prompt="please git push again")
    assert client.post(f"/approvals/{b['id']}/approve").status_code == 200
    # Non-critical self-requests are fine for a maintainer.
    n = _gated(m1, pid)
    assert n["risk"] != "critical"
    assert m1.post(f"/approvals/{n['id']}/approve").status_code == 200


def test_adhoc_approval_admin_only(client):
    u = _user(client, tag="ad")
    t = _gated(u)
    assert t["id"] not in _ids(u.get("/approvals").json())
    assert u.post(f"/approvals/{t['id']}/approve").status_code == 403
    assert t["id"] in _ids(client.get("/approvals").json())
    r = client.post("/approvals/approve", json={"task_ids": [t["id"]]})
    assert [x["id"] for x in r.json()] == [t["id"]]
