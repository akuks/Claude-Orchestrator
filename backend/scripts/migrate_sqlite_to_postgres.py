#!/usr/bin/env python
"""Copy a Claude Orchestrator database into another database (typically a legacy or
current SQLite file into PostgreSQL).

    python scripts/migrate_sqlite_to_postgres.py \
        --from sqlite:///claude_orchestrator.db \
        --to   postgresql://user:pass@localhost/claude_orchestrator

* The SOURCE is never modified: a SQLite source is copied to a temp file, brought to
  the latest schema there (this also upgrades legacy pre-Alembic databases), and read
  from the copy.
* The TARGET is brought to head with Alembic, then every table is copied in FK-safe
  order, in batches. A target that already holds rows is refused unless --force
  (which empties it first).
* On PostgreSQL, serial/identity sequences are reset afterwards so new inserts don't
  collide with the copied integer primary keys (task_events, artifacts, ...).

Run from the backend/ directory. Stop the app before migrating.
"""

import argparse
import asyncio
import shutil
import sys
import tempfile
from datetime import timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import DateTime, Integer, func, insert, select, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

BATCH = 500
SKIP_TABLES = {"alembic_version"}


def async_url(url: str) -> str:
    """Normalise sync-style URLs to the async drivers the app uses."""
    u = make_url(url)
    drivers = {"sqlite": "sqlite+aiosqlite", "postgresql": "postgresql+asyncpg",
               "postgres": "postgresql+asyncpg"}
    if u.drivername in drivers:
        u = u.set(drivername=drivers[u.drivername])
    return u.render_as_string(hide_password=False)


def _sqlite_path(url: str) -> Path | None:
    u = make_url(url)
    if u.get_backend_name() != "sqlite" or not u.database or u.database == ":memory:":
        return None
    return Path(u.database).expanduser().resolve()


def _tables():
    from app import models  # noqa: F401  (registers all tables on Base.metadata)
    from app.database import Base

    return [t for t in Base.metadata.sorted_tables if t.name not in SKIP_TABLES]


def _fix_row(table, row: dict) -> dict:
    # SQLite hands back naive datetimes; tz-aware columns on Postgres want aware ones.
    for col in table.columns:
        v = row.get(col.name)
        if (
            v is not None
            and isinstance(col.type, DateTime)
            and col.type.timezone
            and getattr(v, "tzinfo", None) is None
        ):
            row[col.name] = v.replace(tzinfo=timezone.utc)
    return row


async def _count_rows(conn, tables) -> dict[str, int]:
    out = {}
    for t in tables:
        n = (await conn.execute(select(func.count()).select_from(t))).scalar_one()
        if n:
            out[t.name] = n
    return out


async def _reset_sequences(conn, tables) -> list[str]:
    done = []
    for t in tables:
        pk = list(t.primary_key.columns)
        if len(pk) != 1 or not isinstance(pk[0].type, Integer) or pk[0].autoincrement is False:
            continue
        col = pk[0].name
        await conn.execute(
            text(
                f"SELECT setval(pg_get_serial_sequence('{t.name}', '{col}'), "
                f"COALESCE((SELECT MAX({col}) FROM {t.name}), 1), "
                f"(SELECT MAX({col}) FROM {t.name}) IS NOT NULL) "
                f"WHERE pg_get_serial_sequence('{t.name}', '{col}') IS NOT NULL"
            )
        )
        done.append(f"{t.name}.{col}")
    return done


async def _copy(src_url: str, dst_url: str, force: bool, dst_display: str = "") -> int:
    tables = _tables()
    src = create_async_engine(src_url)
    dst = create_async_engine(dst_url)
    try:
        async with dst.begin() as dconn:
            existing = await _count_rows(dconn, tables)
            if existing and not force:
                print(
                    "ERROR: target database is not empty "
                    f"({', '.join(f'{k}={v}' for k, v in existing.items())}). "
                    "Refusing to overwrite; use --force to empty it first.",
                    file=sys.stderr,
                )
                return 2
            if existing:
                print(
                    f"--force: deleting {sum(existing.values())} existing rows from "
                    f"{dst_display or dst_url}",
                    file=sys.stderr,
                )
                for t in reversed(tables):
                    await dconn.execute(t.delete())
            copied = {}
            async with src.connect() as sconn:
                for t in tables:
                    n = 0
                    result = await sconn.stream(select(t))
                    async for rows in result.mappings().partitions(BATCH):
                        await dconn.execute(
                            insert(t), [_fix_row(t, dict(r)) for r in rows]
                        )
                        n += len(rows)
                    copied[t.name] = n
            seqs = []
            if dconn.dialect.name == "postgresql":
                seqs = await _reset_sequences(dconn, tables)
        for name, n in copied.items():
            print(f"  {name}: {n}")
        if seqs:
            print(f"Reset {len(seqs)} sequence(s).")
        print("Done.")
        return 0
    finally:
        await src.dispose()
        await dst.dispose()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from", dest="src", required=True, help="source DB URL")
    ap.add_argument("--to", dest="dst", required=True, help="target DB URL")
    ap.add_argument("--force", action="store_true", help="empty a non-empty target first")
    args = ap.parse_args(argv)

    from app.database import run_migrations

    src_url, dst_url = async_url(args.src), async_url(args.dst)
    src_file = _sqlite_path(src_url)
    if src_file is None and make_url(src_url).get_backend_name() == "sqlite":
        print("ERROR: unsupported source SQLite URL", file=sys.stderr)
        return 1
    if src_file is not None and not src_file.exists():
        print(f"ERROR: source database not found: {src_file}", file=sys.stderr)
        return 1
    dst_file = _sqlite_path(dst_url)
    if src_url == dst_url or (
        src_file is not None and dst_file is not None and src_file == dst_file
    ):
        print("ERROR: source and target are the same database; refusing.", file=sys.stderr)
        return 1

    redacted = make_url(dst_url).render_as_string(hide_password=True)

    with tempfile.TemporaryDirectory(prefix="co-migrate-") as tmp:
        if src_file is not None:
            work = Path(tmp) / "source.db"
            shutil.copyfile(src_file, work)
            for suffix in ("-wal", "-shm"):  # keep uncheckpointed writes
                side = Path(str(src_file) + suffix)
                if side.exists():
                    shutil.copyfile(side, str(work) + suffix)
            src_url = f"sqlite+aiosqlite:///{work}"
            print("Upgrading a temporary copy of the source to the latest schema…")
            run_migrations(src_url)
        print("Upgrading the target schema to head…")
        run_migrations(dst_url)
        print("Copying…")
        return asyncio.run(_copy(src_url, dst_url, args.force, redacted))


if __name__ == "__main__":
    sys.exit(main())
