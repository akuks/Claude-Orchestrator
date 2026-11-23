"""Phase 5c Task 5: critical paths raise task risk; link a task to a launch item after creation."""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.launch.critical import prefixes
from app.models import LaunchItem, Task, TaskEvent
from app.task_service import classify_risk
from conftest import login_as


def _uid():
    return uuid.uuid4().hex[:8]


@pytest.fixture()
def world(client):
    pid = client.post("/projects", json={"name": f"rl-{_uid()}"}).json()["id"]
    maint = login_as(f"rm-{_uid()}@test.local")
    contrib = login_as(f"rc-{_uid()}@test.local")
    contrib2 = login_as(f"rd-{_uid()}@test.local")
    r = client.put(f"/projects/{pid}/members", json=[
        {"subject_type": "user", "subject_id": maint.user_id, "role": "maintainer"},
        {"subject_type": "user", "subject_id": contrib.user_id, "role": "contributor"},
        {"subject_type": "user", "subject_id": contrib2.user_id, "role": "contributor"}])
    assert r.status_code == 200, r.text
    return {"pid": pid, "base": f"/projects/{pid}/launch", "maint": maint, "contrib": contrib,
            "contrib2": contrib2, "admin": client}


def _put(w, paths, raise_risk):
    r = w["maint"].put(w["base"], json={"critical_paths": paths, "raise_risk": raise_risk})
    assert r.status_code == 200, r.text


def _create(c, w, prompt, **extra):
    r = c.post("/tasks", json={"prompt": prompt, "project_id": w["pid"], **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _events(tid):
    async def go():
        async with SessionLocal() as s:
            rows = (await s.execute(select(TaskEvent).where(TaskEvent.task_id == tid))).scalars().all()
            return [(e.type, e.payload) for e in rows]
    return asyncio.run(go())


def _item(c, w, title="Ship billing"):
    r = c.post(f"{w['base']}/items", json={"title": title, "section": "before"})
    assert r.status_code == 201, r.text
    return r.json()


def test_prefixes():
    assert prefixes(["src/billing/**", "ab/**", "Docs/*.md", "x", "src/billing/*", 5]) == ["src/billing", "docs"]
    assert prefixes(None) == []


def test_classify_risk_unchanged_without_prefixes():
    assert classify_risk("edit src/billing", "p") == "warning"
    assert classify_risk("edit src/billing", "p", None, []) == "warning"
    assert classify_risk("edit SRC/Billing/x.py", "p", None, ["src/billing"]) == "critical"
    assert classify_risk("edit src/billing", "p", ["vapt"], ["src/billing"]) == "warning"
    assert classify_risk("merge it", None) == "critical"


def test_raise_risk_on(world, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "gate_critical_approval", False)
    w = world
    _put(w, ["src/billing/**"], True)
    t = _create(w["contrib"], w, "tidy up Src/Billing/invoice.py")
    assert t["risk"] == "critical"
    assert ("risk", {"risk": "critical", "risk_reason": "critical path src/billing"}) in _events(t["id"])
    # unrelated prompt: unchanged, no event
    t2 = _create(w["contrib"], w, "tidy up docs")
    assert t2["risk"] == "warning" and _events(t2["id"]) == []


def test_raise_risk_off(world):
    w = world
    _put(w, ["src/billing/**"], False)
    t = _create(w["contrib"], w, "tidy up src/billing/invoice.py")
    assert t["risk"] == "warning" and _events(t["id"]) == []


def test_short_prefix_ignored(world):
    w = world
    _put(w, ["ab/**"], True)
    t = _create(w["contrib"], w, "look in ab/ and ab")
    assert t["risk"] == "warning" and _events(t["id"]) == []


def test_risk_text_uses_prefixes(world, monkeypatch):
    from app.task_service import build_task
    w = world
    _put(w, ["src/billing/**"], True)

    async def go():
        async with SessionLocal() as s:
            a = await build_task(s, prompt="HEAD do not merge", project_id=w["pid"], risk_text="fix src/billing")
            b = await build_task(s, prompt="HEAD do not merge", project_id=w["pid"], risk_text="fix other")
            return a.risk, b.risk
    assert asyncio.run(go()) == ("critical", "warning")


def test_patch_link_roles_and_unlink(world):
    w = world
    it = _item(w["contrib"], w)
    t = _create(w["contrib"], w, "do it")
    url = f"/tasks/{t['id']}/launch-item"
    assert w["contrib2"].patch(url, json={"launch_item_id": it["id"]}).status_code == 403
    r = w["contrib"].patch(url, json={"launch_item_id": it["id"]})
    assert r.status_code == 200 and r.json()["launch_item_id"] == it["id"] and r.json()["id"] == t["id"]
    r = w["maint"].patch(url, json={"launch_item_id": None})
    assert r.status_code == 200 and r.json()["launch_item_id"] is None
    r = w["maint"].patch(url, json={"launch_item_id": it["id"]})
    assert r.status_code == 200 and r.json()["launch_item_id"] == it["id"]
    assert w["contrib"].patch(url, json={}).status_code == 422
    assert w["contrib"].patch(url, json={"launch_item_id": None}).json()["launch_item_id"] is None


def test_patch_link_rejections(world, client):
    w = world
    other = client.post("/projects", json={"name": f"rl2-{_uid()}"}).json()["id"]
    r = client.post(f"/projects/{other}/launch/items", json={"title": "elsewhere", "section": "before"})
    foreign = r.json()
    it = _item(w["contrib"], w, "mine")
    done = _item(w["contrib"], w, "finished")
    assert w["maint"].patch(f"{w['base']}/items/{done['number']}",
                            json={"version": 1, "status": "done"}).status_code == 200
    t = _create(w["contrib"], w, "do it")
    url = f"/tasks/{t['id']}/launch-item"
    assert w["contrib"].patch(url, json={"launch_item_id": foreign["id"]}).status_code == 422
    assert w["contrib"].patch(url, json={"launch_item_id": done["id"]}).status_code == 422
    assert w["contrib"].patch(url, json={"launch_item_id": "nope"}).status_code == 422
    assert client.get(f"/tasks/{t['id']}").json()["launch_item_id"] is None
    assert it["id"]


def test_patch_link_running_task_starts_item(world):
    w = world
    it = _item(w["contrib"], w)
    t = _create(w["contrib"], w, "do it", requires_approval=True)

    async def run():
        async with SessionLocal() as s:
            (await s.get(Task, t["id"])).status = "running"
            await s.commit()
    asyncio.run(run())
    r = w["contrib"].patch(f"/tasks/{t['id']}/launch-item", json={"launch_item_id": it["id"]})
    assert r.status_code == 200

    async def item():
        async with SessionLocal() as s:
            return await s.get(LaunchItem, it["id"])
    i = asyncio.run(item())
    assert i.status == "started" and i.started_at is not None


def test_patch_link_queued_task_leaves_item_open(world):
    w = world
    it = _item(w["contrib"], w)
    t = _create(w["contrib"], w, "do it", requires_approval=True)
    assert w["contrib"].patch(f"/tasks/{t['id']}/launch-item", json={"launch_item_id": it["id"]}).status_code == 200
    assert w["contrib"].get(w["base"]).json()["items"][0]["status"] == "open"
