import uuid

from conftest import login_as


def _uid():
    return uuid.uuid4().hex[:8]


def _mk_project(client, env=None):
    body = {"name": f"scope-{_uid()}"}
    if env:
        body["env"] = env
    r = client.post("/projects", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _grant(client, pid, user_id, role):
    r = client.put(
        f"/projects/{pid}/members",
        json=[{"subject_type": "user", "subject_id": user_id, "role": role}],
    )
    assert r.status_code == 200, r.text


def _setup(client, role, env=None):
    pid = _mk_project(client, env)
    u = login_as(f"{role}-{_uid()}@test.local")
    _grant(client, pid, u.user_id, role)
    return pid, u


def test_list_only_visible(client):
    pid = _mk_project(client)
    other = _mk_project(client)
    u = login_as(f"lv-{_uid()}@test.local")
    _grant(client, pid, u.user_id, "contributor")
    ids = {p["id"] for p in u.get("/projects").json()}
    assert pid in ids and other not in ids


def test_get_invisible_404(client):
    pid = _mk_project(client)
    u = login_as(f"gi-{_uid()}@test.local")
    assert u.get(f"/projects/{pid}").status_code == 404
    for path in ("memory", "summaries", "stats", "servers"):
        assert u.get(f"/projects/{pid}/{path}").status_code == 404
    assert u.patch(f"/projects/{pid}", json={"directory": "/tmp"}).status_code == 404


def test_my_role_values(client):
    pid, c = _setup(client, "contributor")
    assert c.get(f"/projects/{pid}").json()["my_role"] == "contributor"
    pid2, m = _setup(client, "maintainer")
    assert m.get(f"/projects/{pid2}").json()["my_role"] == "maintainer"
    assert client.get(f"/projects/{pid}").json()["my_role"] == "maintainer"
    lst = {p["id"]: p["my_role"] for p in m.get("/projects").json()}
    assert lst[pid2] == "maintainer"
    lst = {p["id"]: p["my_role"] for p in client.get("/projects").json()}
    assert lst[pid] == "maintainer"


def test_env_masked_for_member(client):
    pid, u = _setup(client, "maintainer", env={"SECRET": "hunter2"})
    assert client.get(f"/projects/{pid}").json()["env"] == {"SECRET": "hunter2"}
    assert u.get(f"/projects/{pid}").json()["env"] == {"SECRET": "••••"}
    lst = {p["id"]: p for p in u.get("/projects").json()}
    assert lst[pid]["env"] == {"SECRET": "••••"}
    r = u.patch(f"/projects/{pid}", json={"name": "renamed-" + _uid()})
    assert r.status_code == 200
    assert r.json()["env"] == {"SECRET": "••••"}


def test_contributor_cannot_patch(client):
    pid, u = _setup(client, "contributor")
    assert u.patch(f"/projects/{pid}", json={"instructions": "x"}).status_code == 403


def test_maintainer_patch_allowed_fields(client):
    pid, u = _setup(client, "maintainer")
    r = u.patch(f"/projects/{pid}", json={"instructions": "hello", "budget_usd": 5})
    assert r.status_code == 200, r.text
    assert r.json()["instructions"] == "hello"
    assert u.patch(f"/projects/{pid}", json={"archived": True}).status_code == 403
    assert u.patch(f"/projects/{pid}", json={"env": {"A": "b"}}).status_code == 403


def test_mixed_patch_rejected_nothing_saved(client):
    pid, u = _setup(client, "maintainer")
    r = u.patch(f"/projects/{pid}", json={"instructions": "x", "directory": "/tmp"})
    assert r.status_code == 403
    p = client.get(f"/projects/{pid}").json()
    assert p["instructions"] != "x" and p["directory"] != "/tmp"


def test_admin_only_routes(client):
    pid, u = _setup(client, "maintainer")
    assert u.post("/projects", json={"name": f"x-{_uid()}"}).status_code == 403
    assert u.get("/projects/discover").status_code == 403
    assert u.post("/projects/import", json={"directories": []}).status_code == 403
    assert u.put(f"/projects/{pid}/servers", json=[]).status_code == 403
    assert u.delete(f"/projects/{pid}").status_code == 403
    assert u.get(f"/projects/{pid}/servers").status_code == 200
    invisible = _mk_project(client)
    assert u.delete(f"/projects/{invisible}").status_code in (403, 404)
    assert client.delete(f"/projects/{pid}").status_code == 204


def test_read_endpoints_any_role(client):
    pid, u = _setup(client, "contributor")
    for path in ("memory", "summaries", "stats", "servers", "members"):
        assert u.get(f"/projects/{pid}/{path}").status_code == 200, path


def test_regenerate_needs_contributor(client):
    pid = _mk_project(client)
    u = login_as(f"rg-{_uid()}@test.local")
    assert u.post(f"/projects/{pid}/memory/regenerate").status_code == 404
    _grant(client, pid, u.user_id, "contributor")
    assert u.post(f"/projects/{pid}/memory/regenerate").status_code == 200
