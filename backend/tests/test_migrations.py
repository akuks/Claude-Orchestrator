import asyncio
import json
import os
import sqlite3
import uuid

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models
from app.database import _alembic_cfg, _baseline_metadata, run_migrations

HEAD = "0009"  # the latest revision


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
    assert _alembic_version(tmp_path / "fresh.db") == HEAD


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
    assert _alembic_version(db) == HEAD
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
    command.upgrade(_alembic_cfg(url), "0006")

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


def test_migration_0007(scratch_url):
    """0007_policy: new tables and columns; rows that existed before it get
    the defaults (a project is `own`, an MCP server keeps `auto_approve`)."""
    from alembic import command

    url = scratch_url
    command.upgrade(_alembic_cfg(url), "0007")

    def check_head(sc, insp):
        tables = set(insp.get_table_names())
        assert {"action_rules", "guard_org_settings", "action_grants"} <= tables
        assert {"id", "scope", "project_id", "slug", "kind", "match", "pattern", "reason", "enabled",
                "created_by", "created_at", "updated_at"} <= {c["name"] for c in insp.get_columns("action_rules")}
        assert {"id", "disabled_rules", "dev_db_patterns", "updated_by", "updated_at"} <= {
            c["name"] for c in insp.get_columns("guard_org_settings")}
        assert {"id", "root_id", "task_id", "approval_id", "scope", "rule_id", "key_kind", "key", "created_by",
                "created_at", "expires_at", "revoked_at", "revoked_by", "used_at", "use_count"} <= {
            c["name"] for c in insp.get_columns("action_grants")}
        assert {"trust", "quarantine", "on_no_answer", "readonly_paths", "reference_project_ids"} <= {
            c["name"] for c in insp.get_columns("projects")}
        assert {"environment", "default_action"} <= {c["name"] for c in insp.get_columns("mcp_servers")}
        assert {"grant_key", "resume_until", "delivery", "scope", "followup_task_id", "grant_ids"} <= {
            c["name"] for c in insp.get_columns("action_approvals")}
        grant_idx = {tuple(i["column_names"]) for i in insp.get_indexes("action_grants")}
        assert ("root_id",) in grant_idx and ("task_id",) in grant_idx
        assert ("project_id",) in {tuple(i["column_names"]) for i in insp.get_indexes("action_rules")}
        return sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()

    assert _inspect(url, check_head) == "0007"
    command.downgrade(_alembic_cfg(url), "0006")

    def check_down(sc, insp):
        tables = set(insp.get_table_names())
        assert not ({"action_rules", "guard_org_settings", "action_grants"} & tables)
        assert "trust" not in {c["name"] for c in insp.get_columns("projects")}
        assert "default_action" not in {c["name"] for c in insp.get_columns("mcp_servers")}
        assert "grant_key" not in {c["name"] for c in insp.get_columns("action_approvals")}
        sc.exec_driver_sql(
            "INSERT INTO projects (id, name, slug, directory, default_model, env, memory, memory_enabled,"
            " archived, auto_review_prs, created_at, guard_enabled, approval_wait_seconds) VALUES ('p7', 'seven',"
            " 'seven', '/x', 'sonnet', '{}', '', true, false, false, CURRENT_TIMESTAMP, true, 300)")
        sc.exec_driver_sql(
            "INSERT INTO mcp_servers (id, name, transport, scope, args, enabled, status, tools, created_at)"
            " VALUES ('s7', 'db7', 'stdio', 'team', '[]', true, 'unknown', '[]', CURRENT_TIMESTAMP)")
        sc.exec_driver_sql(
            "INSERT INTO tasks (id, title, prompt, status, priority, tags, model, max_turns, attempt, allow_remote,"
            " read_only, requires_approval, risk, created_at) VALUES ('t7', 't', 'p', 'running', 'normal', '[]',"
            " '', 25, 0, true, false, false, 'info', CURRENT_TIMESTAMP)")
        sc.exec_driver_sql(
            "INSERT INTO action_approvals (id, task_id, rule_ids, tool, summary, cwd, reason, command_hash,"
            " status, created_at, expires_at) VALUES ('a7', 't7', '[]', '', '', '', '', '', 'pending',"
            " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
        return sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()

    assert _inspect(url, check_down) == "0006"
    command.upgrade(_alembic_cfg(url), "head")

    def check_defaults(sc, insp):
        q = sc.exec_driver_sql
        proj = tuple(q("SELECT trust, quarantine, on_no_answer, readonly_paths, reference_project_ids"
                       " FROM projects WHERE id='p7'").fetchone())
        srv = tuple(q("SELECT environment, default_action FROM mcp_servers WHERE id='s7'").fetchone())
        appr = tuple(q("SELECT grant_key, resume_until, delivery, scope, followup_task_id, grant_ids"
                       " FROM action_approvals WHERE id='a7'").fetchone())
        return proj, srv, appr

    proj, srv, appr = _inspect(url, check_defaults)

    def as_list(v):
        return json.loads(v) if isinstance(v, str) else v

    assert proj[0] == "own" and bool(proj[1]) is False and proj[2] == "deny"
    assert as_list(proj[3]) == [] and as_list(proj[4]) == []
    assert srv == ("unknown", "auto_approve")
    assert appr[0] == "" and appr[1] is None and appr[2] == "inline" and appr[3] is None and appr[4] is None
    assert as_list(appr[5]) == []

    def new_rows(sc, insp):
        q = sc.exec_driver_sql
        q("INSERT INTO guard_org_settings (id, updated_at) VALUES (1, CURRENT_TIMESTAMP)")
        settings_row = tuple(q("SELECT disabled_rules, dev_db_patterns FROM guard_org_settings").fetchone())
        rule = ("INSERT INTO action_rules (id, scope, project_id, slug, kind, match, pattern, reason, enabled,"
                " created_at, updated_at) VALUES ('{id}', '{scope}', {pid}, 'x', 'confirm', 'text', 'p', '',"
                " true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
        q(rule.format(id="r1", scope="project", pid="'p7'"))
        q(rule.format(id="r2", scope="org", pid="NULL"))
        errors = []
        for rid, scope, pid in (("r3", "project", "'p7'"), ("r4", "org", "NULL")):
            try:
                with sc.begin_nested():
                    q(rule.format(id=rid, scope=scope, pid=pid))
                errors.append("no error")
            except Exception as e:  # noqa: BLE001
                errors.append(type(e).__name__)
        q("INSERT INTO action_grants (id, root_id, scope, rule_id, key_kind, key, created_at, expires_at)"
          " VALUES ('g1', 't7', 'run', 'db-write', 'tool', 'mcp__db__q', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
        use = q("SELECT use_count FROM action_grants WHERE id='g1'").scalar()
        return settings_row, errors, use

    settings_row, errors, use = _inspect(url, new_rows)
    assert [as_list(v) for v in settings_row] == [[], []]
    assert errors == ["IntegrityError", "IntegrityError"]  # one slug per (scope, project); org slugs too
    assert use == 0


def test_migration_0008(scratch_url):
    """0008_resume: task resume columns, task_handoffs, ground_rules; old rows get the
    defaults, a guard follow-up is attributed to the guard and a dispatched child to its agent."""
    from alembic import command

    url = scratch_url
    command.upgrade(_alembic_cfg(url), "0007")
    task_sql = ("INSERT INTO tasks (id, title, prompt, status, priority, tags, model, max_turns, attempt,"
                " allow_remote, read_only, requires_approval, risk, created_at) VALUES ('{id}', '{title}', 'p',"
                " 'completed', 'normal', '[]', '', 25, 0, true, false, false, 'info', CURRENT_TIMESTAMP)")

    def seed(sc, insp):
        sc.exec_driver_sql(task_sql.format(id="t8", title="plain"))
        sc.exec_driver_sql(task_sql.format(id="g8", title="co-guard follow-up: approved request x"))
        # a pre-3a dispatched child (parent and agent set) and a pre-3a follow-up (parent only)
        kid_sql = task_sql.replace("(id, title,", "(id, parent_task_id, agent_id, title,").replace(
            "VALUES ('{id}', '{title}'", "VALUES ('{id}', {parent}, {agent}, '{title}'")
        sc.exec_driver_sql(kid_sql.format(id="k8", parent="'t8'", agent="'a8'", title="dispatched"))
        sc.exec_driver_sql(kid_sql.format(id="f8", parent="'t8'", agent="NULL", title="follow-up"))
        sc.exec_driver_sql("INSERT INTO guard_org_settings (id, updated_at) VALUES (1, CURRENT_TIMESTAMP)")

    _inspect(url, seed)
    command.upgrade(_alembic_cfg(url), "0008")

    def check_head(sc, insp):
        cols = {c["name"] for c in insp.get_columns("tasks")}
        assert {"origin", "notes", "notes_updated_at", "heartbeat_at", "run_pgid", "run_proc_start",
                "recoveries", "recovery", "credential_ref", "head_at_start"} <= cols
        assert {"id", "root_id", "task_id", "version", "status", "reason", "saved_at", "content", "text",
                "prev_content", "prev_text", "closed_at", "closed_by"} <= {
            c["name"] for c in insp.get_columns("task_handoffs")}
        assert ("root_id",) in {tuple(i["column_names"]) for i in insp.get_indexes("task_handoffs")}
        assert "ground_rules" in {c["name"] for c in insp.get_columns("guard_org_settings")}
        q = sc.exec_driver_sql
        rows = {r[0]: (r[1], r[2]) for r in q("SELECT id, origin, recoveries FROM tasks")}
        assert rows["t8"] == ("user", 0) and rows["g8"] == ("guard", 0)
        assert rows["k8"] == ("agent", 0) and rows["f8"] == ("user", 0)
        gr = q("SELECT ground_rules FROM guard_org_settings").scalar()
        assert (json.loads(gr) if isinstance(gr, str) else gr) == {}
        return q("SELECT version_num FROM alembic_version").scalar()

    assert _inspect(url, check_head) == "0008"
    command.downgrade(_alembic_cfg(url), "0007")

    def check_down(sc, insp):
        assert "task_handoffs" not in insp.get_table_names()
        cols = {c["name"] for c in insp.get_columns("tasks")}
        assert not ({"origin", "notes", "heartbeat_at", "recovery", "head_at_start"} & cols)
        assert "ground_rules" not in {c["name"] for c in insp.get_columns("guard_org_settings")}
        return sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()

    assert _inspect(url, check_down) == "0007"
