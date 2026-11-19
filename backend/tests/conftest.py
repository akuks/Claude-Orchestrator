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
_MOCK.write_text(
    """#!/usr/bin/env bash
args="$*"
cat >/dev/null
# Test seam: report whether the scoped agent token reached the subprocess env
# (presence only, never the value).
if [[ -n "$CO_TEST_ENV_PROBE" ]]; then
  if [[ -n "$CO_API_TOKEN" ]]; then echo present >"$CO_TEST_ENV_PROBE"; else echo absent >"$CO_TEST_ENV_PROBE"; fi
fi
if [[ "$args" == *"stream-json"* ]]; then
  printf '{"type":"system","subtype":"init","session_id":"test-session"}\\n'
  printf '{"type":"assistant","message":{"model":"claude-test","content":[{"type":"text","text":"working"}]}}\\n'
  printf '{"type":"result","subtype":"success","result":"done","num_turns":1,"total_cost_usd":0.01,"duration_ms":100,"is_error":false,"usage":{"input_tokens":100,"output_tokens":50}}\\n'
elif [[ "$args" == *"json-schema"* ]]; then
  printf '{"findings":[]}\\n'
else
  printf 'summary\\n'
fi
"""
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


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:  # runs lifespan → init_db, worker, scheduler
        r = c.post("/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200, r.text
        c.headers["X-Requested-With"] = "co"
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
