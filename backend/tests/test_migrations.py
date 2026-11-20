import sqlite3

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
    assert _alembic_version(tmp_path / "fresh.db") == "0005"


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
    assert _alembic_version(db) == "0005"
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
