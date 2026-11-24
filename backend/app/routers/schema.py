"""GET /projects/{id}/schema: tables read from the project's SQL migration files (phase 6c, forge F58).

Read only: nothing is run, no database is contacted, nothing is stored (results are cached in memory by the
files' path/size/mtime signature). Any project member; a task may read its own project with its CO_API_TOKEN.
"""

import asyncio
import os
import time
from collections import OrderedDict

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

router = APIRouter(prefix="/projects/{project_id}/schema", tags=["schema"])

BUDGET_SECONDS = 10.0
MAX_TABLES, MAX_COLUMNS, MAX_LINKS, MAX_DECISIONS = 60, 200, 50, 3
NOTE_NONE = ("No SQL migration files found. Looked in migrations/, db/migrations/, "
             "supabase/migrations/ and prisma/migrations/.")
NOTE_SLOW = "Reading the migration files took too long, so nothing is shown. Try again in a moment."
NOTE_OK = "Worked out by reading the SQL migration files in order. Nothing was run and no database was contacted."
_CACHE_MAX = 32
_cache: "OrderedDict[tuple, list]" = OrderedDict()


def _empty(note: str, limits: list[str] | None = None, found: bool = False) -> dict:
    return {"found": found, "roots": [], "as_of": None, "not_understood": {"count": 0, "samples": []},
            "limits": limits or [], "note": note}


def _work(directory: str):
    """Off the event loop: find, read and replay. Returns (collected, [(root, files, schema)]) or raises TimeoutError."""
    deadline = time.monotonic() + BUDGET_SECONDS
    collected = schema_files.collect(directory)
    key = (os.path.realpath(directory), collected.signature)
    hit = _cache.get(key)
    if hit is not None:
        _cache.move_to_end(key)
        return collected, hit
    out = []
    for rf in collected.roots:
        out.append((rf.root, len(rf.files), schema_replay.replay(rf.files, deadline=deadline)))
    _cache[key] = out
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
    return collected, out


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
            limits.append(f"{t.name}: showing {MAX_COLUMNS} of {len(cols)} columns.")
            cols = cols[:MAX_COLUMNS]
        if len(links) > MAX_LINKS:
            limits.append(f"{t.name}: showing {MAX_LINKS} of {len(links)} links.")
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
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id)
        await _check_agent(s, request, project_id)
        project = await s.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Not found")
        directory = project.directory
        if not directory or not await asyncio.to_thread(os.path.isdir, directory):
            return _empty(NOTE_NONE)
        decisions = await decision_snapshot.snapshot(s, project)
    try:
        collected, results = await asyncio.wait_for(asyncio.to_thread(_work, directory), BUDGET_SECONDS)
    except (asyncio.TimeoutError, TimeoutError):
        return _empty(NOTE_SLOW, ["Took too long."], found=True)
    if not results:
        return _empty(NOTE_NONE, collected.limits)
    limits = list(collected.limits)
    roots, count, samples = [], 0, []
    for root, n_files, schema in results:
        roots.append(_shape(root, n_files, schema, decisions, table, limits))
        count += schema.not_understood_count
        samples += schema.not_understood_samples
    note = NOTE_OK
    if table and not any(r["tables"] for r in roots):
        note = f"No table named {table[:80]} was found in the migration files."
    sha = await asyncio.to_thread(current_sha_fast, directory)
    return {"found": True, "roots": roots, "as_of": sha[:7] if sha else None,
            "not_understood": {"count": count, "samples": samples[:5]}, "limits": limits, "note": note}
