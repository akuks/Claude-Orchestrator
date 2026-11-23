"""Phase 5b Task 1: launch list REST, validation, numbering, task linking and auto-start."""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.launch import store
from app.launch.validate import clean_paths, clean_title
from app.models import LaunchItem, Task
from conftest import login_as

STALE = "changed by someone else — reload"


def _uid():
    return uuid.uuid4().hex[:8]


@pytest.fixture()
def world(client):
    pid = client.post("/projects", json={"name": f"ln-{_uid()}"}).json()["id"]
    maint = login_as(f"lm-{_uid()}@test.local")
    contrib = login_as(f"lc-{_uid()}@test.local")
    out = login_as(f"lo-{_uid()}@test.local")
    r = client.put(f"/projects/{pid}/members", json=[
        {"subject_type": "user", "subject_id": maint.user_id, "role": "maintainer"},
        {"subject_type": "user", "subject_id": contrib.user_id, "role": "contributor"}])
    assert r.status_code == 200, r.text
    return {"pid": pid, "base": f"/projects/{pid}/launch", "maint": maint, "contrib": contrib, "out": out,
            "admin": client}


def _add(c, w, title="Ship billing", section="before"):
    r = c.post(f"{w['base']}/items", json={"title": title, "section": section})
    assert r.status_code == 201, r.text
    return r.json()


def test_empty_launch_reads_as_none(world):
    d = world["contrib"].get(world["base"]).json()
    assert d == {"launch": None, "items": [], "all_linked_done": {}}


def test_roles(world):
    w = world
    c, m, o = w["contrib"], w["maint"], w["out"]
    one = _add(c, w, "one")
    assert one["number"] == 1 and one["status"] == "open" and one["added_by"] == c.user_id
    assert one["added_by_name"] and one["version"] == 1 and one["started_at"] is None
    b = f"{w['base']}/items/1"
    r = c.patch(b, json={"version": 1, "status": "started"})
    assert r.status_code == 200 and r.json()["status"] == "started" and r.json()["started_at"]
    r = c.patch(b, json={"version": 2, "status": "done"})
    assert r.status_code == 200 and r.json()["done_at"]
    # contributor: no title edit, section move, reopen, delete, PUT
    assert c.patch(b, json={"version": 3, "title": "x"}).status_code == 403
    assert c.patch(b, json={"version": 3, "section": "after"}).status_code == 403
    assert c.patch(b, json={"version": 3, "status": "open"}).status_code == 403
    assert c.delete(b).status_code == 403
    assert c.put(w["base"], json={"critical_paths": []}).status_code == 403
    # maintainer does all
    r = m.patch(b, json={"version": 3, "status": "open"})
    assert r.status_code == 200 and r.json()["done_at"] is None and r.json()["status"] == "open"
    assert m.patch(b, json={"version": 4, "title": "renamed", "section": "after"}).json()["section"] == "after"
    assert m.put(w["base"], json={"launch_date": "2027-01-31", "critical_paths": ["src/**"]}).status_code == 200
    assert m.delete(b).status_code == 204
    assert m.get(w["base"]).json()["items"] == []
    # non-member: 404 everywhere
    assert o.get(w["base"]).status_code == 404
    assert o.post(f"{w['base']}/items", json={"title": "x"}).status_code == 404
    assert o.patch(b, json={"version": 1}).status_code == 404
    assert o.delete(b).status_code == 404
    assert o.put(w["base"], json={}).status_code == 404


def test_contributor_moves(world):
    w = world
    c = w["contrib"]
    _add(c, w)
    b = f"{w['base']}/items/1"
    assert c.patch(b, json={"version": 1, "status": "done"}).status_code == 422  # not started yet
    assert c.patch(b, json={"version": 1, "status": "bogus"}).status_code == 422
    assert c.patch(b, json={"version": 1, "status": "dropped"}).json()["status"] == "dropped"
    assert c.patch(b, json={"version": 2, "status": "open"}).status_code == 403


def test_numbering_is_a_high_water_mark(world):
    w = world
    m = w["maint"]
    for t in ("a", "b", "c"):
        _add(m, w, t)
    assert m.delete(f"{w['base']}/items/2").status_code == 204
    assert _add(m, w, "d")["number"] == 4
    assert [i["number"] for i in m.get(w["base"]).json()["items"]] == [1, 3, 4]


def test_list_order_before_then_after(world):
    w = world
    m = w["maint"]
    _add(m, w, "a", "after")
    _add(m, w, "b", "before")
    _add(m, w, "c", "before")
    assert [(i["section"], i["number"]) for i in m.get(w["base"]).json()["items"]] == [
        ("before", 2), ("before", 3), ("after", 1)]


def test_duplicate_titles(world):
    w = world
    c = w["contrib"]
    _add(c, w, "Ship Billing")
    r = c.post(f"{w['base']}/items", json={"title": "  ship billing ", "section": "after"})
    assert r.status_code == 422 and "already exists" in r.json()["detail"]
    b = f"{w['base']}/items/1"
    c.patch(b, json={"version": 1, "status": "started"})
    c.patch(b, json={"version": 2, "status": "done"})
    assert _add(c, w, "ship billing")["number"] == 2  # a done one does not count
    # reopening the done one now would duplicate the open one
    r = w["maint"].patch(b, json={"version": 3, "status": "open"})
    assert r.status_code == 422
    # title edit to a live title
    _add(c, w, "other")
    r = w["maint"].patch(f"{w['base']}/items/3", json={"version": 1, "title": "SHIP BILLING"})
    assert r.status_code == 422


def test_version_conflict(world):
    w = world
    m = w["maint"]
    _add(m, w)
    b = f"{w['base']}/items/1"
    assert m.patch(b, json={"version": 1, "title": "new"}).json()["version"] == 2
    r = m.patch(b, json={"version": 1, "title": "newer"})
    assert r.status_code == 409 and r.json()["detail"] == STALE
    r = w["contrib"].patch(b, json={"version": 1, "status": "started"})
    assert r.status_code == 409 and r.json()["detail"] == STALE
    # PUT: first write creates, later ones need the version
    d = m.put(w["base"], json={"launch_date": "2027-03-01", "critical_paths": ["a/**", "./b//c.py"]}).json()
    assert d["launch"] == {"launch_date": "2027-03-01", "critical_paths": ["a/**", "b/c.py"], "raise_risk": False, "version": 1}
    r = m.put(w["base"], json={"critical_paths": [], "version": 5})
    assert r.status_code == 409 and r.json()["detail"] == STALE
    assert m.put(w["base"], json={"critical_paths": []}).status_code == 409  # version required now
    d = m.put(w["base"], json={"launch_date": None, "critical_paths": [], "version": 1}).json()
    assert d["launch"]["version"] == 2 and d["launch"]["launch_date"] is None


def test_status_moves_stamp_dates(world):
    w = world
    m = w["maint"]
    _add(m, w)
    b = f"{w['base']}/items/1"
    s = m.patch(b, json={"version": 1, "status": "started"}).json()
    assert s["started_at"] and s["done_at"] is None
    d = m.patch(b, json={"version": 2, "status": "done"}).json()
    assert d["done_at"] and d["started_at"] == s["started_at"]
    r = m.patch(b, json={"version": 3, "status": "open"}).json()
    assert r["done_at"] is None and r["status"] == "open"


@pytest.mark.parametrize("paths", [["../x"], ["a/../x"], ["/abs"], ["~/x"], ["a\\b"],
                                   [f"g{i}" for i in range(21)], ["x" * 301], [""], "src"])
def test_bad_critical_paths(world, paths):
    r = world["maint"].put(world["base"], json={"critical_paths": paths})
    assert r.status_code == 422, r.text


def test_critical_paths_limits_ok():
    assert clean_paths([f"g{i}" for i in range(20)]) == [f"g{i}" for i in range(20)]
    assert len(clean_paths(["x" * 300])[0]) == 300
    with pytest.raises(ValueError):
        clean_paths(["x" * 301])


@pytest.mark.parametrize("title", ["", "   ", "x" * 201, "a\nb", "a\rb", "a b"])
def test_bad_titles(world, title):
    r = world["contrib"].post(f"{world['base']}/items", json={"title": title})
    assert r.status_code == 422, r.text
    with pytest.raises(ValueError):
        clean_title(title)


def test_title_ok_and_bad_section(world):
    assert clean_title("x" * 200) == "x" * 200
    r = world["contrib"].post(f"{world['base']}/items", json={"title": "x", "section": "later"})
    assert r.status_code == 422


def _task(c, w, item_id=None, project=True):
    body = {"prompt": "do it"}
    if project:
        body["project_id"] = w["pid"]
    if item_id:
        body["launch_item_id"] = item_id
    return c.post("/tasks", json=body)


def test_link_task_to_item(world, client):
    w = world
    it = _add(w["contrib"], w)
    r = _task(w["contrib"], w, it["id"])
    assert r.status_code == 201, r.text
    assert r.json()["launch_item_id"] == it["id"]
    assert _task(w["contrib"], w).json()["launch_item_id"] is None
    # deleting the item unlinks the task
    assert w["maint"].delete(f"{w['base']}/items/1").status_code == 204
    assert client.get(f"/tasks/{r.json()['id']}").json()["launch_item_id"] is None


def test_link_to_other_projects_item_is_422(world, client):
    w = world
    other = client.post("/projects", json={"name": f"ln2-{_uid()}"}).json()["id"]
    foreign = _add(client, {"base": f"/projects/{other}/launch"}, "elsewhere")
    mine = _add(w["contrib"], w)
    r = _task(w["contrib"], w, foreign["id"])
    assert r.status_code == 422, r.text
    assert _task(w["contrib"], w, "nope").status_code == 422
    assert _task(w["contrib"], w, mine["id"], project=False).status_code == 422  # no project


def test_mark_started_for_task(world):
    w = world
    it = _add(w["contrib"], w)
    tid = _task(w["contrib"], w, it["id"]).json()["id"]

    async def go():
        async with SessionLocal() as s:
            t = await s.get(Task, tid)
            await store.mark_started_for_task(s, t)
            await s.commit()
            await store.mark_started_for_task(s, t)  # a second run: no-op, version not bumped again
            await s.commit()
            return (await s.execute(select(LaunchItem).where(LaunchItem.id == it["id"]))).scalar_one()

    i = asyncio.run(go())
    assert i.status == "started" and i.started_at is not None and i.version == 2


def test_mark_started_only_when_open(world):
    w = world
    it = _add(w["maint"], w)
    b = f"{w['base']}/items/1"
    w["maint"].patch(b, json={"version": 1, "status": "dropped"})
    tid = _task(w["maint"], w, it["id"]).json()["id"]

    async def go():
        async with SessionLocal() as s:
            await store.mark_started_for_task(s, await s.get(Task, tid))
            await s.commit()

    asyncio.run(go())
    assert w["maint"].get(w["base"]).json()["items"][0]["status"] == "dropped"


def test_all_linked_done(world):
    w = world
    m = w["maint"]
    _add(m, w, "a")
    _add(m, w, "b")
    _add(m, w, "c")
    items = m.get(w["base"]).json()["items"]
    t1 = _task(m, w, items[0]["id"]).json()["id"]
    t2 = _task(m, w, items[0]["id"]).json()["id"]
    t3 = _task(m, w, items[1]["id"]).json()["id"]

    async def finish(tid, status):
        from app.database import SessionLocal as SL
        async with SL() as s:
            t = await s.get(Task, tid)
            t.status = status
            await s.commit()

    from conftest import wait_task

    for tid in (t1, t2, t3):
        wait_task(w["admin"], tid)  # the mock run ends them all completed
    asyncio.run(finish(t2, "failed"))
    assert m.get(w["base"]).json()["all_linked_done"] == {"1": False, "2": True}  # item 3 has no tasks
    asyncio.run(finish(t2, "completed"))
    assert m.get(w["base"]).json()["all_linked_done"] == {"1": True, "2": True}


def test_two_concurrent_adds_get_distinct_numbers(world):
    async def one(title):
        async with SessionLocal() as s:
            n = await store.next_number(s, world["pid"])
            s.add(LaunchItem(project_id=world["pid"], number=n, title=title))
            await s.commit()
            return n

    async def both():
        return await asyncio.gather(one("x"), one("y"))

    assert sorted(asyncio.run(both())) == [1, 2]
    assert _add(world["maint"], world)["number"] == 3


def test_old_project_reads_unchanged(world, client):
    p = client.get(f"/projects/{world['pid']}")
    assert p.status_code == 200
    assert world["maint"].get(world["base"]).json()["launch"] is None


def test_worker_starts_the_linked_item(world, client):
    from conftest import wait_task

    w = world
    it = _add(w["contrib"], w)
    tid = _task(w["contrib"], w, it["id"]).json()["id"]
    wait_task(client, tid)
    got = w["contrib"].get(w["base"]).json()["items"][0]
    assert got["status"] == "started" and got["started_at"]
