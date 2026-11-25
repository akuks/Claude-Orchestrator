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

HEAD = "0026"  # the latest revision


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


def test_migration_0012_upgrade_downgrade(scratch_url):
    from alembic import command

    url = scratch_url
    command.upgrade(_alembic_cfg(url), "0011")
    command.upgrade(_alembic_cfg(url), "0012")

    def check(sc, insp):
        assert {"stale_flagged_at", "stale_reason"} <= {c["name"] for c in insp.get_columns("project_decisions")}
        assert {"id", "project_id", "task_id", "source", "kind", "title", "why", "touches", "evidence",
                "dedupe_key", "status", "proposed_by", "reviewed_by", "reviewed_at", "decision_number",
                "created_at"} <= {c["name"] for c in insp.get_columns("decision_proposals")}
        uq = {tuple(u["column_names"]) for u in insp.get_unique_constraints("decision_proposals")}
        assert ("project_id", "dedupe_key") in uq
        idx = {tuple(i["column_names"]) for i in insp.get_indexes("decision_proposals")}
        assert {("project_id",), ("project_id", "status")} <= idx
        return sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()

    assert _inspect(url, check) == "0012"
    command.downgrade(_alembic_cfg(url), "0011")
    assert _inspect(url, lambda sc, insp: "decision_proposals" in insp.get_table_names()) is False
    assert _inspect(url, lambda sc, insp: "stale_reason" in {c["name"] for c in insp.get_columns("project_decisions")}) is False


def test_migration_0013_upgrade_downgrade(scratch_url):
    from alembic import command

    url = scratch_url
    command.upgrade(_alembic_cfg(url), "0012")
    command.upgrade(_alembic_cfg(url), "0013")

    def check(sc, insp):
        assert "model_tiers" in {c["name"] for c in insp.get_columns("guard_org_settings")}
        assert "tier" in {c["name"] for c in insp.get_columns("agents")}
        assert {"brief", "brief_bounds"} <= {c["name"] for c in insp.get_columns("tasks")}
        return sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()

    assert _inspect(url, check) == "0013"
    command.downgrade(_alembic_cfg(url), "0012")

    def check_down(sc, insp):
        assert "model_tiers" not in {c["name"] for c in insp.get_columns("guard_org_settings")}
        assert "tier" not in {c["name"] for c in insp.get_columns("agents")}
        assert not ({"brief", "brief_bounds"} & {c["name"] for c in insp.get_columns("tasks")})
        return sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()

    assert _inspect(url, check_down) == "0012"


def test_migration_0014_upgrade_downgrade(scratch_url):
    from alembic import command

    url = scratch_url
    command.upgrade(_alembic_cfg(url), "0013")
    command.upgrade(_alembic_cfg(url), "0014")

    def check(sc, insp):
        assert {"stage_state", "log"} <= {c["name"] for c in insp.get_columns("workflow_runs")}
        assert "stage_attempt" in {c["name"] for c in insp.get_columns("tasks")}
        assert {"id", "run_id", "stage_index", "attempt", "handout", "command", "kind", "started_at",
                "finished_at", "duration_ms", "exit_code", "outcome", "runner", "passed", "failed",
                "skipped", "failing", "flaky", "code_state", "log_name"} <= {
            c["name"] for c in insp.get_columns("workflow_gate_runs")}
        assert ("run_id",) in {tuple(i["column_names"]) for i in insp.get_indexes("workflow_gate_runs")}
        return sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()

    assert _inspect(url, check) == "0014"
    command.downgrade(_alembic_cfg(url), "0013")

    def check_down(sc, insp):
        assert "workflow_gate_runs" not in insp.get_table_names()
        assert not ({"stage_state", "log"} & {c["name"] for c in insp.get_columns("workflow_runs")})
        assert "stage_attempt" not in {c["name"] for c in insp.get_columns("tasks")}
        return sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()

    assert _inspect(url, check_down) == "0013"


def test_migration_0015_upgrade_downgrade(scratch_url):
    from alembic import command

    url = scratch_url
    command.upgrade(_alembic_cfg(url), "0014")
    def old_rows(sc, insp):  # a project and a task from before 0015
        sc.exec_driver_sql(
            "INSERT INTO projects (id, name, slug, directory, default_model, env, memory, memory_enabled,"
            " archived, auto_review_prs, created_at) VALUES ('p15', 'old', 'old', '/x', 'sonnet', '{}', '',"
            " true, false, false, CURRENT_TIMESTAMP)")
        sc.exec_driver_sql(
            "INSERT INTO tasks (id, title, prompt, status, priority, tags, model, max_turns, attempt, allow_remote,"
            " read_only, requires_approval, risk, created_at) VALUES ('t15', 't', 'p', 'completed', 'normal', '[]',"
            " '', 25, 0, true, false, false, 'info', CURRENT_TIMESTAMP)")

    _inspect(url, old_rows)
    command.upgrade(_alembic_cfg(url), "0015")

    def check(sc, insp):
        assert "launch_seq" in {c["name"] for c in insp.get_columns("projects")}
        assert "launch_item_id" in {c["name"] for c in insp.get_columns("tasks")}
        assert {"project_id", "launch_date", "critical_paths", "version", "updated_at"} <= {
            c["name"] for c in insp.get_columns("project_launches")}
        assert {"id", "project_id", "number", "title", "section", "status", "added_at", "started_at",
                "done_at", "added_by", "version"} <= {c["name"] for c in insp.get_columns("launch_items")}
        uq = {tuple(u["column_names"]) for u in insp.get_unique_constraints("launch_items")}
        assert ("project_id", "number") in uq
        assert ("project_id",) in {tuple(i["column_names"]) for i in insp.get_indexes("launch_items")}
        assert ("launch_item_id",) in {tuple(i["column_names"]) for i in insp.get_indexes("tasks")}
        assert any(f["referred_table"] == "launch_items" for f in insp.get_foreign_keys("tasks"))
        return sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()

    assert _inspect(url, check) == "0015"

    def old_read(sc, insp):
        return (sc.exec_driver_sql("SELECT launch_seq FROM projects WHERE id='p15'").scalar(),
                sc.exec_driver_sql("SELECT launch_item_id FROM tasks WHERE id='t15'").scalar())

    assert _inspect(url, old_read) == (0, None)
    command.downgrade(_alembic_cfg(url), "0014")

    def check_down(sc, insp):
        assert not ({"project_launches", "launch_items"} & set(insp.get_table_names()))
        assert "launch_seq" not in {c["name"] for c in insp.get_columns("projects")}
        assert "launch_item_id" not in {c["name"] for c in insp.get_columns("tasks")}
        return sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()

    assert _inspect(url, check_down) == "0014"


def test_migration_0016_upgrade_downgrade(scratch_url):
    from alembic import command

    url = scratch_url
    command.upgrade(_alembic_cfg(url), "0015")

    def old_rows(sc, insp):  # rows from before 0016
        sc.exec_driver_sql(
            "INSERT INTO projects (id, name, slug, directory, default_model, env, memory, memory_enabled,"
            " archived, auto_review_prs, created_at, launch_seq) VALUES ('p16', 'old', 'old', '/x', 'sonnet', '{}', '',"
            " true, false, false, CURRENT_TIMESTAMP, 0)")
        sc.exec_driver_sql(
            "INSERT INTO tasks (id, title, prompt, status, priority, tags, model, max_turns, attempt, allow_remote,"
            " read_only, requires_approval, risk, created_at) VALUES ('t16', 't', 'p', 'completed', 'normal', '[]',"
            " '', 25, 0, true, false, false, 'info', CURRENT_TIMESTAMP)")
        sc.exec_driver_sql(
            "INSERT INTO project_launches (project_id, critical_paths, version, updated_at)"
            " VALUES ('p16', '[]', 1, CURRENT_TIMESTAMP)")

    _inspect(url, old_rows)
    command.upgrade(_alembic_cfg(url), "0016")

    def check(sc, insp):
        assert "outputs" in {c["name"] for c in insp.get_columns("tasks")}
        assert "budget_usd" in {c["name"] for c in insp.get_columns("workflows")}
        assert "budget_usd" in {c["name"] for c in insp.get_columns("workflow_runs")}
        assert "raise_risk" in {c["name"] for c in insp.get_columns("project_launches")}
        return sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()

    assert _inspect(url, check) == "0016"

    def old_read(sc, insp):
        return (sc.exec_driver_sql("SELECT outputs FROM tasks WHERE id='t16'").scalar(),
                bool(sc.exec_driver_sql("SELECT raise_risk FROM project_launches WHERE project_id='p16'").scalar()))

    assert _inspect(url, old_read) == (None, False)
    command.downgrade(_alembic_cfg(url), "0015")

    def check_down(sc, insp):
        assert "outputs" not in {c["name"] for c in insp.get_columns("tasks")}
        assert "budget_usd" not in {c["name"] for c in insp.get_columns("workflows")}
        assert "budget_usd" not in {c["name"] for c in insp.get_columns("workflow_runs")}
        assert "raise_risk" not in {c["name"] for c in insp.get_columns("project_launches")}
        return sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()

    assert _inspect(url, check_down) == "0015"


def test_migration_0017_upgrade_downgrade(scratch_url):
    from alembic import command

    url = scratch_url
    command.upgrade(_alembic_cfg(url), "0016")

    def old_rows(sc, insp):  # rows from before 0017
        sc.exec_driver_sql(
            "INSERT INTO projects (id, name, slug, directory, default_model, env, memory, memory_enabled,"
            " archived, auto_review_prs, created_at, launch_seq) VALUES ('p17', 'old', 'old', '/x', 'sonnet', '{}', '',"
            " true, false, false, CURRENT_TIMESTAMP, 0)")
        sc.exec_driver_sql(
            "INSERT INTO skill_packs (id, name, local_path, enabled, scope, created_at)"
            " VALUES ('k17', 'old-pack', '/x', true, 'global', CURRENT_TIMESTAMP)")

    _inspect(url, old_rows)
    command.upgrade(_alembic_cfg(url), "0017")

    def check(sc, insp):
        assert "licence" in {c["name"] for c in insp.get_columns("projects")}
        cols = {c["name"] for c in insp.get_columns("skill_packs")}
        assert {"latest_sha", "checked_at"} <= cols
        return sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()

    assert _inspect(url, check) == "0017"

    def old_read(sc, insp):
        return (sc.exec_driver_sql("SELECT licence FROM projects WHERE id='p17'").scalar(),
                sc.exec_driver_sql("SELECT latest_sha FROM skill_packs WHERE id='k17'").scalar(),
                sc.exec_driver_sql("SELECT checked_at FROM skill_packs WHERE id='k17'").scalar())

    assert _inspect(url, old_read) == (None, None, None)
    command.downgrade(_alembic_cfg(url), "0016")

    def check_down(sc, insp):
        assert "licence" not in {c["name"] for c in insp.get_columns("projects")}
        cols = {c["name"] for c in insp.get_columns("skill_packs")}
        assert not ({"latest_sha", "checked_at"} & cols)
        return sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()

    assert _inspect(url, check_down) == "0016"


def test_migration_0018_upgrade_downgrade(scratch_url):
    from alembic import command

    url = scratch_url
    command.upgrade(_alembic_cfg(url), "0017")

    def old_rows(sc, insp):  # a project from before 0018
        sc.exec_driver_sql(
            "INSERT INTO projects (id, name, slug, directory, default_model, env, memory, memory_enabled,"
            " archived, auto_review_prs, created_at, launch_seq) VALUES ('p18', 'old', 'old', '/x', 'sonnet', '{}', '',"
            " true, false, false, CURRENT_TIMESTAMP, 0)")

    _inspect(url, old_rows)
    command.upgrade(_alembic_cfg(url), "0018")

    def check(sc, insp):
        assert "code_map" in {c["name"] for c in insp.get_columns("projects")}
        cols = {c["name"] for c in insp.get_columns("project_indexes")}
        assert {"project_id", "sha", "indexed_at", "status", "error", "fail_count", "next_try_at",
                "started_at"} <= cols
        sc.exec_driver_sql("INSERT INTO project_indexes (project_id) VALUES ('p18')")
        return (sc.exec_driver_sql("SELECT code_map FROM projects WHERE id='p18'").scalar(),
                sc.exec_driver_sql("SELECT status, fail_count FROM project_indexes").first(),
                sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar())

    code_map, idx, ver = _inspect(url, check)
    assert not code_map and tuple(idx) == ("never", 0) and ver == "0018"
    command.downgrade(_alembic_cfg(url), "0017")

    def check_down(sc, insp):
        assert "project_indexes" not in insp.get_table_names()
        assert "code_map" not in {c["name"] for c in insp.get_columns("projects")}
        return sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()

    assert _inspect(url, check_down) == "0017"


def test_migration_0019_upgrade_downgrade(scratch_url):
    from alembic import command

    url = scratch_url
    command.upgrade(_alembic_cfg(url), "0018")

    def old_rows(sc, insp):  # a connected tool from before 0019 (every NOT NULL column without a default)
        sc.exec_driver_sql(
            "INSERT INTO mcp_servers (id, name, transport, scope, command, args, enabled, status, tools, created_at)"
            " VALUES ('m19', 'old-tool', 'stdio', 'team', 'npx', '[]', true, 'unknown', '[]', CURRENT_TIMESTAMP)")

    _inspect(url, old_rows)
    command.upgrade(_alembic_cfg(url), "0019")

    def check(sc, insp):
        assert "start_in_project" in {c["name"] for c in insp.get_columns("mcp_servers")}
        return (sc.exec_driver_sql("SELECT start_in_project FROM mcp_servers WHERE id='m19'").scalar(),
                sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar())

    in_project, ver = _inspect(url, check)
    assert in_project is not None and not in_project and ver == "0019"
    command.downgrade(_alembic_cfg(url), "0018")

    def check_down(sc, insp):
        assert "start_in_project" not in {c["name"] for c in insp.get_columns("mcp_servers")}
        return (sc.exec_driver_sql("SELECT name FROM mcp_servers WHERE id='m19'").scalar(),
                sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar())

    assert _inspect(url, check_down) == ("old-tool", "0018")


def test_migration_0020_upgrade_downgrade(scratch_url):
    from alembic import command

    url = scratch_url
    command.upgrade(_alembic_cfg(url), "0019")

    def old_rows(sc, insp):  # a project from before 0020
        sc.exec_driver_sql("INSERT INTO projects (id, name, slug, directory, default_model, env, memory, memory_enabled, archived, auto_review_prs, created_at)"
            " VALUES ('p20', 'old-p', 'old-p', '/x', 'sonnet', '{}', '', true, false, false, CURRENT_TIMESTAMP)")

    _inspect(url, old_rows)
    command.upgrade(_alembic_cfg(url), "0020")

    def check(sc, insp):
        cols = {c["name"] for c in insp.get_columns("projects")}
        assert {"ui_skills", "brand_skill"} <= cols
        return (sc.exec_driver_sql("SELECT ui_skills, brand_skill FROM projects WHERE id='p20'").one(),
                sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar())

    (ui, brand), ver = _inspect(url, check)
    assert ui is not None and bool(ui) is True and brand is None and ver == "0020"
    command.downgrade(_alembic_cfg(url), "0019")

    def check_down(sc, insp):
        cols = {c["name"] for c in insp.get_columns("projects")}
        assert not ({"ui_skills", "brand_skill"} & cols)
        return (sc.exec_driver_sql("SELECT name FROM projects WHERE id='p20'").scalar(),
                sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar())

    assert _inspect(url, check_down) == ("old-p", "0019")


def test_migration_0021_upgrade_downgrade(scratch_url):
    from alembic import command

    url = scratch_url
    command.upgrade(_alembic_cfg(url), "0020")

    def old_rows(sc, insp):  # a task from before 0021 (every NOT NULL column without a default)
        sc.exec_driver_sql(
            "INSERT INTO tasks (id, title, prompt, status, priority, tags, model, max_turns, attempt, allow_remote,"
            " read_only, requires_approval, risk, created_at, origin, recoveries, auto_continued, stage_attempt)"
            " VALUES ('t21', 'old', 'p', 'queued', 'normal', '[]', '', 25, 0, true, false, false, 'info',"
            " CURRENT_TIMESTAMP, 'user', 0, false, 0)")

    _inspect(url, old_rows)
    command.upgrade(_alembic_cfg(url), "0021")

    def check(sc, insp):
        cols = {c["name"] for c in insp.get_columns("tasks")}
        assert {"not_before", "wait_reason", "plan_requeues"} <= cols
        pcols = {c["name"] for c in insp.get_columns("plan_limit_status")}
        assert {"id", "credential_owner", "status", "resets_at", "utilization", "raw", "api_key",
                "credential_ref", "confirmed", "run_ref", "seen_at", "task_id"} <= pcols
        sc.exec_driver_sql(
            "INSERT INTO plan_limit_status (credential_owner, status, resets_at, utilization, raw, api_key, seen_at,"
            " task_id) VALUES ('u1', 'rejected', CURRENT_TIMESTAMP, 0.97, '{\"status\": \"rejected\"}', false,"
            " CURRENT_TIMESTAMP, 't21')")
        sc.exec_driver_sql(
            "INSERT INTO plan_limit_status (credential_owner, status, raw, seen_at)"
            " VALUES ('org', 'allowed', '{}', CURRENT_TIMESTAMP)")
        assert sc.exec_driver_sql("SELECT plan_requeues FROM tasks WHERE id='t21'").scalar() == 0
        assert not sc.exec_driver_sql("SELECT confirmed FROM plan_limit_status WHERE credential_owner='org'").scalar()
        return (sc.exec_driver_sql("SELECT not_before, wait_reason FROM tasks WHERE id='t21'").one(),
                sc.exec_driver_sql("SELECT api_key FROM plan_limit_status WHERE credential_owner='org'").scalar(),
                sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar())

    (nb, why), org_key, ver = _inspect(url, check)
    assert nb is None and why is None and org_key is not None and not org_key and ver == "0021"
    command.downgrade(_alembic_cfg(url), "0020")

    def check_down(sc, insp):
        assert "plan_limit_status" not in insp.get_table_names()
        assert not ({"not_before", "wait_reason", "plan_requeues"} & {c["name"] for c in insp.get_columns("tasks")})
        return (sc.exec_driver_sql("SELECT title FROM tasks WHERE id='t21'").scalar(),
                sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar())

    assert _inspect(url, check_down) == ("old", "0020")


def test_migration_0022_upgrade_downgrade(scratch_url):
    from alembic import command

    url = scratch_url
    command.upgrade(_alembic_cfg(url), "0021")

    def old_rows(sc, insp):  # org settings and an event from before 0022 (real booleans where there are any)
        sc.exec_driver_sql(
            "INSERT INTO guard_org_settings (id, disabled_rules, dev_db_patterns, ground_rules, model_tiers, updated_at)"
            " VALUES (1, '[]', '[]', '{}', '{}', CURRENT_TIMESTAMP)")
        sc.exec_driver_sql(
            "INSERT INTO tasks (id, title, prompt, status, priority, tags, model, max_turns, attempt, allow_remote,"
            " read_only, requires_approval, risk, created_at, origin, recoveries, auto_continued, stage_attempt,"
            " plan_requeues) VALUES ('t22', 'old', 'p', 'completed', 'normal', '[]', '', 25, 0, true, false, false,"
            " 'info', CURRENT_TIMESTAMP, 'user', 0, false, 0, 0)")
        sc.exec_driver_sql("INSERT INTO task_events (task_id, seq, type, payload, created_at)"
                           " VALUES ('t22', 1, 'text_output', '{}', CURRENT_TIMESTAMP)")

    _inspect(url, old_rows)
    command.upgrade(_alembic_cfg(url), "0022")

    def check(sc, insp):
        assert "retention" in {c["name"] for c in insp.get_columns("guard_org_settings")}
        assert "root_commit" in {c["name"] for c in insp.get_columns("projects")}
        idx = {i["name"]: i["column_names"] for i in insp.get_indexes("task_events")}
        assert idx.get("ix_task_events_created_at") == ["created_at"]
        assert idx.get("ix_task_events_task_id") == ["task_id"]
        return (sc.exec_driver_sql("SELECT retention FROM guard_org_settings WHERE id=1").scalar(),
                sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar())

    kept, ver = _inspect(url, check)
    assert (json.loads(kept) if isinstance(kept, str) else kept) == {}
    assert ver == "0022"
    command.downgrade(_alembic_cfg(url), "0021")

    def check_down(sc, insp):
        assert "retention" not in {c["name"] for c in insp.get_columns("guard_org_settings")}
        assert "root_commit" not in {c["name"] for c in insp.get_columns("projects")}
        idx = {i["name"] for i in insp.get_indexes("task_events")}
        assert "ix_task_events_created_at" not in idx and "ix_task_events_task_id" in idx
        return (sc.exec_driver_sql("SELECT type FROM task_events WHERE task_id='t22'").scalar(),
                sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar())

    assert _inspect(url, check_down) == ("text_output", "0021")


def test_migration_0022_is_one_transaction_and_skips_what_is_there(scratch_url, caplog):
    """No autocommit / CONCURRENTLY step (all-or-nothing at start); a database that already has a 0022 column
    or index (a hand fix, a repaired legacy DB) still upgrades; the index build is announced in the log."""
    import logging
    import pathlib

    from alembic import command

    src = (pathlib.Path(__file__).parent.parent / "migrations" / "versions" / "0022_upkeep.py").read_text()
    assert "autocommit_block" not in src and "concurrently" not in src.lower()
    url = scratch_url
    command.upgrade(_alembic_cfg(url), "0021")

    def partial(sc, insp):
        sc.exec_driver_sql("ALTER TABLE projects ADD COLUMN root_commit VARCHAR(40)")
        sc.exec_driver_sql("CREATE INDEX ix_task_events_created_at ON task_events (created_at)")

    _inspect(url, partial)
    with caplog.at_level(logging.WARNING):
        command.upgrade(_alembic_cfg(url), "0022")
    assert not any("task history table" in r.getMessage() for r in caplog.records)  # the index was there

    def check(sc, insp):
        assert "retention" in {c["name"] for c in insp.get_columns("guard_org_settings")}
        assert "root_commit" in {c["name"] for c in insp.get_columns("projects")}
        return sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()

    assert _inspect(url, check) == "0022"
    command.downgrade(_alembic_cfg(url), "0021")
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        command.upgrade(_alembic_cfg(url), "0022")
    assert any("task history table" in r.getMessage() for r in caplog.records)


def test_migration_0023_upgrade_downgrade(scratch_url):
    from alembic import command

    url = scratch_url
    command.upgrade(_alembic_cfg(url), "0022")

    def old_rows(sc, insp):
        sc.exec_driver_sql("INSERT INTO mcp_servers (id, name, transport, scope, args, enabled, status, tools, created_at,"
                           " environment, default_action, start_in_project) VALUES ('m23', 'old', 'stdio', 'team', '[]', true,"
                           " 'unknown', '[]', CURRENT_TIMESTAMP, 'unknown', 'auto_approve', false)")

    _inspect(url, old_rows)
    command.upgrade(_alembic_cfg(url), "0023")

    def check(sc, insp):
        cols = {c["name"]: c for c in insp.get_columns("mcp_servers")}
        assert "server_version" in cols and cols["server_version"]["nullable"]
        return (sc.exec_driver_sql("SELECT server_version FROM mcp_servers WHERE id='m23'").scalar(),
                sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar())

    assert _inspect(url, check) == (None, "0023")
    command.upgrade(_alembic_cfg(url), "0023")  # again: nothing to do
    command.downgrade(_alembic_cfg(url), "0022")

    def check_down(sc, insp):
        assert "server_version" not in {c["name"] for c in insp.get_columns("mcp_servers")}
        return sc.exec_driver_sql("SELECT name FROM mcp_servers WHERE id='m23'").scalar()

    assert _inspect(url, check_down) == "old"


def test_migration_0024_upgrade_downgrade(scratch_url):
    import pathlib

    from alembic import command

    src = (pathlib.Path(__file__).parent.parent / "migrations" / "versions" / "0024_decision_showings.py").read_text()
    assert "autocommit_block" not in src and 'down_revision = "0023"' in src
    url = scratch_url
    command.upgrade(_alembic_cfg(url), "0023")

    def old_rows(sc, insp):  # a project and a task from before 0024 (real booleans)
        sc.exec_driver_sql(
            "INSERT INTO projects (id, name, slug, directory, default_model, env, memory, memory_enabled, archived,"
            " auto_review_prs, created_at) VALUES ('p24', 'p24', 'p24', '/x', 'sonnet', '{}', '', true, false,"
            " false, CURRENT_TIMESTAMP)")
        sc.exec_driver_sql(
            "INSERT INTO tasks (id, title, prompt, status, priority, tags, model, max_turns, attempt, allow_remote,"
            " read_only, requires_approval, risk, created_at, origin, recoveries, auto_continued, stage_attempt,"
            " plan_requeues) VALUES ('t24', 'old', 'p', 'completed', 'normal', '[]', '', 25, 0, true, false, false,"
            " 'info', CURRENT_TIMESTAMP, 'user', 0, false, 0, 0)")

    _inspect(url, old_rows)
    command.upgrade(_alembic_cfg(url), "0024")

    def check(sc, insp):
        assert "decision_showings" in insp.get_table_names()
        cols = {c["name"]: c for c in insp.get_columns("decision_showings")}
        assert {"id", "project_id", "decision_number", "task_id", "shown_at", "outcome", "retried",
                "settled_at"} <= set(cols)
        assert cols["outcome"]["nullable"] and cols["settled_at"]["nullable"]
        idx = {i["name"]: i["column_names"] for i in insp.get_indexes("decision_showings")}
        assert idx.get("ix_decision_showings_project_number") == ["project_id", "decision_number"]
        assert idx.get("ix_decision_showings_project_shown") == ["project_id", "shown_at"]
        fks = {tuple(fk["constrained_columns"]): (fk["referred_table"], (fk.get("options") or {}).get("ondelete"))
               for fk in insp.get_foreign_keys("decision_showings")}
        assert fks == {("project_id",): ("projects", "CASCADE"), ("task_id",): ("tasks", "CASCADE")}
        uqs = [u["column_names"] for u in insp.get_unique_constraints("decision_showings")]
        assert ["task_id", "decision_number"] in uqs
        sc.exec_driver_sql("INSERT INTO decision_showings (project_id, decision_number, task_id, shown_at)"
                           " VALUES ('p24', 1, 't24', CURRENT_TIMESTAMP)")
        dup = False
        try:
            with sc.begin_nested():
                sc.exec_driver_sql("INSERT INTO decision_showings (project_id, decision_number, task_id, shown_at)"
                                   " VALUES ('p24', 1, 't24', CURRENT_TIMESTAMP)")
        except Exception:  # noqa: BLE001 - the unique (task, number)
            dup = True
        assert dup
        return (sc.exec_driver_sql("SELECT retried FROM decision_showings WHERE task_id='t24'").scalar(),
                sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar())

    retried, ver = _inspect(url, check)
    assert not retried and ver == "0024"
    command.upgrade(_alembic_cfg(url), "0024")  # again: nothing to do
    command.downgrade(_alembic_cfg(url), "0023")

    def check_down(sc, insp):
        assert "decision_showings" not in insp.get_table_names()
        return (sc.exec_driver_sql("SELECT title FROM tasks WHERE id='t24'").scalar(),
                sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar())

    assert _inspect(url, check_down) == ("old", "0023")


def test_migration_0025_upgrade_downgrade(scratch_url):
    import pathlib

    from alembic import command

    src = (pathlib.Path(__file__).parent.parent / "migrations" / "versions" / "0025_user_preferences.py").read_text()
    assert "autocommit_block" not in src and 'down_revision = "0024"' in src
    url = scratch_url
    command.upgrade(_alembic_cfg(url), "0024")

    def old_rows(sc, insp):  # a person from before 0025 (real booleans)
        sc.exec_driver_sql(
            "INSERT INTO users (id, email, name, password_hash, role, active, created_at)"
            " VALUES ('u25', 'u25@test.local', 'u25', 'x', 'member', true, CURRENT_TIMESTAMP)")

    _inspect(url, old_rows)
    command.upgrade(_alembic_cfg(url), "0025")

    def check(sc, insp):
        assert "user_preferences" in insp.get_table_names()
        cols = {c["name"]: c for c in insp.get_columns("user_preferences")}
        assert {"user_id", "enabled", "items", "updated_at"} == set(cols)
        assert not cols["enabled"]["nullable"] and not cols["items"]["nullable"]
        assert insp.get_pk_constraint("user_preferences")["constrained_columns"] == ["user_id"]
        fks = {tuple(fk["constrained_columns"]): (fk["referred_table"], (fk.get("options") or {}).get("ondelete"))
               for fk in insp.get_foreign_keys("user_preferences")}
        assert fks == {("user_id",): ("users", "CASCADE")}
        sc.exec_driver_sql("INSERT INTO user_preferences (user_id, updated_at) VALUES ('u25', CURRENT_TIMESTAMP)")
        row = sc.exec_driver_sql("SELECT enabled, items FROM user_preferences WHERE user_id='u25'").one()
        items = row[1] if isinstance(row[1], list) else json.loads(row[1])
        return (bool(row[0]), items, sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar())

    assert _inspect(url, check) == (True, [], "0025")
    command.upgrade(_alembic_cfg(url), "0025")  # again: nothing to do
    command.downgrade(_alembic_cfg(url), "0024")

    def check_down(sc, insp):
        assert "user_preferences" not in insp.get_table_names()
        return (sc.exec_driver_sql("SELECT email FROM users WHERE id='u25'").scalar(),
                sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar())

    assert _inspect(url, check_down) == ("u25@test.local", "0024")


def test_migration_0026_upgrade_downgrade(scratch_url):
    import pathlib

    from alembic import command

    src = (pathlib.Path(__file__).parent.parent / "migrations" / "versions" / "0026_editor_keys.py").read_text()
    assert "autocommit_block" not in src and 'down_revision = "0025"' in src
    url = scratch_url
    command.upgrade(_alembic_cfg(url), "0025")

    def old_rows(sc, insp):  # a person from before 0026 (real booleans)
        sc.exec_driver_sql(
            "INSERT INTO users (id, email, name, password_hash, role, active, created_at)"
            " VALUES ('u26', 'u26@test.local', 'u26', 'x', 'member', true, CURRENT_TIMESTAMP)")

    _inspect(url, old_rows)
    command.upgrade(_alembic_cfg(url), "0026")

    def check(sc, insp):
        assert "editor_keys" in insp.get_table_names()
        cols = {c["name"]: c for c in insp.get_columns("editor_keys")}
        assert set(cols) == {"id", "user_id", "name", "key_hash", "prefix", "projects", "created_at", "expires_at",
                             "last_used_at", "use_count", "revoked_at"}
        for name in ("user_id", "name", "key_hash", "prefix", "created_at", "expires_at", "use_count"):
            assert not cols[name]["nullable"], name
        for name in ("projects", "last_used_at", "revoked_at"):
            assert cols[name]["nullable"], name
        fks = {tuple(fk["constrained_columns"]): (fk["referred_table"], (fk.get("options") or {}).get("ondelete"))
               for fk in insp.get_foreign_keys("editor_keys")}
        assert fks == {("user_id",): ("users", "CASCADE")}
        assert ["key_hash"] in [u["column_names"] for u in insp.get_unique_constraints("editor_keys")]
        assert ["user_id"] in [i["column_names"] for i in insp.get_indexes("editor_keys")]
        sc.exec_driver_sql(
            "INSERT INTO editor_keys (id, user_id, name, key_hash, prefix, created_at, expires_at)"
            " VALUES ('k26', 'u26', 'laptop', 'h26', 'coe_abcd', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
        dup = False
        try:
            with sc.begin_nested():
                sc.exec_driver_sql(
                    "INSERT INTO editor_keys (id, user_id, name, key_hash, prefix, created_at, expires_at)"
                    " VALUES ('k26b', 'u26', 'again', 'h26', 'coe_abcd', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
        except Exception:  # noqa: BLE001 - the unique key_hash
            dup = True
        assert dup
        row = sc.exec_driver_sql("SELECT use_count, projects, revoked_at FROM editor_keys WHERE id='k26'").one()
        return (tuple(row), sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar())

    assert _inspect(url, check) == ((0, None, None), "0026")
    command.upgrade(_alembic_cfg(url), "0026")  # again: nothing to do
    command.downgrade(_alembic_cfg(url), "0025")

    def check_down(sc, insp):
        assert "editor_keys" not in insp.get_table_names()
        return (sc.exec_driver_sql("SELECT email FROM users WHERE id='u26'").scalar(),
                sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar())

    assert _inspect(url, check_down) == ("u26@test.local", "0025")
