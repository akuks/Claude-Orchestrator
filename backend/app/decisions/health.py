"""Decision health (F52): which active decisions may be out of date. Read-only; two git calls per check."""

import asyncio
import os
import re
from bisect import bisect_left
from datetime import datetime, timezone

from .. import gitsafe
from ..redaction import clean_text, redact
from .globmatch import CASE_INSENSITIVE, matches
from .validate import display_id

UNVERIFIED_DAYS = 30
MAX_SUBJECTS = 3
SUBJECT_MAX = 120
MAX_FILES = 200_000
TOO_LARGE = "repository too large for the health check"
GIT_TIMEOUT = 10.0
_QUOTED = re.compile(r'^"(.*)"$')
_NO_COMMITS = "does not have any commits"  # git log's message (LC_ALL=C) in a repository with no commits yet


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


def _subject(text: str) -> str:
    return redact(clean_text(text), [])[:SUBJECT_MAX]


def _see_missing(root: str, see: str) -> bool:
    real_root = os.path.realpath(root)
    target = os.path.realpath(os.path.join(real_root, see))
    if target != real_root and not target.startswith(real_root + os.sep):
        return True
    return not os.path.exists(target)


def _norm(p: str) -> str:
    return p.lower() if CASE_INSENSITIVE else p


def _prefix(glob: str) -> str:
    """Literal leading segments of a glob (every match starts with this string)."""
    lit = []
    for seg in glob.split("/"):
        if not seg:
            continue
        if "*" in seg or "?" in seg:
            break
        lit.append(seg)
    return _norm("/".join(lit))


class _Index:
    """Sorted normalised file list: a glob only tests files under its literal prefix."""

    def __init__(self, files):
        self.keys = sorted((_norm(f), f) for f in files)
        self.names = [k for k, _ in self.keys]

    def candidates(self, prefix):
        i = bisect_left(self.names, prefix)
        while i < len(self.names) and self.names[i].startswith(prefix):
            yield self.keys[i][1]
            i += 1

    def any_match(self, glob):
        return any(matches(glob, f) for f in self.candidates(_prefix(glob)))


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
    files = None
    commits: list = []
    no_commits = False
    ls = gitsafe.run_git(root, "ls-files", "-co", "--exclude-standard", "-z", timeout=GIT_TIMEOUT)
    if not ls.ok:
        out["git_error"] = "git unavailable" if ls.missing else (ls.err or "git failed")[:200]
    else:
        names = [f for f in ls.out.split("\0") if f]
        if len(names) > MAX_FILES:
            out["git_error"] = TOO_LARGE
        else:
            files = _Index(names)
    if files is not None:
        verified = [_utc(d.last_verified_at) for d in decisions if d.touches]
        if verified:
            since = min(verified).isoformat()
            lg = gitsafe.run_git(root, "log", "--name-only", "--format=%x1e%H%x1f%cI%x1f%s", f"--since={since}",
                                 timeout=GIT_TIMEOUT)
            if not lg.ok and not lg.missing and _NO_COMMITS in (lg.err or ""):
                no_commits = True  # a fresh repository: the ls-files checks still run, there is no history
            elif not lg.ok:
                out["git_error"] = "git unavailable" if lg.missing else (lg.err or "git failed")[:200]
                files = None
            else:
                commits = _parse_log(lg.out)
    git_ok = files is not None
    glob_found: dict[str, bool] = {}  # distinct glob -> matches some file (stops at the first match)
    glob_commits: dict[str, list[int]] = {}  # distinct glob -> indexes of commits touching a matching file

    def found(g):
        if g not in glob_found:
            glob_found[g] = files.any_match(g)
        return glob_found[g]

    cindex = sorted((_norm(f), f, n) for n, (_, _, cfiles) in enumerate(commits) for f in set(cfiles))
    cnames = [c[0] for c in cindex]

    def touching(g):
        if g not in glob_commits:
            pre = _prefix(g)
            hit = set()
            i = bisect_left(cnames, pre)
            while i < len(cnames) and cnames[i].startswith(pre):
                _, f, n = cindex[i]
                if n not in hit and matches(g, f):
                    hit.add(n)
                i += 1
            glob_commits[g] = sorted(hit)
        return glob_commits[g]

    now = datetime.now(timezone.utc)
    for d in decisions:
        touches = list(d.touches or [])
        unmatched = [g for g in touches if not found(g)] if git_ok else []
        see_missing = bool(d.kind == "reference" and d.see and _see_missing(root, d.see))
        changed = None
        unverified = None
        if touches and git_ok and not no_commits:
            lv = _utc(d.last_verified_at)
            idx = sorted({n for g in set(touches) for n in touching(g) if commits[n][0] > lv})
            subjects = [_subject(commits[n][1]) for n in idx[:MAX_SUBJECTS]]
            if idx:
                changed = {"count": len(idx), "subjects": subjects}
        elif not touches:
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
