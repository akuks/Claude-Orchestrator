"""Findings, usage, MCP observability, knowledge docs, project-scoped MCP servers
and skill packs follow project membership (Phase 2, Task 6)."""

import asyncio
import uuid
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models import Finding, McpCall, Project, Task
from conftest import login_as


def _uid():
    return uuid.uuid4().hex[:8]


def _mk_project(client):
    r = client.post("/projects", json={"name": f"ms-{_uid()}"})
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


def _user(client, pid=None, role=None, tag="u"):
    u = login_as(f"{tag}-{_uid()}@test.local")
    if pid:
        _grant(client, pid, u.user_id, role)
    return u


def _add(*rows):
    """Insert rows directly; returns their ids (tasks are inserted completed so
    the worker never picks them up)."""

    async def go():
        async with SessionLocal() as s:
            s.add_all(rows)
            await s.commit()
            return [r.id for r in rows]

    return asyncio.run(go())


def _task(project=None, cost=1.0, created_by=None, tin=10, tout=5, model="m-test"):
    return Task(
        id=uuid.uuid4().hex,
        title="usage",
        prompt="usage",
        project=project["name"] if project else None,
        project_id=project["id"] if project else None,
        status="completed",
        created_by=created_by,
        total_cost_usd=cost,
        input_tokens=tin,
        output_tokens=tout,
        model_used=model,
        completed_at=datetime.now(timezone.utc),
    )


def _finding(pid, sev="high"):
    return Finding(project_id=pid, fingerprint=uuid.uuid4().hex, severity=sev, title="f")


# ---------------------------------------------------------------- findings


def test_findings_scoped_and_patch_needs_contributor(client):
    p, other = _mk_project(client), _mk_project(client)
    fid, hid = _add(_finding(p["id"]), _finding(other["id"], "critical"))
    contrib = _user(client, p["id"], "contributor")
    ids = {f["id"] for f in contrib.get("/findings").json()}
    assert fid in ids and hid not in ids
    assert contrib.get(f"/findings?project_id={other['id']}").json() == []
    summary = contrib.get("/findings/summary").json()
    assert summary["total"] == 1 and summary["open_critical"] == 0
    assert set(summary["projects"]) == {p["id"]}
    assert contrib.patch(f"/findings/{hid}", json={"status": "fixed"}).status_code == 404
    r = contrib.patch(f"/findings/{fid}", json={"status": "accepted"})
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    assert client.patch(f"/findings/{hid}", json={"status": "fixed"}).status_code == 200


# ---------------------------------------------------------------- usage


def test_usage_scoped(client):
    p, other = _mk_project(client), _mk_project(client)
    u = _user(client, p["id"], "contributor")
    _add(
        _task(p, 1.25, model=f"m-{_uid()}"),
        _task(None, 0.5, created_by=u.user_id),
        _task(other, 7.0),
        _task(None, 3.0),  # someone else's ad-hoc task
    )
    s = u.get("/usage/summary").json()
    assert s["all_time"]["cost_usd"] == 1.75 and s["all_time"]["tasks"] == 2
    assert s["today"]["cost_usd"] == 1.75
    assert round(sum(d["cost_usd"] for d in u.get("/usage/timeseries").json()), 4) == 1.75
    by_project = {r["project"]: r["cost_usd"] for r in u.get("/usage/by-project").json()}
    assert by_project == {p["name"]: 1.25, "(no project)": 0.5}
    assert round(sum(r["cost_usd"] for r in u.get("/usage/by-model").json()), 4) == 1.75
    admin_names = {r["project"] for r in client.get("/usage/by-project").json()}
    assert {p["name"], other["name"]} <= admin_names


def test_usage_by_user(client):
    p, other = _mk_project(client), _mk_project(client)
    u = _user(client, p["id"], "contributor", tag="me")
    v = _user(client, p["id"], "contributor", tag="mate")
    _add(
        _task(p, 1.5, created_by=u.user_id),
        _task(p, 2.25, created_by=v.user_id),
        _task(p, 0.5),
        _task(other, 9.0, created_by=v.user_id),
    )
    rows = u.get("/usage/by-user").json()
    assert [(r["user_id"], r["cost_usd"]) for r in rows] == [
        (v.user_id, 2.25),
        (u.user_id, 1.5),
        (None, 0.5),
    ]
    assert rows[2]["name"] == "(unassigned)" and rows[2]["email"] is None
    assert rows[0]["email"].startswith("mate-") and rows[0]["tasks"] == 1
    assert rows[0]["tokens"] == 15
    admin_rows = {r["user_id"]: r for r in client.get("/usage/by-user").json()}
    assert admin_rows[v.user_id]["cost_usd"] == 11.25


# ---------------------------------------------------------------- zero grants


def test_zero_grants_empty_everywhere(client):
    _mk_project(client)  # something exists that they must not see
    z = _user(client, tag="zero")
    for path in ("/projects", "/tasks", "/findings", "/schedules", "/workflows/runs", "/approvals"):
        r = z.get(path)
        assert r.status_code == 200 and r.json() == [], path
    docs = z.get("/context-docs")
    assert docs.status_code == 200 and all(d["scope"] == "global" for d in docs.json())
    s = z.get("/usage/summary").json()
    for window in ("today", "last_7d", "last_30d", "all_time"):
        assert s[window]["cost_usd"] == 0 and s[window]["tasks"] == 0
    assert z.get("/usage/by-project").json() == []
    assert z.get("/usage/by-user").json() == []
    assert z.get("/mcp/observability").json()["total_calls"] == 0


# ---------------------------------------------------------------- MCP


def test_mcp_observability_scoped(client):
    p, other = _mk_project(client), _mk_project(client)
    u = _user(client, p["id"], "contributor")
    seen, hidden, orphan = (f"srv-{_uid()}" for _ in range(3))
    t_vis, t_hid = _add(_task(p), _task(other))
    _add(
        McpCall(task_id=t_vis, server=seen, tool="a", is_error=True),
        McpCall(task_id=t_vis, server=seen, tool="a"),
        McpCall(task_id=t_hid, server=hidden, tool="b"),
        McpCall(task_id=None, server=orphan, tool="c"),
    )
    o = u.get("/mcp/observability").json()
    assert o["total_calls"] == 2 and o["total_errors"] == 1
    assert {r["server"] for r in o["by_server"]} == {seen}
    assert {r["server"] for r in o["top_tools"]} <= {seen}
    admin = {r["server"] for r in client.get("/mcp/observability").json()["by_server"]}
    assert {seen, hidden, orphan} <= admin


def _mcp(client, scope="project", project=None):
    body = {"name": f"mcp-{_uid()}", "command": "true", "scope": scope, "project": project}
    r = client.post("/mcp/servers", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_project_scoped_mcp_and_skill_hidden(client, tmp_path):
    p = _mk_project(client)
    inside = _user(client, p["id"], "contributor")
    out = _user(client, tag="out")
    proj_srv, team_srv = _mcp(client, project=p["name"]), _mcp(client, scope="team")
    ghost_srv = _mcp(client, project=f"no-such-{_uid()}")
    r = client.post(
        "/skills",
        json={"name": f"sk-{_uid()}", "local_path": str(tmp_path), "scope": "project", "project_id": p["id"]},
    )
    assert r.status_code == 201, r.text
    pack = r.json()["id"]

    out_ids = {s["id"] for s in out.get("/mcp/servers").json()}
    assert team_srv in out_ids and proj_srv not in out_ids and ghost_srv not in out_ids
    assert out.get(f"/mcp/servers/{proj_srv}").status_code == 404
    assert out.get(f"/mcp/servers/{proj_srv}/policies").status_code == 404
    assert out.get(f"/mcp/servers/{team_srv}").status_code == 200
    assert pack not in {k["id"] for k in out.get("/skills").json()}

    in_ids = {s["id"] for s in inside.get("/mcp/servers").json()}
    assert proj_srv in in_ids and ghost_srv not in in_ids
    assert inside.get(f"/mcp/servers/{proj_srv}").status_code == 200
    assert inside.get(f"/mcp/servers/{proj_srv}/policies").status_code == 200
    assert inside.get(f"/mcp/servers/{ghost_srv}").status_code == 404
    assert pack in {k["id"] for k in inside.get("/skills").json()}

    admin_ids = {s["id"] for s in client.get("/mcp/servers").json()}
    assert {proj_srv, team_srv, ghost_srv} <= admin_ids
    assert client.get(f"/mcp/servers/{ghost_srv}").status_code == 200


def test_mcp_project_match_after_rename(client):
    """A rename carries project-scoped servers along (R11); matching is by the
    project's current name."""
    p = _mk_project(client)
    inside = _user(client, p["id"], "contributor")
    srv, team_srv = _mcp(client, project=p["name"]), _mcp(client, scope="team")
    new_name = f"renamed-{_uid()}"
    assert client.patch(f"/projects/{p['id']}", json={"name": new_name}).status_code == 200
    r = inside.get(f"/mcp/servers/{srv}")
    assert r.status_code == 200 and r.json()["project"] == new_name
    assert srv in {s["id"] for s in inside.get("/mcp/servers").json()}
    assert client.get(f"/mcp/servers/{team_srv}").json()["project"] is None
    # A server created later with the old name doesn't resolve to the renamed
    # project; a project that later takes the old name owns it instead.
    stale = _mcp(client, project=p["name"])
    assert inside.get(f"/mcp/servers/{stale}").status_code == 404
    q = _mk_project(client)
    assert client.patch(f"/projects/{q['id']}", json={"name": p["name"]}).status_code == 200
    q_member = _user(client, q["id"], "contributor")
    assert q_member.get(f"/mcp/servers/{stale}").status_code == 200
    assert q_member.get(f"/mcp/servers/{srv}").status_code == 404


def test_usage_budget_from_visible_projects_only(client):
    """An invisible project sharing a visible one's name (legacy data: the API
    now refuses duplicate names) can't supply its budget."""
    p = _mk_project(client)
    assert client.patch(f"/projects/{_mk_project(client)['id']}", json={"name": p["name"]}).status_code == 409
    _add(Project(name=p["name"], slug=f"twin-{_uid()}", directory="/nonexistent", budget_usd=0.01))
    u = _user(client, p["id"], "contributor")
    _add(_task(p, 2.0))
    row = next(r for r in u.get("/usage/by-project").json() if r["project"] == p["name"])
    assert row["budget_usd"] is None and row["over_budget"] is False


# ---------------------------------------------------------------- knowledge docs


def _doc(c, pid=None, path=None):
    body = {"path": path or f"kb/{_uid()}.md", "body": "x"}
    if pid:
        body.update(scope="project", project_id=pid)
    return c.post("/context-docs", json=body)


def test_context_docs_roles(client):
    p, other = _mk_project(client), _mk_project(client)
    contrib = _user(client, p["id"], "contributor")
    maint = _user(client, p["id"], "maintainer")
    assert _doc(contrib, p["id"]).status_code == 403
    assert _doc(maint).status_code == 403  # global → admin only
    assert _doc(maint, other["id"]).status_code == 404
    r = _doc(maint, p["id"])
    assert r.status_code == 201, r.text
    mine = r.json()["id"]
    hidden = _doc(client, other["id"]).json()["id"]
    glob = _doc(client).json()["id"]

    ids = {d["id"] for d in contrib.get("/context-docs").json()}
    assert mine in ids and glob in ids and hidden not in ids
    assert contrib.get(f"/context-docs/{hidden}").status_code == 404
    assert contrib.get(f"/context-docs/{mine}").status_code == 200
    assert contrib.get(f"/context-docs/preview?project_id={other['id']}").status_code == 404
    prev = contrib.get(f"/context-docs/preview?project_id={p['id']}")
    assert prev.status_code == 200 and mine in {d["id"] for d in prev.json()["docs"]}

    assert contrib.patch(f"/context-docs/{mine}", json={"body": "y"}).status_code == 403
    assert contrib.delete(f"/context-docs/{mine}").status_code == 403
    assert maint.patch(f"/context-docs/{hidden}", json={"body": "y"}).status_code == 404
    assert maint.delete(f"/context-docs/{hidden}").status_code == 404
    assert maint.patch(f"/context-docs/{glob}", json={"body": "y"}).status_code == 403
    # Moving a doc needs the rule for both the old and the new place.
    assert maint.patch(f"/context-docs/{mine}", json={"project_id": other["id"]}).status_code == 404
    assert maint.patch(f"/context-docs/{mine}", json={"scope": "global"}).status_code == 403
    r = maint.patch(f"/context-docs/{mine}", json={"body": "y"})
    assert r.status_code == 200 and r.json()["body"] == "y"
    assert maint.delete(f"/context-docs/{mine}").status_code == 204
