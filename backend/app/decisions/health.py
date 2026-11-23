"""Decision health (F52): which active decisions may be out of date. Read-only; two git calls per check."""

import asyncio
import os
import re
from datetime import datetime, timezone

from .. import gitsafe
from .globmatch import matches
from .validate import display_id

UNVERIFIED_DAYS = 30
MAX_SUBJECTS = 3
GIT_TIMEOUT = 10.0
_QUOTED = re.compile(r'^"(.*)"$')


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _unquote(name: str) -> str:
    m = _QUOTED.match(name)
    if not m:
        return name
    try:
        return m.group(1).encode("latin-1", "backslashreplace").decode("unicode_escape").encode(
            "latin-1", "replace").decode("utf-8", "replace")
    except Exception:
        return m.group(1)


def _parse_log(out: str) -> list[tuple[datetime, str, list[str]]]:
    commits = []
    for chunk in out.split("\x1e"):
        if not chunk.strip():
            continue
        head, _, rest = chunk.partition("\n")
        parts = head.split("\x1f")
        if len(parts) < 3:
            continue
        try:
            when = _utc(datetime.fromisoformat(parts[1]))
        except ValueError:
            continue
        files = [_unquote(f) for f in rest.split("\n") if f.strip()]
        commits.append((when, parts[2], files))
    return commits


def _see_missing(root: str, see: str) -> bool:
    real_root = os.path.realpath(root)
    target = os.path.realpath(os.path.join(real_root, see))
    if target != real_root and not target.startswith(real_root + os.sep):
        return True
    return not os.path.exists(target)


def _compute(project, decisions) -> dict:
    root = (project.directory or "").strip() if project is not None else ""
    if not root:
        return {"available": False}
    out = {"available": True, "git_error": None, "ok": 0, "items": []}
    if not os.path.isdir(root):
        out["git_error"] = "project directory not found"
        return out
    if not decisions:
        return out
    ls = gitsafe.run_git(root, "ls-files", "-co", "--exclude-standard", "-z", timeout=GIT_TIMEOUT)
    if not ls.ok:
        out["git_error"] = "git unavailable" if ls.missing else (ls.err or "git failed")[:200]
        return out
    files = [f for f in ls.out.split("\0") if f]
    verified = [_utc(d.last_verified_at) for d in decisions if d.touches]
    commits: list = []
    if verified:
        since = min(verified).isoformat()
        lg = gitsafe.run_git(root, "log", "--name-only", "--format=%x1e%H%x1f%cI%x1f%s", f"--since={since}",
                             timeout=GIT_TIMEOUT)
        if not lg.ok:
            out["git_error"] = "git unavailable" if lg.missing else (lg.err or "git failed")[:200]
            return out
        commits = _parse_log(lg.out)
    now = datetime.now(timezone.utc)
    for d in decisions:
        touches = list(d.touches or [])
        unmatched = [g for g in touches if not any(matches(g, f) for f in files)]
        see_missing = bool(d.kind == "reference" and d.see and _see_missing(root, d.see))
        changed = None
        unverified = None
        if touches:
            lv = _utc(d.last_verified_at)
            subjects = []
            for when, subject, cfiles in commits:
                if when > lv and any(matches(g, f) for g in touches for f in cfiles):
                    subjects.append(subject)
            if subjects:
                changed = {"count": len(subjects), "subjects": subjects[:MAX_SUBJECTS]}
        else:
            days = (now - _utc(d.last_verified_at)).days
            if days >= UNVERIFIED_DAYS:
                unverified = days
        if unmatched or see_missing or changed or unverified is not None:
            out["items"].append({
                "number": d.number, "display_id": display_id(d.number), "touches_unmatched": unmatched,
                "see_missing": see_missing, "changed_since_verified": changed, "unverified_days": unverified})
        else:
            out["ok"] += 1
    return out


async def health(project, decisions: list) -> dict:
    return await asyncio.to_thread(_compute, project, decisions)
