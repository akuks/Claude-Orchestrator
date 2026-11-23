"""After a run (phase 4b): read the commits it made and flag decisions that may be out of date.

``read_commits`` is sync (call it through ``asyncio.to_thread``); it never raises."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from .. import gitsafe
from ..config import settings
from ..guard import policy_store
from ..models import Project, ProjectDecision
from .globmatch import matches
from .health import _unquote

MAX_COMMITS = 50
MAX_FILES_PER_COMMIT = 200
GIT_TIMEOUT = 10.0
STALE_AFTER = timedelta(days=14)
FLAG_AGAIN_AFTER = timedelta(days=1)
MAX_FLAGS_PER_COMMIT = 2
MAX_FLAGS_PER_RUN = 6
TITLE_CUT = 80
REASON_MAX = 300
_START_RE = re.compile(r"^[0-9a-f]{7,40}$")
_HEX40 = re.compile(r"^[0-9a-f]{7,40}$")


@dataclass
class Commit:
    sha: str
    short: str
    subject: str
    files: list[str]


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def read_commits(directory: str | None, start: str | None, *, timeout: float = GIT_TIMEOUT) -> list[Commit]:
    """The non-merge commits made since ``start`` (a hex sha), newest first; [] on anything unusual."""
    if not directory or not start or not _START_RE.match(start):
        return []
    try:
        head = gitsafe.run_git(directory, "rev-parse", "--verify", "HEAD", timeout=timeout)
        if not head.ok or not _HEX40.match(head.out) or head.out.startswith(start) or start.startswith(head.out):
            return []
        lg = gitsafe.run_git(directory, "log", f"{start}..{head.out}", "--no-merges", f"--max-count={MAX_COMMITS}",
                             "--name-only", "--format=%x1e%H%x09%h%x09%s", timeout=timeout)
    except ValueError:
        return []
    if not lg.ok:
        return []
    commits = []
    for chunk in lg.out.split("\x1e"):
        if not chunk.strip():
            continue
        first, _, rest = chunk.partition("\n")
        parts = first.split("\t", 2)
        if len(parts) < 3:
            continue
        files = [_unquote(f) for f in rest.split("\n") if f.strip()][:MAX_FILES_PER_COMMIT]
        commits.append(Commit(sha=parts[0].strip(), short=parts[1].strip(), subject=parts[2].strip(), files=files))
    return commits


async def enabled_project(s, project_id: str | None) -> Project | None:
    """The project when the automatic suggestion parts may run for it, else None."""
    if not project_id or not settings.proposals_enabled:
        return None
    project = await s.get(Project, project_id)
    if project is None or project.trust != "own":
        return None
    if (await policy_store.org_settings(s))["ground_rules"].get("decisions") is not True:
        return None
    return project


async def flag_stale(s, project_id: str, commits: list[Commit], task_title: str, now: datetime) -> list[int]:
    """Mark active decisions whose files the commits changed as maybe out of date; returns their numbers.
    The caller commits. No version bump and no ``updated_at`` change."""
    if not commits:
        return []
    rows = (await s.execute(
        select(ProjectDecision).where(ProjectDecision.project_id == project_id, ProjectDecision.status == "active")
        .order_by(ProjectDecision.created_at.desc(), ProjectDecision.number.desc()))).scalars().all()
    now = _utc(now)
    candidates = [d for d in rows if d.touches and _utc(d.last_verified_at) < now - STALE_AFTER
                  and (d.stale_flagged_at is None or _utc(d.stale_flagged_at) < now - FLAG_AGAIN_AFTER)]
    title = (task_title or "")[:TITLE_CUT]
    flagged: list[int] = []
    for c in commits:
        n = 0
        for d in candidates:
            if n >= MAX_FLAGS_PER_COMMIT or len(flagged) >= MAX_FLAGS_PER_RUN:
                break
            if d.number in flagged:
                continue
            path = next((f for f in c.files if any(matches(t, f) for t in d.touches)), None)
            if path is None:
                continue
            reason = f"{path} changed in {c.short} ({title})"[:REASON_MAX]
            res = await s.execute(
                update(ProjectDecision).where(ProjectDecision.id == d.id, ProjectDecision.status == "active")
                .values(stale_flagged_at=now, stale_reason=reason, updated_at=ProjectDecision.updated_at))
            if res.rowcount:
                flagged.append(d.number)
                n += 1
    return flagged
