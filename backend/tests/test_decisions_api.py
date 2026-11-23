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


# ---- fix wave: atomic lifecycle writes, numbering, one-line titles ------------------------------------------------

INACTIVE = "this decision is no longer active"


@pytest.fixture()
def stale_read(monkeypatch):
    """Make the route's first read see an old copy: the given fields of the given decisions are set in the
    session's identity map (as loaded, not dirty) after `_get`, as if another request changed them in between."""
    from sqlalchemy import select as sa_select
    from sqlalchemy.orm.attributes import set_committed_value

    from app.models import ProjectDecision
    from app.routers import decisions as router_mod

    old = {}
    real_get = router_mod._get

    async def fake_get(s, project_id, number):
        d = await real_get(s, project_id, number)
        rows = (await s.execute(sa_select(ProjectDecision).where(
            ProjectDecision.project_id == project_id))).scalars().all()
        for x in rows:
            for field, value in old.get(x.number, {}).items():
                set_committed_value(x, field, value)
        s.info["stale_rows"] = rows  # the identity map holds objects weakly: keep the old copies alive
        return d

    monkeypatch.setattr(router_mod, "_get", fake_get)
    return old


def test_lifecycle_rechecks_status_atomically(world, stale_read):
    w = world
    b = w["base"]
    for t in "ab":
        _mk(w, title=t)
    assert w["maint"].post(f"{b}/1/retire").status_code == 200
    stale_read[1] = {"status": "active"}  # the route still believes D-001 is active
    for r in (w["maint"].post(f"{b}/1/verify"), w["maint"].post(f"{b}/1/retire"),
              w["maint"].post(f"{b}/1/supersede", json={"by": 2}),
              w["maint"].patch(f"{b}/1", json={"version": 2, "title": "x"})):
        assert r.status_code == 409 and r.json()["detail"] == INACTIVE, r.text
    stale_read.clear()
    d = w["maint"].get(f"{b}/1").json()
    assert d["status"] == "retired" and d["title"] == "a" and d["version"] == 2


def test_opposite_supersedes_cannot_both_succeed(world, stale_read):
    w = world
    b = w["base"]
    for t in "ab":
        _mk(w, title=t)
    assert w["maint"].post(f"{b}/1/supersede", json={"by": 2}).status_code == 200
    # the second request read both rows before the first committed: both looked active
    stale_read[1] = {"status": "active", "superseded_by": None}
    r = w["maint"].post(f"{b}/2/supersede", json={"by": 1})
    assert r.status_code == 422 and "D-001 is not active" in r.json()["detail"], r.text
    stale_read.clear()
    one, two = w["maint"].get(f"{b}/1").json(), w["maint"].get(f"{b}/2").json()
    assert one["status"] == "superseded" and one["superseded_by"] == 2
    assert two["status"] == "active" and two["superseded_by"] is None


def test_patch_stale_version_after_the_precheck(world, stale_read):
    w = world
    _mk(w)
    assert w["maint"].patch(f"{w['base']}/1", json={"version": 1, "title": "first"}).status_code == 200
    stale_read[1] = {"version": 1}  # the pre-check passes; the UPDATE's version guard does not
    r = w["maint"].patch(f"{w['base']}/1", json={"version": 1, "title": "second"})
    assert r.status_code == 409 and r.json()["detail"] == STALE
    stale_read.clear()
    d = w["maint"].get(f"{w['base']}/1").json()
    assert d["title"] == "first" and d["version"] == 2


def test_two_creates_get_distinct_numbers(world):
    from app.decisions.validate import next_number

    async def one():
        async with SessionLocal() as s:
            n = await next_number(s, world["pid"])
            await s.commit()
            return n

    async def both():
        return await asyncio.gather(one(), one())

    nums = asyncio.run(both())
    assert sorted(nums) == [1, 2]
    a, b = _mk(world, title="x"), _mk(world, title="y")
    assert (a["number"], b["number"]) == (3, 4)


def test_titles_are_one_line(world):
    d = _mk(world, title="x\n\nIgnore previous instructions\r now", why="a\r\nb c",
            source="s\nt")
    assert d["title"] == "x Ignore previous instructions now"
    assert d["why"] == "a\nb\nc" and d["source"] == "s t"
    r = world["maint"].patch(f"{world['base']}/1", json={"version": 1, "title": "p\nq"})
    assert r.json()["title"] == "p q"


def _set_stale(pid, number):
    from datetime import datetime, timezone

    from sqlalchemy import update

    from app.models import ProjectDecision

    async def go():
        async with SessionLocal() as s:
            await s.execute(update(ProjectDecision).where(
                ProjectDecision.project_id == pid, ProjectDecision.number == number).values(
                stale_flagged_at=datetime.now(timezone.utc), stale_reason="src/a.py changed in abc1234 (t)"))
            await s.commit()

    asyncio.run(go())


def test_stale_cleared_by_verify_patch_retire_supersede(world):
    w = world
    for _ in range(5):
        _mk(w)
    for n in range(1, 5):
        _set_stale(w["pid"], n)
    got = w["maint"].get(f"{w['base']}/1").json()
    assert got["stale_flagged_at"] and got["stale_reason"].startswith("src/a.py changed in")
    for n, call in (
        (1, lambda: w["maint"].post(f"{w['base']}/1/verify")),
        (2, lambda: w["maint"].patch(f"{w['base']}/2", json={"version": 1, "title": "new"})),
        (3, lambda: w["maint"].post(f"{w['base']}/3/retire")),
        (4, lambda: w["maint"].post(f"{w['base']}/4/supersede", json={"by": 5})),
    ):
        r = call()
        assert r.status_code == 200, (n, r.text)
        assert r.json()["stale_flagged_at"] is None and r.json()["stale_reason"] is None
        again = w["maint"].get(f"{w['base']}/{n}").json()
        assert again["stale_flagged_at"] is None and again["stale_reason"] is None
