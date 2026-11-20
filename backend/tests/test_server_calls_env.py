"""Server-side Claude calls and follow-ups run as the right person (Phase 3a, Task 3).

Memory updates / findings extraction run with the task owner's env; a manual
memory regenerate runs as the clicking user; a follow-up by someone other than
the parent's creator starts a fresh session; /system/claude reports the
caller's credential status. The mock claude records the env of a one-shot
call whose stdin carries [call-probe:KEY] (conftest). Every credential here is
an obviously fake test string."""

import asyncio
import json
import time
import uuid

from app import agent_env, security
from app.credentials import MISSING_CLAUDE
from app.database import SessionLocal
from app.models import Task, TaskSummary
from conftest import login_as, read_env_probe, wait_task

FALLBACK = "sk-test-fallback"
CLAUDE_VARS = {"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"}
SERVER = {
    "ANTHROPIC_API_KEY": "server-anthropic-must-not-leak",
    "CLAUDE_CODE_OAUTH_TOKEN": "server-oauth-must-not-leak",
    "GH_TOKEN": "server-gh-must-not-leak",
    "GITHUB_TOKEN": "server-github-must-not-leak",
    "RANDOM_SERVER_VAR": "server-random",
}


def _uid():
    return uuid.uuid4().hex[:8]


def _fake(kind: str) -> str:
    return f"fake-{kind}-{uuid.uuid4().hex}"


def _key() -> str:
    return "c" + uuid.uuid4().hex


def _restore_fallback(admin):
    r = admin.put("/admin/org-fallback", json={"api_key": FALLBACK})
    assert r.status_code == 200, r.status_code


def _server_env(monkeypatch):
    for k, v in SERVER.items():
        monkeypatch.setenv(k, v)


def _project(admin, **extra):
    r = admin.post("/projects", json={"name": f"sc-{_uid()}", **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _grant(admin, pid, user_id, role):
    keep = [
        {k: m[k] for k in ("subject_type", "subject_id", "role")}
        for m in admin.get(f"/projects/{pid}/members").json()
        if m["subject_id"] != user_id
    ]
    keep.append({"subject_type": "user", "subject_id": user_id, "role": role})
    r = admin.put(f"/projects/{pid}/members", json=keep)
    assert r.status_code == 200, r.text


def _wait_probe(key: str, timeout: float = 15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        got = read_env_probe(key)
        if got is not None:
            return got
        time.sleep(0.2)
    return None


def _db(coro_fn):
    async def go():
        async with SessionLocal() as s:
            return await coro_fn(s)

    return asyncio.run(go())


def test_memory_update_uses_owner_env(client, monkeypatch):
    _server_env(monkeypatch)
    pid = _project(client, env={"PROBE_PROJECT_VAR": "from-project"})
    owner = login_as(f"sc-mem-{_uid()}@test.local")
    _grant(client, pid, owner.user_id, "contributor")
    tok, gh = _fake("oauth"), _fake("gh")
    r = owner.put("/me/credentials", json={
        "claude_token": tok, "claude_kind": "oauth_token", "github_token": gh,
    })
    assert r.status_code == 200
    key = _key()
    t = owner.post("/tasks", json={"prompt": f"do work [call-probe:{key}]", "project_id": pid})
    assert t.status_code == 201, t.text
    done = wait_task(owner, t.json()["id"])
    assert done["status"] == "completed", done.get("error")

    got = _wait_probe(f"{key}-print")
    assert got is not None, "memory update never ran claude"
    pairs, names = got
    assert pairs["CLAUDE_CODE_OAUTH_TOKEN"] == tok
    assert names & CLAUDE_VARS == {"CLAUDE_CODE_OAUTH_TOKEN"}
    assert pairs["GH_TOKEN"] == gh
    assert pairs["PROBE_PROJECT_VAR"] == "from-project"
    assert "GITHUB_TOKEN" not in names and "RANDOM_SERVER_VAR" not in names
    assert not set(SERVER.values()) & set(pairs.values())
    assert not any(n.startswith("CO_") for n in names)


def test_memory_update_skips_quietly_without_credential(client):
    pid = _project(client)
    owner = login_as(f"sc-memskip-{_uid()}@test.local")
    _grant(client, pid, owner.user_id, "contributor")

    async def mk(s):
        t = Task(
            title="t", prompt="p", project_id=pid, status="completed",
            result_text="r", created_by=owner.user_id,
        )
        s.add(t)
        await s.commit()
        return t.id

    tid = _db(mk)
    from app import memory

    try:
        assert client.delete("/admin/org-fallback").status_code == 200
        asyncio.run(memory.update_after_task(tid, owner.user_id))  # no exception
    finally:
        _restore_fallback(client)

    async def summaries(s):
        from sqlalchemy import select

        return (
            await s.execute(select(TaskSummary).where(TaskSummary.task_id == tid))
        ).scalars().all()

    assert _db(summaries) == []


def test_findings_extraction_uses_owner_env(client, monkeypatch):
    _server_env(monkeypatch)
    pid = _project(client)
    owner = login_as(f"sc-sec-{_uid()}@test.local")
    _grant(client, pid, owner.user_id, "contributor")
    tok = _fake("apikey")
    r = owner.put("/me/credentials", json={"claude_token": tok, "claude_kind": "api_key"})
    assert r.status_code == 200, r.status_code
    key = _key()

    async def mk(s):
        t = Task(
            title="Security review (full)", prompt="scan", project_id=pid,
            status="completed", tags=["vapt"], created_by=owner.user_id,
            result_text=f"## Summary\nNo issues. [call-probe:{key}]",
        )
        s.add(t)
        await s.commit()
        return t.id

    tid = _db(mk)
    asyncio.run(security.extract_findings(tid, owner.user_id))
    got = read_env_probe(f"{key}-schema")
    assert got is not None
    pairs, names = got
    assert pairs["ANTHROPIC_API_KEY"] == tok
    assert names & CLAUDE_VARS == {"ANTHROPIC_API_KEY"}
    assert not set(SERVER.values()) & set(pairs.values())


def _seed_summary(pid: str, text: str):
    async def mk(s):
        s.add(TaskSummary(task_id=uuid.uuid4().hex, project_id=pid, title="seed", summary=text))
        await s.commit()

    _db(mk)


def test_manual_regenerate_uses_clicker(client, monkeypatch):
    _server_env(monkeypatch)
    pid = _project(client)
    clicker = login_as(f"sc-regen-{_uid()}@test.local")
    _grant(client, pid, clicker.user_id, "maintainer")
    tok = _fake("oauth")
    r = clicker.put("/me/credentials", json={"claude_token": tok, "claude_kind": "oauth_token"})
    assert r.status_code == 200, r.status_code
    key = _key()
    _seed_summary(pid, f"did a thing [call-probe:{key}]")

    r = clicker.post(f"/projects/{pid}/memory/regenerate")
    assert r.status_code == 200, r.text
    got = read_env_probe(f"{key}-print")
    assert got is not None
    pairs, names = got
    assert pairs["CLAUDE_CODE_OAUTH_TOKEN"] == tok
    assert names & CLAUDE_VARS == {"CLAUDE_CODE_OAUTH_TOKEN"}
    assert not set(SERVER.values()) & set(pairs.values())

    # A maintainer with no credential and no fallback: 409 with the message.
    other = login_as(f"sc-regen2-{_uid()}@test.local")
    _grant(client, pid, other.user_id, "maintainer")
    key2 = _key()
    _seed_summary(pid, f"another [call-probe:{key2}]")
    try:
        assert client.delete("/admin/org-fallback").status_code == 200
        r = other.post(f"/projects/{pid}/memory/regenerate")
    finally:
        _restore_fallback(client)
    assert r.status_code == 409
    assert r.json()["detail"] == MISSING_CLAUDE
    assert read_env_probe(f"{key2}-print") is None


def _resume_of(task_id):
    async def go(s):
        return (await s.get(Task, task_id)).resume_session_id

    return _db(go)


def _thread_parent(client):
    pid = _project(client)
    owner = login_as(f"sc-fu-{_uid()}@test.local")
    _grant(client, pid, owner.user_id, "contributor")
    t = owner.post("/tasks", json={"prompt": "parent work", "project_id": pid})
    assert t.status_code == 201, t.text
    done = wait_task(owner, t.json()["id"])
    assert done["status"] == "completed", done.get("error")
    assert done["session_id"]
    return pid, owner, done


def test_followup_same_user_resumes(client):
    _, owner, parent = _thread_parent(client)
    r = owner.post(f"/tasks/{parent['id']}/followup", json={"prompt": "more"})
    assert r.status_code == 201, r.text
    assert _resume_of(r.json()["id"]) == parent["session_id"]
    wait_task(owner, r.json()["id"])


def test_followup_by_other_user_no_resume(client):
    pid, _, parent = _thread_parent(client)
    mate = login_as(f"sc-fu2-{_uid()}@test.local")
    _grant(client, pid, mate.user_id, "maintainer")
    r = mate.post(f"/tasks/{parent['id']}/followup", json={"prompt": "more"})
    assert r.status_code == 201, r.text
    child = r.json()
    assert child["created_by"] == mate.user_id
    assert child["root_id"] == parent["id"]  # still the same thread
    assert _resume_of(child["id"]) is None  # fresh session on the sender's account
    done = wait_task(mate, child["id"])
    assert done["status"] == "completed", done.get("error")


def test_system_claude_reports_you(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "server-anthropic-must-not-leak")
    # No own credential, fallback set → fallback.
    m = login_as(f"sc-sys-{_uid()}@test.local")
    d = m.get("/system/claude").json()
    for k in ("claude_bin", "found", "version", "authenticated", "auth_method"):
        assert k in d
    assert d["found"] is True
    assert d["you"]["claude"] == "fallback"
    assert d["authenticated"] is True and d["auth_method"] == "fallback"

    # Own credential → own; no secret anywhere in the response.
    tok, gh = _fake("oauth"), _fake("gh")
    m.put("/me/credentials", json={
        "claude_token": tok, "claude_kind": "oauth_token", "github_token": gh,
    })
    d = m.get("/system/claude").json()
    assert d["you"]["claude"] == "own" and d["you"]["claude_kind"] == "oauth_token"
    assert d["you"]["github"] is True
    assert d["authenticated"] is True and d["auth_method"] == "own"
    blob = json.dumps(d)
    assert tok not in blob and gh not in blob and "server-anthropic" not in blob

    # Nothing and no fallback → none, not "authenticated" by the server's key.
    n = login_as(f"sc-sys2-{_uid()}@test.local")
    try:
        assert client.delete("/admin/org-fallback").status_code == 200
        d = n.get("/system/claude").json()
    finally:
        _restore_fallback(client)
    assert d["you"]["claude"] == "none"
    assert d["authenticated"] is False and d["auth_method"] is None
    assert FALLBACK not in json.dumps(d)


def test_project_env_cannot_set_auth_or_routing(client):
    hostile = {
        "ANTHROPIC_API_KEY": "proj-key",
        "CLAUDE_CODE_OAUTH_TOKEN": "proj-oauth",
        "ANTHROPIC_AUTH_TOKEN": "proj-auth-token",
        "ANTHROPIC_BASE_URL": "https://proj.invalid",
        "GITHUB_TOKEN": "proj-github",
        "GH_TOKEN": "proj-gh",
        "PROBE_PROJECT_VAR": "kept",
    }
    m = login_as(f"sc-pe-{_uid()}@test.local")

    async def build(s):
        return dict(await agent_env.build_agent_env(s, m.user_id, project_env=hostile))

    env = _db(build)  # no own credential: org fallback, no GitHub token
    assert env["ANTHROPIC_API_KEY"] == FALLBACK
    for n in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
              "GITHUB_TOKEN", "GH_TOKEN"):
        assert n not in env, n
    assert env["PROBE_PROJECT_VAR"] == "kept"

    gh = _fake("gh")
    m.put("/me/credentials", json={"github_token": gh})
    env = _db(build)
    assert env["GH_TOKEN"] == gh and "GITHUB_TOKEN" not in env


def test_build_agent_env_allow_fallback_false(client):
    m = login_as(f"sc-af-{_uid()}@test.local")

    async def build(s):
        return await agent_env.build_agent_env(s, m.user_id, allow_fallback=False)

    import pytest

    with pytest.raises(agent_env.MissingCredential):
        _db(build)
    tok = _fake("oauth")
    m.put("/me/credentials", json={"claude_token": tok, "claude_kind": "oauth_token"})
    assert _db(build)["CLAUDE_CODE_OAUTH_TOKEN"] == tok
