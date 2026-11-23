"""Phase 4a Task 2: decision REST — create/list/patch/verify/retire/supersede, roles, locking."""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.database import SessionLocal
from app.main import app
from conftest import login_as

STALE = "changed by someone else — reload"


def _uid():
    return uuid.uuid4().hex[:8]


@pytest.fixture()
def world(client):
    pid = client.post("/projects", json={"name": f"dec-{_uid()}"}).json()["id"]
    maint = login_as(f"dm-{_uid()}@test.local")
    viewer = login_as(f"dv-{_uid()}@test.local")
    out = login_as(f"do-{_uid()}@test.local")
    r = client.put(f"/projects/{pid}/members", json=[
        {"subject_type": "user", "subject_id": maint.user_id, "role": "maintainer"},
        {"subject_type": "user", "subject_id": viewer.user_id, "role": "contributor"}])
    assert r.status_code == 200, r.text
    return {"pid": pid, "base": f"/projects/{pid}/decisions", "maint": maint, "viewer": viewer, "out": out}


def _mk(w, **kw):
    r = w["maint"].post(w["base"], json={"title": "Use Postgres", **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_create(world):
    d = _mk(world, why="durable", touches=["src/db/**", "./src//db.py"])
    assert d["display_id"] == "D-001" and d["number"] == 1 and d["status"] == "active"
    assert d["decided_by"] == world["maint"].user_id and d["decided_by_name"]
    assert d["last_verified_at"] == d["decided_at"] and d["version"] == 1
    assert d["touches"] == ["src/db/**", "src/db.py"] and d["see"] is None
    assert _mk(world)["display_id"] == "D-002"


def test_list_filters_newest_first(world):
    _mk(world, title="a")
    _mk(world, title="b", kind="rule")
    _mk(world, title="c")
    w = world
    assert w["maint"].post(f"{w['base']}/2/retire").json()["status"] == "retired"
    nums = [d["number"] for d in w["viewer"].get(w["base"]).json()]
    assert nums == [3, 1]
    assert [d["number"] for d in w["viewer"].get(w["base"], params={"status": "all"}).json()] == [3, 2, 1]
    assert [d["number"] for d in w["viewer"].get(w["base"], params={"status": "all", "kind": "rule"}).json()] == [2]
    assert w["viewer"].get(w["base"], params={"kind": "bogus"}).status_code == 422


def test_roles(world):
    w = world
    _mk(w)
    v, o = w["viewer"], w["out"]
    assert v.get(w["base"]).status_code == 200 and v.get(f"{w['base']}/1").status_code == 200
    for method, path, body in [
        ("POST", "", {"title": "x"}),
        ("PATCH", "/1", {"version": 1, "title": "x"}),
        ("POST", "/1/verify", None), ("POST", "/1/retire", None),
        ("POST", "/1/supersede", {"by": 1}),
    ]:
        kw = {"json": body} if body is not None else {}
        assert v.request(method, w["base"] + path, **kw).status_code == 403, path
        assert o.request(method, w["base"] + path, **kw).status_code == 404, path
    assert o.get(w["base"]).status_code == 404 and o.get(f"{w['base']}/1").status_code == 404
    assert w["maint"].get(f"{w['base']}/99").status_code == 404
    assert world["maint"].get(f"{w['base']}/1").json()["status"] == "active"


def test_agent_token_refused(world):
    async def mint():
        async with SessionLocal() as s:
            raw = await auth.mint_token(s, world["maint"].user_id, "agent-task", None, 3600)
            await s.commit()
            return raw

    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {asyncio.run(mint())}"
    assert c.get(world["base"]).status_code in (401, 403)
    assert c.post(world["base"], json={"title": "x"}).status_code in (401, 403)


def test_patch_locking(world):
    w = world
    _mk(w)
    r1 = w["maint"].patch(f"{w['base']}/1", json={"version": 1, "title": "first"})
    assert r1.status_code == 200 and r1.json()["version"] == 2 and r1.json()["title"] == "first"
    r2 = w["maint"].patch(f"{w['base']}/1", json={"version": 1, "title": "second"})
    assert r2.status_code == 409 and r2.json()["detail"] == STALE
    assert w["maint"].get(f"{w['base']}/1").json()["title"] == "first"
    r3 = w["maint"].patch(f"{w['base']}/1", json={"version": 2, "why": "w", "touches": ["a/b"]})
    assert r3.json()["version"] == 3 and r3.json()["title"] == "first" and r3.json()["touches"] == ["a/b"]
    assert w["maint"].patch(f"{w['base']}/1", json={"title": "x"}).status_code == 422  # version required


def test_patch_kind_see_against_stored(world):
    w = world
    _mk(w, kind="reference", see="docs/a.md", title="ref")
    _mk(w, title="plain")
    b = w["base"]
    # only kind changes: the stored see makes a non-reference invalid
    r = w["maint"].patch(f"{b}/1", json={"version": 1, "kind": "rule"})
    assert r.status_code == 422 and "see" in r.json()["detail"]
    # only see changes on a plain decision
    assert w["maint"].patch(f"{b}/2", json={"version": 1, "see": "x/y"}).status_code == 422
    # clearing see on a reference
    assert w["maint"].patch(f"{b}/1", json={"version": 1, "see": ""}).status_code == 422
    # both together is fine
    r = w["maint"].patch(f"{b}/1", json={"version": 1, "kind": "rule", "see": ""})
    assert r.status_code == 200 and r.json()["see"] is None and r.json()["kind"] == "rule"
    r = w["maint"].patch(f"{b}/2", json={"version": 1, "kind": "reference", "see": "x/y"})
    assert r.status_code == 200 and r.json()["see"] == "x/y"


def test_verify_and_retire(world):
    w = world
    d = _mk(w)
    v = w["maint"].post(f"{w['base']}/1/verify").json()
    assert v["last_verified_at"] > d["last_verified_at"] and v["version"] == 2 and v["status"] == "active"
    r = w["maint"].post(f"{w['base']}/1/retire").json()
    assert r["status"] == "retired" and r["version"] == 3


def test_supersede(world):
    w = world
    b = w["base"]
    for t in "abcd":
        _mk(w, title=t)
    w["maint"].post(f"{b}/4/retire")
    for by in (1, 4, 99):  # itself, retired, missing
        assert w["maint"].post(f"{b}/1/supersede", json={"by": by}).status_code == 422, by
    r = w["maint"].post(f"{b}/1/supersede", json={"by": 2})
    assert r.status_code == 200 and r.json()["status"] == "superseded" and r.json()["superseded_by"] == 2
    assert w["maint"].post(f"{b}/3/supersede", json={"by": 1}).status_code == 422  # by is superseded now


def test_supersede_other_project(client, world):
    w = world
    pid2 = client.post("/projects", json={"name": f"dec-p2-{_uid()}"}).json()["id"]
    for _ in range(3):
        assert client.post(f"/projects/{pid2}/decisions", json={"title": "z"}).status_code == 201
    _mk(w)
    # number 3 exists only in the other project
    assert w["maint"].post(f"{w['base']}/1/supersede", json={"by": 3}).status_code == 422


@pytest.mark.parametrize("body,needle", [
    ({"title": ""}, "title"),
    ({"title": "x" * 201}, "200"),
    ({"title": "x", "kind": "nope"}, "kind"),
    ({"title": "x", "why": "w" * 2001}, "2000"),
    ({"title": "x", "touches": ["/etc/passwd"]}, "relative"),
    ({"title": "x", "touches": ["a/../b"]}, ".."),
    ({"title": "x", "touches": [str(i) for i in range(21)]}, "20"),
    ({"title": "x", "kind": "reference"}, "reference"),
    ({"title": "x", "see": "a/b"}, "see"),
])
def test_create_validation(world, body, needle):
    r = world["maint"].post(world["base"], json=body)
    assert r.status_code == 422, r.text
    assert needle in r.json()["detail"]


def test_text_redacted(world):
    d = _mk(world, title="tok ghp_" + "a" * 36 + " end")
    assert "ghp_" not in d["title"]


def test_non_active_refused(world):
    w = world
    b = w["base"]
    for t in "abc":
        _mk(w, title=t)
    assert w["maint"].post(f"{b}/1/supersede", json={"by": 2}).status_code == 200  # 1 superseded
    assert w["maint"].post(f"{b}/3/retire").status_code == 200  # 3 retired
    for n in (1, 3):
        calls = [
            w["maint"].post(f"{b}/{n}/verify"), w["maint"].post(f"{b}/{n}/retire"),
            w["maint"].post(f"{b}/{n}/supersede", json={"by": 2}),
            w["maint"].patch(f"{b}/{n}", json={"version": w["maint"].get(f"{b}/{n}").json()["version"], "title": "x"}),
        ]
        for r in calls:
            assert r.status_code == 409 and r.json()["detail"] == "this decision is no longer active", (n, r.text)
    d = w["maint"].get(f"{b}/1").json()
    assert d["status"] == "superseded" and d["superseded_by"] == 2 and d["title"] == "a"
