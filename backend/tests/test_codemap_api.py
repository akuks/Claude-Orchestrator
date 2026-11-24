"""Code map REST routes (phase 6b)."""

import asyncio
import shutil
import time
import uuid

from app.codemap import status as cm_status
from app.database import SessionLocal
from app.models import McpServer, Project, ProjectIndex
from conftest import login_as
from test_codemap_indexer import commit_more, make_repo, mock  # noqa: F401 - fixture


def _project(client, tmp_path, name=None):
    d = tmp_path / f"d-{uuid.uuid4().hex[:6]}"
    d.mkdir()
    r = client.post("/projects", json={"name": name or f"cm-{uuid.uuid4().hex[:8]}", "directory": str(d)})
    assert r.status_code == 201, r.text
    return r.json(), d


def _wait(client, pid):
    for _ in range(100):  # the job runs on the server's loop: poll the API
        if client.get(f"/projects/{pid}/code-map").json()["status"] != "running":
            return
        time.sleep(0.1)
    raise AssertionError("the index job did not finish")


def _grant(client, pid, user_id, role):
    r = client.put(f"/projects/{pid}/members", json=[{"subject_type": "user", "subject_id": user_id, "role": role}])
    assert r.status_code == 200, r.text


def test_old_project_reads_off(client, tmp_path):
    p, _ = _project(client, tmp_path)
    assert p["code_map"] is False
    r = client.get(f"/projects/{p['id']}/code-map")
    assert r.status_code == 200
    body = r.json()
    assert body == {"enabled": False, "status": "never", "sha": None, "indexed_at": None, "behind": None,
                    "error": None, "next_try_at": None, "server_registered": False}


def test_switch_on_needs_a_directory(client, tmp_path):
    p, d = _project(client, tmp_path)
    shutil.rmtree(d)
    r = client.patch(f"/projects/{p['id']}", json={"code_map": True})
    assert r.status_code == 422
    d.mkdir()
    r = client.patch(f"/projects/{p['id']}", json={"code_map": True})
    assert r.status_code == 200 and r.json()["code_map"] is True
    assert client.get(f"/projects/{p['id']}/code-map").json()["enabled"] is True


def test_roles(client, tmp_path):
    p, d = _project(client, tmp_path)
    client.patch(f"/projects/{p['id']}", json={"code_map": True})
    contributor = login_as(f"cm-c-{uuid.uuid4().hex[:6]}@test.local")
    outsider = login_as(f"cm-o-{uuid.uuid4().hex[:6]}@test.local")
    _grant(client, p["id"], contributor.user_id, "contributor")
    assert contributor.post(f"/projects/{p['id']}/code-map/refresh").status_code == 403
    assert contributor.get(f"/projects/{p['id']}/code-map").status_code == 200
    assert contributor.get(f"/projects/{p['id']}/code-map/log").status_code == 200
    assert contributor.patch(f"/projects/{p['id']}", json={"code_map": False}).status_code == 403
    for method, path in (("get", ""), ("post", "/refresh"), ("get", "/log")):
        assert getattr(outsider, method)(f"/projects/{p['id']}/code-map{path}").status_code == 404


def test_refresh_and_log_and_behind(client, tmp_path, mock):  # noqa: F811
    mdir, set_mode = mock
    d = tmp_path / "repo"
    head = make_repo(d)
    r = client.post("/projects", json={"name": f"cm-{uuid.uuid4().hex[:8]}", "directory": str(d)})
    p = r.json()
    # refresh needs the switch
    assert client.post(f"/projects/{p['id']}/code-map/refresh").status_code == 409
    client.patch(f"/projects/{p['id']}", json={"code_map": True})
    set_mode("fail")
    r = client.post(f"/projects/{p['id']}/code-map/refresh")
    assert r.status_code == 200 and r.json()["status"] == "running"
    _wait(client, p['id'])
    body = client.get(f"/projects/{p['id']}/code-map").json()
    assert body["status"] == "failed" and "code 3" in body["error"] and body["next_try_at"]
    lines = client.get(f"/projects/{p['id']}/code-map/log").json()["lines"]
    assert any("boom" in ln for ln in lines) and not any("ghp_abcdefghijkl" in ln for ln in lines)
    # manual refresh ignores back-off
    set_mode("ok")
    client.post(f"/projects/{p['id']}/code-map/refresh")
    _wait(client, p['id'])
    body = client.get(f"/projects/{p['id']}/code-map").json()
    assert body["status"] == "ok" and body["sha"] == head and body["behind"] == 0 and body["error"] is None
    commit_more(d)
    commit_more(d, "3")
    assert client.get(f"/projects/{p['id']}/code-map").json()["behind"] == 2


def test_server_registered(client, tmp_path):
    p, _ = _project(client, tmp_path)
    name = f"cm-srv-{uuid.uuid4().hex[:6]}"

    async def add(**kw):
        async with SessionLocal() as s:
            m = McpServer(**kw)
            s.add(m)
            await s.commit()
            return m.id

    async def check():
        async with SessionLocal() as s:
            return await cm_status.server_registered(s, await s.get(Project, p["id"]))

    assert asyncio.run(check()) in (True, False)  # other tests may register one
    asyncio.run(add(name=name, scope="project", project=p["name"], command="npx",
                    args=["-y", "codebase-memory-mcp@0.12.1"], enabled=True))
    assert asyncio.run(check()) is True
    assert client.get(f"/projects/{p['id']}/code-map").json()["server_registered"] is True
