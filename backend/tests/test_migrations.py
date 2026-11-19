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
    with Session(engine) as s:
        s.add(models.Project(id="p1", name="legacy", slug="legacy", directory="/x"))
        s.commit()
    engine.dispose()


def test_fresh_db_migrates_to_head(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/fresh.db"
    run_migrations(url)
    tables = _tables(tmp_path / "fresh.db")
    assert {"tasks", "projects", "context_docs", "alembic_version"} <= tables
    assert _alembic_version(tmp_path / "fresh.db") == "0002"


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
    with Session(engine) as s:
        s.add(models.Task(id="t1", title="old", prompt="hi"))
        s.commit()
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
    assert _alembic_version(db) == "0002"
    assert "users" in _tables(db)
