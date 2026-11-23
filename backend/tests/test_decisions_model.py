"""Phase 4a task 1: ProjectDecision, validation, numbering, project delete cascade, migration 0011."""

import asyncio
import uuid

import pytest
from alembic import command
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal, _alembic_cfg
from app.decisions.validate import clean_fields, display_id, next_number
from app.models import Project, ProjectDecision
from test_migrations import _inspect, scratch_url  # noqa: F401 - the fixture


def _run(coro):
    return asyncio.run(coro)


def _project():
    pid = uuid.uuid4().hex

    async def go():
        async with SessionLocal() as s:
            s.add(Project(id=pid, name="d" + pid[:8], slug="d" + pid[:8], directory="/x"))
            await s.commit()

    _run(go())
    return pid


def _add(s, pid, number, title="t"):
    s.add(ProjectDecision(project_id=pid, number=number, title=title))


def test_display_id():
    assert display_id(7) == "D-007"
    assert display_id(1234) == "D-1234"


def test_touches_validation():
    ok = clean_fields({"title": "t", "touches": ["src/**/*.py", "src/auth", "./a//b/", "src/auth"]}, partial=False)
    assert ok["touches"] == ["src/**/*.py", "src/auth", "a/b"]
    for bad in (["/etc"], ["~/x"], ["a/../b"], ["a\\b"], [""], ["./"], [5], ["x"] * 21, ["a" * 301], "src"):
        with pytest.raises(ValueError):
            clean_fields({"title": "t", "touches": bad}, partial=False)
    assert len(clean_fields({"title": "t", "touches": [f"f{i}" for i in range(20)]}, partial=False)["touches"]) == 20


def test_limits_and_kind():
    for f, n in (("title", 201), ("why", 2001), ("source", 301)):
        with pytest.raises(ValueError):
            clean_fields({"title": "t", f: "a" * n}, partial=False)
    with pytest.raises(ValueError):
        clean_fields({"title": "  "}, partial=False)
    with pytest.raises(ValueError):
        clean_fields({"title": "t", "kind": "nope"}, partial=False)
    assert clean_fields({"why": "w"}, partial=True) == {"why": "w"}
    d = clean_fields({"title": " t "}, partial=False)
    assert d == {"title": "t", "kind": "decision", "why": "", "see": "", "source": "", "touches": []}


def test_reference_needs_see():
    with pytest.raises(ValueError):
        clean_fields({"title": "t", "kind": "reference"}, partial=False)
    with pytest.raises(ValueError):
        clean_fields({"title": "t", "kind": "reference", "see": "  "}, partial=False)
    assert clean_fields({"title": "t", "kind": "reference", "see": "docs/a.md"}, partial=False)["see"] == "docs/a.md"
    with pytest.raises(ValueError):
        clean_fields({"kind": "reference", "see": ""}, partial=True)


def test_see_only_for_reference():
    with pytest.raises(ValueError):
        clean_fields({"title": "t", "kind": "rule", "see": "docs/a.md"}, partial=False)
    with pytest.raises(ValueError):
        clean_fields({"title": "t", "kind": "reference", "see": "/abs"}, partial=False)
    assert clean_fields({"title": "t", "kind": "rule", "see": ""}, partial=False)["see"] == ""


def test_text_cleaned_and_redacted():
    tok = "ghp_" + "a" * 36
    d = clean_fields({"title": f"a​b {tok}", "why": f"w {tok}", "source": f"s {tok}"}, partial=False)
    assert "​" not in d["title"] and tok not in d["title"] and d["title"].startswith("ab ")
    assert tok not in d["why"] and tok not in d["source"]


def test_next_number_never_reused(client):
    pid = _project()

    async def go():
        async with SessionLocal() as s:
            nums = []
            for _ in range(3):
                n = await next_number(s, pid)
                nums.append(n)
                _add(s, pid, n)
            await s.commit()
            assert nums == [1, 2, 3]
            await s.execute(delete(ProjectDecision).where(ProjectDecision.project_id == pid, ProjectDecision.number == 3))
            await s.commit()
            n = await next_number(s, pid)
            await s.commit()
            return n

    assert _run(go()) == 4


def test_unique_number_per_project(client):
    a, b = _project(), _project()

    async def go():
        async with SessionLocal() as s:
            _add(s, a, 1)
            _add(s, b, 1)  # another project may reuse the number
            await s.commit()
            _add(s, a, 1)
            with pytest.raises(IntegrityError):
                await s.commit()

    _run(go())


def test_project_delete_removes_decisions(client):
    pid = client.post("/projects", json={"name": "DecDel"}).json()["id"]

    async def seed():
        async with SessionLocal() as s:
            _add(s, pid, 1)
            await s.commit()

    _run(seed())
    assert client.delete(f"/projects/{pid}").status_code == 204

    async def count():
        async with SessionLocal() as s:
            return (await s.execute(
                select(func.count()).select_from(ProjectDecision).where(ProjectDecision.project_id == pid))).scalar()

    assert _run(count()) == 0


def test_migration_0011_upgrade_downgrade(scratch_url):  # noqa: F811
    url = scratch_url
    command.upgrade(_alembic_cfg(url), "0010")

    def seed(sc, insp):
        sc.exec_driver_sql(
            "INSERT INTO projects (id, name, slug, directory, default_model, env, memory,"
            " memory_enabled, archived, auto_review_prs, created_at)"
            " VALUES ('p11', 'old', 'old', '/x', 'sonnet', '{}', '', true, false, false, CURRENT_TIMESTAMP)")

    _inspect(url, seed)
    command.upgrade(_alembic_cfg(url), "head")

    def check(sc, insp):
        assert {"id", "project_id", "number", "kind", "title", "why", "see", "touches", "decided_by", "decided_at",
                "last_verified_at", "source", "status", "superseded_by", "version", "created_at",
                "updated_at"} <= {c["name"] for c in insp.get_columns("project_decisions")}
        idx = {tuple(i["column_names"]) for i in insp.get_indexes("project_decisions")}
        assert {("project_id",), ("project_id", "status")} <= idx
        uq = {tuple(u["column_names"]) for u in insp.get_unique_constraints("project_decisions")}
        assert ("project_id", "number") in uq
        assert {f["referred_table"] for f in insp.get_foreign_keys("project_decisions")} == {"projects", "users"}
        assert sc.exec_driver_sql("SELECT decision_seq FROM projects WHERE id='p11'").scalar() == 0
        return sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()

    assert _inspect(url, check) == "0011"
    command.downgrade(_alembic_cfg(url), "0010")
    assert _inspect(url, lambda sc, insp: "project_decisions" in insp.get_table_names()) is False
    assert _inspect(url, lambda sc, insp: "decision_seq" in {c["name"] for c in insp.get_columns("projects")}) is False
