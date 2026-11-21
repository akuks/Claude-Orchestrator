"""Phase 2 Task 4: the policy settings APIs - custom rules, built-ins, org
guard settings, the repo rules file, project trust/readonly/reference fields,
create/import trust suggestions and MCP environment/default action."""

import asyncio
import os
import subprocess
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.database import SessionLocal
from app.guard import custom, rules
from app.main import app
from app.models import ActionRule, Task
from conftest import login_as


def _uid():
    return uuid.uuid4().hex[:8]


def _run(coro):
    return asyncio.run(coro)


def _agent_client(user_id):
    tid = uuid.uuid4().hex

    async def go():
        async with SessionLocal() as s:
            s.add(Task(id=tid, title="t", prompt="p", root_id=tid, status="running", created_by=user_id))
            await s.flush()
            raw = await auth.mint_token(s, user_id, "agent-task", tid, 3600)
            await s.commit()
            return raw

    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {_run(go())}"
    return c


def _git(d, *args, email="someone@else.example"):
    env = dict(os.environ, GIT_AUTHOR_NAME="x", GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_NAME="x",
               GIT_COMMITTER_EMAIL=email, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
    subprocess.run(["git", "-C", str(d), *args], check=True, capture_output=True, env=env)


def _repo(d: Path, *, remote=True, author="someone@else.example"):
    d.mkdir(parents=True, exist_ok=True)
    _git(d, "init", "-q")
    (d / "f.txt").write_text("x")
    _git(d, "add", "f.txt")
    _git(d, "commit", "-q", "-m", "c", email=author)
    if remote:
        _git(d, "remote", "add", "origin", "https://example.invalid/r.git")
    return d


@pytest.fixture(scope="module")
def world(client):
    """Project P: maintainer M, contributor C; outsider X; agent token A."""
    tag = _uid()
    p = client.post("/projects", json={"name": f"gp-{tag}"}).json()
    q = client.post("/projects", json={"name": f"gq-{tag}"}).json()  # M sees Q; C does not
    m = login_as(f"gp-maint-{tag}@test.local")
    c = login_as(f"gp-contrib-{tag}@test.local")
    x = login_as(f"gp-out-{tag}@test.local")
    for pid, members in ((p["id"], [(m, "maintainer"), (c, "contributor")]), (q["id"], [(m, "contributor")])):
        r = client.put(f"/projects/{pid}/members", json=[
            {"subject_type": "user", "subject_id": u.user_id, "role": role} for u, role in members])
        assert r.status_code == 200, r.text
    return {"p": p, "q": q, "m": m, "c": c, "x": x, "admin": client, "agent": _agent_client(m.user_id)}


def _rule(**over):
    body = {"scope": "org", "kind": "confirm", "match": "text", "pattern": f"deploy-{_uid()}",
            "reason": "deploys need a person"}
    body.update(over)
    return body


# ---- custom rules ------------------------------------------------------------------------


def test_org_rule_crud_by_admin(world):
    a = world["admin"]
    r = a.post("/guard/rules", json=_rule(name="Prod Deploy!"))
    assert r.status_code == 201, r.text
    rule = r.json()
    assert rule["scope"] == "org" and rule["project_id"] is None
    assert rule["slug"].startswith("prod-deploy") and rule["rule_id"] == f"org.{rule['slug']}"
    assert rule["enabled"] is True and rule["created_by"]
    assert any(x["id"] == rule["id"] for x in a.get("/guard/rules").json())
    r = a.patch(f"/guard/rules/{rule['id']}", json={"enabled": False, "kind": "never", "reason": "no"})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False and r.json()["kind"] == "never" and r.json()["slug"] == rule["slug"]
    assert a.delete(f"/guard/rules/{rule['id']}").status_code == 204
    assert a.patch(f"/guard/rules/{rule['id']}", json={"enabled": True}).status_code == 404


def test_org_rules_people_read_admins_write(world):
    a, m, c, x, agent = world["admin"], world["m"], world["c"], world["x"], world["agent"]
    rule = a.post("/guard/rules", json=_rule()).json()
    for person in (m, c, x):
        assert any(r["id"] == rule["id"] for r in person.get("/guard/rules").json())
        assert person.post("/guard/rules", json=_rule()).status_code == 403
        assert person.patch(f"/guard/rules/{rule['id']}", json={"enabled": False}).status_code == 403
        assert person.delete(f"/guard/rules/{rule['id']}").status_code == 403
    assert agent.get("/guard/rules").status_code == 403
    assert agent.post("/guard/rules", json=_rule()).status_code == 403
    assert agent.patch(f"/guard/rules/{rule['id']}", json={"enabled": False}).status_code == 403
    assert agent.delete(f"/guard/rules/{rule['id']}").status_code == 403
    assert a.get(f"/guard/rules").status_code == 200
    assert next(r for r in a.get("/guard/rules").json() if r["id"] == rule["id"])["enabled"] is True


def test_project_rules_permission_matrix(world):
    a, m, c, x, agent = world["admin"], world["m"], world["c"], world["x"], world["agent"]
    pid = world["p"]["id"]
    body = _rule(scope="project", project_id=pid)
    # maintainer and admin write; contributor 403; outsider 404; agent token 403
    r = m.post("/guard/rules", json=body)
    assert r.status_code == 201, r.text
    rule = r.json()
    assert rule["rule_id"].startswith("project.") and rule["project_id"] == pid
    assert a.post("/guard/rules", json=_rule(scope="project", project_id=pid)).status_code == 201
    assert c.post("/guard/rules", json=_rule(scope="project", project_id=pid)).status_code == 403
    assert x.post("/guard/rules", json=_rule(scope="project", project_id=pid)).status_code == 404
    assert agent.post("/guard/rules", json=_rule(scope="project", project_id=pid)).status_code == 403
    # reads: visible project
    for person in (a, m, c):
        got = person.get("/guard/rules", params={"project_id": pid})
        assert got.status_code == 200 and any(r["id"] == rule["id"] for r in got.json())
    assert x.get("/guard/rules", params={"project_id": pid}).status_code == 404
    assert agent.get("/guard/rules", params={"project_id": pid}).status_code == 403
    # the org list never carries project rules
    assert not any(r["id"] == rule["id"] for r in x.get("/guard/rules").json())
    # patch / delete
    assert c.patch(f"/guard/rules/{rule['id']}", json={"enabled": False}).status_code == 403
    assert x.patch(f"/guard/rules/{rule['id']}", json={"enabled": False}).status_code == 404
    assert agent.patch(f"/guard/rules/{rule['id']}", json={"enabled": False}).status_code == 403
    assert c.delete(f"/guard/rules/{rule['id']}").status_code == 403
    assert x.delete(f"/guard/rules/{rule['id']}").status_code == 404
    r = m.patch(f"/guard/rules/{rule['id']}", json={"pattern": "rm -rf build", "enabled": False})
    assert r.status_code == 200 and r.json()["pattern"] == "rm -rf build"
    assert m.delete(f"/guard/rules/{rule['id']}").status_code == 204


def test_rule_scope_and_project_checks(world):
    a, pid = world["admin"], world["p"]["id"]
    assert a.post("/guard/rules", json=_rule(scope="repo")).status_code == 400
    assert a.post("/guard/rules", json=_rule(scope="project")).status_code == 400  # no project
    assert a.post("/guard/rules", json=_rule(scope="org", project_id=pid)).status_code == 400
    assert a.post("/guard/rules", json=_rule(scope="project", project_id="nope")).status_code == 404
    assert a.post("/guard/rules", json=_rule(kind="allow")).status_code == 400
    assert a.post("/guard/rules", json=_rule(match="glob")).status_code == 400
    assert a.get("/guard/rules", params={"project_id": "nope"}).status_code == 404


def test_rule_pattern_validation(world):
    a = world["admin"]
    r = a.post("/guard/rules", json=_rule(match="regex", pattern="(unclosed"))
    assert r.status_code == 400 and r.json()["detail"].startswith("invalid regex: ")
    assert a.post("/guard/rules", json=_rule(pattern="x" * 501)).status_code == 400
    assert a.post("/guard/rules", json=_rule(pattern="x" * 500)).status_code == 201
    assert a.post("/guard/rules", json=_rule(match="regex", pattern="a" * 500)).status_code == 201
    assert a.post("/guard/rules", json=_rule(pattern="   ")).status_code == 400
    assert a.post("/guard/rules", json=_rule(pattern="rm​ -rf")).status_code == 400
    ok = a.post("/guard/rules", json=_rule(match="regex", pattern=r"^git\s+push\b")).json()
    r = a.patch(f"/guard/rules/{ok['id']}", json={"pattern": "[bad"})
    assert r.status_code == 400 and r.json()["detail"].startswith("invalid regex: ")
    # switching a text rule to regex validates the stored pattern as a regex
    t = a.post("/guard/rules", json=_rule(pattern="a(b")).json()
    assert a.patch(f"/guard/rules/{t['id']}", json={"match": "regex"}).status_code == 400


def test_rule_reason_is_cleaned_and_cut(world):
    a = world["admin"]
    r = a.post("/guard/rules", json=_rule(reason="line1\nline2‮​  \x07end"))
    assert r.status_code == 201 and r.json()["reason"] == "line1 line2 end"
    long = a.post("/guard/rules", json=_rule(reason="y" * 300)).json()
    assert long["reason"] == "y" * custom.REASON_MAX


def test_slug_collisions_within_a_scope(world):
    a, m, pid, qid = world["admin"], world["m"], world["p"]["id"], world["q"]["id"]
    name = f"Collide {_uid()}"
    s1 = a.post("/guard/rules", json=_rule(name=name)).json()["slug"]
    s2 = a.post("/guard/rules", json=_rule(name=name)).json()["slug"]
    s3 = a.post("/guard/rules", json=_rule(name=name)).json()["slug"]
    assert s2 == f"{s1}-2" and s3 == f"{s1}-3"
    # another scope / project does not collide
    p1 = a.post("/guard/rules", json=_rule(name=name, scope="project", project_id=pid)).json()["slug"]
    q1 = a.post("/guard/rules", json=_rule(name=name, scope="project", project_id=qid)).json()["slug"]
    assert p1 == s1 and q1 == s1
    # unnamed rules take their slug from the reason, else the pattern
    assert a.post("/guard/rules", json=_rule(reason="", pattern="!!!")).json()["slug"].startswith("rule")
    long = a.post("/guard/rules", json=_rule(name="n" * 80)).json()["slug"]
    assert len(long) <= custom.SLUG_MAX
    assert "." not in s1


def test_rules_cap_per_scope(world, monkeypatch):
    from app.routers import guard as guard_router

    a, pid = world["admin"], world["p"]["id"]
    have = len(a.get("/guard/rules", params={"project_id": pid}).json())
    monkeypatch.setattr(guard_router, "RULES_PER_SCOPE", have + 1)
    assert a.post("/guard/rules", json=_rule(scope="project", project_id=pid)).status_code == 201
    r = a.post("/guard/rules", json=_rule(scope="project", project_id=pid))
    assert r.status_code == 409
    # the org scope has its own count
    org_have = len(a.get("/guard/rules").json())
    monkeypatch.setattr(guard_router, "RULES_PER_SCOPE", org_have)
    assert a.post("/guard/rules", json=_rule()).status_code == 409


def test_rules_cap_constant_is_the_global_one():
    from app.routers import guard as guard_router

    assert guard_router.RULES_PER_SCOPE == custom.RULES_PER_SCOPE == 200


# ---- built-ins and org settings -------------------------------------------------------------


def test_builtins(world):
    got = world["c"].get("/guard/builtins")
    assert got.status_code == 200
    rows = {r["id"]: r for r in got.json()}
    assert set(rows) == set(rules.RULES)
    for rid, (reason, hard) in rules.RULES.items():
        assert rows[rid] == {"id": rid, "reason": reason, "hard_reason": hard,
                             "disableable": rid in rules.DISABLEABLE}
    assert world["agent"].get("/guard/builtins").status_code == 403


def test_org_settings_admin_writes(world):
    a, m = world["admin"], world["m"]
    before = a.get("/guard/org-settings").json()
    try:
        r = a.put("/guard/org-settings", json={
            "disabled_rules": ["git-reset-hard", "db-write", "git-reset-hard"],
            "dev_db_patterns": [" host:*.dev.internal ", "mcp:dev-*", "staging-db"],
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["disabled_rules"] == ["git-reset-hard", "db-write"]
        assert body["dev_db_patterns"] == ["host:*.dev.internal", "mcp:dev-*", "staging-db"]
        assert body["updated_by"]
        assert m.get("/guard/org-settings").json()["disabled_rules"] == ["git-reset-hard", "db-write"]
        # omitted fields stay
        r = a.put("/guard/org-settings", json={"disabled_rules": []})
        assert r.json()["disabled_rules"] == [] and r.json()["dev_db_patterns"][0] == "host:*.dev.internal"
        assert m.put("/guard/org-settings", json={"disabled_rules": []}).status_code == 403
        assert world["agent"].get("/guard/org-settings").status_code == 403
        assert world["agent"].put("/guard/org-settings", json={}).status_code == 403
    finally:
        a.put("/guard/org-settings", json={"disabled_rules": before["disabled_rules"],
                                            "dev_db_patterns": before["dev_db_patterns"]})


@pytest.mark.parametrize("rid", ["self-protect", "agent-config", "readonly-source", "guest-quarantine",
                                 "mcp-block", "unparseable", "org.x", "nope"])
def test_org_settings_reject_locked_rules(world, rid):
    r = world["admin"].put("/guard/org-settings", json={"disabled_rules": [rid]})
    assert r.status_code == 400 and rid in r.json()["detail"]


@pytest.mark.parametrize("pat", ["*", "host:*", "mcp:*", "db*", "mcp:a*b*", "", 42])
def test_org_settings_reject_bad_dev_patterns(world, pat):
    r = world["admin"].put("/guard/org-settings", json={"dev_db_patterns": [pat]})
    assert r.status_code in (400, 422), r.text


# ---- project fields ---------------------------------------------------------------------------


def test_project_defaults_and_maintainer_fields(world):
    a, m, c, pid = world["admin"], world["m"], world["c"], world["p"]["id"]
    got = a.get(f"/projects/{pid}").json()
    assert got["trust"] == "own" and got["quarantine"] is False and got["on_no_answer"] == "deny"
    assert got["readonly_paths"] == [] and got["reference_project_ids"] == []
    r = m.patch(f"/projects/{pid}", json={
        "trust": "external", "quarantine": True, "on_no_answer": "deny_and_resume",
        "readonly_paths": [{"path": "legacy", "reason": "old code​"}, {"path": "/opt/ref"}],
        "reference_project_ids": [world["q"]["id"]],
    })
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["trust"] == "external" and b["quarantine"] is True and b["on_no_answer"] == "deny_and_resume"
    assert b["readonly_paths"] == [{"path": "legacy", "reason": "old code"}, {"path": "/opt/ref", "reason": ""}]
    assert b["reference_project_ids"] == [world["q"]["id"]]
    assert c.patch(f"/projects/{pid}", json={"trust": "own"}).status_code == 403
    # null leaves a field alone
    r = m.patch(f"/projects/{pid}", json={"trust": None, "readonly_paths": None, "quarantine": None})
    assert r.status_code == 200 and r.json()["trust"] == "external" and len(r.json()["readonly_paths"]) == 2
    r = m.patch(f"/projects/{pid}", json={"trust": "own", "quarantine": False, "on_no_answer": "deny",
                                          "readonly_paths": [], "reference_project_ids": []})
    assert r.status_code == 200 and r.json()["readonly_paths"] == []


@pytest.mark.parametrize("field,value", [
    ("trust", "quarantined"), ("on_no_answer", "resume"), ("trust", ""),
])
def test_project_enum_validation(world, field, value):
    assert world["m"].patch(f"/projects/{world['p']['id']}", json={field: value}).status_code == 400


@pytest.mark.parametrize("path", [
    "/", "//", "/..", str(Path.home()), str(Path.home().parent), "~", "~/code", "a\x00b", "", "  ",
    "/usr/..",
])
def test_readonly_path_validation(world, path):
    r = world["m"].patch(f"/projects/{world['p']['id']}", json={"readonly_paths": [{"path": path}]})
    assert r.status_code in (400, 422), (path, r.status_code, r.text)


def test_readonly_path_relative_resolves_against_the_project(world, tmp_path, monkeypatch):
    a = world["admin"]
    home = tmp_path / "home"
    d = home / "proj"
    d.mkdir(parents=True)
    p = a.post("/projects", json={"name": f"gr-{_uid()}", "directory": str(d)}).json()
    monkeypatch.setenv("HOME", str(home))
    assert Path.home() == home
    # "../" from the project folder is home itself; "../.." is above it
    assert a.patch(f"/projects/{p['id']}", json={"readonly_paths": [{"path": ".."}]}).status_code == 400
    assert a.patch(f"/projects/{p['id']}", json={"readonly_paths": [{"path": "../.."}]}).status_code == 400
    # the project folder itself (and so a folder above it) would block every change
    assert a.patch(f"/projects/{p['id']}", json={"readonly_paths": [{"path": "."}]}).status_code == 400
    r = a.patch(f"/projects/{p['id']}", json={"readonly_paths": [{"path": "vendor"}, {"path": "../other"}]})
    assert r.status_code == 200 and [e["path"] for e in r.json()["readonly_paths"]] == ["vendor", "../other"]
    # a symlink to / is caught by its real path
    (d / "up").symlink_to("/")
    assert a.patch(f"/projects/{p['id']}", json={"readonly_paths": [{"path": "up"}]}).status_code == 400


def test_readonly_paths_cap(world):
    m, pid = world["m"], world["p"]["id"]
    ok = [{"path": f"/opt/ref{i}"} for i in range(50)]
    assert m.patch(f"/projects/{pid}", json={"readonly_paths": ok}).status_code == 200
    r = m.patch(f"/projects/{pid}", json={"readonly_paths": ok + [{"path": "/opt/ref50"}]})
    assert r.status_code == 400
    assert m.patch(f"/projects/{pid}", json={"readonly_paths": []}).status_code == 200


def test_reference_projects_validation(world, client):
    a, m, c = world["admin"], world["m"], world["c"]
    pid, qid = world["p"]["id"], world["q"]["id"]
    hidden = a.post("/projects", json={"name": f"gh-{_uid()}"}).json()["id"]  # M cannot see it
    assert m.patch(f"/projects/{pid}", json={"reference_project_ids": [pid]}).status_code == 400
    assert m.patch(f"/projects/{pid}", json={"reference_project_ids": ["nope"]}).status_code == 400
    r = m.patch(f"/projects/{pid}", json={"reference_project_ids": [hidden]})
    unknown = m.patch(f"/projects/{pid}", json={"reference_project_ids": ["nope"]})
    assert r.status_code == 400  # same answer as an unknown id: existence is not revealed
    assert r.json()["detail"].replace(hidden, "ID") == unknown.json()["detail"].replace("nope", "ID")
    # a contributor of Q who maintains P may reference Q
    assert m.patch(f"/projects/{pid}", json={"reference_project_ids": [qid]}).status_code == 200
    # the admin sees every project
    assert a.patch(f"/projects/{pid}", json={"reference_project_ids": [hidden, qid, qid]}).json()[
        "reference_project_ids"] == [hidden, qid]
    many = [a.post("/projects", json={"name": f"gm-{_uid()}"}).json()["id"] for _ in range(21)]
    assert a.patch(f"/projects/{pid}", json={"reference_project_ids": many[:20]}).status_code == 200
    assert a.patch(f"/projects/{pid}", json={"reference_project_ids": many}).status_code == 400
    assert a.patch(f"/projects/{pid}", json={"reference_project_ids": []}).status_code == 200


# ---- repo rules file --------------------------------------------------------------------------


def test_rules_file_endpoint(world, tmp_path):
    a, c, x = world["admin"], world["c"], world["x"]
    pid = world["p"]["id"]
    directory = Path(a.get(f"/projects/{pid}").json()["directory"])
    rd = directory / ".claude-orchestrator"
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "rules.md").write_text("## Confirm\n- `terraform apply` : infra\n## Settings\n- disable: git-reset-hard\n")
    try:
        got = c.get(f"/projects/{pid}/guard/rules-file")
        assert got.status_code == 200, got.text
        b = got.json()
        assert b["present"] is True and b["read"] is True
        assert [r["pattern"] for r in b["confirm"]] == ["terraform apply"]
        assert b["confirm"][0]["id"].startswith("repo.")
        assert [e["line"] for e in b["errors"]] == [4]
        assert x.get(f"/projects/{pid}/guard/rules-file").status_code == 404
        assert world["agent"].get(f"/projects/{pid}/guard/rules-file").status_code == 403
        a.patch(f"/projects/{pid}", json={"trust": "external"})
        b = c.get(f"/projects/{pid}/guard/rules-file").json()
        assert b["read"] is False and b["confirm"] == [] and b["never"] == [] and b["errors"] == []
    finally:
        a.patch(f"/projects/{pid}", json={"trust": "own", "quarantine": False})
        (rd / "rules.md").unlink()
        rd.rmdir()


# ---- create / import trust ----------------------------------------------------------------------


def test_create_suggests_trust(world, tmp_path):
    a = world["admin"]
    fresh = a.post("/projects", json={"name": f"gt-{_uid()}"}).json()
    assert fresh["trust"] == "own" and fresh["quarantine"] is False and fresh["trust_reason"]
    foreign = _repo(tmp_path / "foreign", author=f"stranger-{_uid()}@else.example")
    # some org member must have a git email for the check to run
    m = world["m"]
    assert m.put("/me/credentials", json={"git_email": f"gp-m-{_uid()}@test.local"}).status_code == 200
    r = a.post("/projects", json={"name": f"gt-{_uid()}", "directory": str(foreign)})
    assert r.status_code == 201, r.text
    assert r.json()["trust"] == "external" and r.json()["quarantine"] is True and r.json()["trust_reason"]
    assert a.get(f"/projects/{r.json()['id']}").json()["trust"] == "external"


def test_import_suggests_trust(world, tmp_path):
    a, m = world["admin"], world["m"]
    email = f"gp-imp-{_uid()}@test.local"
    assert m.put("/me/credentials", json={"git_email": email}).status_code == 200
    mine = _repo(tmp_path / f"mine-{_uid()}", author=email.upper())
    theirs = _repo(tmp_path / f"theirs-{_uid()}", author="nobody@else.example")
    r = a.post("/projects/import", json={"directories": [str(mine), str(theirs)]})
    assert r.status_code == 200, r.text
    by_dir = {Path(p["directory"]).name: p for p in r.json()}
    assert by_dir[mine.name]["trust"] == "own" and by_dir[mine.name]["quarantine"] is False
    assert by_dir[theirs.name]["trust"] == "external" and by_dir[theirs.name]["quarantine"] is True
    assert all(p["trust_reason"] for p in r.json())


# ---- MCP ----------------------------------------------------------------------------------------


def test_mcp_environment_and_default_action(world):
    a, m = world["admin"], world["m"]
    r = a.post("/mcp/servers", json={"name": f"gmcp-{_uid()}", "command": "true"})
    assert r.status_code == 201, r.text
    srv = r.json()
    assert srv["environment"] == "unknown" and srv["default_action"] == "require_approval"
    r = a.patch(f"/mcp/servers/{srv['id']}", json={"environment": "dev", "default_action": "block"})
    assert r.status_code == 200 and r.json()["environment"] == "dev" and r.json()["default_action"] == "block"
    assert a.get(f"/mcp/servers/{srv['id']}").json()["environment"] == "dev"
    assert a.patch(f"/mcp/servers/{srv['id']}", json={"environment": "staging"}).status_code == 400
    assert a.patch(f"/mcp/servers/{srv['id']}", json={"default_action": "allow"}).status_code == 400
    assert a.post("/mcp/servers", json={"name": f"gmcp-{_uid()}", "command": "true",
                                        "environment": "qa"}).status_code == 400
    r = a.post("/mcp/servers", json={"name": f"gmcp-{_uid()}", "command": "true", "environment": "prod",
                                     "default_action": "auto_approve"})
    assert r.json()["environment"] == "prod" and r.json()["default_action"] == "auto_approve"
    assert m.patch(f"/mcp/servers/{srv['id']}", json={"environment": "prod"}).status_code == 403


def test_classify_risk_unchanged():
    from app.task_service import classify_risk

    assert "Phase 5" in (classify_risk.__doc__ or "")
    assert classify_risk("git push origin main", None) == "critical"
    assert classify_risk("list files", "p") == "warning"
    assert classify_risk("list files", None) == "info"
    assert classify_risk("merge it", "p", ["security"]) == "warning"


def test_deleting_a_project_deletes_its_rules(world):
    a = world["admin"]
    p = a.post("/projects", json={"name": f"gd-{_uid()}"}).json()
    rule = a.post("/guard/rules", json=_rule(scope="project", project_id=p["id"])).json()
    assert a.delete(f"/projects/{p['id']}").status_code == 204

    async def go():
        async with SessionLocal() as s:
            return await s.get(ActionRule, rule["id"])

    assert _run(go()) is None


def test_project_rule_rows_carry_their_project(world):
    async def go():
        async with SessionLocal() as s:
            from sqlalchemy import select

            return (await s.execute(select(ActionRule).where(ActionRule.scope == "project"))).scalars().all()

    assert all(r.project_id for r in _run(go()))
