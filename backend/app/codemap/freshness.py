"""How fresh a code map is (phase 7d, spec section 3): the indexed commit against the folder's HEAD, worked out at
task start with the safe git helper (``gitsafe``) and put into the task's policy for co-guard to show.

``freshness(directory, sha)`` returns ``{state, behind, changed, more, head}``:

* ``current`` - HEAD is the indexed commit (``behind`` 0);
* ``ahead`` - the indexed commit is an ancestor of HEAD, ``behind`` commits back;
* ``different`` - the indexed commit is not an ancestor of HEAD, or this repository does not have it (a rewritten
  history); ``behind`` None;
* ``unknown`` - could not check: no git, not a repository, no usable sha, a shallow clone that cuts the history
  off, a call that failed or the time budget ran out.

``changed`` is up to CHANGED_MAX files changed since the index - the committed ones (``diff --name-only -z <sha>
HEAD``) then the uncommitted and untracked ones (``status``), each once - and ``more`` how many others there are;
both only for ``current`` / ``ahead`` and only with ``files``. ``head`` is the HEAD it compared with (None when
unknown). Every git call has at most CALL_TIMEOUT seconds (``run_git`` runs two processes, so each gets half)
and all of them together at most ``budget``. Never raises.
"""

from __future__ import annotations

import os
import re
import time

from .. import gitsafe

BUDGET = 2.0  # seconds for all the calls of one check
CALL_TIMEOUT = 1.0
CHANGED_MAX = 10
NAME_MAX = 200  # characters of one file name kept
_HEX = re.compile(r"[0-9a-f]{7,40}")
_MIN_LEFT = 0.05  # less than this left: no new call


def _unknown() -> dict:
    return {"state": "unknown", "behind": None, "changed": [], "more": 0, "head": None}


class _Out(Exception):
    """The time budget ran out or a call could not run."""


def _shallow(directory: str, git) -> bool:
    r = git("rev-parse", "--git-dir")
    if not r.ok or not r.out:
        return True  # cannot tell: treat as cut off (could not check)
    gdir = r.out if os.path.isabs(r.out) else os.path.join(directory, r.out)
    return os.path.exists(os.path.join(gdir, "shallow"))


def _names(directory: str, sha: str, ahead: bool, git) -> tuple[list[str], int]:
    """(first CHANGED_MAX names, how many more): the committed changes since ``sha``, then the uncommitted and
    untracked files. A call that fails or runs out of time leaves its part out."""
    seen: dict[str, None] = {}
    if ahead:
        try:
            r = git("diff", "--name-only", "-z", sha, "HEAD", strip=False)
        except _Out:
            r = None
        if r is not None and r.ok:
            for name in r.out.split("\0"):
                if name:
                    seen.setdefault(name, None)
    try:
        r = git("status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames", strip=False)
    except _Out:
        r = None
    if r is not None and r.ok:
        for entry in r.out.split("\0"):
            if len(entry) > 3 and entry[2] == " ":
                seen.setdefault(entry[3:], None)
    names = list(seen)
    return [n[:NAME_MAX] for n in names[:CHANGED_MAX]], max(0, len(names) - CHANGED_MAX)


def freshness(directory, sha, *, files: bool = True, budget: float = BUDGET) -> dict:
    """See the module doc."""
    try:
        return _freshness(directory, sha, files, budget)
    except Exception:  # noqa: BLE001 - a note never stops a task
        return _unknown()


def _freshness(directory, sha, files: bool, budget: float) -> dict:
    if not isinstance(directory, str) or not directory or not os.path.isdir(directory):
        return _unknown()
    if not isinstance(sha, str) or not _HEX.fullmatch(sha):
        return _unknown()
    deadline = time.monotonic() + budget

    def git(*args, strip=True):
        left = deadline - time.monotonic()
        if left < _MIN_LEFT:
            raise _Out()
        # run_git gives its filter-driver listing and the command each the timeout: half each keeps the budget
        r = gitsafe.run_git(directory, *args, timeout=min(CALL_TIMEOUT, left) / 2, strip=strip)
        if r.failed:  # git missing, timed out or killed: nothing it said can be trusted
            raise _Out()
        return r

    try:
        r = git("rev-parse", "--verify", "-q", "HEAD")
        head = r.out if r.ok and _HEX.fullmatch(r.out or "") else None
        if head is None:
            return _unknown()
        out = {"state": "unknown", "behind": None, "changed": [], "more": 0, "head": head}
        if head.startswith(sha):
            out.update(state="current", behind=0)
        else:
            has = git("rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
            if not has.ok:
                if has.code != 1 or _shallow(directory, git):
                    return dict(out, state="unknown")
                return dict(out, state="different")
            anc = git("merge-base", "--is-ancestor", sha, head)
            if anc.code == 1:
                return dict(out, state="unknown" if _shallow(directory, git) else "different")
            if anc.code != 0:
                return dict(out, state="unknown")
            cnt = git("rev-list", "--count", f"{sha}..{head}")
            if not cnt.ok or not cnt.out.isdigit():
                return dict(out, state="unknown")
            out.update(state="ahead", behind=int(cnt.out))
    except _Out:
        return _unknown()
    if files:
        changed, more = _names(directory, sha, out["state"] == "ahead", git)
        out.update(changed=changed, more=more)
    return out
