"""Every route requires a logged-in user or an API token; CSRF header for cookie
writes; admin-only global config; agent-task tokens confined to their thread."""

import asyncio
import hashlib
import hmac
import json
import re

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from conftest import ADMIN_EMAIL, ADMIN_PASSWORD

# The ONLY routes reachable without credentials. Adding a route anywhere else
# without auth makes test_every_route_requires_auth fail.
OPEN_ROUTES = {
    ("GET", "/health"),
    ("POST", "/auth/login"),
    ("GET", "/auth/setup-status"),
    ("POST", "/auth/setup"),
    ("POST", "/webhooks/github"),
    # co-guard identity proof (Ruling R22): HMAC of a caller challenge with a
    # per-boot secret; reveals nothing and carries no project data.
    ("GET", "/guard/whoami"),
}


def _fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


def _flat_routes(application):
    """(route, full path template) for every route. Newer FastAPI keeps included
    routers lazily in app.routes; iter_route_contexts flattens them."""
    try:
        from fastapi.routing import iter_route_contexts
    except ImportError:  # older FastAPI: app.routes is already flat
        return [(r, r.path) for r in application.routes]
    # rc.path is "" for routes without an include context (e.g. WebSockets).
    return [
        (rc.original_route, rc.path or rc.original_route.path)
        for rc in iter_route_contexts(application.routes)
    ]


def _all_http_routes(application):
    for r, path in _flat_routes(application):
        if isinstance(r, APIRoute):
            for m in sorted(r.methods):
                yield m, path


def _all_ws_routes(application):
    return [p for r, p in _flat_routes(application) if isinstance(r, APIWebSocketRoute)]


def _run(coro):
    return asyncio.run(coro)


def _mint(user_id: str, purpose: str, task_id: str | None = None) -> str:
    from app import auth
    from app.database import SessionLocal

    async def go():
        async with SessionLocal() as s:
            raw = await auth.mint_token(s, user_id, purpose, task_id, 3600)
            await s.commit()
            return raw

    return _run(go())


def _revoke(task_id: str) -> None:
    from app import auth
    from app.database import SessionLocal

    async def go():
        async with SessionLocal() as s:
            await auth.revoke_task_tokens(s, task_id)
            await s.commit()

    _run(go())


def _bearer(raw: str) -> TestClient:
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {raw}"
    return c


# --------------------------------------------------------------------------- anon


def test_open_set_matches_real_routes():
    routes = set(_all_http_routes(app))
    assert OPEN_ROUTES <= routes, OPEN_ROUTES - routes


def test_every_route_requires_auth(anon_client):
    checked = 0
    for method, path in _all_http_routes(app):
        if (method, path) in OPEN_ROUTES:
            continue
        r = anon_client.request(method, _fill(path))
        assert r.status_code == 401, (method, path, r.status_code, r.text)
        checked += 1
    assert checked > 50
    ws = _all_ws_routes(app)
    assert ws
    for path in ws:
        with pytest.raises(WebSocketDisconnect) as e:
            with anon_client.websocket_connect(_fill(path)) as conn:
                conn.receive_json()
        assert e.value.code == 4401, path


def test_invalid_cookie_and_bad_bearer_are_401():
    c = TestClient(app)
    c.cookies.set("co_session", "garbage")
    assert c.get("/tasks").status_code == 401
    assert _bearer("co_not-a-real-token").get("/tasks").status_code == 401
    assert _bearer("").get("/tasks").status_code == 401


def test_open_routes_stay_open(anon_client):
    assert anon_client.get("/health").status_code == 200
    assert anon_client.get("/auth/setup-status").status_code == 200


def test_github_webhook_still_open(anon_client):
    body = json.dumps({"zen": "hi"}).encode()
    sig = "sha256=" + hmac.new(b"testsecret", body, hashlib.sha256).hexdigest()
    r = TestClient(app).post(  # no cookie, no CSRF header — GitHub sends neither
        "/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": sig},
    )
    assert r.status_code == 200 and r.json()["pong"] is True


# --------------------------------------------------------------------------- CSRF


def test_cookie_post_without_csrf_header_is_403(client):
    c = TestClient(app)  # logged in, but no X-Requested-With
    assert c.post("/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}).status_code == 200
    assert c.get("/tasks").status_code == 200  # safe methods need no header
    r = c.post("/projects", json={"name": "NoCsrf"})
    assert r.status_code == 403 and r.json()["detail"] == "missing X-Requested-With"
    assert c.delete("/tasks/x").status_code == 403
    assert c.post("/auth/logout").status_code == 403
    c.headers["X-Requested-With"] = "co"
    assert c.post("/projects", json={"name": "WithCsrf"}).status_code == 201


def test_logged_in_ws_stream_works(client):
    from conftest import wait_task

    t = client.post("/tasks", json={"prompt": "hello ws"}).json()
    wait_task(client, t["id"])
    with client.websocket_connect(f"/tasks/{t['id']}/stream") as ws:
        ev = ws.receive_json()
    assert ev["seq"] >= 1


def test_system_claude_requires_login(client, anon_client):
    assert anon_client.get("/system/claude").status_code == 401
    assert client.get("/system/claude").status_code == 200


# --------------------------------------------------------------------------- roles


def test_member_cannot_write_mcp_or_skills(member_client, client):
    srv = client.post("/mcp/servers", json={"name": "lock-srv", "transport": "http", "url": "http://x"})
    assert srv.status_code == 201
    sid = srv.json()["id"]
    m = member_client
    assert m.post("/mcp/servers", json={"name": "m-srv", "transport": "http", "url": "http://x"}).status_code == 403
    assert m.patch(f"/mcp/servers/{sid}", json={"enabled": False}).status_code == 403
    assert m.put(f"/mcp/servers/{sid}/policies", json=[]).status_code == 403
    assert m.post(f"/mcp/servers/{sid}/test").status_code == 403
    assert m.delete(f"/mcp/servers/{sid}").status_code == 403
    assert m.post("/skills", json={"local_path": "/tmp"}).status_code == 403
    assert m.patch("/skills/x", json={"description": "d"}).status_code == 403
    assert m.delete("/skills/x").status_code == 403
    # Reads stay open to every logged-in user.
    assert m.get("/mcp/servers").status_code == 200
    assert m.get(f"/mcp/servers/{sid}").status_code == 200
    assert m.get("/skills").status_code == 200
    assert m.get("/skills/presets").status_code == 200
    assert client.delete(f"/mcp/servers/{sid}").status_code == 204


def test_member_context_docs_global_writes_are_admin_only(member_client, client):
    m = member_client
    # Projects are admin-created; project docs need a maintainer grant.
    assert m.post("/projects", json={"name": "MemberCtx"}).status_code == 403
    proj = client.post("/projects", json={"name": "MemberCtx"}).json()
    me = next(u for u in client.get("/users").json() if u["email"] == "member@test.local")
    assert client.put(
        f"/projects/{proj['id']}/members",
        json=[{"subject_type": "user", "subject_id": me["id"], "role": "maintainer"}],
    ).status_code == 200
    assert m.post("/context-docs", json={"path": "g/one"}).status_code == 403  # default scope global
    assert m.post("/context-docs", json={"path": "g/one", "scope": "global"}).status_code == 403
    pdoc = m.post("/context-docs", json={"path": "p/one", "scope": "project", "project_id": proj["id"]})
    assert pdoc.status_code == 201
    pid = pdoc.json()["id"]
    assert m.patch(f"/context-docs/{pid}", json={"title": "ok"}).status_code == 200
    assert m.patch(f"/context-docs/{pid}", json={"scope": "global"}).status_code == 403
    g = client.post("/context-docs", json={"path": "g/admin"}).json()
    assert m.get(f"/context-docs/{g['id']}").status_code == 200
    assert m.patch(f"/context-docs/{g['id']}", json={"title": "x"}).status_code == 403
    assert m.delete(f"/context-docs/{g['id']}").status_code == 403
    assert m.delete(f"/context-docs/{pid}").status_code == 204
    assert client.delete(f"/context-docs/{g['id']}").status_code == 204


def test_member_can_use_regular_routes(member_client):
    assert member_client.get("/auth/me").json()["role"] == "member"
    assert member_client.get("/tasks").status_code == 200


# --------------------------------------------------------------------------- tokens


@pytest.fixture(scope="module")
def thread(client):
    """root A (coordinator run) → child C (dispatched) → grandchild G; unrelated B."""
    lead = client.post("/agents", json={
        "name": "TokLead", "system_prompt": "delegate", "default_prompt": "coordinate",
        "coordinator": True, "requires_approval": True,
    }).json()
    client.post("/agents", json={"name": "TokWorker", "system_prompt": "w", "default_prompt": "x"})
    a = client.post(f"/agents/{lead['id']}/run", json={}).json()
    c = client.post("/agents/dispatch", json={
        "parent_task_id": a["id"], "items": [{"agent": "TokWorker", "prompt": "child"}],
    }).json()[0]
    g = client.post("/agents/dispatch", json={
        "parent_task_id": c["id"], "items": [{"agent": "TokWorker", "prompt": "grandchild"}],
    }).json()[0]
    b = client.post("/tasks", json={"prompt": "unrelated"}).json()
    me = client.get("/auth/me").json()
    return {"a": a, "c": c, "g": g, "b": b, "user_id": me["id"]}


def test_agent_token_can_read_own_thread_only(thread):
    t = _bearer(_mint(thread["user_id"], "agent-task", thread["a"]["id"]))
    for k in ("a", "c", "g"):
        assert t.get(f"/tasks/{thread[k]['id']}").status_code == 200, k
        assert t.get(f"/tasks/{thread[k]['id']}/events").status_code == 200, k
    assert t.get(f"/tasks/{thread['b']['id']}").status_code == 403
    assert t.get(f"/tasks/{thread['b']['id']}/events").status_code == 403
    assert t.get("/tasks/does-not-exist").status_code == 403


def test_agent_token_is_confined_to_allowlist(thread):
    t = _bearer(_mint(thread["user_id"], "agent-task", thread["a"]["id"]))
    aid = thread["a"]["id"]
    assert t.get("/tasks").status_code == 403
    assert t.get(f"/tasks/{aid}/thread").status_code == 403
    assert t.get(f"/tasks/{aid}/report").status_code == 403
    assert t.post(f"/tasks/{aid}/cancel").status_code == 403
    assert t.get("/mcp/servers").status_code == 403
    assert t.get("/auth/me").status_code == 403
    assert t.get("/system/claude").status_code == 403
    with pytest.raises(WebSocketDisconnect) as e:
        with t.websocket_connect(f"/tasks/{aid}/stream") as ws:
            ws.receive_json()
    assert e.value.code == 4401


def test_agent_token_dispatch_only_under_own_task(thread):
    t = _bearer(_mint(thread["user_id"], "agent-task", thread["a"]["id"]))
    item = [{"agent": "TokWorker", "prompt": "more"}]
    r = t.post("/agents/dispatch", json={"parent_task_id": thread["a"]["id"], "items": item})
    assert r.status_code == 201, r.text  # bearer auth: no CSRF header needed
    assert r.json()[0]["parent_task_id"] == thread["a"]["id"]
    assert t.post("/agents/dispatch", json={"parent_task_id": thread["c"]["id"], "items": item}).status_code == 403
    assert t.post("/agents/dispatch", json={"parent_task_id": thread["b"]["id"], "items": item}).status_code == 403
    assert t.post("/agents/dispatch", json={"items": item}).status_code == 403


def test_revoked_agent_token_is_401(thread, client):
    b = client.post("/tasks", json={"prompt": "revoke me"}).json()
    raw = _mint(thread["user_id"], "agent-task", b["id"])
    t = _bearer(raw)
    assert t.get(f"/tasks/{b['id']}").status_code == 200
    _revoke(b["id"])
    assert t.get(f"/tasks/{b['id']}").status_code == 401


def test_personal_token_acts_as_user_without_csrf(thread):
    t = _bearer(_mint(thread["user_id"], "personal"))
    assert t.get("/tasks").status_code == 200
    assert t.get("/auth/me").json()["email"] == ADMIN_EMAIL
    assert t.post("/projects", json={"name": "ViaPAT"}).status_code == 201
    # A token is not a browser session: it can't change the password.
    r = t.post("/auth/password", json={"current_password": ADMIN_PASSWORD, "new_password": "whatever-123456"})
    assert r.status_code == 403


def test_ws_stream_rejects_foreign_origin(client):
    from conftest import wait_task

    t = client.post("/tasks", json={"prompt": "origin check"}).json()
    wait_task(client, t["id"])
    url = f"/tasks/{t['id']}/stream"
    # Same-site but a different origin (another port/subdomain): the browser
    # still sends the Lax cookie, so the server must check Origin itself.
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect(url, headers={"Origin": "http://evil.localhost:9999"}) as ws:
            ws.receive_json()
    assert e.value.code == 4403
    with client.websocket_connect(url, headers={"Origin": "http://localhost:5200"}) as ws:
        assert ws.receive_json()["seq"] >= 1
    with client.websocket_connect(url) as ws:  # non-browser client: no Origin
        assert ws.receive_json()["seq"] >= 1
