import uuid

from conftest import login_as


def _u():
    return uuid.uuid4().hex[:8]


def _mkproject(client):
    r = client.post("/projects", json={"name": f"TM-{_u()}"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_teams_admin_only(client):
    m = login_as(f"tm-admin-only-{_u()}@test.local")
    assert m.post("/teams", json={"name": f"t-{_u()}"}).status_code == 403
    assert m.get("/teams").status_code == 200


def test_team_crud_and_members(client):
    u1 = login_as(f"tm-crud1-{_u()}@test.local").user_id
    u2 = login_as(f"tm-crud2-{_u()}@test.local").user_id
    name = f"team-{_u()}"
    r = client.post("/teams", json={"name": name})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    assert client.post("/teams", json={"name": name}).status_code == 409
    r = client.patch(f"/teams/{tid}", json={"name": name + "-x"})
    assert r.status_code == 200 and r.json()["name"] == name + "-x"
    r = client.put(f"/teams/{tid}/members", json={"user_ids": [u1, u2]})
    assert r.status_code == 200
    assert {m["id"] for m in r.json()["members"]} == {u1, u2}
    assert set(r.json()["members"][0]) == {"id", "name", "email"}
    r = client.put(f"/teams/{tid}/members", json={"user_ids": [u2]})
    assert {m["id"] for m in r.json()["members"]} == {u2}
    assert client.put(f"/teams/{tid}/members", json={"user_ids": ["nope"]}).status_code == 400
    listed = {t["id"]: t for t in client.get("/teams").json()}
    assert listed[tid]["name"] == name + "-x"
    assert client.delete(f"/teams/{tid}").status_code == 204
    assert tid not in {t["id"] for t in client.get("/teams").json()}


def test_delete_team_removes_grants(client):
    m = login_as(f"tm-delgrant-{_u()}@test.local")
    pid = _mkproject(client)
    tid = client.post("/teams", json={"name": f"team-{_u()}"}).json()["id"]
    client.put(f"/teams/{tid}/members", json={"user_ids": [m.user_id]})
    r = client.put(
        f"/projects/{pid}/members",
        json=[{"subject_type": "team", "subject_id": tid, "role": "contributor"}],
    )
    assert r.status_code == 200, r.text
    assert m.get(f"/projects/{pid}/members").status_code == 200
    assert client.delete(f"/teams/{tid}").status_code == 204
    assert m.get(f"/projects/{pid}/members").status_code == 404
    assert client.get(f"/projects/{pid}/members").json() == []


def test_members_put_by_maintainer(client):
    contrib = login_as(f"tm-contrib-{_u()}@test.local")
    maint = login_as(f"tm-maint-{_u()}@test.local")
    outsider = login_as(f"tm-out-{_u()}@test.local")
    pid = _mkproject(client)
    grants = [
        {"subject_type": "user", "subject_id": contrib.user_id, "role": "contributor"},
        {"subject_type": "user", "subject_id": maint.user_id, "role": "maintainer"},
    ]
    assert client.put(f"/projects/{pid}/members", json=grants).status_code == 200
    r = contrib.put(f"/projects/{pid}/members", json=grants)
    assert r.status_code == 403
    r = maint.put(f"/projects/{pid}/members", json=grants[:1] + grants[1:])
    assert r.status_code == 200, r.text
    rows = {(x["subject_id"], x["role"]) for x in r.json()}
    assert rows == {(contrib.user_id, "contributor"), (maint.user_id, "maintainer")}
    assert all(x["label"] for x in r.json())
    assert contrib.get(f"/projects/{pid}/members").status_code == 200
    assert outsider.get(f"/projects/{pid}/members").status_code == 404
    assert outsider.put(f"/projects/{pid}/members", json=[]).status_code == 404


def test_members_put_rejects_bad_role_and_unknown_subject(client):
    u = login_as(f"tm-bad-{_u()}@test.local").user_id
    pid = _mkproject(client)
    url = f"/projects/{pid}/members"
    assert client.put(url, json=[{"subject_type": "user", "subject_id": u, "role": "owner"}]).status_code == 400
    assert client.put(url, json=[{"subject_type": "user", "subject_id": "nope", "role": "contributor"}]).status_code == 400
    assert client.put(url, json=[{"subject_type": "team", "subject_id": "nope", "role": "contributor"}]).status_code == 400
    assert client.put(url, json=[{"subject_type": "group", "subject_id": u, "role": "contributor"}]).status_code == 400
    dup = {"subject_type": "user", "subject_id": u, "role": "contributor"}
    assert client.put(url, json=[dup, dup]).status_code == 400


def test_directory_any_user(client):
    m = login_as(f"tm-dir-{_u()}@test.local")
    r = m.get("/users/directory")
    assert r.status_code == 200
    rows = r.json()
    assert any(x["id"] == m.user_id for x in rows)
    for x in rows:
        assert set(x) == {"id", "name", "email"}
