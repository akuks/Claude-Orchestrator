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
