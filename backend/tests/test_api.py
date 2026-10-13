"""Integration tests through the API with a mock `claude` binary."""

import hashlib
import hmac
import json

from conftest import wait_task


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_claude_status(client):
    d = client.get("/system/claude").json()
    assert d["found"] is True  # mock binary present


def test_task_lifecycle(client):
    tid = client.post("/tasks", json={"prompt": "do a thing"}).json()["id"]
    t = wait_task(client, tid)
    assert t["status"] == "completed"
    assert t["result_text"] == "done"
    assert t["total_cost_usd"] == 0.01
    assert t["input_tokens"] == 100 and t["output_tokens"] == 50
    assert t["model_used"] == "claude-test"


def test_critical_task_is_auto_gated_then_approved(client):
    t = client.post("/tasks", json={"prompt": "merge PR #9 into main"}).json()
    assert t["status"] == "awaiting_approval" and t["risk"] == "critical"
    # It must be in the inbox with blast-radius context.
    inbox = client.get("/approvals").json()
    assert any(x["id"] == t["id"] and "context" in x for x in inbox)
    # Approve → it runs to completion.
    client.post(f"/approvals/{t['id']}/approve")
    done = wait_task(client, t["id"])
    assert done["status"] == "completed"


def test_reject_flow(client):
    tid = client.post(
        "/tasks", json={"prompt": "clean up", "requires_approval": True}
    ).json()["id"]
    client.post(f"/approvals/{tid}/reject", json={"reason": "not now"})
    t = client.get(f"/tasks/{tid}").json()
    assert t["status"] == "cancelled" and "not now" in (t["decision_reason"] or "")


def test_report_download(client):
    tid = client.post("/tasks", json={"prompt": "x"}).json()["id"]
    wait_task(client, tid)
    resp = client.get(f"/tasks/{tid}/report?format=pdf")
    assert resp.status_code == 200 and resp.content[:4] == b"%PDF"


def test_usage_summary(client):
    tid = client.post("/tasks", json={"prompt": "spend a little"}).json()["id"]
    wait_task(client, tid)
    d = client.get("/usage/summary").json()
    assert d["all_time"]["cost_usd"] > 0


def test_agent_run(client):
    a = client.post(
        "/agents",
        json={
            "name": "Auditor",
            "system_prompt": "You are a security auditor.",
            "default_prompt": "audit the code",
            "max_budget_usd": 1,
        },
    ).json()
    # Run with defaults.
    tid = client.post(f"/agents/{a['id']}/run", json={}).json()["id"]
    t = wait_task(client, tid)
    assert t["status"] == "completed"
    assert t["title"].startswith("Auditor:")
    assert t["max_budget_usd"] == 1
    # Override the task input at run time.
    t2 = client.post(f"/agents/{a['id']}/run", json={"prompt": "audit the auth module"}).json()
    assert "auth module" in t2["title"]


def test_agent_requires_approval_gates_every_run(client):
    # An agent flagged requires_approval lands every run in the approvals inbox.
    a = client.post(
        "/agents",
        json={
            "name": "Remediation",
            "system_prompt": "You apply approved fixes to servers.",
            "default_prompt": "apply the fix",
            "requires_approval": True,
        },
    ).json()
    assert a["requires_approval"] is True
    t = client.post(f"/agents/{a['id']}/run", json={}).json()
    assert t["status"] == "awaiting_approval"
    # Approve → it runs to completion.
    client.post(f"/approvals/{t['id']}/approve")
    assert wait_task(client, t["id"])["status"] == "completed"


def test_allow_remote_is_opt_in_for_agents(client):
    # Ad-hoc tasks keep remote access; agents are locked down unless opted in.
    adhoc = client.post("/tasks", json={"prompt": "hello"}).json()
    assert adhoc["allow_remote"] is True

    locked = client.post(
        "/agents", json={"name": "Local", "system_prompt": "x", "default_prompt": "go"}
    ).json()
    assert locked["allow_remote"] is False
    t1 = client.post(f"/agents/{locked['id']}/run", json={}).json()
    assert t1["allow_remote"] is False

    remote = client.post(
        "/agents",
        json={"name": "SSHer", "system_prompt": "x", "default_prompt": "go", "allow_remote": True},
    ).json()
    assert remote["allow_remote"] is True
    t2 = client.post(f"/agents/{remote['id']}/run", json={}).json()
    assert t2["allow_remote"] is True


def test_presets_include_governed_dev_templates(client):
    presets = {p["name"]: p for p in client.get("/agents/presets").json()}
    # The dev-team roles exist.
    for role in ["Frontend Developer", "Backend Developer", "Fullstack Developer",
                 "Code Reviewer", "QA / Tester", "DevOps Engineer"]:
        assert role in presets, role
    # Governance is encoded, not just persona: DevOps is SSH-capable and gated.
    devops = presets["DevOps Engineer"]
    assert devops["allow_remote"] is True and devops["requires_approval"] is True
    # Reviewer is not a remote/gated role.
    assert not presets["Code Reviewer"].get("allow_remote")
    # Creating an agent from the DevOps template carries its governance through.
    a = client.post("/agents", json={
        "name": "Deployer", "system_prompt": devops["system_prompt"],
        "default_prompt": devops["default_prompt"],
        "allow_remote": devops["allow_remote"],
        "requires_approval": devops["requires_approval"],
    }).json()
    assert a["allow_remote"] is True and a["requires_approval"] is True


def test_agent_run_with_attached_file(client):
    import base64
    a = client.post("/agents", json={"name": "FileAgent", "system_prompt": "x",
                                      "default_prompt": "do it"}).json()
    content = base64.b64encode(b"# Spec\nBuild the thing.").decode()
    t = client.post(f"/agents/{a['id']}/run", json={
        "prompt": "Implement the attached spec",
        "input_files": [{"name": "spec.md", "content_base64": content}],
    }).json()
    # The attachment is referenced in the task prompt so the agent can read it.
    full = client.get(f"/tasks/{t['id']}").json()
    assert "spec.md" in full["prompt"] and "uploads" in full["prompt"]
    assert wait_task(client, t["id"])["status"] == "completed"


def test_agent_bulk_run_creates_one_task_per_line(client):
    a = client.post("/agents", json={"name": "BulkAgent", "system_prompt": "x",
                                      "default_prompt": "go"}).json()
    r = client.post(f"/agents/{a['id']}/bulk", json={
        "prompts": ["Fix bug A", "Add feature B", "Write tests for C"],
    })
    tasks = r.json()
    assert len(tasks) == 3
    assert all(t["agent_id"] == a["id"] and "bulk" in t["tags"] for t in tasks)
    # Empty list is rejected.
    assert client.post(f"/agents/{a['id']}/bulk", json={"prompts": ["  "]}).status_code == 400


def test_coordinator_dispatch_creates_linked_children(client):
    # A coordinator task fans out to specialists; children link to it + run.
    coord = client.post("/agents", json={
        "name": "Lead", "system_prompt": "delegate", "default_prompt": "coordinate",
        "coordinator": True,
    }).json()
    assert coord["coordinator"] is True
    fe = client.post("/agents", json={"name": "FE", "system_prompt": "ui", "default_prompt": "x"}).json()
    be = client.post("/agents", json={"name": "BE", "system_prompt": "api", "default_prompt": "x"}).json()
    parent = client.post(f"/agents/{coord['id']}/run", json={}).json()

    children = client.post("/agents/dispatch", json={
        "parent_task_id": parent["id"],
        "items": [
            {"agent": "FE", "prompt": "build the UI"},
            {"agent": be["id"], "prompt": "add the API"},
        ],
    }).json()
    assert len(children) == 2
    # Children link to the coordinator and share its thread root.
    for c in children:
        assert c["parent_task_id"] == parent["id"]
        assert c["root_id"] == parent["root_id"]
        assert "delegated" in c["tags"]
    # Unknown agent is rejected.
    assert client.post("/agents/dispatch", json={
        "items": [{"agent": "nope", "prompt": "x"}]}).status_code == 404


def test_agent_folder_roundtrips(client):
    a = client.post("/agents", json={
        "name": "Grouped", "system_prompt": "x", "default_prompt": "go",
        "folder": "Canonizer/Dev",
    }).json()
    assert a["folder"] == "Canonizer/Dev"
    u = client.patch(f"/agents/{a['id']}", json={"folder": "Ops"}).json()
    assert u["folder"] == "Ops"


def test_schedule_runs_an_agent(client):
    # A schedule can target an agent instead of carrying its own prompt.
    a = client.post(
        "/agents",
        json={
            "name": "Brief",
            "system_prompt": "You are a chief-of-staff.",
            "default_prompt": "give me my brief",
            "max_budget_usd": 0.5,
            "tags": ["personal"],
        },
    ).json()
    # No prompt needed when an agent is attached.
    sch = client.post(
        "/schedules",
        json={"name": "Daily Brief", "cron": "0 8 * * *", "agent_id": a["id"], "enabled": False},
    ).json()
    assert sch["agent_id"] == a["id"]

    # Run-now builds a task from the agent's role/budget, not the (empty) prompt.
    t = client.post(f"/schedules/{sch['id']}/run").json()
    assert t["agent_id"] == a["id"]
    assert t["max_budget_usd"] == 0.5
    assert set(["agent", "scheduled"]).issubset(set(t["tags"]))
    assert wait_task(client, t["id"])["status"] == "completed"


def test_schedule_requires_prompt_or_agent(client):
    r = client.post("/schedules", json={"name": "Empty", "cron": "0 8 * * *"})
    assert r.status_code == 400


def test_server_inventory_crud(client):
    p = client.post("/projects", json={"name": "InfraTest"}).json()
    # Empty to start.
    assert client.get(f"/projects/{p['id']}/servers").json() == []
    # Replace with two servers.
    servers = [
        {"name": "web", "host": "1.2.3.4", "user": "ubuntu", "pem": "~/keys/web.pem", "notes": "nginx"},
        {"name": "db", "host": "5.6.7.8", "user": "admin", "pem": "/keys/db.pem"},
    ]
    r = client.put(f"/projects/{p['id']}/servers", json=servers).json()
    assert {x["name"] for x in r} == {"web", "db"}
    # Persisted + round-trips.
    got = {x["name"]: x for x in client.get(f"/projects/{p['id']}/servers").json()}
    assert got["web"]["host"] == "1.2.3.4" and got["web"]["notes"] == "nginx"
    assert got["db"]["pem"] == "/keys/db.pem"
    # Duplicate names rejected.
    dup = [{"name": "web", "host": "a", "user": "u", "pem": "p"},
           {"name": "web", "host": "b", "user": "u", "pem": "p"}]
    assert client.put(f"/projects/{p['id']}/servers", json=dup).status_code == 400


def test_project_env_roundtrips(client):
    # Per-project env (toolchain scoping) is stored and returned.
    p = client.post("/projects", json={
        "name": "EnvProj",
        "env": {"PATH_PREPEND": "/opt/homebrew/opt/php@7.4/bin", "APP_ENV": "development"},
    }).json()
    assert p["env"]["PATH_PREPEND"].endswith("php@7.4/bin")
    # And can be updated.
    u = client.patch(f"/projects/{p['id']}", json={"env": {"APP_ENV": "staging"}}).json()
    assert u["env"] == {"APP_ENV": "staging"}


def test_workflow_runs_stages_in_order(client):
    import time
    fe = client.post("/agents", json={"name": "WF-FE", "system_prompt": "ui", "default_prompt": "x"}).json()
    be = client.post("/agents", json={"name": "WF-BE", "system_prompt": "api", "default_prompt": "x"}).json()
    qa = client.post("/agents", json={"name": "WF-QA", "system_prompt": "qa", "default_prompt": "x"}).json()
    wf = client.post("/workflows", json={
        "name": "mini pipeline",
        "stages": [
            {"name": "Develop", "items": [
                {"agent": "WF-FE", "prompt": "frontend for {{input}}"},
                {"agent": "WF-BE", "prompt": "backend for {{input}}"},
            ]},
            {"name": "Test", "items": [{"agent": "WF-QA", "prompt": "test {{input}}"}]},
        ],
    }).json()
    assert len(wf["stages"]) == 2
    # Unknown agent rejected.
    assert client.post("/workflows", json={
        "name": "bad", "stages": [{"name": "s", "items": [{"agent": "nope", "prompt": "x"}]}]
    }).status_code == 400

    run = client.post(f"/workflows/{wf['id']}/run", json={"input": "issue #1"}).json()
    assert run["status"] in ("running", "pending")

    # Stage 0 creates 2 parallel tasks with {{input}} substituted.
    board = client.get(f"/workflows/runs/{run['id']}").json()
    s0 = [t for t in board["tasks"] if t["stage_index"] == 0]
    assert len(s0) == 2 and all("issue #1" in t["prompt"] for t in s0)

    # Runner advances through both stages to completion (mock tasks complete fast).
    deadline = time.time() + 40
    final = None
    while time.time() < deadline:
        final = client.get(f"/workflows/runs/{run['id']}").json()
        if final["run"]["status"] in ("completed", "failed"):
            break
        time.sleep(1)
    assert final["run"]["status"] == "completed"
    # Stage 1 (Test) ran after stage 0.
    assert any(t["stage_index"] == 1 for t in final["tasks"])


def test_github_webhook_triggers_review(client):
    client.post(
        "/projects",
        json={"name": "WHTest", "github_repo": "acme/repo", "auto_review_prs": True},
    )
    body = json.dumps(
        {
            "action": "opened",
            "number": 1,
            "repository": {"full_name": "acme/repo"},
            "pull_request": {"number": 1, "head": {"ref": "feat"}, "base": {"ref": "main"}},
        }
    ).encode()
    sig = "sha256=" + hmac.new(b"testsecret", body, hashlib.sha256).hexdigest()
    r = client.post(
        "/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": sig},
    )
    assert r.json().get("task_id")

    # Bad signature is rejected.
    r2 = client.post(
        "/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": "sha256=bad"},
    )
    assert r2.status_code == 401
