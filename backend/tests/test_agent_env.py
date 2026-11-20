"""Clean per-user agent env (Phase 3a, Task 2).

A task's subprocess env is the allowlisted base + project env + the task
creator's own credentials (or the org fallback key) + per-user config dirs.
The mock claude records its env when the prompt carries [env-probe:KEY]
(conftest). Every credential below is an obviously fake test string."""

import asyncio
import json
import os
import stat
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app import agent_env
from app.config import settings
from app.credentials import MISSING_CLAUDE
from app.database import SessionLocal
from app.models import TaskEvent
from conftest import clear_env_probe, env_probe_key, login_as, read_env_probe, wait_task

FALLBACK = "sk-test-fallback"  # what conftest sets for the whole session
CLAUDE_VARS = {"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"}


def _uid():
    return uuid.uuid4().hex[:8]


def _fake(kind: str) -> str:
    return f"fake-{kind}-{uuid.uuid4().hex}"


def _restore_fallback(admin):
    r = admin.put("/admin/org-fallback", json={"api_key": FALLBACK})
    assert r.status_code == 200, r.status_code


def _run_probed(c, extra: dict | None = None):
    key = env_probe_key()
    t = c.post("/tasks", json={"prompt": f"show env [env-probe:{key}]", **(extra or {})})
    assert t.status_code == 201, t.text
    done = wait_task(c, t.json()["id"])
    return done, key


def _events(task_id):
    async def go():
        async with SessionLocal() as s:
            return (
                await s.execute(
                    select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.seq)
                )
            ).scalars().all()

    return asyncio.run(go())


def test_server_secrets_not_inherited(client, monkeypatch):
    server = {
        "ANTHROPIC_API_KEY": "server-anthropic-must-not-leak",
        "GH_TOKEN": "server-gh-must-not-leak",
        "GITHUB_TOKEN": "server-github-must-not-leak",
        "CLAUDE_CODE_OAUTH_TOKEN": "server-oauth-must-not-leak",
        "RANDOM_SERVER_VAR": "server-random",
    }
    for k, v in server.items():
        monkeypatch.setenv(k, v)

    # A user with nothing of their own runs on the org fallback only.
    m = login_as(f"env-s-{_uid()}@test.local")
    done, key = _run_probed(m)
    assert done["status"] == "completed", done.get("error")
    pairs, names = read_env_probe(key)
    assert pairs.get("ANTHROPIC_API_KEY") == FALLBACK
    for n in ("GH_TOKEN", "GITHUB_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "RANDOM_SERVER_VAR"):
        assert n not in names, n
    assert not set(server.values()) & set(pairs.values())

    # A user with their own OAuth token: no API key at all (not the server's).
    m2 = login_as(f"env-s2-{_uid()}@test.local")
    tok = _fake("oauth")
    m2.put("/me/credentials", json={"claude_token": tok, "claude_kind": "oauth_token"})
    done, key = _run_probed(m2)
    assert done["status"] == "completed", done.get("error")
    pairs, names = read_env_probe(key)
    assert pairs["CLAUDE_CODE_OAUTH_TOKEN"] == tok
    assert "ANTHROPIC_API_KEY" not in names
    assert not set(server.values()) & set(pairs.values())


def test_owner_credentials_injected(client):
    m = login_as(f"env-o-{_uid()}@test.local")
    tok, gh = _fake("oauth"), _fake("gh")
    r = m.put("/me/credentials", json={
        "claude_token": tok, "claude_kind": "oauth_token", "github_token": gh,
        "git_name": "Ada Tester", "git_email": "ada@test.local",
    })
    assert r.status_code == 200
    done, key = _run_probed(m)
    assert done["status"] == "completed", done.get("error")
    pairs, names = read_env_probe(key)
    assert pairs["CLAUDE_CODE_OAUTH_TOKEN"] == tok
    assert names & CLAUDE_VARS == {"CLAUDE_CODE_OAUTH_TOKEN"}  # exactly one
    assert pairs["GH_TOKEN"] == gh
    assert pairs["GIT_AUTHOR_NAME"] == pairs["GIT_COMMITTER_NAME"] == "Ada Tester"
    assert pairs["GIT_AUTHOR_EMAIL"] == pairs["GIT_COMMITTER_EMAIL"] == "ada@test.local"
    assert pairs["CO_API_TOKEN"] == "present"

    # The env event names what was applied, never a value.
    ev = [e for e in _events(done["id"]) if e.type == "env"]
    assert ev and ev[0].payload["credentials"] == ["claude", "github"]
    blob = json.dumps([e.payload for e in _events(done["id"])])
    assert tok not in blob and gh not in blob

    # Switching to an API key: the OAuth token is gone, never both.
    key2 = _fake("apikey")
    m.put("/me/credentials", json={"claude_token": key2, "claude_kind": "api_key"})
    done, key = _run_probed(m)
    assert done["status"] == "completed", done.get("error")
    pairs, names = read_env_probe(key)
    assert pairs["ANTHROPIC_API_KEY"] == key2
    assert names & CLAUDE_VARS == {"ANTHROPIC_API_KEY"}


def test_fallback_used_then_removed(client):
    m = login_as(f"env-f-{_uid()}@test.local")
    done, key = _run_probed(m)
    assert done["status"] == "completed", done.get("error")
    pairs, names = read_env_probe(key)
    assert pairs["ANTHROPIC_API_KEY"] == FALLBACK
    assert names & CLAUDE_VARS == {"ANTHROPIC_API_KEY"}
    clear_env_probe(key)
    try:
        assert client.delete("/admin/org-fallback").status_code == 200
        r = m.post(f"/tasks/{done['id']}/retry")
        assert r.status_code == 200, r.text
        again = wait_task(m, done["id"])
    finally:
        _restore_fallback(client)
    assert again["status"] == "failed"
    assert again["error"] == MISSING_CLAUDE
    assert read_env_probe(key) is None  # claude was never spawned
    errs = [e.payload["message"] for e in _events(done["id"]) if e.type == "error"]
    assert MISSING_CLAUDE in errs


def test_missing_credential_fails_task_with_message(client):
    m = login_as(f"env-m-{_uid()}@test.local")
    try:
        assert client.delete("/admin/org-fallback").status_code == 200
        done, key = _run_probed(m)
    finally:
        _restore_fallback(client)
    assert done["status"] == "failed"
    assert done["error"] == MISSING_CLAUDE
    assert read_env_probe(key) is None
    assert any(
        e.type == "error" and e.payload.get("message") == MISSING_CLAUDE for e in _events(done["id"])
    )

    # Legacy ownerless tasks never borrow anyone's credential, even the fallback.
    async def legacy():
        async with SessionLocal() as s:
            await agent_env.build_agent_env(s, None)

    with pytest.raises(agent_env.MissingCredential) as ei:
        asyncio.run(legacy())
    assert str(ei.value) == MISSING_CLAUDE


def _mode(p: Path) -> int:
    return stat.S_IMODE(os.stat(p).st_mode)


def test_per_user_config_dirs(client):
    a = login_as(f"env-d-{_uid()}@test.local")
    b = login_as(f"env-d2-{_uid()}@test.local")
    gh = _fake("gh")
    a.put("/me/credentials", json={
        "github_token": gh, "git_name": "Dir Tester", "git_email": "dir@test.local",
    })
    seen = {}
    for c in (a, b):
        done, key = _run_probed(c)
        assert done["status"] == "completed", done.get("error")
        pairs, _ = read_env_probe(key)
        root = Path(settings.workspaces_dir) / "_users" / c.user_id
        assert pairs["CLAUDE_CONFIG_DIR"] == str(root / "claude")
        assert pairs["GH_CONFIG_DIR"] == str(root / "gh")
        assert pairs["GIT_CONFIG_GLOBAL"] == str(root / "gitconfig")
        for d in (root, root / "claude", root / "gh"):
            assert d.is_dir() and _mode(d) == 0o700, d
        assert _mode(root / "gitconfig") == 0o600
        seen[c.user_id] = pairs
    assert seen[a.user_id]["CLAUDE_CONFIG_DIR"] != seen[b.user_id]["CLAUDE_CONFIG_DIR"]

    cfg_a = (Path(settings.workspaces_dir) / "_users" / a.user_id / "gitconfig").read_text()
    assert "Dir Tester" in cfg_a and "dir@test.local" in cfg_a
    assert "[safe]" in cfg_a and "directory = *" in cfg_a
    assert gh not in cfg_a  # no secret in a file
    cfg_b = (Path(settings.workspaces_dir) / "_users" / b.user_id / "gitconfig").read_text()
    assert "[user]" not in cfg_b and "directory = *" in cfg_b


def test_project_env_still_applied(client):
    p = client.post("/projects", json={
        "name": f"EnvApplied-{_uid()}",
        "env": {
            "PATH_PREPEND": "/opt/fake-toolchain/bin",
            "PROBE_PROJECT_VAR": "from-project",
            # Credentials win over project env.
            "ANTHROPIC_API_KEY": "project-env-key",
            "CLAUDE_CODE_OAUTH_TOKEN": "project-env-oauth",
        },
    })
    assert p.status_code == 201, p.text
    pid = p.json()["id"]
    done, key = _run_probed(client, {"project_id": pid})
    assert done["status"] == "completed", done.get("error")
    pairs, names = read_env_probe(key)
    assert pairs["PATH"].startswith("/opt/fake-toolchain/bin" + os.pathsep)
    assert pairs["PROBE_PROJECT_VAR"] == "from-project"
    assert pairs["ANTHROPIC_API_KEY"] == FALLBACK  # the admin has no own credential
    assert names & CLAUDE_VARS == {"ANTHROPIC_API_KEY"}

    ev = [e for e in _events(done["id"]) if e.type == "env"]
    assert ev
    payload = ev[0].payload
    assert "PROBE_PROJECT_VAR" in payload["applied"] and "PATH_PREPEND" in payload["applied"]
    assert payload["credentials"] == ["claude (org fallback)"]
    blob = json.dumps(payload)
    assert "from-project" not in blob and "/opt/fake-toolchain" not in blob
    assert FALLBACK not in blob
