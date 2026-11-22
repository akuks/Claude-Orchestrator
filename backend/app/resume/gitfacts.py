"""Read-only git facts for a resume snapshot. Never writes, never raises:
a git failure or timeout gives "unknown" (None / error), not an exception.
Every call goes through ``gitsafe.run_git`` and shares one time ``Budget``."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from .. import gitsafe
from ..redaction import clean_text

GIT_BUDGET = 5.0
DIRTY_CAP = 2000
SUBJECT_MAX = 200

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_OWN_RE = re.compile(r"^\[(?:[^\]\s]+|detached HEAD)(?: \(root-commit\))? ([0-9a-f]{7,40})\]", re.M)


class Budget:
    def __init__(self, seconds: float = GIT_BUDGET):
        self._end = time.monotonic() + seconds

    def left(self) -> float:
        return max(0.0, self._end - time.monotonic())


@dataclass
class Facts:
    is_repo: bool
    branch: str | None
    head: str | None
    no_commits: bool
    dirty: int | None
    dirty_capped: bool
    error: str | None


@dataclass
class Commit:
    sha: str
    subject: str


def _git(directory, budget: Budget, *args: str) -> gitsafe.GitResult:
    left = budget.left()
    if left <= 0:
        return gitsafe.GitResult(False, "", True, "git time budget spent")
    return gitsafe.run_git(directory, *args, timeout=left)


def facts(directory, budget: Budget) -> Facts:
    r = _git(directory, budget, "rev-parse", "--git-dir")
    if r.failed:
        return Facts(False, None, None, False, None, False, r.err or "git failed")
    if not r.ok:
        return Facts(False, None, None, False, None, False, None)
    err = None
    b = _git(directory, budget, "symbolic-ref", "-q", "--short", "HEAD")
    branch = b.out or None if b.ok else None
    refs = _git(directory, budget, "for-each-ref", "--count=1", "refs/")
    if refs.failed or not refs.ok:
        err = refs.err or "git failed"
    no_commits = refs.ok and not refs.out
    head = None
    if not no_commits:
        h = _git(directory, budget, "rev-parse", "--verify", "-q", "HEAD")
        head = h.out[:12] if h.ok and h.out else None
        if h.failed:
            err = err or h.err or "git failed"
    dirty, capped = None, False
    st = _git(directory, budget, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
    if st.ok:
        n = sum(1 for e in st.out.split("\0") if e.strip())
        dirty, capped = min(n, DIRTY_CAP), n > DIRTY_CAP
    else:
        err = err or st.err or "git status failed"
    return Facts(True, branch, head, bool(no_commits), dirty, capped, err)


def commits_between(directory, base: str, head: str, budget: Budget, limit: int = 50) -> list[Commit] | None:
    try:
        r = _git(directory, budget, "log", "--format=%H%x1f%s", f"--max-count={int(limit)}", f"{base}..{head}")
    except ValueError:
        return None
    if not r.ok:
        return None
    out = []
    for line in r.out.split("\n"):
        sha, _, subject = line.partition("\x1f")
        if _SHA40.match(sha):
            out.append(Commit(sha, clean_text(subject, SUBJECT_MAX)))
    return out


def is_ancestor(directory, a: str, b: str, budget: Budget) -> bool | None:
    try:
        r = _git(directory, budget, "merge-base", "--is-ancestor", a, b)
    except ValueError:
        return None
    if r.failed:
        return None
    if r.code == 0:
        return True
    return False if r.code == 1 else None


def own_shas(texts: list[str]) -> set[str]:
    return {m for t in texts for m in _OWN_RE.findall(t or "")}


def outside_commits(directory, *, since: str | None, head: str | None, own: set[str], budget: Budget) -> dict:
    none = {"commits": [], "rewritten": False}
    if not since or not head or since == head:
        return none
    anc = is_ancestor(directory, since, head, budget)
    if anc is False:
        return {"commits": [], "rewritten": True}
    if anc is None:
        return none
    cs = commits_between(directory, since, head, budget)
    if not cs:
        return none
    return {"commits": [c for c in cs if not any(c.sha.startswith(o) for o in own)], "rewritten": False}
