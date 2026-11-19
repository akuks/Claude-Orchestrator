import importlib.util
import pathlib
import sqlite3

import pytest

from app.database import run_migrations

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "migrate_sqlite_to_postgres.py"


def _load():
    spec = importlib.util.spec_from_file_location("migrate_script", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed(db):
    run_migrations(f"sqlite+aiosqlite:///{db}")
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO projects (id, name, slug, directory, default_model, env, memory,"
            " memory_enabled, archived, auto_review_prs, created_at)"
            " VALUES ('p1','proj','proj','/x','sonnet','{}','',1,0,0,CURRENT_TIMESTAMP)"
        )
        c.execute(
            "INSERT INTO tasks (id, title, prompt, project_id, status, priority, tags, model,"
            " max_turns, attempt, allow_remote, read_only, requires_approval, risk, created_at) VALUES ('t1','hello','do it','p1','done','normal',"
            "'[\"a\"]','',25,0,1,0,0,'info',CURRENT_TIMESTAMP)"
        )
        c.execute(
            "INSERT INTO task_events (task_id, seq, type, payload, created_at)"
            " VALUES ('t1', 1, 'text', '{\"x\": 1}', CURRENT_TIMESTAMP)"
        )


def _count(db, table):
    with sqlite3.connect(db) as c:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_copy_sqlite_to_sqlite(tmp_path):
    mod = _load()
    src, dst = tmp_path / "src.db", tmp_path / "dst.db"
    _seed(src)
    before = src.read_bytes()

    assert mod.main(["--from", f"sqlite:///{src}", "--to", f"sqlite:///{dst}"]) == 0
    assert src.read_bytes() == before  # source never modified
    for t in ("projects", "tasks", "task_events"):
        assert _count(dst, t) == _count(src, t) == 1
    with sqlite3.connect(dst) as c:
        assert c.execute("SELECT title FROM tasks WHERE id='t1'").fetchone()[0] == "hello"
        assert c.execute("SELECT payload FROM task_events").fetchone()[0] == '{"x": 1}'

    # Non-empty target is refused without --force
    assert mod.main(["--from", f"sqlite:///{src}", "--to", f"sqlite:///{dst}"]) != 0
    assert _count(dst, "tasks") == 1
    # --force replaces rather than duplicating
    assert mod.main(["--from", f"sqlite:///{src}", "--to", f"sqlite:///{dst}", "--force"]) == 0
    assert _count(dst, "tasks") == 1 and _count(dst, "task_events") == 1


def test_legacy_source_is_upgraded_on_a_copy(tmp_path):
    mod = _load()
    from sqlalchemy import create_engine

    from app.database import _alembic_cfg, _baseline_metadata

    src, dst = tmp_path / "legacy.db", tmp_path / "dst.db"
    eng = create_engine(f"sqlite:///{src}")
    _baseline_metadata(_alembic_cfg("sqlite+aiosqlite://")).create_all(eng)
    eng.dispose()
    before = src.read_bytes()
    assert mod.main(["--from", f"sqlite:///{src}", "--to", f"sqlite:///{dst}"]) == 0
    assert src.read_bytes() == before
    assert "users" in {
        r[0] for r in sqlite3.connect(dst).execute("SELECT name FROM sqlite_master")
    }
