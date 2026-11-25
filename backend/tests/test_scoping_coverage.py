"""Every project-bound route enforces membership (Phase 2, Task 7).

Like test_lockdown.py enumerates every route to prove each one needs a login,
this enumerates every route and calls it as an *outsider* (a logged-in member
with no grant on project P). A route bound to P by its path must answer 403 or
404; a list route must answer 200 with none of P's objects in the body. A route
that is legitimately not project-bound is listed in NOT_PROJECT_BOUND with the
reason, so adding a route anywhere makes this test look at it."""

import asyncio
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from starlette.websockets import WebSocketDisconnect

from app.database import SessionLocal
from app.main import app
from app.models import (
    ActionApproval, ActionGrant, DecisionProposal, Finding, McpCall, Task, TaskHandoff, TaskQuestion,
    WorkflowGateRun,
)
from conftest import login_as
from test_lockdown import OPEN_ROUTES, _all_http_routes, _all_ws_routes

# Routes with no project binding. Reasons are what a reviewer should accept.
NOT_PROJECT_BOUND = {
    # Auth/session plumbing about the caller themselves.
    ("GET", "/auth/me"): "the caller's own identity",
    ("POST", "/auth/logout"): "ends the caller's own session (would log the outsider out)",
    ("POST", "/auth/password"): "changes the caller's own password",
    # Admin-managed global catalogs: per the spec everyone sees the full list,
    # and an entry may name P's id as its default project (running it is checked).
    ("GET", "/agents"): "global agent catalog, visible to everyone by spec",
    ("GET", "/templates"): "global template catalog, visible to everyone by spec",
    ("GET", "/workflows"): "global workflow catalog, visible to everyone by spec",
    ("GET", "/workflows/{workflow_id}/export"): "export of a global workflow catalog entry; same visibility as GET /workflows",
    ("GET", "/agents/presets"): "built-in static presets, no stored data",
    ("GET", "/templates/presets"): "built-in static presets, no stored data",
    ("GET", "/workflows/presets"): "built-in static presets, no stored data",
    ("GET", "/skills/presets"): "built-in static presets, no stored data",
    ("POST", "/skills/lint"): "pure text lint of the posted body, reads and stores nothing",
    ("GET", "/skills/template"): "built-in static skill template, no stored data",
    # Directory-style reads that never carry project data.
    ("GET", "/teams"): "team names/members are visible to everyone by spec; teams hold no project data",
    ("GET", "/users/directory"): "id/name/email of active users for the Members picker, by spec",
    ("GET", "/schedules/preview"): "pure cron-expression preview (needs ?cron=), reads no data",
    ("GET", "/system/claude"): "local claude CLI version/status, no project data",
    # Personal credentials: always the caller's own record (no user id anywhere).
    ("GET", "/me/credentials"): "the caller's own credential status, never another user's",
    ("PUT", "/me/credentials"): "writes the caller's own credentials only",
    ("GET", "/plan/status"): "the caller's own Claude plan status, never another user's",
    ("DELETE", "/me/credentials/claude"): "removes the caller's own Claude credential only",
    ("DELETE", "/me/credentials/github"): "removes the caller's own GitHub token only",
    ("POST", "/me/credentials/test"): "probes the caller's own credentials, no project data",
    # Personal preferences (phase 8b): always the caller's own list (no user id anywhere).
    ("GET", "/me/preferences"): "the caller's own preferences, never another user's",
    ("PUT", "/me/preferences"): "writes the caller's own preferences only",
    # co-guard policy (Phase 2): org-wide, readable by every person by spec.
    ("GET", "/guard/builtins"): "the static built-in rule table (ids, reasons), no stored data",
    ("GET", "/guard/org-settings"): "org-wide disabled built-ins and dev DB patterns, no project data",
}

# Admin-only routes with no project binding: the outsider must get 403.
ADMIN_ONLY = {
    ("GET", "/users"),
    ("GET", "/plan/status/all"),
    ("DELETE", "/plan/status/{owner}"),
    ("POST", "/users"),
    ("PATCH", "/users/{user_id}"),
    ("POST", "/users/{user_id}/reset-password"),
    ("POST", "/teams"),
    ("PATCH", "/teams/{team_id}"),
    ("DELETE", "/teams/{team_id}"),
    ("PUT", "/teams/{team_id}/members"),
    ("GET", "/projects/discover"),
    ("GET", "/admin/org-fallback"),
    ("GET", "/admin/health"),
    ("GET", "/admin/health/capacity"),
    ("GET", "/admin/health/retention"),
    ("PATCH", "/admin/health/retention"),
    ("POST", "/admin/health/retention/run"),
    ("PUT", "/admin/org-fallback"),
    ("DELETE", "/admin/org-fallback"),
    ("PUT", "/guard/org-settings"),
}

# Writes that silently skip ids the caller may not decide: 200 with an empty list.
SILENT_SKIP = {("POST", "/approvals/approve")}


def _uid():
    return uuid.uuid4().hex[:8]


def _ok(r, code=201):
    assert r.status_code == code, r.text
    return r.json()


def _add(*rows):
    async def go():
        async with SessionLocal() as s:
            s.add_all(rows)
            await s.commit()
            return [r.id for r in rows]

    return asyncio.run(go())


def _set_task(task_id, **fields):
    async def go():
        async with SessionLocal() as s:
            t = await s.get(Task, task_id)
            for k, v in fields.items():
                setattr(t, k, v)
            await s.commit()

    asyncio.run(go())


@pytest.fixture(scope="module")
def world(client, tmp_path_factory):
    """Project P with one of everything in it, created by the admin; an
    outsider (member, no grants) and a team/user the admin-only routes target."""
    # A new list/aggregate route over a new table needs a P row of that type here.
    tag = _uid()
    p = _ok(client.post("/projects", json={"name": f"cov-{tag}"}))
    pid = p["id"]

    task = _ok(client.post("/tasks", json={
        "prompt": f"cov-task-{tag}", "project_id": pid, "requires_approval": True,
    }))
    ws_dir = tmp_path_factory.mktemp("cov-ws")
    (ws_dir / "out.txt").write_text("secret artifact")
    _set_task(task["id"], workspace_dir=str(ws_dir))

    agent = _ok(client.post("/agents", json={
        "name": f"cov-agent-{tag}", "system_prompt": "x", "default_prompt": "go",
        "requires_approval": True, "project_id": pid,
    }))
    free_agent = _ok(client.post("/agents", json={
        "name": f"cov-free-{tag}", "system_prompt": "x", "default_prompt": "go",
        "requires_approval": True,
    }))
    template = _ok(client.post("/templates", json={
        "name": f"cov-tpl-{tag}", "prompt": "go", "project_id": pid,
    }))
    workflow = _ok(client.post("/workflows", json={
        "name": f"cov-wf-{tag}",
        "stages": [{"name": "S", "items": [{"agent": agent["id"], "prompt": "do {{input}}"}]}],
    }))
    run = _ok(client.post(f"/workflows/{workflow['id']}/run", json={"input": f"cov-run-{tag}"}))
    (gate_run_id,) = _add(WorkflowGateRun(
        run_id=run["id"], stage_index=0, attempt=0, handout="h", command="true", kind="run",
        outcome="passed", log_name=None,
    ))
    schedule = _ok(client.post("/schedules", json={
        "name": f"cov-sch-{tag}", "cron": "0 3 * * *", "prompt": "hi",
        "requires_approval": True, "project_id": pid,
    }))
    doc = _ok(client.post("/context-docs", json={
        "path": f"cov/{tag}.md", "body": "x", "scope": "project", "project_id": pid,
    }))
    pack_dir = tmp_path_factory.mktemp("cov-pack")
    pack = _ok(client.post("/skills", json={
        "name": f"cov-pack-{tag}", "local_path": str(pack_dir), "scope": "project", "project_id": pid,
    }))
    server = _ok(client.post("/mcp/servers", json={
        "name": f"cov-mcp-{tag}", "command": "true", "scope": "project", "project": p["name"],
    }))
    finding_title = f"cov-finding-{tag}"
    (finding_id,) = _add(Finding(
        project_id=pid, fingerprint=uuid.uuid4().hex, severity="high", title=finding_title,
    ))
    # A completed, costed P task (inserted finished, so the worker never picks it
    # up) plus an MCP call from it, so aggregates have P data they could leak.
    done_id = uuid.uuid4().hex
    _add(Task(
        id=done_id, title=f"cov-done-{tag}", prompt=f"cov-done-{tag}", project=p["name"],
        project_id=pid, status="completed", total_cost_usd=2.5, input_tokens=100,
        output_tokens=50, model_used=f"cov-model-{tag}", completed_at=datetime.now(timezone.utc),
    ))
    _add(McpCall(task_id=done_id, server=f"cov-mcp-{tag}", tool="t"))
    # an open question on that P task: GET /questions must not list it, answer/dismiss must refuse
    (question_id,) = _add(TaskQuestion(
        task_id=done_id, root_id=done_id, project_id=pid, question=f"cov-question-{tag}?", source="agent",
    ))
    # an open handoff with an open todo on that P thread: GET /handoffs/open must not list it
    _add(TaskHandoff(root_id=done_id, task_id=done_id, status="open", content={
        "step_task_id": done_id, "step": {"source": "notes", "n": 1, "m": 2, "text": f"cov-step-{tag}"}}))
    # A running P task with a pending co-guard action request (owned by the
    # admin, so it is decidable by P's maintainers and listed for them).
    running_id = uuid.uuid4().hex
    _add(Task(id=running_id, title=f"cov-running-{tag}", prompt="x", project=p["name"], project_id=pid,
              root_id=running_id, status="running"))
    now = datetime.now(timezone.utc)
    (approval_id,) = _add(ActionApproval(
        task_id=running_id, project_id=pid, rule_ids=["git-reset-hard"], tool="Bash",
        summary=f"cov-action-{tag}", status="pending", created_at=now, expires_at=now + timedelta(hours=1),
    ))
    (grant_id,) = _add(ActionGrant(
        root_id=running_id, task_id=running_id, approval_id=approval_id, scope="run", rule_id="git-reset-hard",
        key_kind="command", key="a" * 64, created_at=now, expires_at=now + timedelta(hours=1),
    ))
    (proposal_id,) = _add(DecisionProposal(
        project_id=pid, source="person", title=f"cov-proposal-{tag}", evidence="", status="pending"))
    team = _ok(client.post("/teams", json={"name": f"cov-team-{tag}"}))
    # A P custom rule: GET /guard/rules (org list, or ?project_id=P) must not leak it.
    rule = _ok(client.post("/guard/rules", json={
        "scope": "project", "project_id": pid, "name": f"cov-rule-{tag}", "pattern": f"cov-pattern-{tag}",
        "reason": f"cov-reason-{tag}",
    }))

    outsider = login_as(f"cov-out-{tag}@test.local")
    ids = {
        "project_id": pid,
        "task_id": task["id"],
        "schedule_id": schedule["id"],
        "finding_id": str(finding_id),
        "doc_id": doc["id"],
        "pack_id": pack["id"],
        "server_id": server["id"],
        "run_id": run["id"],
        "i": "0",
        "rid": gate_run_id,
        "workflow_id": workflow["id"],
        "agent_id": agent["id"],
        "template_id": template["id"],
        "filename": "out.txt",
        "user_id": outsider.user_id,
        "owner": outsider.user_id,
        "team_id": team["id"],
        "approval_id": approval_id,
        "rule_id": rule["id"],
        "grant_id": grant_id,
        "root_id": running_id,
        "question_id": question_id,
        "proposal_id": proposal_id,
        "number": "1",
    }
    # Strings that must never appear in a list the outsider gets back. Agents,
    # templates and workflows are global by spec, so their ids aren't secret.
    secrets = {
        pid, p["name"], task["id"], f"cov-task-{tag}", schedule["id"], f"cov-sch-{tag}",
        finding_title, doc["id"], f"cov/{tag}.md", pack["id"], f"cov-pack-{tag}",
        server["id"], f"cov-mcp-{tag}", run["id"], f"cov-run-{tag}",
        done_id, f"cov-done-{tag}", f"cov-model-{tag}",
        running_id, f"cov-running-{tag}", approval_id, f"cov-action-{tag}",
        rule["id"], f"cov-pattern-{tag}", f"cov-reason-{tag}", grant_id, question_id, f"cov-question-{tag}?",
        f"cov-step-{tag}", proposal_id, f"cov-proposal-{tag}",
    }
    return {
        "done_id": done_id,
        "ids": ids, "secrets": secrets, "outsider": outsider, "project": p,
        "free_agent": free_agent, "task": task,
    }


def _fill(path: str, ids: dict) -> str:
    return re.sub(r"\{([^}:]+)(?::[^}]*)?\}", lambda m: ids[m.group(1)], path)


def _bodies(world) -> dict:
    """A valid body per write route, pointing at P wherever the body can, so a
    422 never masks a missing check."""
    i, p = world["ids"], world["project"]
    item = [{"agent": world["ids"]["agent_id"], "prompt": "go"}]
    return {
        ("POST", "/tasks"): {"prompt": "x", "project_id": i["project_id"], "requires_approval": True},
        ("POST", "/tasks/{task_id}/questions"): {"question": "x?"},
        ("POST", "/questions/{question_id}/answer"): {"answer": "x"},
        ("POST", "/tasks/{task_id}/followup"): {"prompt": "x"},
        ("PATCH", "/tasks/{task_id}/launch-item"): {"launch_item_id": None},
        ("POST", "/projects"): {"name": f"cov-new-{_uid()}"},
        ("POST", "/projects/import"): {"directories": ["/tmp"]},
        ("PATCH", "/projects/{project_id}"): {"name": f"cov-hijack-{_uid()}"},
        ("PUT", "/projects/{project_id}/members"): [
            {"subject_type": "user", "subject_id": i["user_id"], "role": "maintainer"}
        ],
        ("PUT", "/projects/{project_id}/servers"): [],
        ("POST", "/templates"): {"name": f"cov-t-{_uid()}", "prompt": "x", "project_id": i["project_id"]},
        ("POST", "/schedules"): {"name": "x", "cron": "0 3 * * *", "prompt": "x",
                                 "project_id": i["project_id"]},
        ("PATCH", "/schedules/{schedule_id}"): {"name": "hijack"},
        ("POST", "/projects/{project_id}/decisions"): {"title": "hijack"},
        ("PATCH", "/projects/{project_id}/decisions/{number}"): {"version": 1, "title": "hijack"},
        ("POST", "/projects/{project_id}/decisions/{number}/supersede"): {"by": 2},
        ("POST", "/projects/{project_id}/decisions/proposals"): {"title": "hijack"},
        ("POST", "/projects/{project_id}/decisions/proposals/{proposal_id}/accept"): {"kind": "decision",
                                                                                      "title": "hijack"},
        ("POST", "/tasks/{task_id}/decision-proposals"): {"title": "hijack"},
        ("POST", "/approvals/{task_id}/reject"): {},
        ("POST", "/workflows/runs/{run_id}/stages/{i}/reject"): {"reason": "x"},
        ("POST", "/workflows/runs/{run_id}/budget"): {"budget_usd": 5.0},
        # co-guard: filing/reporting name P's task in the body (agent tokens only).
        ("POST", "/guard/approvals"): {"task_id": i["task_id"], "rule_ids": ["git-reset-hard"], "tool": "Bash"},
        ("POST", "/guard/report"): {"task_id": i["task_id"], "event": "PreToolUse", "decision": "deny"},
        ("POST", "/guard/approvals/{approval_id}/reject"): {"reason": "x"},
        ("POST", "/guard/rules"): {"scope": "project", "project_id": i["project_id"], "pattern": "x"},
        ("PATCH", "/guard/rules/{rule_id}"): {"enabled": False, "pattern": "hijack"},
        ("POST", "/approvals/approve"): {"task_ids": [i["task_id"]]},
        ("PATCH", "/findings/{finding_id}"): {"status": "accepted"},
        ("POST", "/agents"): {"name": f"cov-a-{_uid()}", "system_prompt": "x", "default_prompt": "x",
                              "project_id": i["project_id"]},
        ("PATCH", "/agents/{agent_id}"): {"description": "hijack"},
        ("POST", "/agents/{agent_id}/run"): {},
        ("POST", "/agents/dispatch"): {"parent_task_id": i["task_id"], "items": item},
        ("POST", "/agents/{agent_id}/bulk"): {"prompts": ["x"]},
        ("POST", "/workflows"): {"name": f"cov-w-{_uid()}", "stages": [{"name": "S", "items": item}]},
        ("PATCH", "/workflows/{workflow_id}"): {"description": "hijack"},
        ("POST", "/workflows/{workflow_id}/run"): {"input": "x"},
        ("POST", "/skills"): {"name": f"cov-s-{_uid()}", "local_path": "/tmp", "scope": "project",
                              "project_id": i["project_id"]},
        ("PATCH", "/skills/{pack_id}"): {"enabled": False},
        ("POST", "/context-docs"): {"path": f"cov/{_uid()}.md", "scope": "project",
                                    "project_id": i["project_id"]},
        ("PATCH", "/context-docs/{doc_id}"): {"body": "hijack"},
        ("POST", "/context-docs/templates/brand"): {"project_id": i["project_id"]},
        ("POST", "/mcp/servers"): {"name": f"cov-m-{_uid()}", "command": "true", "scope": "project",
                                   "project": p["name"]},
        ("PATCH", "/mcp/servers/{server_id}"): {"enabled": False},
        ("PUT", "/mcp/servers/{server_id}/policies"): [],
        ("POST", "/users"): {"email": f"cov-{_uid()}@test.local", "name": "x", "role": "admin",
                             "password": "cov-password-123456"},
        ("PATCH", "/users/{user_id}"): {"role": "admin"},
        ("POST", "/users/{user_id}/reset-password"): {"password": "cov-password-123456"},
        ("POST", "/teams"): {"name": f"cov-t-{_uid()}"},
        ("PATCH", "/teams/{team_id}"): {"name": f"cov-t-{_uid()}"},
        ("PUT", "/teams/{team_id}/members"): {"user_ids": [i["user_id"]]},
    }


def out_404(r) -> bool:
    return r.status_code == 404


def _leaks(text: str, secrets: set[str]) -> set[str]:
    return {s for s in secrets if s in text}


def test_not_project_bound_is_exact():
    routes = set(_all_http_routes(app))
    for listed in (NOT_PROJECT_BOUND.keys(), ADMIN_ONLY, SILENT_SKIP):
        stale = set(listed) - routes
        assert not stale, f"no such route: {stale}"
    assert all(len(reason) > 10 for reason in NOT_PROJECT_BOUND.values())
    assert not (set(NOT_PROJECT_BOUND) & ADMIN_ONLY)
    assert not (set(NOT_PROJECT_BOUND) & OPEN_ROUTES)


def test_every_project_bound_route_enforces_membership(client, world):
    out, ids, secrets = world["outsider"], world["ids"], world["secrets"]
    bodies = _bodies(world)
    failures, checked = [], 0
    for method, path in _all_http_routes(app):
        key = (method, path)
        if key in OPEN_ROUTES or key in NOT_PROJECT_BOUND:
            continue
        checked += 1
        url = _fill(path, ids)
        kw = {}
        if key in bodies:
            kw["json"] = bodies[key]
        elif method in ("POST", "PUT", "PATCH"):
            kw["json"] = {}
        if key == ("GET", "/workflows/repo-files"):
            kw["params"] = {"project_id": ids["project_id"]}
        r = out.request(method, url, **kw)
        if key in ADMIN_ONLY:
            ok = r.status_code == 403
        elif key in SILENT_SKIP:
            ok = r.status_code == 200 and r.json() == []
        elif "{" in path or method != "GET" or key == ("GET", "/workflows/repo-files"):
            ok = r.status_code in (403, 404)
        else:
            # A list: the plain call and one filtered to P must both come
            # back without P's objects (the filtered one may also refuse).
            leaked = _leaks(r.text, secrets)
            ok = r.status_code == 200 and not leaked
            if ok:
                f = out.get(url, params={"project_id": ids["project_id"], "project": world["project"]["name"]})
                leaked = _leaks(f.text, secrets)
                ok = (f.status_code == 200 and not leaked) or f.status_code in (403, 404)
                r = f
            if leaked:
                failures.append((method, path, r.status_code, f"leaks {sorted(leaked)}"))
                continue
        if not ok:
            failures.append((method, path, r.status_code, r.text[:200]))
    assert not failures, "\n".join(map(str, failures))
    assert checked > 80

    # Nothing the outsider called changed P's objects.
    t = client.get(f"/tasks/{ids['task_id']}").json()
    assert t["status"] == "awaiting_approval" and t["project_id"] == ids["project_id"]
    assert client.get(f"/projects/{ids['project_id']}").json()["name"] == world["project"]["name"]
    assert client.get(f"/projects/{ids['project_id']}/members").json() == []
    assert client.get(f"/schedules/{ids['schedule_id']}").status_code == 200
    assert client.get(f"/context-docs/{ids['doc_id']}").json()["body"] == "x"
    assert client.get(f"/mcp/servers/{ids['server_id']}").json()["enabled"] is True
    assert any(k["id"] == ids["pack_id"] and k["enabled"] for k in client.get("/skills").json())
    finding = next(f for f in client.get(f"/findings?project_id={ids['project_id']}").json())
    assert finding["status"] == "open"
    me = next(u for u in client.get("/users").json() if u["id"] == ids["user_id"])
    assert me["role"] == "member"
    act = client.get(f"/guard/approvals/{ids['approval_id']}").json()
    assert act["status"] == "pending" and act["decided_by"] is None
    grants = client.get("/guard/grants", params={"root_id": ids["root_id"]}).json()
    assert [(g["id"], g["revoked_at"]) for g in grants] == [(ids["grant_id"], None)]
    assert out_404(world["outsider"].get("/guard/grants", params={"root_id": ids["root_id"]}))
    rules = client.get("/guard/rules", params={"project_id": ids["project_id"]}).json()
    assert [(r["id"], r["enabled"], r["pattern"].startswith("cov-pattern-")) for r in rules] == [
        (ids["rule_id"], True, True)]


def test_body_borne_project_refs_refused(client, world):
    """P named only in the body of a call on a non-P object is refused too."""
    out, i, free = world["outsider"], world["ids"], world["free_agent"]
    pid, name = i["project_id"], world["project"]["name"]
    # `project` (a name) is not a way in: the task ignores it and stays ad-hoc.
    r = out.post("/tasks", json={"prompt": "x", "project": name, "requires_approval": True})
    assert r.status_code in (403, 404) or (
        r.status_code == 201 and r.json()["project_id"] is None and r.json()["project"] is None
    ), r.text
    calls = [
        (f"/agents/{free['id']}/run", {"project_id": pid}),
        (f"/agents/{free['id']}/run", {"parent_task_id": i["task_id"]}),
        (f"/agents/{free['id']}/bulk", {"prompts": ["x"], "project_id": pid}),
        ("/agents/dispatch", {"project_id": pid, "items": [{"agent": free["id"], "prompt": "x"}]}),
        ("/schedules", {"name": "x", "cron": "0 3 * * *", "agent_id": i["agent_id"]}),
        # Template runs take no override; a body trying to unbind P's template is ignored.
        (f"/templates/{i['template_id']}/run", {"project_id": None}),
    ]
    for url, body in calls:
        r = out.post(url, json=body)
        assert r.status_code in (403, 404), (url, body, r.status_code, r.text)


def _ws_close_code(c, url, timeout=10.0):
    """Close code of a WebSocket the server should refuse. Fails (instead of
    hanging) if the server accepts and then sends nothing."""
    got = {}

    def go():
        try:
            with c.websocket_connect(url) as ws:
                ws.receive_json()
            got["code"] = "accepted"
        except WebSocketDisconnect as e:
            got["code"] = e.code

    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(timeout)
    assert not t.is_alive(), f"{url}: accepted and silent"
    return got.get("code")


def test_open_handoffs_list_p_thread_for_its_maintainer_only(client, world):
    """The P handoff is a real candidate (a P maintainer sees it), so the walk's outsider check means something.
    A fresh maintainer, not the admin: the admin's list is the whole suite's newest 10."""
    pid = world["ids"]["project_id"]
    boss = login_as(f"cov-boss-{_uid()}@test.local")
    keep = [{"subject_type": m["subject_type"], "subject_id": m["subject_id"], "role": m["role"]}
            for m in client.get(f"/projects/{pid}/members").json()]
    try:
        r = client.put(f"/projects/{pid}/members", json=keep + [
            {"subject_type": "user", "subject_id": boss.user_id, "role": "maintainer"}])
        assert r.status_code == 200, r.text
        assert world["done_id"] in [r["root_id"] for r in boss.get("/handoffs/open").json()]
        assert world["done_id"] not in world["outsider"].get("/handoffs/open").text
    finally:
        client.put(f"/projects/{pid}/members", json=keep)


def test_outsider_ws_on_project_task_is_4404(world):
    for path in _all_ws_routes(app):
        assert _ws_close_code(world["outsider"], _fill(path, world["ids"])) == 4404, path


def test_aggregates_exclude_project_for_zero_grants(world):
    """Counts and totals carry no ids, so check them directly: a user with no
    grants and no tasks of their own sees zeros, though P has a costed task,
    a finding and an MCP call."""
    z = login_as(f"cov-zero-{_uid()}@test.local")
    st = z.get("/tasks/stats").json()
    assert st["by_status"] == {}, st
    assert st["tasks_today"] == st["running"] == st["queued"] == st["completed_today"] == 0, st
    s = z.get("/usage/summary").json()
    for window in ("today", "last_7d", "last_30d", "all_time"):
        assert s[window]["tasks"] == 0 and s[window]["cost_usd"] == 0, (window, s)
    assert z.get("/findings/summary").json()["total"] == 0
    assert z.get("/mcp/observability").json()["total_calls"] == 0
    assert z.get("/usage/timeseries").json() == []
    assert z.get("/usage/by-user").json() == []
    assert z.get("/usage/by-project").json() == []
    assert z.get("/usage/by-model").json() == []
