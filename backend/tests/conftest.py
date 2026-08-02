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

os.environ.update(
    {
        "CO_DATABASE_URL": f"sqlite+aiosqlite:///{_TMP}/test.db",
        "CO_WORKSPACES_DIR": f"{_TMP}/workspaces",
        "CO_PROJECTS_DIR": f"{_TMP}/projects",
        "CO_SECRET_KEY_FILE": f"{_TMP}/.secret.key",
        "CO_CLAUDE_BIN": str(_MOCK),
        "CO_MCP_HEALTH_INTERVAL_SECONDS": "99999",
        "CO_SCHEDULER_INTERVAL_SECONDS": "99999",
        "CO_APPROVAL_CHECK_INTERVAL_SECONDS": "99999",
        "CO_GITHUB_WEBHOOK_SECRET": "testsecret",
        "CO_RETRY_MAX_ATTEMPTS": "0",
        "CO_GATE_CRITICAL_APPROVAL": "true",
    }
)


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:  # runs lifespan → init_db, worker, scheduler
        yield c


def wait_task(client, task_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = client.get(f"/tasks/{task_id}").json()
        if t["status"] in ("completed", "failed", "cancelled"):
            return t
        time.sleep(0.3)
    return client.get(f"/tasks/{task_id}").json()
