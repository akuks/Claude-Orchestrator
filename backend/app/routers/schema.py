"""GET /projects/{id}/schema: tables read from the project's SQL migration files (phase 6c, forge F58).

Read only: nothing is run, no database is contacted, nothing is stored (results are cached in memory by the
files' path/size/mtime signature). Any project member; a task may read its own project with its CO_API_TOKEN.
"""

import asyncio
import logging
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException, Request

from ..access import effective_project_id, require_project_role
from ..database import SessionLocal
from ..decisions import snapshot as decision_snapshot
from ..decisions.globmatch import matches
from ..decisions.validate import display_id
from ..models import Project, Task
from ..schema import files as schema_files
from ..schema import replay as schema_replay
from ..skillpacks.update import current_sha_fast

log = logging.getLogger(__name__)
router = APIRouter(prefix="/projects/{project_id}/schema", tags=["schema"])

BUDGET_SECONDS = 10.0
MAX_TABLES, MAX_COLUMNS, MAX_LINKS, MAX_DECISIONS = 60, 200, 50, 3
NOTE_NONE = ("No SQL migration files found. Looked in migrations/, db/migrations/, "
             "supabase/migrations/ and prisma/migrations/.")
NOTE_SLOW = "Reading the migration files took too long."
NOTE_FAILED = "The migration files could not be read."
NOTE_OK = "Worked out by reading the SQL migration files in order. Nothing was run and no database was contacted."
WORKERS = 2
_CACHE_MAX = 32
_CACHE_BYTES = 16 * 1024 * 1024
_TIMEOUT_MEMORY = 60.0

_pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="schema")
_lock = threading.Lock()  # guards everything below
_slots = threading.Semaphore(WORKERS)
_inflight: dict = {}  # real folder path -> the running job's future (shared by overlapping requests)
_cache: "OrderedDict[tuple, tuple[list, int]]" = OrderedDict()
_cache_bytes = 0
_timed_out: dict[tuple, float] = {}  # (folder, signature) -> remembered until (monotonic)


def _empty(note: str, limits: list[str] | None = None, found: bool = False) -> dict:
    return {"found": found, "roots": [], "as_of": None, "not_understood": {"count": 0, "samples": []},
            "limits": limits or [], "note": note}


def _slow() -> dict:
    return _empty(NOTE_SLOW, [NOTE_SLOW], found=True)


def _cache_get(key):
    with _lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit[0]
    return None


def _cache_put(key, value, size: int):
    global _cache_bytes
    with _lock:
        old = _cache.pop(key, None)
        if old is not None:
            _cache_bytes -= old[1]
        _cache[key] = (value, size)
        _cache_bytes += size
        while _cache and (len(_cache) > _CACHE_MAX or _cache_bytes > _CACHE_BYTES):
            _, (_, sz) = _cache.popitem(last=False)
            _cache_bytes -= sz


def _work(directory: str, deadline: float):
    """In a worker: list, (maybe) read and replay. Returns (limits, [(root, files, schema)], sha) or None when
    the budget ran out. The folder check and the sha read are in here too."""
    if not os.path.isdir(directory):
        return ([], [], None)
    sc = None
    try:
        sc = schema_files.scan(directory, deadline)
        key = (sc.base, sc.signature)
        with _lock:
            until = _timed_out.get(key)
            if until is not None and until > time.monotonic():
                return None
            _timed_out.pop(key, None)
        hit = _cache_get(key)
        if hit is None:
            collected = schema_files.read(sc, deadline)
            if collected.changed:  # a file changed since the listing: list again once, then take it as it is
                sc.close()
                sc = schema_files.scan(directory, deadline)
                key = (sc.base, sc.signature)
                collected = schema_files.read(sc, deadline, accept_changed=True)
            results = [(rf.root, len(rf.files), schema_replay.replay(rf.files, deadline=deadline))
                       for rf in collected.roots]
            hit = (collected.limits, results)
            _cache_put(key, hit, sum(len(t) for rf in collected.roots for _, t in rf.files) + 1024)
    except TimeoutError:
        if sc is not None and sc.signature:  # ran out during the listing: nothing to remember it by
            with _lock:
                _timed_out[(sc.base, sc.signature)] = time.monotonic() + _TIMEOUT_MEMORY
                for k in [k for k, v in _timed_out.items() if v < time.monotonic()]:
                    _timed_out.pop(k, None)
        return None
    finally:
        if sc is not None:
            sc.close()
    sha = current_sha_fast(directory)
    return (hit[0], hit[1], sha[:7] if sha else None)


def _job(directory: str, key: str, deadline: float):
    try:
        return _work(directory, deadline)
    finally:
        with _lock:
            _inflight.pop(key, None)
        _slots.release()


async def _submit(directory: str, deadline: float):
    """The running job for this folder (shared), or a new one. Waits for a free worker within the budget;
    None when the budget runs out first."""
    key = os.path.realpath(directory)
    while True:
        with _lock:
            fut = _inflight.get(key)
            if fut is not None:
                return fut
            if _slots.acquire(blocking=False):
                try:
                    fut = _pool.submit(_job, key, key, deadline)
                except BaseException:
                    _slots.release()
                    raise
                _inflight[key] = fut
                return fut
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(0.02)


async def _check_agent(s, request: Request, project_id: str) -> None:
    p = request.state.principal
    if not p.is_agent:
        return
    task = await s.get(Task, p.token.task_id) if p.token.task_id else None
    if task is None or await effective_project_id(s, task) != project_id:
        raise HTTPException(403, "Token not allowed for this project")


def _shape(root: str, n_files: int, schema, decisions: list[dict], only: str | None, limits: list[str]) -> dict:
    tables = list(schema.tables.values())
    if only:
        tables = [t for t in tables if t.name.lower() == only.lower()]
    if len(tables) > MAX_TABLES:
        limits.append(f"{root}: showing {MAX_TABLES} of {len(tables)} tables.")
        tables = tables[:MAX_TABLES]
    out = []
    for t in tables:
        cols, links = t.columns, t.links
        if len(cols) > MAX_COLUMNS:
            limits.append(f"{root}: {t.name}: showing {MAX_COLUMNS} of {len(cols)} columns.")
            cols = cols[:MAX_COLUMNS]
        if len(links) > MAX_LINKS:
            limits.append(f"{root}: {t.name}: showing {MAX_LINKS} of {len(links)} links.")
            links = links[:MAX_LINKS]
        covering = [{"id": display_id(d["n"]), "title": d["title"]} for d in decisions
                    if any(matches(pat, t.file) for pat in d["touches"])][:MAX_DECISIONS]
        out.append({
            "name": t.name, "uncertain": t.uncertain, "defined_at": t.defined_at,
            "columns": [{"name": c.name, "type": c.type, "nullable": c.nullable, "primary_key": c.primary_key,
                         "defined_at": c.defined_at} for c in cols],
            "links": [{"columns": lk.columns, "to_table": lk.to_table, "to_columns": lk.to_columns,
                       "defined_at": lk.defined_at} for lk in links],
            "decisions": covering,
        })
    return {"root": root, "files": n_files, "tables": out}


@router.get("")
async def get_schema(project_id: str, request: Request, table: str | None = None):
    deadline = time.monotonic() + BUDGET_SECONDS
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id)
        await _check_agent(s, request, project_id)
        project = await s.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Not found")
        directory = project.directory
        if not directory:
            return _empty(NOTE_NONE)
        try:
            decisions = await asyncio.wait_for(decision_snapshot.snapshot(s, project), max(0.0, deadline - time.monotonic()))
        except (asyncio.TimeoutError, TimeoutError):
            return _slow()
    fut = await _submit(directory, deadline)
    if fut is None:
        return _slow()
    try:
        got = await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(fut)), max(0.0, deadline - time.monotonic()))
    except (asyncio.TimeoutError, TimeoutError):
        return _slow()
    except Exception:
        log.exception("schema: reading the migration files failed")
        return _empty(NOTE_FAILED, found=True)
    if got is None:
        return _slow()
    scan_limits, results, sha = got
    if not results:
        return _empty(NOTE_NONE, scan_limits)
    limits = list(scan_limits)
    roots, count, samples = [], 0, []
    for root, n_files, schema in results:
        roots.append(_shape(root, n_files, schema, decisions, table, limits))
        count += schema.not_understood_count
        samples += schema.not_understood_samples
    note = NOTE_OK
    if table and not any(r["tables"] for r in roots):
        note = f"No table named {table[:80]} was found in the migration files."
    return {"found": True, "roots": roots, "as_of": sha,
            "not_understood": {"count": count, "samples": samples[:5]}, "limits": limits, "note": note}
