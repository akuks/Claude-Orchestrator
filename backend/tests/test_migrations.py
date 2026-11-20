import asyncio
import os
import sqlite3
import uuid

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models
from app.database import _alembic_cfg, _baseline_metadata, run_migrations


def _tables(db) -> set[str]:
    with sqlite3.connect(db) as c:
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _alembic_version(db):
    with sqlite3.connect(db) as c:
        row = c.execute("SELECT version_num FROM alembic_version").fetchone()
    return row[0] if row else None


def _count(db, table) -> int:
    with sqlite3.connect(db) as c:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _make_legacy_db(db) -> None:
    engine = create_engine(f"sqlite:///{db}")
    # A legacy DB has exactly the frozen baseline (0001) schema, no alembic_version.
    _baseline_metadata(_alembic_cfg("sqlite+aiosqlite://")).create_all(engine)
    # Raw SQL: the ORM model now has columns the frozen baseline lacks.
    with engine.begin() as c:
        c.exec_driver_sql(
            "INSERT INTO projects (id, name, slug, directory, default_model, env, memory,"
            " memory_enabled, archived, auto_review_prs, created_at)"
            " VALUES ('p1', 'legacy', 'legacy', '/x', 'sonnet', '{}', '', 1, 0, 0, CURRENT_TIMESTAMP)"
        )
    engine.dispose()


def test_fresh_db_migrates_to_head(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/fresh.db"
    run_migrations(url)
    tables = _tables(tmp_path / "fresh.db")
    assert {"tasks", "projects", "context_docs", "alembic_version"} <= tables
    assert _alembic_version(tmp_path / "fresh.db") == "0006"


def test_legacy_db_is_stamped_not_recreated(tmp_path):
    db = tmp_path / "legacy.db"
    _make_legacy_db(db)
    run_migrations(f"sqlite+aiosqlite:///{db}")
    assert _alembic_version(db) is not None
    assert _count(db, "projects") == 1


def test_legacy_db_missing_table_and_column_is_repaired(tmp_path):
    db = tmp_path / "old.db"
    _make_legacy_db(db)
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as c:
        c.exec_driver_sql(
            "INSERT INTO tasks (id, title, prompt, status, priority, tags, model, max_turns,"
            " attempt, allow_remote, read_only, requires_approval, risk, created_at)"
            " VALUES ('t1', 'old', 'hi', 'queued', 'normal', '[]', '', 25, 0, 1, 0, 0, 'info',"
            " CURRENT_TIMESTAMP)"
        )
    engine.dispose()
    with sqlite3.connect(db) as c:
        c.execute("DROP TABLE context_docs")
        c.execute("ALTER TABLE tasks DROP COLUMN read_only")
    run_migrations(f"sqlite+aiosqlite:///{db}")
    assert "context_docs" in _tables(db)
    with sqlite3.connect(db) as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(tasks)")}
        row = c.execute("SELECT id, prompt, read_only FROM tasks").fetchall()
    assert "read_only" in cols
    assert row == [("t1", "hi", 0)]
    assert _alembic_version(db) == "0006"
    assert "users" in _tables(db)


def test_migration_0004(tmp_path):
    from alembic import command

    db = tmp_path / "m4.db"
    url = f"sqlite+aiosqlite:///{db}"
    run_migrations(url)
    assert {"teams", "team_members", "project_members"} <= _tables(db)
    with sqlite3.connect(db) as c:
        assert "project_ids" in {r[1] for r in c.execute("PRAGMA table_info(workflow_runs)")}
    command.downgrade(_alembic_cfg(url), "0003")
    assert not ({"teams", "team_members", "project_members"} & _tables(db))
    with sqlite3.connect(db) as c:
        assert "project_ids" not in {r[1] for r in c.execute("PRAGMA table_info(workflow_runs)")}
    assert _alembic_version(db) == "0003"


def test_migration_0005(tmp_path):
    from alembic import command

    db = tmp_path / "m5.db"
    url = f"sqlite+aiosqlite:///{db}"
    run_migrations(url)
    assert {"user_credentials", "org_settings"} <= _tables(db)
    with sqlite3.connect(db) as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(user_credentials)")}
        assert {"user_id", "claude_kind", "claude_enc", "github_enc", "git_name", "git_email",
                "updated_at"} <= cols
        assert "automation_owner_id" in {r[1] for r in c.execute("PRAGMA table_info(projects)")}
    command.downgrade(_alembic_cfg(url), "0004")
    assert not ({"user_credentials", "org_settings"} & _tables(db))
    with sqlite3.connect(db) as c:
        assert "automation_owner_id" not in {r[1] for r in c.execute("PRAGMA table_info(projects)")}
    assert _alembic_version(db) == "0004"


@pytest.fixture
def scratch_url(tmp_path):
    """A fresh database: SQLite in tmp_path, or - when CO_DATABASE_URL is
    Postgres (the CI job) - a scratch database created on that server and
    dropped afterwards (the session's own database is never downgraded)."""
    base = os.environ.get("CO_DATABASE_URL", "")
    if not base.startswith("postgresql"):
        yield f"sqlite+aiosqlite:///{tmp_path}/m6.db"
        return
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine

    name = "co_mig_" + uuid.uuid4().hex[:12]
    admin_url = make_url(base)

    async def ddl(sql):
        eng = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            async with eng.connect() as c:
                await c.exec_driver_sql(sql)
        finally:
            await eng.dispose()

    asyncio.run(ddl(f'CREATE DATABASE "{name}"'))
    try:
        yield admin_url.set(database=name).render_as_string(hide_password=False)
    finally:
        asyncio.run(ddl(f'DROP DATABASE IF EXISTS "{name}"'))


def _inspect(url, fn):
    from sqlalchemy import inspect
    from sqlalchemy.ext.asyncio import create_async_engine

    async def go():
        eng = create_async_engine(url)
        try:
            async with eng.begin() as c:
                return await c.run_sync(lambda sc: fn(sc, inspect(sc)))
        finally:
            await eng.dispose()

    return asyncio.run(go())


def test_migration_0006(scratch_url):
    from alembic import command

    url = scratch_url
    run_migrations(url)

    def check_head(sc, insp):
        assert "action_approvals" in insp.get_table_names()
        cols = {c["name"] for c in insp.get_columns("action_approvals")}
        assert {"id", "task_id", "project_id", "requested_by", "rule_ids", "tool", "summary", "cwd", "reason",
                "command_hash", "idempotency_key", "status", "created_at", "expires_at", "decided_by",
                "decided_at", "decision_reason"} <= cols
        assert {"guard_enabled", "approval_wait_seconds"} <= {c["name"] for c in insp.get_columns("projects")}
        idx = {i["name"]: i for i in insp.get_indexes("action_approvals")}
        assert idx["uq_action_approvals_pending_key"]["unique"]
        return sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()

    assert _inspect(url, check_head) == "0006"
    command.downgrade(_alembic_cfg(url), "0005")

    def check_down(sc, insp):
        assert "action_approvals" not in insp.get_table_names()
        assert "guard_enabled" not in {c["name"] for c in insp.get_columns("projects")}
        sc.exec_driver_sql(
            "INSERT INTO projects (id, name, slug, directory, default_model, env, memory, memory_enabled,"
            " archived, auto_review_prs, created_at) VALUES ('p6', 'six', 'six', '/x', 'sonnet', '{}', '',"
            " true, false, false, CURRENT_TIMESTAMP)")
        return sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()

    assert _inspect(url, check_down) == "0005"
    command.upgrade(_alembic_cfg(url), "head")

    def check_defaults(sc, insp):
        return tuple(sc.exec_driver_sql(
            "SELECT guard_enabled, approval_wait_seconds FROM projects WHERE id='p6'").fetchone())

    enabled, wait = _inspect(url, check_defaults)
    assert bool(enabled) is True and wait == 300

    def partial_unique(sc, insp):
        sc.exec_driver_sql(
            "INSERT INTO tasks (id, title, prompt, status, priority, tags, model, max_turns, attempt, allow_remote,"
            " read_only, requires_approval, risk, created_at) VALUES ('t6', 't', 'p', 'running', 'normal', '[]',"
            " '', 25, 0, true, false, false, 'info', CURRENT_TIMESTAMP)")
        row = ("INSERT INTO action_approvals (id, task_id, rule_ids, tool, summary, cwd, reason, command_hash,"
               " idempotency_key, status, created_at, expires_at) VALUES ('{id}', 't6', '[]', '', '', '', '',"
               " '', 'k', '{st}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
        sc.exec_driver_sql(row.format(id="a1", st="pending"))
        sc.exec_driver_sql(row.format(id="a2", st="rejected"))
        try:
            with sc.begin_nested():
                sc.exec_driver_sql(row.format(id="a3", st="pending"))
        except Exception as e:  # noqa: BLE001
            return type(e).__name__
        return "no error"

    assert _inspect(url, partial_unique) == "IntegrityError"
