import asyncio

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    **({"poolclass": NullPool} if settings.db_null_pool else {}),
)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _add_missing_columns(sync_conn) -> None:
    """Minimal additive migration for SQLite: create_all won't ALTER existing
    tables, so add any columns introduced after a DB was first created."""
    wanted = {
        "tasks": [
            ("session_id", "VARCHAR(64)"),
            ("resume_session_id", "VARCHAR(64)"),
            ("root_id", "VARCHAR(32)"),
            ("project_id", "VARCHAR(32)"),
            ("model_used", "VARCHAR(64)"),
            ("schedule_id", "VARCHAR(32)"),
            ("requires_approval", "BOOLEAN DEFAULT 0"),
            ("risk", "VARCHAR(16) DEFAULT 'info'"),
            ("decided_at", "DATETIME"),
            ("decision_reason", "TEXT"),
            ("input_tokens", "INTEGER"),
            ("output_tokens", "INTEGER"),
            ("max_budget_usd", "FLOAT"),
            ("attempt", "INTEGER DEFAULT 0"),
            ("agent_id", "VARCHAR(32)"),
            ("system_prompt", "TEXT"),
            ("allow_remote", "BOOLEAN DEFAULT 1"),
            ("read_only", "BOOLEAN DEFAULT 0"),
            ("workflow_run_id", "VARCHAR(32)"),
            ("stage_index", "INTEGER"),
        ],
        "schedules": [
            ("requires_approval", "BOOLEAN DEFAULT 0"),
            ("agent_id", "VARCHAR(32)"),
        ],
        "agents": [
            ("requires_approval", "BOOLEAN DEFAULT 0"),
            ("allow_remote", "BOOLEAN DEFAULT 0"),
            ("read_only", "BOOLEAN DEFAULT 0"),
            ("folder", "VARCHAR(200) DEFAULT ''"),
            ("coordinator", "BOOLEAN DEFAULT 0"),
        ],
        "projects": [
            ("budget_usd", "FLOAT"),
            ("github_repo", "VARCHAR(200)"),
            ("auto_review_prs", "BOOLEAN DEFAULT 0"),
            ("env", "JSON"),
        ],
    }
    for table, cols in wanted.items():
        try:
            existing = {
                r[1]
                for r in sync_conn.exec_driver_sql(
                    f"PRAGMA table_info({table})"
                ).fetchall()
            }
        except Exception:
            continue
        if not existing:  # table not created yet; create_all handles it
            continue
        for name, ddl in cols:
            if name not in existing:
                sync_conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"
                )
    # Backfill thread roots: first, every task with no root becomes its own root.
    try:
        sync_conn.exec_driver_sql(
            "UPDATE tasks SET root_id = id WHERE root_id IS NULL"
        )
    except Exception:
        return
    # Then thread follow-ups under their parent's root. A follow-up is identified
    # by resume_session_id (duplicates set parent_task_id but not that), so
    # duplicates correctly stay their own root. Repeat to resolve multi-step
    # chains; idempotent — correctly-threaded rows already match and are skipped.
    propagate = """
        UPDATE tasks
        SET root_id = (SELECT p.root_id FROM tasks p WHERE p.id = tasks.parent_task_id)
        WHERE resume_session_id IS NOT NULL
          AND parent_task_id IS NOT NULL
          AND root_id <> (SELECT p.root_id FROM tasks p WHERE p.id = tasks.parent_task_id)
    """
    try:
        for _ in range(50):
            if not sync_conn.exec_driver_sql(propagate).rowcount:
                break
    except Exception:
        pass


def _alembic_cfg(url: str):
    from pathlib import Path

    from alembic.config import Config

    backend_dir = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "migrations"))
    cfg.attributes["url"] = url
    return cfg


def _baseline_metadata(cfg):
    """Frozen 0001 schema: run Alembic to the baseline on a scratch SQLite file and
    reflect it. Immune to later model changes (new columns/tables)."""
    import tempfile
    from pathlib import Path

    from alembic import command
    from sqlalchemy import MetaData, create_engine

    with tempfile.TemporaryDirectory(prefix="co-baseline-") as d:
        scratch = Path(d) / "baseline.db"
        cfg.attributes["url"] = f"sqlite+aiosqlite:///{scratch}"
        command.upgrade(cfg, "0001")
        eng = create_engine(f"sqlite:///{scratch}")
        try:
            md = MetaData()
            md.reflect(eng)
        finally:
            eng.dispose()
    md.remove(md.tables["alembic_version"])
    return md


def run_migrations(url: str) -> None:
    """Bring the database at `url` to the latest schema (blocking).

    A legacy SQLite DB created by the old create_all path has tables but no
    alembic_version: patch its missing columns, create any missing baseline
    tables from the frozen 0001 schema, then stamp it at the baseline so
    `upgrade head` applies only later migrations."""
    from alembic import command
    from sqlalchemy import inspect

    cfg = _alembic_cfg(url)

    async def _is_legacy() -> bool:
        eng = create_async_engine(url)
        try:
            async with eng.begin() as conn:

                def _check(sync_conn) -> bool:
                    tables = set(inspect(sync_conn).get_table_names())
                    if "alembic_version" in tables or "tasks" not in tables:
                        return False
                    if sync_conn.dialect.name == "sqlite":
                        _add_missing_columns(sync_conn)
                    return True

                return await conn.run_sync(_check)
        finally:
            await eng.dispose()

    async def _create_missing(baseline) -> None:
        eng = create_async_engine(url)
        try:
            async with eng.begin() as conn:
                # checkfirst (default) skips tables the legacy DB already has.
                await conn.run_sync(lambda c: baseline.create_all(c))
        finally:
            await eng.dispose()

    legacy = asyncio.run(_is_legacy())
    if legacy:
        baseline = _baseline_metadata(cfg)
        cfg.attributes["url"] = url
        asyncio.run(_create_missing(baseline))
        command.stamp(cfg, "0001")
    command.upgrade(cfg, "head")


async def init_db() -> None:
    await asyncio.to_thread(run_migrations, settings.database_url)


async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
