"""Test fixtures. Env is configured BEFORE importing the app so config picks it
up, and a mock `claude` binary stands in for the real CLI (stream-json for tasks,
structured JSON for --json-schema, plain text otherwise)."""

import os
import pathlib
import tempfile
import time

import pytest

_TMP = tempfile.mkdtemp(prefix="co-tests-")
_MOCK = pathlib.Path(_TMP) / "mock_claude.sh"
_PROBES = pathlib.Path(_TMP) / "env-probes"
_PROBES.mkdir()
# Env vars the mock records for a probed task run (names AND values: tests only
# use obviously fake credentials). CO_API_TOKEN is recorded as presence only.
_PROBE_VARS = (
    "CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY GH_TOKEN GITHUB_TOKEN "
    "GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL "
    "CLAUDE_CONFIG_DIR GH_CONFIG_DIR GIT_CONFIG_GLOBAL RANDOM_SERVER_VAR "
    "PATH PROBE_PROJECT_VAR CO_TASK_ID CO_API_BASE CO_POLICY_FILE CO_TASK_DIR "
    "CLAUDE_CODE_SIMPLE CLAUDE_CODE_SAFE_MODE"
)
# Test seam (co-guard): a task prompt carrying [guard-spool] makes the mock act
# like co-guard's hook: it appends spool lines to $CO_TASK_DIR/spool.jsonl, one
# of them quoting the run's GH_TOKEN / CO_API_TOKEN (must come out redacted).
_GUARD_SPOOL = r"""
  if [[ -n "$CO_TASK_DIR" && -d "$CO_TASK_DIR" ]]; then
    printf '{"event":"PreToolUse","decision":"deny","rule_ids":["rm-rf-outside"],"tool":"Bash","summary":"rm -rf ~","reason":"deletes home"}\n' >>"$CO_TASK_DIR/spool.jsonl"
    printf '{"event":"PreToolUse","decision":"approved","rule_ids":["mcp-require-approval"],"tool":"mcp__db__query","summary":"token %s and %s","status":"approved","request_id":"r1"}\n' "$GH_TOKEN" "$CO_API_TOKEN" >>"$CO_TASK_DIR/spool.jsonl"
    printf 'not json\n' >>"$CO_TASK_DIR/spool.jsonl"
    printf '{"event":"SessionEnd","reason":"other","junk_key":"dropped"}\n' >>"$CO_TASK_DIR/spool.jsonl"
  fi"""
# Test seam: a task prompt carrying [env-echo] makes the mock behave like an
# agent that prints its env: "NAME=value " for each of _ECHO_VARS that is set
# goes into its assistant text, a tool_use input, a tool_result, a raw
# (non-JSON) stdout line, stderr and the final result. [env-echo-fail] does the
# same, then reports is_error and exits 1 (so stderr becomes task.error).
_ECHO_VARS = (
    "CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY GH_TOKEN GITHUB_TOKEN CO_API_TOKEN "
    "PATH PROBE_PROJECT_VAR PROBE_DB_PASSWORD PROBE_SHORT_TOKEN PROBE_SHAPED"
)
_ENV_ECHO = r"""
  jesc() { local s=$1; s=${s//\\/\\\\}; s=${s//\"/\\\"}; printf '%s' "$s"; }
  dump=""
  for n in __ECHO_VARS__; do
    if [[ -n "${!n+x}" ]]; then dump+="$n=${!n} "; fi
  done
  j=$(jesc "$dump")
  printf '{"type":"system","subtype":"init","session_id":"test-session"}\n'
  printf '{"type":"assistant","message":{"model":"claude-test","content":[{"type":"text","text":"env: %s"},{"type":"tool_use","id":"tu1","name":"Bash","input":{"command":"echo %s"}}]}}\n' "$j" "$j"
  printf '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"tu1","content":"%s"}]}}\n' "$j"
  printf 'raw stdout %s\n' "$dump"
  printf 'stderr %s\n' "$dump" >&2
  if [[ "$prompt" == *"[env-echo-fail]"* ]]; then
    printf '{"type":"result","subtype":"error","result":"env: %s","num_turns":1,"total_cost_usd":0.01,"duration_ms":100,"is_error":true,"usage":{"input_tokens":1,"output_tokens":1}}\n' "$j"
    exit 1
  fi
  printf '{"type":"result","subtype":"success","result":"env: %s","num_turns":1,"total_cost_usd":0.01,"duration_ms":100,"is_error":false,"usage":{"input_tokens":1,"output_tokens":1}}\n' "$j"
  exit 0""".replace("__ECHO_VARS__", _ECHO_VARS)
_MOCK.write_text(
    """#!/usr/bin/env bash
args="$*"
prompt="$(cat)"
# Test seam: the agent env is allowlisted, so a probe path set in the test's
# os.environ can never reach this script. Instead a task prompt carrying the
# marker [env-probe:KEY] makes the task run (stream-json only, so memory and
# findings calls quoting the prompt don't overwrite it) record its env to
# PROBES/KEY.vars (selected names=values), PROBES/KEY.names (every name) and
# PROBES/KEY.args (argv, one per line).
if [[ "$args" == *"stream-json"* && "$prompt" =~ \\[env-probe:([A-Za-z0-9_-]+)\\] ]]; then
  f="__PROBES__/${BASH_REMATCH[1]}"
  {
    for n in __PROBE_VARS__; do
      if [[ -n "${!n+x}" ]]; then printf '%s=%s\\n' "$n" "${!n}"; fi
    done
    if [[ -n "$CO_API_TOKEN" ]]; then echo "CO_API_TOKEN=present"; fi
  } >"$f.vars.tmp"
  env | cut -d= -f1 >"$f.names"
  printf '%s\\n' "$@" >"$f.args"
  # co-guard: the policy file (deleted at task end) and its mode, the task dir's mode
  if [[ -n "$CO_POLICY_FILE" && -f "$CO_POLICY_FILE" ]]; then
    cp "$CO_POLICY_FILE" "$f.policy"
    (stat -c %a "$CO_POLICY_FILE" 2>/dev/null || stat -f %Lp "$CO_POLICY_FILE") >"$f.policymode"
    (stat -c %a "$CO_TASK_DIR" 2>/dev/null || stat -f %Lp "$CO_TASK_DIR") >"$f.taskdirmode"
  fi
  mv "$f.vars.tmp" "$f.vars"
fi
# Server-side one-shot calls (memory: --print; findings: --print --json-schema)
# whose stdin carries [call-probe:KEY] record to PROBES/KEY-print or KEY-schema.
if [[ "$args" != *"stream-json"* && "$prompt" =~ \\[call-probe:([A-Za-z0-9_-]+)\\] ]]; then
  kind=print
  if [[ "$args" == *"json-schema"* ]]; then kind=schema; fi
  f="__PROBES__/${BASH_REMATCH[1]}-$kind"
  {
    for n in __PROBE_VARS__; do
      if [[ -n "${!n+x}" ]]; then printf '%s=%s\\n' "$n" "${!n}"; fi
    done
  } >"$f.vars.tmp.$$"
  env | cut -d= -f1 >"$f.names.tmp.$$"
  mv "$f.names.tmp.$$" "$f.names"
  mv "$f.vars.tmp.$$" "$f.vars"
fi
if [[ "$args" == *"stream-json"* && "$prompt" == *"[guard-spool]"* ]]; then
  __GUARD_SPOOL__
fi
if [[ "$args" == *"stream-json"* && "$prompt" == *"[env-echo"* ]]; then
  __ENV_ECHO__
fi
if [[ "$args" == *"stream-json"* ]]; then
  printf '{"type":"system","subtype":"init","session_id":"test-session"}\\n'
  printf '{"type":"assistant","message":{"model":"claude-test","content":[{"type":"text","text":"working"}]}}\\n'
  printf '{"type":"result","subtype":"success","result":"done","num_turns":1,"total_cost_usd":0.01,"duration_ms":100,"is_error":false,"usage":{"input_tokens":100,"output_tokens":50}}\\n'
elif [[ "$args" == *"json-schema"* ]]; then
  printf '{"findings":[]}\\n'
elif [[ "$prompt" == *"[env-echo"* ]]; then
  # Memory calls quoting an [env-echo] task also echo the env (summary/memory).
  dump=""
  for n in __ECHO_VARS__; do
    if [[ -n "${!n+x}" ]]; then dump+="$n=${!n} "; fi
  done
  printf 'summary %s\\n' "$dump"
else
  printf 'summary\\n'
fi
""".replace("__PROBES__", str(_PROBES))
    .replace("__PROBE_VARS__", _PROBE_VARS)
    .replace("__ENV_ECHO__", _ENV_ECHO)
    .replace("__GUARD_SPOOL__", _GUARD_SPOOL)
    .replace("__ECHO_VARS__", _ECHO_VARS)
)
_MOCK.chmod(0o755)

# A pre-set CO_DATABASE_URL (e.g. the Postgres CI job) wins over the temp SQLite DB.
os.environ.setdefault("CO_DATABASE_URL", f"sqlite+aiosqlite:///{_TMP}/test.db")
os.environ.update(
    {
        "CO_DB_NULL_POOL": "1",
        "CO_WORKSPACES_DIR": f"{_TMP}/workspaces",
        "CO_PROJECTS_DIR": f"{_TMP}/projects",
        "CO_SECRET_KEY_FILE": f"{_TMP}/.secret.key",
        "CO_CLAUDE_BIN": str(_MOCK),
        "CO_MCP_HEALTH_INTERVAL_SECONDS": "99999",
        "CO_SCHEDULER_INTERVAL_SECONDS": "99999",
        "CO_WORKFLOW_RUNNER_INTERVAL_SECONDS": "1",
        "CO_APPROVAL_CHECK_INTERVAL_SECONDS": "99999",
        "CO_GITHUB_WEBHOOK_SECRET": "testsecret",
        "CO_ADMIN_EMAIL": "admin@test.local",
        "CO_ADMIN_PASSWORD": "test-password-123",
        "CO_RETRY_MAX_ATTEMPTS": "0",
        "CO_GATE_CRITICAL_APPROVAL": "true",
    }
)


ADMIN_EMAIL = os.environ["CO_ADMIN_EMAIL"]
ADMIN_PASSWORD = os.environ["CO_ADMIN_PASSWORD"]
MEMBER_EMAIL = "member@test.local"
_ADMIN_CLIENT = None  # set by the session-scoped `client` fixture


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:  # runs lifespan → init_db, worker, scheduler
        r = c.post("/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200, r.text
        c.headers["X-Requested-With"] = "co"
        # Tasks need a Claude credential; an org fallback lets every test run
        # without one. Tests that need "no fallback" delete and restore it.
        r = c.put("/admin/org-fallback", json={"api_key": "sk-test-fallback"})
        assert r.status_code == 200, r.status_code
        global _ADMIN_CLIENT
        _ADMIN_CLIENT = c
        yield c


@pytest.fixture(scope="session")
def anon_client(client):
    """No session cookie (but the CSRF header, so 401 vs 403 is unambiguous)."""
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    c.headers["X-Requested-With"] = "co"
    return c


@pytest.fixture(scope="session")
def member_client(client):
    """A logged-in non-admin. Created directly in the DB until the /users API
    exists (Task 6)."""
    import asyncio

    from fastapi.testclient import TestClient

    from app import auth
    from app.database import SessionLocal
    from app.main import app
    from app.models import User

    async def mk():
        async with SessionLocal() as s:
            s.add(
                User(
                    email=MEMBER_EMAIL,
                    name="Member",
                    password_hash=auth.hash_password(ADMIN_PASSWORD),
                    role="member",
                )
            )
            await s.commit()

    asyncio.run(mk())
    c = TestClient(app)
    r = c.post("/auth/login", json={"email": MEMBER_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    c.headers["X-Requested-With"] = "co"
    return c


def wait_task(client, task_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = client.get(f"/tasks/{task_id}").json()
        if t["status"] in ("completed", "failed", "cancelled"):
            return t
        time.sleep(0.3)
    return client.get(f"/tasks/{task_id}").json()


def env_probe_key() -> str:
    """A fresh key; put f"[env-probe:{key}]" in a task prompt to record its env."""
    import uuid

    return "p" + uuid.uuid4().hex


def read_env_probe(key: str) -> tuple[dict[str, str], set[str]] | None:
    """(selected name=value pairs, every env name) of the probed run, or None
    if the mock never ran for that key."""
    vars_f = _PROBES / f"{key}.vars"
    if not vars_f.exists():
        return None
    pairs = dict(
        line.split("=", 1) for line in vars_f.read_text().splitlines() if "=" in line
    )
    names = set((_PROBES / f"{key}.names").read_text().split())
    return pairs, names


def read_probe_args(key: str) -> list[str] | None:
    """argv (one per line) of the probed task run, or None if it never ran."""
    f = _PROBES / f"{key}.args"
    return f.read_text().splitlines() if f.exists() else None


def read_probe_file(key: str, suffix: str) -> str | None:
    """Another file the probed run recorded (policy, policymode, taskdirmode)."""
    f = _PROBES / f"{key}.{suffix}"
    return f.read_text() if f.exists() else None


def clear_env_probe(key: str) -> None:
    for suffix in ("vars", "names", "args", "policy", "policymode", "taskdirmode"):
        (_PROBES / f"{key}.{suffix}").unlink(missing_ok=True)


def login_as(email: str, role: str = "member"):
    """Create a user via the admin /users API and return a logged-in client
    (CSRF header set). Pass a unique email per call site to stay order-independent."""
    from fastapi.testclient import TestClient

    from app.main import app

    # Reuse the session-scoped admin client (tests using login_as request `client`).
    admin = _ADMIN_CLIENT
    assert admin is not None, "login_as requires the `client` fixture"
    r = admin.post(
        "/users",
        json={"email": email, "name": email.split("@")[0], "role": role, "password": ADMIN_PASSWORD},
    )
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    c = TestClient(app)
    r = c.post("/auth/login", json={"email": email, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    c.headers["X-Requested-With"] = "co"
    c.user_id = uid
    return c
