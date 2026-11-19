"""Ownership (created_by) on user-owned rows + scoped per-task agent tokens."""

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import auth
from app.database import SessionLocal
from app.main import app
from app.models import ApiToken, Task, User
from app.routers.auth import assign_orphans_to
from conftest import ADMIN_EMAIL, wait_task


def _admin_id() -> str:
    async def go():
        async with SessionLocal() as s:
            return (await s.execute(select(User.id).where(User.email == ADMIN_EMAIL))).scalar_one()

    return asyncio.run(go())


def _mint(task_id: str, user_id: str) -> str:
    async def go():
        async with SessionLocal() as s:
            raw = await auth.mint_token(s, user_id, "agent-task", task_id, 600)
            await s.commit()
            return raw

    return asyncio.run(go())


def _agent_client(raw: str) -> TestClient:
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {raw}"
    return c


def _agent(client, name, **kw):
    r = client.post("/agents", json={"name": name, "system_prompt": "x", **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_created_by_set_on_api_create(client):
    aid = _admin_id()
    t = client.post("/tasks", json={"prompt": "own me", "requires_approval": True}).json()
    p = client.post("/projects", json={"name": "OwnProj"}).json()
    a = _agent(client, "own-agent")
    s = client.post(
        "/schedules", json={"name": "own-sch", "cron": "0 3 * * *", "prompt": "hi"}
    ).json()
    assert t["created_by"] == aid
    assert client.get(f"/tasks/{t['id']}").json()["created_by"] == aid
    assert p["id"]
    asyncio.run(_assert_owner(aid, p["id"], a["id"], s["id"]))


async def _assert_owner(aid, project_id, agent_id, schedule_id):
    from app.models import Agent, Project, Schedule

    async with SessionLocal() as s:
        assert (await s.get(Project, project_id)).created_by == aid
        assert (await s.get(Agent, agent_id)).created_by == aid
        assert (await s.get(Schedule, schedule_id)).created_by == aid


def test_dispatch_children_inherit_creator(client):
    aid = _admin_id()
    _agent(client, "kid-agent")
    parent = client.post("/tasks", json={"prompt": "coord", "requires_approval": True}).json()
    kids = client.post(
        "/agents/dispatch",
        json={"parent_task_id": parent["id"], "items": [{"agent": "kid-agent", "prompt": "do"}]},
    )
    assert kids.status_code == 201, kids.text
    assert all(k["created_by"] == aid for k in kids.json())

    # Through a scoped token: still the parent's creator.
    raw = _mint(parent["id"], aid)
    r = _agent_client(raw).post(
        "/agents/dispatch",
        json={"parent_task_id": parent["id"], "items": [{"agent": "kid-agent", "prompt": "x"}]},
    )
    assert r.status_code == 201, r.text
    assert r.json()[0]["created_by"] == aid


def test_orphans_assigned_to_first_admin(client):
    aid = _admin_id()

    async def go():
        async with SessionLocal() as s:
            t = Task(title="legacy", prompt="p", created_by=None)
            s.add(t)
            await s.commit()
            tid = t.id
        await assign_orphans_to(aid)
        async with SessionLocal() as s:
            return (await s.get(Task, tid)).created_by

    assert asyncio.run(go()) == aid


def test_agent_token_scope(client):
    aid = _admin_id()
    _agent(client, "scope-agent")
    mine = client.post("/tasks", json={"prompt": "mine", "requires_approval": True}).json()
    other = client.post("/tasks", json={"prompt": "other", "requires_approval": True}).json()
    c = _agent_client(_mint(mine["id"], aid))
    ok = c.post(
        "/agents/dispatch",
        json={"parent_task_id": mine["id"], "items": [{"agent": "scope-agent", "prompt": "x"}]},
    )
    assert ok.status_code == 201, ok.text
    assert c.get(f"/tasks/{mine['id']}").status_code == 200
    assert c.get(f"/tasks/{other['id']}").status_code == 403
    assert c.post(f"/approvals/{other['id']}/approve").status_code == 403
    assert c.post("/skills", json={"name": "x"}).status_code == 403


def test_agent_token_revoked_after_task(client, monkeypatch, tmp_path):
    probe = tmp_path / "probe"
    monkeypatch.setenv("CO_TEST_ENV_PROBE", str(probe))
    t = client.post("/tasks", json={"prompt": "say hi"}).json()
    done = wait_task(client, t["id"])
    assert done["status"] == "completed"
    # The token reached the subprocess env (presence only)...
    assert probe.read_text().strip() == "present"

    async def rows():
        async with SessionLocal() as s:
            return (
                await s.execute(select(ApiToken).where(ApiToken.task_id == t["id"]))
            ).scalars().all()

    toks = asyncio.run(rows())
    assert toks, "a token should have been minted for the run"
    # ...and is revoked once the task is finished.
    assert all(tok.revoked_at is not None for tok in toks)
    # It never leaked into the prompt.
    assert "co_" not in client.get(f"/tasks/{t['id']}").json()["prompt"]


def test_coordinator_prompt_uses_bearer():
    from app.worker import _coordinator_context

    async def go():
        async with SessionLocal() as s:
            return await _coordinator_context(s, "TID", "ME")

    text = asyncio.run(go())
    assert text.count('Authorization: Bearer $CO_API_TOKEN') == 2
    assert "/agents/dispatch -H" in text


def test_approval_records_decider(client):
    aid = _admin_id()
    a = client.post("/tasks", json={"prompt": "needs ok", "requires_approval": True}).json()
    b = client.post("/tasks", json={"prompt": "no thanks", "requires_approval": True}).json()
    c = client.post("/tasks", json={"prompt": "bulk ok", "requires_approval": True}).json()
    assert client.post(f"/approvals/{a['id']}/approve").json()["decided_by"] == aid
    assert client.post(f"/approvals/{b['id']}/reject", json={}).json()["decided_by"] == aid
    out = client.post("/approvals/approve", json={"task_ids": [c["id"]]}).json()
    assert out[0]["decided_by"] == aid


def _make_member_task_parent(client):
    return client.post("/tasks", json={"prompt": "admin parent", "requires_approval": True}).json()


def test_member_children_owned_by_member_not_parent_owner(client, member_client):
    me = member_client.get("/auth/me").json()["id"]
    admin = _admin_id()
    assert me != admin
    _agent(client, "r8-agent")
    parent = _make_member_task_parent(client)
    assert parent["created_by"] == admin
    d = member_client.post(
        "/agents/dispatch",
        json={"parent_task_id": parent["id"], "items": [{"agent": "r8-agent", "prompt": "x"}]},
    )
    # Non-agent callers pass the parent id only as linkage; owner is the caller.
    assert d.status_code == 201, d.text
    assert d.json()[0]["created_by"] == me
    ag = client.get("/agents").json()
    aid = next(a["id"] for a in ag if a["name"] == "r8-agent")
    r = member_client.post(f"/agents/{aid}/run", json={"parent_task_id": parent["id"], "prompt": "p"})
    assert r.status_code == 201, r.text
    assert r.json()["created_by"] == me


def test_member_followup_owned_by_member(client, member_client):
    me = member_client.get("/auth/me").json()["id"]
    parent = _make_member_task_parent(client)

    async def setsess():
        async with SessionLocal() as s:
            (await s.get(Task, parent["id"])).session_id = "sess-x"
            await s.commit()

    asyncio.run(setsess())
    r = member_client.post(f"/tasks/{parent['id']}/followup", json={"prompt": "more"})
    assert r.status_code == 201, r.text
    assert r.json()["created_by"] == me


def _token_count(task_id):
    async def go():
        async with SessionLocal() as s:
            return len(
                (await s.execute(select(ApiToken).where(ApiToken.task_id == task_id))).scalars().all()
            )

    return asyncio.run(go())


def _live_tokens(task_id):
    async def go():
        async with SessionLocal() as s:
            rows = (
                await s.execute(select(ApiToken).where(ApiToken.task_id == task_id))
            ).scalars().all()
            return [t for t in rows if t.revoked_at is None]

    return asyncio.run(go())


def test_stale_token_revoked_when_a_new_attempt_starts(client, monkeypatch, tmp_path):
    import time

    from app.config import settings

    slow = tmp_path / "slow_claude.sh"
    slow.write_text("#!/usr/bin/env bash\ncat >/dev/null\nsleep 4\n")
    slow.chmod(0o755)
    monkeypatch.setattr(settings, "claude_bin", str(slow))
    aid = _admin_id()
    t = client.post("/tasks", json={"prompt": "restart me", "requires_approval": True}).json()
    stale = _mint(t["id"], aid)  # as left behind by an interrupted run
    assert len(_live_tokens(t["id"])) == 1
    client.post(f"/approvals/{t['id']}/approve")
    # While the new attempt is RUNNING (fresh token minted), the stale one is dead.
    deadline = time.time() + 15
    while time.time() < deadline:
        if _token_count(t["id"]) == 2:  # stale + the new attempt's token
            break
        time.sleep(0.1)
    assert _token_count(t["id"]) == 2
    live = _live_tokens(t["id"])
    assert len(live) == 1  # only the new attempt's token
    assert _agent_client(stale).get(f"/tasks/{t['id']}").status_code == 401
    client.post(f"/tasks/{t['id']}/cancel")
    wait_task(client, t["id"])


def test_token_revoked_during_retry_backoff(client, monkeypatch, tmp_path):
    from app.config import settings

    failing = tmp_path / "fail_claude.sh"
    failing.write_text("#!/usr/bin/env bash\ncat >/dev/null\necho 'rate limit exceeded' >&2\nexit 1\n")
    failing.chmod(0o755)
    monkeypatch.setattr(settings, "claude_bin", str(failing))
    monkeypatch.setattr(settings, "retry_max_attempts", 1)
    monkeypatch.setattr(settings, "retry_backoff_seconds", 3600)  # stays in backoff
    t = client.post("/tasks", json={"prompt": "flaky"}).json()
    import time

    deadline = time.time() + 20
    while time.time() < deadline:
        if client.get(f"/tasks/{t['id']}").json()["attempt"] == 1:
            break
        time.sleep(0.2)
    assert _live_tokens(t["id"]) == []
    client.post(f"/tasks/{t['id']}/cancel")
