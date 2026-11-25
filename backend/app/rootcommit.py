"""A project's repository first commit (phase 7d, F4): stored once, compared at every task start, so a folder that
was cloned again (a different repository now sits there) is noticed. Git only through gitsafe; never raises."""

import asyncio
import logging
import re

from . import gitsafe
from .database import SessionLocal
from .models import Project

log = logging.getLogger(__name__)

TIMEOUT = 5.0
NOTICE = ("This folder now holds a different repository than before (it may have been cloned again). "
          "Decisions and the code map may not apply.")
_HEX = re.compile(r"^[0-9a-f]{40}$")


def first_commit_sync(directory) -> str | None:
    """The repository's first commit (40 hex), or None: not a git repository, no git, no commits, or a shallow
    clone whose history has no root. With several roots the first line (stable for a given repository)."""
    try:
        r = gitsafe.run_git(directory, "rev-list", "--max-parents=0", "HEAD", timeout=TIMEOUT)
    except Exception:  # noqa: BLE001
        return None
    if not r.ok:
        return None
    lines = sorted(x.strip().lower() for x in r.out.splitlines() if x.strip())
    first = lines[0] if lines else ""
    return first if _HEX.match(first) else None


async def first_commit(directory) -> str | None:
    if not directory:
        return None
    return await asyncio.to_thread(first_commit_sync, directory)


async def store(project_id: str, directory) -> None:
    """Set the project's stored first commit from its folder (None clears it). Never raises."""
    try:
        sha = await first_commit(directory)
        async with SessionLocal() as s:
            p = await s.get(Project, project_id)
            if p is not None and p.root_commit != sha:
                p.root_commit = sha
                await s.commit()
    except Exception:  # noqa: BLE001
        log.debug("root commit: could not store for %s", project_id, exc_info=True)


async def at_task_start(project_id: str | None, directory) -> str | None:
    """At a task start: fills an empty stored value; returns the notice text when the folder's first commit differs
    from the stored one (the stored value is left for a maintainer to accept). Never raises, never slow."""
    if not project_id:
        return None
    try:
        async with SessionLocal() as s:
            p = await s.get(Project, project_id)
            stored = p.root_commit if p is not None else None
            if p is None:
                return None
        now = await first_commit(directory)
        if now is None:
            return None
        if not stored:
            async with SessionLocal() as s:
                p = await s.get(Project, project_id)
                if p is not None and not p.root_commit:
                    p.root_commit = now
                    await s.commit()
            return None
        return NOTICE if now != stored else None
    except Exception:  # noqa: BLE001
        log.debug("root commit: check failed for %s", project_id, exc_info=True)
        return None
