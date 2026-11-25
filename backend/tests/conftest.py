"""Test fixtures. Env is configured BEFORE importing the app so config picks it
up, and a mock `claude` binary stands in for the real CLI (stream-json for tasks,
structured JSON for --json-schema, plain text otherwise)."""

import contextlib
import os
import pathlib
import tempfile
import time

import pytest

_TMP = tempfile.mkdtemp(prefix="co-tests-")
_MOCK = pathlib.Path(_TMP) / "mock_claude.sh"
_PROBES = pathlib.Path(_TMP) / "env-probes"
_PROBES.mkdir()
_FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "stream"
# The mock's script lives in tests/mock_claude.sh (its seams are listed in its
# header); the two placeholders are filled here, and by the wrapper a scratch
# server uses.
_MOCK.write_text(
    (pathlib.Path(__file__).parent / "mock_claude.sh").read_text()
    .replace("__PROBES__", str(_PROBES))
    .replace("__FIXTURES__", str(_FIXTURES))
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
        "CO_CODE_MAP_SCAN_INTERVAL_SECONDS": "99999",
        # tests sweep explicitly (WorkerManager._sweep_once)
        "CO_RECOVERY_SWEEP_SECONDS": "99999",
        "CO_GITHUB_WEBHOOK_SECRET": "testsecret",
        "CO_ADMIN_EMAIL": "admin@test.local",
        "CO_ADMIN_PASSWORD": "test-password-123",
        "CO_RETRY_MAX_ATTEMPTS": "0",
        "CO_GATE_CRITICAL_APPROVAL": "true",
        # No server listens on the approval address in tests; test_guard_worker
        # turns the check on against a real listener.
        "CO_GUARD_VERIFY_API": "false",
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
    for suffix in ("vars", "names", "args", "policy", "policymode", "taskdirmode", "handoff"):
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


@contextlib.contextmanager
def time_limit(limit: float):
    """Passes when wall time OR this process's CPU time is under ``limit`` (a busy CI box stretches wall time)."""
    w, c = time.monotonic(), time.process_time()
    yield
    wall, cpu = time.monotonic() - w, time.process_time() - c
    assert wall < limit or cpu < limit, f"wall {wall:.2f}s and cpu {cpu:.2f}s both >= {limit}s"


@pytest.fixture
def fail_after_60s():
    """Fail a test that runs over 60 s (e.g. a lock wait on Postgres) instead of freezing the run; SIGALRM, main
    thread only. Use with ``pytestmark = pytest.mark.usefixtures("fail_after_60s")``."""
    import signal

    def boom(*_):
        raise TimeoutError("test ran over 60 s (a lock wait?)")

    old = signal.signal(signal.SIGALRM, boom)
    signal.alarm(60)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
