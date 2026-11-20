"""co-guard hook (hooks/co_hook.py) run as a real subprocess, exactly as Claude
Code runs it: ``<python> -I <plugin>/hooks/co_hook.py <Event>`` with the hook
payload on stdin, from a plugin built by ``ensure_plugin``. Approvals talk to
a tiny stub HTTP server (Ruling R2 contract). Fail closed everywhere."""

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.guard import co_hook
from app.guard.plugin import ensure_plugin

ERROR = "co-guard error — action blocked for safety"
TOKEN = "co_" + "T" * 43


@pytest.fixture(scope="module")
def plugin(tmp_path_factory):
    return ensure_plugin(tmp_path_factory.mktemp("sys") / "co-guard")


class Stub:
    """POST /guard/approvals -> 201 pending; GET /guard/approvals/{id} -> the
    scripted statuses in order (the last repeats); records every request."""

    def __init__(self, statuses=("approved",), post_status=201, get_status=200, post_body=None, get_id="req-1",
                 report_delay=0.0):
        self.statuses = list(statuses)
        self.post_status = post_status
        self.post_body = post_body or {"id": "req-1", "status": "pending", "expires_at": "x"}
        self.get_id = get_id
        self.report_delay = report_delay
        self.headers = []
        self.get_status = get_status
        self.requests = []
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"null")
                stub.requests.append(("POST", self.path, self.headers.get("Authorization"), body))
                stub.headers.append(dict(self.headers))
                if self.path == "/guard/approvals":
                    self._reply(stub.post_status, stub.post_body)
                else:
                    time.sleep(stub.report_delay)
                    self._reply(200, {})

            def do_GET(self):
                stub.requests.append(("GET", self.path, self.headers.get("Authorization"), None))
                st = stub.statuses.pop(0) if len(stub.statuses) > 1 else stub.statuses[0]
                self._reply(stub.get_status, {"id": stub.get_id, "status": st})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def paths(self, method=None):
        return [p for m, p, _a, _b in self.requests if method in (None, m)]


@pytest.fixture
def stub():
    s = Stub()
    yield s
    s.close()


class Run:
    def __init__(self, plugin, tmp_path, api_base, **policy):
        self.tdir = tmp_path / "_tasks" / "T1"
        self.tdir.mkdir(parents=True)
        self.project = tmp_path / "proj"
        self.project.mkdir()
        self.policy = {
            "task_id": "T1", "api_base": api_base, "project_dir": str(self.project), "cwd": str(self.project),
            "home": str(tmp_path / "home"), "guard_enabled": True, "approval_wait_seconds": 5,
            "approval_poll_seconds": 0.2,
            "mcp_tools": {"mcp__db__query": "require_approval", "mcp__db__drop": "block",
                          "mcp__db__list": "auto_approve"},
            "protected_paths": [str(plugin), str(self.tdir)],
            "env_paths": {"CO_POLICY_FILE": str(self.tdir / "policy.json"), "CO_TASK_DIR": str(self.tdir)},
        }
        self.policy.update(policy)
        (self.tdir / "policy.json").write_text(json.dumps(self.policy))
        self.plugin = plugin
        self.env = {"PATH": os.environ.get("PATH", ""), "CO_POLICY_FILE": str(self.tdir / "policy.json"),
                    "CO_TASK_DIR": str(self.tdir), "CO_TASK_ID": "T1", "CO_API_BASE": api_base,
                    "CO_API_TOKEN": TOKEN, "GH_TOKEN": "ghp_" + "x" * 36}

    def __call__(self, payload, event="PreToolUse", raw=None, env=None, timeout=60):
        stdin = raw if raw is not None else json.dumps(payload)
        t0 = time.monotonic()
        p = subprocess.run([sys.executable, "-I", "-S", str(self.plugin / "hooks" / "co_hook.py"), event],
                           input=stdin, capture_output=True, text=True, timeout=timeout,
                           env=dict(self.env, **(env or {})), cwd=str(self.project))
        self.elapsed = time.monotonic() - t0
        assert p.returncode == 0, p.stderr
        out = p.stdout.strip()
        return json.loads(out) if out else None

    def spool(self):
        f = self.tdir / "spool.jsonl"
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def bash(cmd):
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": cmd},
            "tool_use_id": "tu1", "session_id": "s1"}


def decision(out):
    return (out or {}).get("hookSpecificOutput", {}).get("permissionDecision")


def reason(out):
    return out["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.fixture
def run(plugin, tmp_path, stub):
    return Run(plugin, tmp_path, stub.base)


# ---- allow / deny without a person ----------------------------------------


def test_allow_prints_nothing_and_does_not_touch_the_network(run, stub):
    assert run(bash("ls -la && git status")) is None
    assert stub.requests == [] and run.spool() == []
    assert run({"hook_event_name": "PreToolUse", "tool_name": "mcp__db__list", "tool_input": {}}) is None


def test_hard_deny(run, stub):
    out = run(bash("rm -rf ~"))
    assert decision(out) == "deny"
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "rm-rf-outside" in reason(out)
    assert stub.paths("POST") == ["/guard/report"]  # no approval for hard rules
    sp = run.spool()
    assert sp[0]["decision"] == "deny" and sp[0]["rule_ids"] == ["rm-rf-outside"] and sp[0]["tool"] == "Bash"


def test_self_protect_write_to_policy(run):
    out = run({"tool_name": "Write", "tool_input": {"file_path": str(run.tdir / "policy.json"), "content": "{}"}})
    assert decision(out) == "deny" and "self-protect" in reason(out)


def test_mcp_block(run, stub):
    out = run({"tool_name": "mcp__db__drop", "tool_input": {"t": 1}})
    assert decision(out) == "deny" and "mcp-block" in reason(out)
    assert "/guard/approvals" not in stub.paths()


# ---- approvals -------------------------------------------------------------


def test_approve_then_approved(plugin, tmp_path):
    stub = Stub(statuses=("pending", "pending", "approved"))
    try:
        run = Run(plugin, tmp_path, stub.base)
        out = run(bash("git reset --hard HEAD~1"))
        assert decision(out) is None  # no explicit allow: Claude Code's deny rules still apply
        assert "approved" in out["hookSpecificOutput"]["additionalContext"]
        posts = [r for r in stub.requests if r[1] == "/guard/approvals"]
        assert len(posts) == 1
        _m, _p, auth, body = posts[0]
        assert auth == f"Bearer {TOKEN}"
        assert set(body) == {"task_id", "rule_ids", "tool", "summary", "cwd", "reason", "command_hash"}
        assert body["task_id"] == "T1" and body["rule_ids"] == ["git-reset-hard"] and body["tool"] == "Bash"
        assert body["summary"] == "git reset --hard HEAD~1" and body["cwd"] == str(run.project)
        assert len(body["command_hash"]) == 64
        assert stub.paths("GET").count("/guard/approvals/req-1") == 3
        rec = run.spool()[-1]
        assert rec["decision"] == "approved" and rec["request_id"] == "req-1"
        assert "/guard/report" in stub.paths("POST")
    finally:
        stub.close()


def test_approve_mcp_require_approval(plugin, tmp_path):
    stub = Stub(statuses=("approved",))
    try:
        run = Run(plugin, tmp_path, stub.base)
        out = run({"tool_name": "mcp__db__query", "tool_input": {"sql": "select 1"}})
        assert decision(out) is None and "approved" in json.dumps(out)
        body = [r[3] for r in stub.requests if r[1] == "/guard/approvals"][0]
        assert body["rule_ids"] == ["mcp-require-approval"] and body["tool"] == "mcp__db__query"
    finally:
        stub.close()


@pytest.mark.parametrize("status,word", [("rejected", "rejected"), ("expired", "expired")])
def test_rejected_or_expired_deny(plugin, tmp_path, status, word):
    stub = Stub(statuses=("pending", status))
    try:
        run = Run(plugin, tmp_path, stub.base)
        out = run(bash("git clean -fdx"))
        assert decision(out) == "deny" and word in reason(out) and "req-1" in reason(out)
        assert run.spool()[-1]["status"] == status
    finally:
        stub.close()


def test_timeout_waiting_denies(plugin, tmp_path):
    stub = Stub(statuses=("pending",))
    try:
        run = Run(plugin, tmp_path, stub.base, approval_wait_seconds=1.5)
        out = run(bash("git stash drop"))
        assert decision(out) == "deny" and "no decision in time" in reason(out)
        assert 1.0 < run.elapsed < 6
    finally:
        stub.close()


def test_api_down_denies_after_the_wait(plugin, tmp_path):
    stub = Stub()
    base = stub.base
    stub.close()  # nothing listens there any more
    run = Run(plugin, tmp_path, base, approval_wait_seconds=1.5)
    out = run(bash("git reset --hard"))
    assert decision(out) == "deny" and "not filed" in reason(out)
    assert run.elapsed < 8


def test_api_errors_while_waiting_keep_polling_then_deny(plugin, tmp_path):
    stub = Stub(statuses=("pending",), get_status=500)
    try:
        run = Run(plugin, tmp_path, stub.base, approval_wait_seconds=1.5)
        out = run(bash("git reset --hard"))
        assert decision(out) == "deny"
        assert len(stub.paths("GET")) >= 2  # kept polling through the errors
    finally:
        stub.close()


def test_filing_failures_are_retried_then_deny(plugin, tmp_path):
    stub = Stub(statuses=("approved",), post_status=503)
    try:
        run = Run(plugin, tmp_path, stub.base, approval_wait_seconds=3)
        out = run(bash("git reset --hard"))
        assert decision(out) == "deny"  # never filed: the POST always fails
        assert stub.paths("POST").count("/guard/approvals") >= 2  # retried
    finally:
        stub.close()


def test_wait_is_clamped_below_the_hook_timeout():
    assert co_hook.wait_seconds({"approval_wait_seconds": 5000}) == 900
    assert co_hook.wait_seconds({"approval_wait_seconds": "x"}) == 300
    assert co_hook.HOOK_TIMEOUT == 930 and co_hook.HOOK_TIMEOUT - co_hook.EXIT_MARGIN <= 925


# ---- fail closed ------------------------------------------------------------


@pytest.mark.parametrize("raw", ["", "not json", "[]", "null", '{"tool_name": 5}', '{"hook_event_name": "Stop"}'])
def test_malformed_stdin_denies(run, raw):
    out = run(None, raw=raw)
    assert decision(out) == "deny" and reason(out) == ERROR


def test_missing_policy_denies(run):
    out = run(bash("ls"), env={"CO_POLICY_FILE": str(run.tdir / "nope.json")})
    assert decision(out) == "deny" and reason(out) == ERROR


def test_unset_policy_env_denies(run):
    env = dict(run.env)
    env.pop("CO_POLICY_FILE")
    run.env = env
    out = run(bash("ls"))
    assert decision(out) == "deny" and reason(out) == ERROR


@pytest.mark.parametrize("content", ["{broken", "[1, 2]", '"str"'])
def test_bad_policy_denies(run, content):
    (run.tdir / "policy.json").write_text(content)
    out = run(bash("ls"))
    assert decision(out) == "deny" and reason(out) == ERROR


def test_unreadable_policy_denies(run):
    f = run.tdir / "policy.json"
    f.chmod(0)
    try:
        if os.access(f, os.R_OK):
            pytest.skip("running as root")
        out = run(bash("ls"))
        assert decision(out) == "deny" and reason(out) == ERROR
    finally:
        f.chmod(0o600)


def test_exception_inside_rules_denies(run):
    pol = dict(run.policy, protected_paths=5)  # iterating an int raises inside rules.evaluate
    (run.tdir / "policy.json").write_text(json.dumps(pol))
    out = run(bash("ls"))
    assert decision(out) == "deny" and reason(out) == ERROR
    assert run.spool()[-1]["reason"] == "co-guard error"


def test_unknown_event_denies(run):
    out = run(bash("ls"), event="Bogus")
    assert decision(out) == "deny" and reason(out) == ERROR
    out = run(bash("ls"), event="")
    assert decision(out) == "deny"


# ---- other events: spool only ---------------------------------------------------


def test_other_events_spool_only(run, stub):
    for ev, payload in (("SessionStart", {"source": "startup", "session_id": "s1"}),
                        ("SubagentStart", {"agent_id": "a1", "agent_type": "general-purpose"}),
                        ("SubagentStop", {"agent_id": "a1"}),
                        ("Stop", {"stop_hook_active": False}),
                        ("SessionEnd", {"reason": "other"})):
        assert run(dict(payload, hook_event_name=ev), event=ev) is None
    assert [r["event"] for r in run.spool()] == ["SessionStart", "SubagentStart", "SubagentStop", "Stop",
                                                 "SessionEnd"]
    assert stub.requests == []
    # malformed input on a non-PreToolUse event: no output, no crash
    assert run(None, event="Stop", raw="garbage") is None


def test_post_tool_use_spools_only_approved_calls(plugin, tmp_path):
    stub = Stub(statuses=("approved",))
    try:
        run = Run(plugin, tmp_path, stub.base)
        run(bash("git reset --hard"))  # approved, tool_use_id tu1
        run({"tool_name": "Bash", "tool_use_id": "tu1", "tool_response": {}}, event="PostToolUse")
        run({"tool_name": "Bash", "tool_use_id": "tu2", "tool_response": {}}, event="PostToolUse")
        posts = [r for r in run.spool() if r["event"] == "PostToolUse"]
        assert [(r["tool_use_id"], r["decision"]) for r in posts] == [("tu1", "ran")]
    finally:
        stub.close()


# ---- spool never holds secrets ------------------------------------------------------


def test_spool_and_report_are_redacted_and_short(run, stub):
    secret_cmd = f"rm -rf ~ # {TOKEN} ghp_{'y' * 36} https://u:pw@h.example " + "x" * 600
    run(bash(secret_cmd))
    rec = run.spool()[-1]
    text = json.dumps(rec)
    assert TOKEN not in text and "ghp_" not in text and "u:pw" not in text
    assert len(rec["summary"]) <= 300
    report = [b for m, p, _a, b in stub.requests if p == "/guard/report"][0]
    assert TOKEN not in json.dumps(report) and report["task_id"] == "T1"


def test_env_secret_values_are_redacted(run):
    run(bash("rm -rf ~ " + run.env["GH_TOKEN"]))
    assert run.env["GH_TOKEN"] not in json.dumps(run.spool())



# ---- fix round 1: C2 (only approvals this hook filed, at a literal IP) and minors ----


def _approve_cmd():
    return bash("git reset --hard")


@pytest.mark.parametrize("base", ["http://localhost:{port}", "http://[::1]", "https://127.0.0.1:{port}",
                                  "http://u:p@127.0.0.1:{port}", "http://127.0.0.1:{port}/x", ""])
def test_api_base_must_be_a_literal_ip_and_port(plugin, tmp_path, stub, base):
    run = Run(plugin, tmp_path, base.format(port=stub.server.server_address[1]), approval_wait_seconds=5)
    out = run(_approve_cmd())
    assert decision(out) == "deny" and "literal IP" in reason(out)
    assert run.elapsed < 3 and stub.requests == []


@pytest.mark.parametrize("post_body", [
    {"id": "FAKE", "status": "approved"},  # a fake listener's shortcut (the reviewer's fake6.py)
    {"id": "req-1", "status": "rejected"},
    {"status": "pending"},
    {"id": "", "status": "pending"},
    {"id": True, "status": "pending"},
    ["not", "an", "object"],
])
def test_post_must_answer_pending_with_an_id(plugin, tmp_path, post_body):
    stub = Stub(post_body=post_body)
    try:
        run = Run(plugin, tmp_path, stub.base)
        out = run(_approve_cmd())
        assert decision(out) == "deny" and "unexpected answer" in reason(out)
        assert stub.paths("GET") == [] and run.elapsed < 3
    finally:
        stub.close()


def test_get_for_another_request_is_refused(plugin, tmp_path):
    stub = Stub(statuses=("approved",), get_id="someone-elses")
    try:
        run = Run(plugin, tmp_path, stub.base)
        out = run(_approve_cmd())
        assert decision(out) == "deny" and "another request" in reason(out)
    finally:
        stub.close()


def test_unknown_status_is_refused(plugin, tmp_path):
    stub = Stub(statuses=("granted",))
    try:
        out = Run(plugin, tmp_path, stub.base)(_approve_cmd())
        assert decision(out) == "deny" and "unknown status" in reason(out)
    finally:
        stub.close()


@pytest.mark.parametrize("code", [400, 401, 403, 404, 409, 422])
def test_4xx_fails_fast(plugin, tmp_path, code):
    stub = Stub(post_status=code)
    try:
        run = Run(plugin, tmp_path, stub.base, approval_wait_seconds=30)
        out = run(_approve_cmd())
        assert decision(out) == "deny" and f"HTTP {code}" in reason(out)
        assert run.elapsed < 3 and stub.paths("POST").count("/guard/approvals") == 1
    finally:
        stub.close()


def test_get_4xx_fails_fast(plugin, tmp_path):
    stub = Stub(statuses=("pending",), get_status=403)
    try:
        run = Run(plugin, tmp_path, stub.base, approval_wait_seconds=30)
        out = run(_approve_cmd())
        assert decision(out) == "deny" and "HTTP 403" in reason(out) and run.elapsed < 3
    finally:
        stub.close()


def test_missing_token_denies_at_once(run, stub):
    env = dict(run.env)
    env.pop("CO_API_TOKEN")
    run.env = env
    out = run(_approve_cmd())
    assert decision(out) == "deny" and "no CO_API_TOKEN" in reason(out)
    assert run.elapsed < 3 and "/guard/approvals" not in stub.paths()


def test_idempotency_key_is_sent(plugin, tmp_path):
    stub = Stub(statuses=("approved",))
    try:
        run = Run(plugin, tmp_path, stub.base)
        run(_approve_cmd())
        keys = [h.get("Idempotency-Key") for h in stub.headers if h.get("Idempotency-Key")]
        assert len(keys) == 1 and len(keys[0]) == 64
        body = [r[3] for r in stub.requests if r[1] == "/guard/approvals"][0]
        import hashlib

        assert keys[0] == hashlib.sha256((body["command_hash"] + ":tu1").encode()).hexdigest()
    finally:
        stub.close()


def test_poll_interval_is_clamped():
    assert co_hook.poll_seconds({"approval_poll_seconds": 0.01}) == 0.5
    assert co_hook.poll_seconds({"approval_poll_seconds": "x"}) == 2.0
    assert co_hook.poll_seconds({}) == 2.0


def test_decision_is_printed_before_the_report(plugin, tmp_path):
    stub = Stub(report_delay=3)
    try:
        run = Run(plugin, tmp_path, stub.base)
        p = subprocess.Popen([sys.executable, "-I", "-S", str(plugin / "hooks" / "co_hook.py"), "PreToolUse"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=run.env)
        t0 = time.monotonic()
        p.stdin.write(json.dumps(bash("rm -rf ~")))
        p.stdin.close()
        line = p.stdout.readline()
        t_line = time.monotonic() - t0
        p.wait(timeout=30)
        t_exit = time.monotonic() - t0
        assert json.loads(line)["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert t_exit - t_line >= 0.3  # the report (0.5 s timeout) came after the answer
    finally:
        stub.close()


def test_unknown_tool_goes_to_approval(plugin, tmp_path):
    stub = Stub(statuses=("rejected",))
    try:
        run = Run(plugin, tmp_path, stub.base)
        out = run({"tool_name": "REPL", "tool_input": {"code": "x"}})
        assert decision(out) == "deny" and "rejected" in reason(out)
        body = [r[3] for r in stub.requests if r[1] == "/guard/approvals"][0]
        assert body["rule_ids"] == ["unknown-tool"] and body["tool"] == "REPL"
    finally:
        stub.close()


def test_monitor_and_powershell(run):
    assert decision(run({"tool_name": "Monitor", "tool_input": {"command": "rm -rf ~"}})) == "deny"
    out = run({"tool_name": "PowerShell", "tool_input": {"command": "dir"}})
    assert decision(out) == "deny" and "unsupported-shell" in reason(out)
    assert run({"tool_name": "Read", "tool_input": {"file_path": "/etc/hosts"}}) is None


# ---- Ruling R19b: ConfigChange (unverified with a real CLI) ----------------------------


def _config(run, tmp_path, content, source="project_settings"):
    f = tmp_path / "settings.json"
    f.write_text(content if isinstance(content, str) else json.dumps(content))
    p = subprocess.run([sys.executable, "-I", "-S", str(run.plugin / "hooks" / "co_hook.py"), "ConfigChange"],
                       input=json.dumps({"hook_event_name": "ConfigChange", "source": source, "file_path": str(f)}),
                       capture_output=True, text=True, env=run.env, timeout=30)
    return p.returncode, p.stderr


@pytest.mark.parametrize("content", [{"disableAllHooks": True}, {"enabledPlugins": {"co-guard@inline": False}},
                                     {"allowManagedHooksOnly": True}, '{"disableAllHooks": true,'])
def test_config_change_switching_hooks_off_is_blocked(run, tmp_path, content):
    code, err = _config(run, tmp_path, content)
    assert code == 2 and "co-guard" in err
    assert run.spool()[-1]["decision"] == "blocked"


@pytest.mark.parametrize("content", [{"disableAllHooks": False}, {"model": "sonnet"},
                                     {"enabledPlugins": {"other@x": False}}, "{broken"])
def test_config_change_other_edits_are_allowed(run, tmp_path, content):
    code, _err = _config(run, tmp_path, content)
    assert code == 0
