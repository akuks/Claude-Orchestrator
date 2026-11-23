"""Phase 5b Task 2: GET /projects/{id}/launch/facts."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.database import SessionLocal
from app.launch import facts
from app.models import LaunchItem, Project
from conftest import login_as


def _uid():
    return uuid.uuid4().hex[:8]


@pytest.fixture()
def world(client):
    pid = client.post("/projects", json={"name": f"lf-{_uid()}"}).json()["id"]
    contrib = login_as(f"lf-{_uid()}@test.local")
    out = login_as(f"lf-o-{_uid()}@test.local")
    r = client.put(f"/projects/{pid}/members", json=[
        {"subject_type": "user", "subject_id": contrib.user_id, "role": "contributor"}])
    assert r.status_code == 200, r.text
    return {"pid": pid, "base": f"/projects/{pid}/launch/facts", "c": contrib, "out": out, "admin": client}


def _seed(pid, rows):
    """rows: (number, section, status, days_started_to_done | None)"""
    async def go():
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        async with SessionLocal() as s:
            for n, sec, st, span in rows:
                s.add(LaunchItem(project_id=pid, number=n, title=f"item {n}", section=sec, status=st,
                                 started_at=base if span is not None else None,
                                 done_at=base + timedelta(days=span) if span is not None else None))
            proj = await s.get(Project, pid)
            proj.launch_seq = max(proj.launch_seq or 0, max(r[0] for r in rows))
            await s.commit()
    asyncio.run(go())


def test_empty(world):
    r = world["c"].get(world["base"])
    assert r.status_code == 200
    assert r.json() == {"launch_date": None, "open": 0, "done": 0, "would_be": 1, "after": [],
                        "critical_touched": [], "estimate": None}


def test_counts_would_be_after_and_critical(world):
    w = world
    assert w["admin"].put(f"/projects/{w['pid']}/launch", json={
        "launch_date": "2027-02-03", "critical_paths": ["src/billing/**", "db/*.sql", "docs/**"]}).status_code == 200
    _seed(w["pid"], [(1, "before", "open", None), (2, "before", "started", None), (3, "before", "done", 2),
                     (4, "after", "open", None), (7, "before", "dropped", None)])
    d = w["c"].get(w["base"], params={"files": "src/billing/x/y.py,db/a.sql,README.md"}).json()
    assert d["launch_date"] == "2027-02-03"
    assert (d["open"], d["done"], d["would_be"]) == (2, 1, 8)
    assert d["after"] == ["#4 item 4"]
    assert d["critical_touched"] == ["src/billing/**", "db/*.sql"]
    assert d["estimate"] is None
    assert w["c"].get(w["base"]).json()["critical_touched"] == []


def test_estimate_needs_ten(world):
    w = world
    spans = [1, 2, 3, 4, 5, 6, 7, 8, 9]
    _seed(w["pid"], [(i + 1, "before", "done", sp) for i, sp in enumerate(spans)])
    assert w["c"].get(w["base"]).json()["estimate"] is None
    _seed(w["pid"], [(10, "before", "done", 20)])
    e = w["c"].get(w["base"]).json()["estimate"]
    assert e == {"median_days": 5.5, "min_days": 1, "max_days": 20, "n": 10}


def test_estimate_unit_median_over_integer_days():
    class I:
        def __init__(self, a, b):
            self.started_at, self.done_at = a, b
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    items = [I(t, t + timedelta(days=d)) for d in [0, 1, 1, 2, 3, 3, 3, 4, 5, 6, 7]]
    items.append(I(None, t))
    assert facts.estimate(items) == {"median_days": 3, "min_days": 0, "max_days": 7, "n": 11}


def test_files_validation(world):
    w = world
    g = lambda files: w["c"].get(w["base"], params={"files": files}).status_code  # noqa: E731
    assert g(",".join(f"f{i}.py" for i in range(50))) == 200
    assert g(",".join(f"f{i}.py" for i in range(51))) == 422
    assert g("../etc/passwd") == 422
    assert g("a/../b") == 422
    assert g("/abs/path") == 422
    assert g("~/x") == 422
    assert g("a\\b") == 422


def test_outsider_404(world):
    assert world["out"].get(world["base"]).status_code == 404


def test_would_be_follows_counter_after_delete(world):
    w = world
    for n in (1, 2, 3):
        assert w["c"].post(f"/projects/{w['pid']}/launch/items", json={"title": f"t{n}"}).status_code == 201
    assert w["admin"].delete(f"/projects/{w['pid']}/launch/items/3").status_code == 204
    assert w["c"].get(w["base"]).json()["would_be"] == 4
    r = w["c"].post(f"/projects/{w['pid']}/launch/scope-check", json={"prompt": "can we add x"})
    assert r.json()["would_be"] == 4
