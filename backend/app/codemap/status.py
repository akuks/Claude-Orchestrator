"""The code map status of a project (the GET shape) and its log tail."""

from __future__ import annotations

import asyncio
import os
import re

from sqlalchemy import select

from .. import gitsafe, redaction
from ..models import McpServer, Project, ProjectIndex
from ..skillpacks.update import current_sha_fast
from . import indexer

SERVER_MARK = "codebase-memory"
_HEX = re.compile(r"^[0-9a-f]{7,40}$")
LOG_LINES = 50
LOG_READ_BYTES = 64 * 1024


def is_code_map_server(server) -> bool:
    """The code map tool: the command (or its arguments, or the name) contains `codebase-memory`."""
    hay = " ".join([server.name or "", server.command or "", *[str(a) for a in (server.args or [])]])
    return SERVER_MARK in hay.lower()


async def server_registered(s, project) -> bool:
    rows = (await s.execute(select(McpServer).where(McpServer.enabled.is_(True)))).scalars().all()
    return any(is_code_map_server(m) and (m.scope != "project" or m.project == project.name) for m in rows)


def _behind(directory: str, sha: str) -> int | None:
    head = current_sha_fast(directory)
    if head is None or not _HEX.match(sha) or not _HEX.match(head):
        return None
    if head == sha:
        return 0
    r = gitsafe.run_git(directory, "rev-list", "--count", f"{sha[:40]}..{head}", timeout=5)
    return int(r.out) if r.ok and r.out.isdigit() else None


async def for_project(s, project: Project) -> dict:
    row = await s.get(ProjectIndex, project.id)
    status = "running" if indexer.inflight(project.id) else (row.status if row is not None else "never")
    sha = row.sha if row is not None else None
    behind = None
    if sha and project.directory:
        behind = await asyncio.to_thread(_behind, project.directory, sha)
    return {
        "enabled": bool(project.code_map),
        "status": status,
        "sha": sha,
        "indexed_at": row.indexed_at if row is not None else None,
        "behind": behind,
        "error": row.error if row is not None and status == "failed" else None,
        "next_try_at": row.next_try_at if row is not None else None,
        "server_registered": await server_registered(s, project),
    }


def _read_tail(path) -> list[str]:
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return []
    with os.fdopen(fd, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        f.seek(max(0, size - LOG_READ_BYTES))
        data = f.read(LOG_READ_BYTES).decode("utf-8", "replace")
    return data.splitlines()[-LOG_LINES:]


async def log_lines(project: Project) -> list[str]:
    lines = await asyncio.to_thread(_read_tail, indexer.log_path(project.id))
    values = redaction.env_values([project.env])
    return [redaction.mask_values(redaction.redact(ln, []), values)[:2000] for ln in lines]
