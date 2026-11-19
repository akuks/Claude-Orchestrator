"""Admin user management: create, role, disable, reset password."""

import asyncio

from fastapi.testclient import TestClient

from app.main import app

PW = "member-password-1"


def _login(email, pw):
    c = TestClient(app)
    c.headers["X-Requested-With"] = "co"
    r = c.post("/auth/login", json={"email": email, "password": pw})
    return c, r


def _mk(client, email, role="member", pw=PW):
    r = client.post("/users", json={"email": email, "name": "N", "role": role, "password": pw})
    assert r.status_code == 201, r.text
    return r.json()


def test_admin_creates_member_and_member_logs_in(client):
    u = _mk(client, "Created.Member@Test.local")
    assert u["email"] == "created.member@test.local" and u["role"] == "member" and u["active"]
    assert "password" not in u and "password_hash" not in u
    c, r = _login("created.member@test.local", PW)
    assert r.status_code == 200
    assert any(x["id"] == u["id"] for x in client.get("/users").json())


def test_duplicate_email_case_insensitive_409(client):
    _mk(client, "dup@test.local")
    r = client.post("/users", json={"email": "DUP@test.local", "name": "", "role": "member", "password": PW})
    assert r.status_code == 409


def test_member_gets_403_on_users(member_client):
    assert member_client.get("/users").status_code == 403
    assert member_client.post("/users", json={"email": "x@y.zz", "role": "member", "password": PW}).status_code == 403


def test_agent_token_gets_403_on_users(client):
    from app import auth
    from app.database import SessionLocal

    uid = client.get("/auth/me").json()["id"]

    async def go():
        async with SessionLocal() as s:
            raw = await auth.mint_token(s, uid, "agent-task", "task-x", 600)
            await s.commit()
            return raw

    raw = asyncio.run(go())
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {raw}"
    assert c.get("/users").status_code == 403


def test_disable_user_kills_sessions(client):
    u = _mk(client, "tobedisabled@test.local")
    c, _ = _login("tobedisabled@test.local", PW)
    assert c.get("/auth/me").status_code == 200
    r = client.patch(f"/users/{u['id']}", json={"active": False})
    assert r.status_code == 200 and r.json()["active"] is False
    assert c.get("/auth/me").status_code == 401
    assert _login("tobedisabled@test.local", PW)[1].status_code != 200


def test_reset_password_kills_sessions(client):
    u = _mk(client, "resetme@test.local")
    c, _ = _login("resetme@test.local", PW)
    assert client.post(f"/users/{u['id']}/reset-password", json={"password": "brand-new-pass-9"}).status_code == 204
    assert c.get("/auth/me").status_code == 401
    assert _login("resetme@test.local", PW)[1].status_code != 200
    assert _login("resetme@test.local", "brand-new-pass-9")[1].status_code == 200


def test_last_admin_cannot_demote_or_disable_self(client):
    me = client.get("/auth/me").json()
    assert client.patch(f"/users/{me['id']}", json={"role": "member"}).status_code == 400
    assert client.patch(f"/users/{me['id']}", json={"active": False}).status_code == 400
    # With a second active admin, demotion is allowed; undo via that admin.
    a2 = _mk(client, "second-admin@test.local", role="admin")
    c2, _ = _login("second-admin@test.local", PW)
    assert client.patch(f"/users/{a2['id']}", json={"active": False}).status_code == 200
    assert client.patch(f"/users/{a2['id']}", json={"active": True, "role": "member"}).status_code == 200
    assert c2 is not None


def test_password_too_short_400(client):
    r = client.post("/users", json={"email": "short@test.local", "role": "member", "password": "short"})
    assert r.status_code == 400 and "10" in r.json()["detail"]
    u = _mk(client, "short2@test.local")
    assert client.post(f"/users/{u['id']}/reset-password", json={"password": "short"}).status_code == 400


def test_bad_role_and_unknown_user(client):
    r = client.post("/users", json={"email": "r@test.local", "role": "owner", "password": PW})
    assert r.status_code == 400
    u = _mk(client, "rolecheck@test.local")
    assert client.patch(f"/users/{u['id']}", json={"role": "root"}).status_code == 400
    assert client.patch("/users/nope", json={"name": "x"}).status_code == 404
    assert client.post("/users/nope/reset-password", json={"password": PW}).status_code == 404
