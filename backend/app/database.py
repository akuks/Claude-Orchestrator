from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url, echo=False, future=True)
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


async def init_db() -> None:
    # Import models so they register with Base.metadata before create_all.
    from . import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)


async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
