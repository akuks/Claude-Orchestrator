"""Final-review fixes: webhook secret, env scrubbing, sliding sessions,
bootstrap password length, disabled-owner automations."""

import asyncio
import datetime
import hashlib
import hmac
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import auth
from app.config import settings
from app.database import SessionLocal
from app.models import AuthSession, Schedule, Task, TaskEvent, User

from conftest import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    MEMBER_EMAIL,
    env_probe_key,
    read_env_probe,
    wait_task,
)


# ---- 1. webhook secret required --------------------------------------------
def test_webhook_rejects_when_secret_unset(client, monkeypatch):
    monkeypatch.setattr(settings, "github_webhook_secret", "")
    body = json.dumps({"zen": "hi"}).encode()
    sig = "sha256=" + hmac.new(b"", body, hashlib.sha256).hexdigest()
    r = TestClient(client.app).post(
        "/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": sig},
    )
    assert r.status_code == 503
    assert r.json()["detail"] == "GitHub webhook secret not configured"
    from app.routers import webhooks

    assert webhooks._verify(body, None) is False


# ---- 3. agent env scrubbing -------------------------------------------------
def test_agent_env_has_no_co_server_secrets(client, monkeypatch):
    key = env_probe_key()
    monkeypatch.setenv("CO_DATABASE_URL_LEAKCHECK", "postgresql://u:pw@h/db")
    monkeypatch.setenv("CO_SECRET_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    t = client.post("/tasks", json={"prompt": f"print env [env-probe:{key}]"}).json()
    assert wait_task(client, t["id"])["status"] == "completed"
    pairs, got = read_env_probe(key)
    # co-guard's per-task vars (Task 3) are task data, not server config.
    per_task = {"CO_API_TOKEN", "CO_TASK_ID", "CO_API_BASE", "CO_POLICY_FILE", "CO_TASK_DIR"}
    leaked = {n for n in got if n.startswith("CO_") and n not in per_task}
    assert not leaked, leaked
    assert per_task <= got
    assert "CO_ADMIN_PASSWORD" not in got and "CO_DATABASE_URL" not in got
    assert "CO_API_TOKEN" in got  # still minted for the task
    assert {"PATH", "ANTHROPIC_API_KEY"} <= got
    # ...and that ANTHROPIC_API_KEY is the org fallback, not the server's own.
    assert pairs["ANTHROPIC_API_KEY"] == "sk-test-fallback"


# ---- 4. sliding session -----------------------------------------------------
def _login_client():
    c = TestClient(__import__("app.main", fromlist=["app"]).app)
    c.headers["X-Requested-With"] = "co"
    r = c.post("/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200
    return c


def _sess(c):
    async def go():
        async with SessionLocal() as s:
            return await s.get(AuthSession, auth._sha(c.cookies.get("co_session")))

    return asyncio.run(go())


def _age(c, seconds):
    async def go():
        async with SessionLocal() as s:
            row = await s.get(AuthSession, auth._sha(c.cookies.get("co_session")))
            row.last_seen_at = row.last_seen_at - datetime.timedelta(seconds=seconds)
            await s.commit()

    asyncio.run(go())


def test_session_slides_only_after_60s_and_resends_cookie(client):
    c = _login_client()
    before = _sess(c).expires_at
    r1 = c.get("/auth/me")
    r2 = c.get("/auth/me")
    assert r1.status_code == r2.status_code == 200
    assert _sess(c).expires_at == before
    assert "set-cookie" not in r1.headers and "set-cookie" not in r2.headers

    _age(c, 120)
    r3 = c.get("/auth/me")
    assert r3.status_code == 200
    assert _sess(c).expires_at > before
    sc = r3.headers["set-cookie"].lower()
    assert "co_session=" in sc and "max-age=604800" in sc
    assert "httponly" in sc and "samesite=lax" in sc and "path=/" in sc
    assert "secure" not in sc


# ---- 7. bootstrap admin password length ------------------------------------
def test_bootstrap_admin_rejects_short_password(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.database import Base
    from app.routers import auth as auth_router

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/b.db")

    async def go():
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        sm = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(auth_router, "SessionLocal", sm)
        monkeypatch.setattr(settings, "admin_email", "a@b.com")
        monkeypatch.setattr(settings, "admin_password", "short")
        await auth_router.bootstrap_admin()
        async with sm() as s:
            n1 = len((await s.execute(select(User))).scalars().all())
        monkeypatch.setattr(settings, "admin_password", "long-enough-pw")
        await auth_router.bootstrap_admin()
        async with sm() as s:
            n2 = len((await s.execute(select(User))).scalars().all())
        await eng.dispose()
        return n1, n2

    assert asyncio.run(go()) == (0, 1)


# ---- 8. disabled owner ------------------------------------------------------
def _set_active(email, active):
    async def go():
        async with SessionLocal() as s:
            u = (await s.execute(select(User).where(User.email == email))).scalar_one()
            u.active = active
            await s.commit()
            return u.id

    return asyncio.run(go())


def test_worker_fails_task_of_disabled_owner(client, member_client):
    key = env_probe_key()
    # Disable before the worker reaches it: create approval-gated, then approve as admin.
    t = member_client.post(
        "/tasks", json={"prompt": f"hello [env-probe:{key}]", "requires_approval": True}
    ).json()
    _set_active(MEMBER_EMAIL, False)
    try:
        assert client.post(f"/approvals/{t['id']}/approve").status_code == 200
        done = wait_task(client, t["id"])
    finally:
        _set_active(MEMBER_EMAIL, True)
    assert done["status"] == "failed"
    assert done["error"] == "Task owner is disabled"
    assert read_env_probe(key) is None  # claude was never spawned

    async def evs():
        async with SessionLocal() as s:
            return (
                await s.execute(select(TaskEvent).where(TaskEvent.task_id == t["id"]))
            ).scalars().all()

    assert any(e.type == "error" for e in asyncio.run(evs()))


def test_scheduler_skips_disabled_owner(client, member_client):
    import app.scheduler as sched_mod

    sch = member_client.post(
        "/schedules", json={"name": "OwnerOff", "cron": "*/15 * * * *", "prompt": "hi", "enabled": True}
    ).json()
    _set_active(MEMBER_EMAIL, False)

    async def drive():
        async with SessionLocal() as s:
            row = await s.get(Schedule, sch["id"])
            row.next_run_at = datetime.datetime(2000, 1, 1)
            await s.commit()
        await sched_mod.Scheduler(None)._tick()
        async with SessionLocal() as s:
            n = len(
                (await s.execute(select(Task).where(Task.schedule_id == sch["id"]))).scalars().all()
            )
            nxt = (await s.get(Schedule, sch["id"])).next_run_at
        return n, nxt

    try:
        n, nxt = asyncio.run(drive())
    finally:
        _set_active(MEMBER_EMAIL, True)
    assert n == 0
    assert nxt.replace(tzinfo=None) > datetime.datetime(2001, 1, 1)  # clock advanced, no tight loop
