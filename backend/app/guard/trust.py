"""Suggest a new project's trust (Phase 2): forge ``setup.js`` ``decide``,
generalised from "my git email" to every org member's.

``suggest_trust(directory, member_emails) -> ("own" | "external", reason)``,
in order:

1. no git remote (or not a git repo, or git unusable) -> own;
2. no commit yet (unborn HEAD) -> own;
3. no member email known -> own, and the reason says trust was not checked;
4. some commit in ``git log --all`` is authored by a member email (whole
   address, any case) -> own; none -> external.

Once a remote is known, any git failure or time-out means external (forge
review M6: guessing external only asks for approvals; guessing own reads a
stranger's rules file). All git calls share one ``GIT_TIMEOUT`` (5 s) budget.
git runs with optional locks off, no pager, no prompts and with commands from
the repo's config switched off (fsmonitor, signature checks), so suggesting
trust never runs code from the repo.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass

GIT_TIMEOUT = 5  # seconds, for all git calls of one suggestion
MAX_EMAILS = 500

_SAFE = ["-c", "core.fsmonitor=false", "-c", "log.showSignature=false", "-c", "core.hooksPath=/dev/null",
         "--no-optional-locks", "--no-pager"]
_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "GIT_ASKPASS": "", "SSH_ASKPASS": "",
        "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}

R_NO_REMOTE = "the folder has no git remote, so it is treated as code made here"
R_NO_COMMITS = "the repo has no commits yet, so it is treated as code made here"
R_NOT_CHECKED = ("trust was not checked: no org member has a git email set (Settings → Credentials); "
                 "a maintainer can mark it external")
R_MEMBER = "an org member has commits in this repo"
R_FOREIGN = "the repo has a git remote and no commit is by an org member, so it is treated as someone else's code"
R_FAILED = "git failed after finding a remote, so the repo is treated as someone else's code"


@dataclass
class _Result:
    ok: bool
    out: str
    failed: bool  # an error or time-out (not just a non-zero exit)


def _git(directory, args, deadline) -> _Result:
    left = deadline - time.monotonic()
    if left <= 0:
        return _Result(False, "", True)
    try:
        r = subprocess.run(
            ["git", *_SAFE, *args], cwd=str(directory), stdin=subprocess.DEVNULL, capture_output=True,
            timeout=min(GIT_TIMEOUT, left), env={**os.environ, **_ENV},
        )
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return _Result(False, "", True)
    return _Result(r.returncode == 0, r.stdout.decode("utf-8", "replace").strip(), r.returncode < 0)


def _emails(member_emails) -> list[str]:
    seen: dict[str, None] = {}
    for e in member_emails or ():
        if isinstance(e, str) and e.strip() and "\n" not in e:
            seen.setdefault(e.strip().lower(), None)
    return list(seen)[:MAX_EMAILS]


def suggest_trust(directory, member_emails) -> tuple[str, str]:
    deadline = time.monotonic() + GIT_TIMEOUT
    if not os.path.isdir(directory):
        return "own", R_NO_REMOTE
    remotes = _git(directory, ["remote"], deadline)
    if not remotes.ok or not remotes.out:
        return "own", R_NO_REMOTE
    head = _git(directory, ["rev-parse", "--verify", "-q", "HEAD"], deadline)
    if head.failed:
        return "external", R_FAILED
    if not head.ok:
        return "own", R_NO_COMMITS
    emails = _emails(member_emails)
    if not emails:
        return "own", R_NOT_CHECKED
    # Authors only (forge); -i --fixed-strings with <email> matches the whole address in any case.
    log = _git(directory, ["log", "--all", "-i", "--fixed-strings", "--max-count=1", "--format=%H",
                           *(f"--author=<{e}>" for e in emails)], deadline)
    if not log.ok:
        return "external", R_FAILED
    return ("own", R_MEMBER) if log.out else ("external", R_FOREIGN)
