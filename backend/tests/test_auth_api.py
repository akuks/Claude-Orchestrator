import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import auth
from app.config import settings

ADMIN = "admin@test.local"
PW = "test-password-123"


def _fresh():
    from app.main import app

    return TestClient(app)


def _mk_user(client, email, pw=PW, **kw):
    from app.database import SessionLocal
    from app.models import User

    async def go():
        async with SessionLocal() as s:
            s.add(User(email=email, name="T", password_hash=auth.hash_password(pw), **kw))
            await s.commit()

    asyncio.run(go())


def test_env_bootstrap_admin(client):
    r = _fresh().post("/auth/login", json={"email": ADMIN, "password": PW})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["role"] == "admin" and body["email"] == ADMIN
    assert "password_hash" not in body and "hash" not in str(body).lower()


def test_setup_status_and_409_when_users_exist(client):
    assert client.get("/auth/setup-status").json() == {"needs_setup": False}
    r = client.post("/auth/setup", json={"email": "x@x.com", "name": "X", "password": "longenough123"})
    assert r.status_code == 409


def test_setup_only_when_no_users(tmp_path, monkeypatch):
    """Zero-user path: run the router against a separate fresh SQLite DB."""
    from app.database import Base
    from app.routers import auth as auth_router

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/fresh.db")

    async def init():
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)

    asyncio.run(init())
    monkeypatch.setattr(auth_router, "SessionLocal", async_sessionmaker(eng, expire_on_commit=False))
    app = FastAPI()
    app.include_router(auth_router.router)
    with TestClient(app) as c:
        assert c.get("/auth/setup-status").json() == {"needs_setup": True}
        r = c.post("/auth/setup", json={"email": " Boss@X.com ", "name": "Boss", "password": "longenough123"})
        assert r.status_code == 201, r.text
        assert r.json()["role"] == "admin" and r.json()["email"] == "boss@x.com"
        assert c.get("/auth/me").json()["email"] == "boss@x.com"  # logged in
        assert c.get("/auth/setup-status").json() == {"needs_setup": False}
        r = c.post("/auth/setup", json={"email": "b@x.com", "name": "B", "password": "longenough123"})
        assert r.status_code == 409
        short = c.post("/auth/setup", json={"email": "b@x.com", "name": "B", "password": "short"})
        assert short.status_code == 422
    asyncio.run(eng.dispose())


def test_login_case_insensitive_email(client):
    c = _fresh()
    r = c.post("/auth/login", json={"email": " ADMIN@TEST.local ", "password": PW})
    assert r.status_code == 200
    sc = r.headers["set-cookie"].lower()
    assert "co_session=" in sc and "httponly" in sc and "samesite=lax" in sc and "path=/" in sc
    assert f"max-age={settings.session_ttl_days * 86400}" in sc
    assert "secure" not in sc  # http test client


def test_login_wrong_password_401(client):
    c = _fresh()
    a = c.post("/auth/login", json={"email": ADMIN, "password": "wrong-password-1"})
    b = c.post("/auth/login", json={"email": "nobody@test.local", "password": "wrong-password-1"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_disabled_user_cannot_login(client):
    _mk_user(client, "off@test.local", active=False)
    r = _fresh().post("/auth/login", json={"email": "off@test.local", "password": PW})
    assert r.status_code == 401


def test_lockout_after_5_failures(client):
    _mk_user(client, "lock@test.local")
    c = _fresh()
    for _ in range(5):
        assert c.post("/auth/login", json={"email": "lock@test.local", "password": "bad-password-1"}).status_code == 401
    r = c.post("/auth/login", json={"email": "LOCK@test.local", "password": PW})
    assert r.status_code == 429


def test_success_clears_failures(client):
    _mk_user(client, "clr@test.local")
    c = _fresh()
    for _ in range(4):
        c.post("/auth/login", json={"email": "clr@test.local", "password": "bad-password-1"})
    assert c.post("/auth/login", json={"email": "clr@test.local", "password": PW}).status_code == 200
    for _ in range(4):
        assert c.post("/auth/login", json={"email": "clr@test.local", "password": "bad-password-1"}).status_code == 401


def test_logout_invalidates_session(client):
    c = _fresh()
    c.post("/auth/login", json={"email": ADMIN, "password": PW})
    assert c.get("/auth/me").status_code == 200
    assert c.post("/auth/logout").status_code == 204
    assert c.get("/auth/me").status_code == 401


def test_me_without_cookie_401(client):
    assert _fresh().get("/auth/me").status_code == 401


def test_password_change_revokes_other_sessions(client):
    _mk_user(client, "pw@test.local")
    c1, c2 = _fresh(), _fresh()
    for c in (c1, c2):
        assert c.post("/auth/login", json={"email": "pw@test.local", "password": PW}).status_code == 200
    bad = c1.post("/auth/password", json={"current_password": "nope-nope-nope", "new_password": "new-password-123"})
    assert bad.status_code == 401
    short = c1.post("/auth/password", json={"current_password": PW, "new_password": "short"})
    assert short.status_code == 422
    r = c1.post("/auth/password", json={"current_password": PW, "new_password": "new-password-123"})
    assert r.status_code == 204
    assert c1.get("/auth/me").status_code == 200
    assert c2.get("/auth/me").status_code == 401
    assert _fresh().post("/auth/login", json={"email": "pw@test.local", "password": "new-password-123"}).status_code == 200


def test_concurrent_setup_creates_exactly_one_admin(tmp_path, monkeypatch):
    import httpx
    from sqlalchemy import func, select

    from app.database import Base
    from app.models import User
    from app.routers import auth as auth_router

    async def go():
        eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/race.db")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        sm = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(auth_router, "SessionLocal", sm)
        app = FastAPI()
        app.include_router(auth_router.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            rs = await asyncio.gather(
                *[
                    c.post("/auth/setup", json={"email": f"a{i}@x.com", "name": "A", "password": "longenough123"})
                    for i in range(2)
                ]
            )
        async with sm() as s:
            n = (await s.execute(select(func.count()).select_from(User))).scalar_one()
        await eng.dispose()
        return sorted(r.status_code for r in rs), n

    codes, n = asyncio.run(go())
    assert codes == [201, 409] and n == 1
