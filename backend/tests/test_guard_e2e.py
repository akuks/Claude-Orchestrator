"""End to end: the real co_hook.py (from a plugin built by ensure_plugin) as a
subprocess, against a live uvicorn serving this app on a random free port,
with a real minted agent token. A person decides through the API.

approve -> the hook answers with additionalContext only (no deny);
reject  -> deny; server-side expiry -> deny; the hook's own timeout -> deny
and the request can no longer be approved."""

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.guard.plugin import ensure_plugin
from app.models import ActionApproval
from conftest import login_as
from test_guard_approvals import _get, _mint, _set, _task
from test_guard_worker import _serve_real_app


@pytest.fixture(scope="module")
def live(client, tmp_path_factory):
    server, th, port = _serve_real_app()
    tag = uuid.uuid4().hex[:8]
    p = client.post("/projects", json={"name": f"e2e-{tag}"}).json()
    owner = login_as(f"e2e-owner-{tag}@test.local")
    decider = login_as(f"e2e-dec-{tag}@test.local")
    assert client.put(f"/projects/{p['id']}/members", json=[
        {"subject_type": "user", "subject_id": owner.user_id, "role": "maintainer"},
        {"subject_type": "user", "subject_id": decider.user_id, "role": "maintainer"},
    ]).status_code == 200
    plugin = ensure_plugin(tmp_path_factory.mktemp("sys") / "co-guard")
    try:
        yield {"port": port, "p": p, "owner": owner, "decider": decider, "plugin": plugin}
    finally:
        server.should_exit = True
        th.join(5)


class Hook:
    def __init__(self, live, tmp_path, wait=30):
        self.task_id = _task(live["owner"].user_id, live["p"]["id"])
        token = _mint(live["owner"].user_id, self.task_id)
        tdir = tmp_path / "_tasks" / self.task_id
        tdir.mkdir(parents=True)
        proj = tmp_path / "proj"
        proj.mkdir(exist_ok=True)
        policy = {
            "task_id": self.task_id, "api_base": f"http://127.0.0.1:{live['port']}",
            "project_dir": str(proj), "cwd": str(proj), "home": str(tmp_path / "home"),
            "guard_enabled": True, "approval_wait_seconds": wait, "approval_poll_seconds": 0.2,
            "mcp_tools": {}, "protected_paths": [str(live["plugin"]), str(tdir)],
            "env_paths": {"CO_POLICY_FILE": str(tdir / "policy.json"), "CO_TASK_DIR": str(tdir)},
        }
        (tdir / "policy.json").write_text(json.dumps(policy))
        self.tdir, self.proj, self.plugin, self.token = tdir, proj, live["plugin"], token
        self.env = {"PATH": os.environ.get("PATH", ""), "CO_POLICY_FILE": str(tdir / "policy.json"),
                    "CO_TASK_DIR": str(tdir), "CO_TASK_ID": self.task_id, "CO_API_BASE": policy["api_base"],
                    "CO_API_TOKEN": token}

    def start(self, cmd="git reset --hard HEAD~1", tool_use_id=None):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": cmd},
                   "tool_use_id": tool_use_id or "tu-" + uuid.uuid4().hex[:6], "session_id": "s1"}
        self.proc = subprocess.Popen(
            [sys.executable, "-I", "-S", str(self.plugin / "hooks" / "co_hook.py"), "PreToolUse"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=self.env, cwd=str(self.proj))
        self.proc.stdin.write(json.dumps(payload))
        self.proc.stdin.close()
        return self

    def request_id(self, viewer, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = viewer.get("/guard/approvals", params={"task_id": self.task_id, "status": "all"})
            assert r.status_code == 200, r.text
            if r.json():
                return r.json()[0]["id"]
            assert self.proc.poll() is None, self.proc.stdout.read()
            time.sleep(0.1)
        raise AssertionError("the hook never filed a request")

    def result(self, timeout=60):
        out, err = self.proc.communicate(timeout=timeout)
        assert self.proc.returncode == 0, err
        return json.loads(out.strip())["hookSpecificOutput"]


def test_hook_approved_by_a_maintainer(live, tmp_path):
    hook = Hook(live, tmp_path).start()
    rid = hook.request_id(live["decider"])
    # R25: the task owner cannot approve it
    assert live["owner"].post(f"/guard/approvals/{rid}/approve").status_code == 403
    assert live["decider"].post(f"/guard/approvals/{rid}/approve").status_code == 200
    out = hook.result()
    assert "permissionDecision" not in out
    assert "approved" in out["additionalContext"] and rid in out["additionalContext"]
    assert _get(ActionApproval, rid).decided_by == live["decider"].user_id


def test_hook_rejected(live, tmp_path):
    hook = Hook(live, tmp_path).start("git clean -fdx")
    rid = hook.request_id(live["decider"])
    assert live["decider"].post(f"/guard/approvals/{rid}/reject", json={"reason": "no"}).status_code == 200
    out = hook.result()
    assert out["permissionDecision"] == "deny" and "rejected" in out["permissionDecisionReason"]


def test_hook_sees_server_expiry(live, tmp_path):
    hook = Hook(live, tmp_path).start("git stash drop")
    rid = hook.request_id(live["decider"])
    _set(ActionApproval, rid, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    out = hook.result()
    assert out["permissionDecision"] == "deny" and "expired" in out["permissionDecisionReason"]
    assert live["decider"].post(f"/guard/approvals/{rid}/approve").status_code == 409


def test_hook_timeout_then_late_approval_is_refused(live, tmp_path):
    hook = Hook(live, tmp_path, wait=2).start()
    rid = hook.request_id(live["decider"])
    out = hook.result()
    assert out["permissionDecision"] == "deny" and "no decision in time" in out["permissionDecisionReason"]
    # the hook's best-effort timeout report closed the request
    deadline = time.time() + 5
    while time.time() < deadline and _get(ActionApproval, rid).status == "pending":
        time.sleep(0.1)
    r = live["decider"].post(f"/guard/approvals/{rid}/approve")
    assert r.status_code == 409 and r.json()["detail"] == "expired"


def test_planted_request_with_the_hooks_key_is_refused(live, tmp_path):
    """Fix round 1: the agent (same token) pre-files a harmless-looking request
    under the key the hook will use; the hook's POST conflicts and it denies -
    a person can never approve the planted text for the real call."""
    import hashlib

    from app.main import app
    from fastapi.testclient import TestClient

    hook = Hook(live, tmp_path)
    cmd, tuid = "git reset --hard HEAD~3", "tu-planted"
    canon = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}, sort_keys=True, default=str)
    chash = hashlib.sha256(canon.encode()).hexdigest()
    key = hashlib.sha256((chash + ":" + tuid).encode()).hexdigest()
    agent = TestClient(app)
    agent.headers["Authorization"] = f"Bearer {hook.token}"
    planted = agent.post("/guard/approvals", headers={"Idempotency-Key": key}, json={
        "task_id": hook.task_id, "rule_ids": ["git-reset-hard"], "tool": "Bash", "summary": "git status",
        "cwd": str(hook.proj), "reason": "harmless", "command_hash": chash})
    assert planted.status_code == 201
    out = hook.start(cmd, tool_use_id=tuid).result()
    assert out["permissionDecision"] == "deny" and "409" in out["permissionDecisionReason"]
    assert planted.json()["id"] not in out["permissionDecisionReason"]
