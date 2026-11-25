"""What was built (phase 7g): one bounded ``git log --numstat`` through the safe git helper, turned into a
per-project history. Commit titles and names are plain text (control characters stripped, lengths capped)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .. import gitsafe
from ..redaction import clean_text
from . import gitfacts

MAX_COMMITS = 2000
TITLE_MAX = 200
NAME_MAX = 60
TASK_LOOKBACK_DAYS = 14
TASK_CAP = 500
EVENT_CAP = 20000

_HEAD_RE = re.compile(r"^([0-9a-f]{40})\x1f")
_NUM_RE = re.compile(r"^(\d+|-)\t(\d+|-)\t")
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")


def _plain(text, limit: int) -> str:
    return _CTRL.sub("", clean_text(text))[:limit].strip()


def _author(name: str, email: str) -> str:
    local = email.split("@", 1)[0] if email else ""
    return _plain(local or name, NAME_MAX) or "unknown"


def parse_log(text: str) -> list[dict]:
    """Records from the fixed format. A header line starts with a 40-hex sha and has five \\x1f fields (a subject
    holds no newline, so it cannot forge a record); count lines follow it."""
    out: list[dict] = []
    cur = None
    for line in text.split("\n"):
        if _HEAD_RE.match(line):
            f = line.split("\x1f")
            cur = None
            if len(f) != 5:
                continue
            try:
                when = datetime.fromisoformat(f[1])
            except ValueError:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            cur = {"sha": f[0], "short": f[0][:7], "date": f[1][:25], "_when": when,
                   "author": _author(f[2], f[3]), "title": _plain(f[4], TITLE_MAX),
                   "added": 0, "removed": 0, "files": 0}
            out.append(cur)
        elif cur is not None and _NUM_RE.match(line):
            a, r, _ = line.split("\t", 2)
            cur["added"] += int(a) if a.isdigit() else 0
            cur["removed"] += int(r) if r.isdigit() else 0
            cur["files"] += 1
    return out


def history(directory, days: int, budget: gitfacts.Budget, now: datetime | None = None) -> dict:
    """``{is_repo, commits, truncated}`` (commits newest first, no task, authored within ``days``)."""
    now = now or datetime.now(timezone.utc)
    r = gitfacts._git(directory, budget, "rev-parse", "--git-dir")
    if r.failed and r.missing:
        return {"is_repo": False, "commits": [], "truncated": False}
    if not r.ok and not r.failed:
        return {"is_repo": False, "commits": [], "truncated": False}
    if r.failed:
        return {"is_repo": True, "commits": [], "truncated": True}
    cutoff = now - timedelta(days=days)
    since = (cutoff - timedelta(days=1)).strftime("%Y-%m-%d")  # git filters on commit date; we re-check authored
    lg = gitfacts._git(directory, budget, "log", "--no-merges", "--numstat", gitsafe.ACTIVITY_FORMAT,
                       f"--since={since}", "-n", str(MAX_COMMITS + 1))
    if lg.failed:
        return {"is_repo": True, "commits": [], "truncated": True}
    if not lg.ok:  # e.g. no commits yet
        return {"is_repo": True, "commits": [], "truncated": False}
    recs = parse_log(lg.out)
    truncated = len(recs) > MAX_COMMITS
    seen, commits = set(), []
    for c in recs[:MAX_COMMITS]:
        if c["sha"] in seen or c["_when"] < cutoff:
            continue
        seen.add(c["sha"])
        commits.append(c)
    return {"is_repo": True, "commits": commits, "truncated": truncated}


def summarize(commits: list[dict], days: int, now: datetime | None = None) -> tuple[list[dict], list[dict]]:
    now = now or datetime.now(timezone.utc)
    by_day: dict[str, dict] = {}
    for i in range(days + 1):
        d = (now - timedelta(days=days - i)).strftime("%Y-%m-%d")
        by_day[d] = {"date": d, "commits": 0, "added": 0, "removed": 0}
    people: dict[str, dict] = {}
    for c in commits:
        d = by_day.setdefault(c["date"][:10], {"date": c["date"][:10], "commits": 0, "added": 0, "removed": 0})
        p = people.setdefault(c["author"], {"name": c["author"], "commits": 0, "added": 0, "removed": 0})
        for t in (d, p):
            t["commits"] += 1
            t["added"] += c["added"]
            t["removed"] += c["removed"]
    return ([by_day[k] for k in sorted(by_day)],
            sorted(people.values(), key=lambda p: (-p["commits"], p["name"])))


async def task_map(project_id: str, since: datetime, shas: list[str]) -> dict[str, dict]:
    """sha -> {id, title} for tasks of this project whose own commit output ("[branch abc1234] ...") names it.
    Only what runs recorded is used; a commit made outside a run stays unmapped. Bounded."""
    from sqlalchemy import select

    from ..database import SessionLocal
    from ..models import Task, TaskEvent

    if not shas:
        return {}
    found: dict[str, dict] = {}
    lo = since - timedelta(days=TASK_LOOKBACK_DAYS)
    async with SessionLocal() as s:
        tasks = (await s.execute(
            select(Task.id, Task.title).where(Task.project_id == project_id, Task.created_at >= lo)
            .order_by(Task.created_at.desc()).limit(TASK_CAP))).all()
        info = {t.id: t.title for t in tasks}
        ids = list(info)
        scanned = 0
        for i in range(0, len(ids), 200):
            rows = (await s.execute(
                select(TaskEvent.task_id, TaskEvent.payload)
                .where(TaskEvent.task_id.in_(ids[i:i + 200]), TaskEvent.type == "tool_result")
                .limit(EVENT_CAP - scanned))).all()
            scanned += len(rows)
            for tid, payload in rows:
                content = (payload or {}).get("content") if isinstance(payload, dict) else None
                if not isinstance(content, str):
                    continue
                for short in gitfacts.own_shas([content]):
                    for sha in shas:
                        if sha.startswith(short) and sha not in found:
                            found[sha] = {"id": tid, "title": _plain(info[tid], 200)}
            if scanned >= EVENT_CAP:
                break
    return found
