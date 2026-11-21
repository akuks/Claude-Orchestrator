"""Suggest a new project's trust (Phase 2): forge ``setup.js`` ``decide``,
generalised from "my git email" to every org member's.

``suggest_trust(directory, member_emails) -> ("own" | "external", reason)``,
in order:

1. git missing -> own (``R_NO_GIT``); the folder is not a git repo (no
   ``.git`` and git says "not a git repository") -> own (``R_NOT_REPO``);
2. no git remote -> own;
3. no commit on any ref (``for-each-ref refs/`` finds none; HEAD alone is not
   asked, a foreign repo can sit on an unborn branch) -> own;
4. no member email known -> own, and the reason says trust was not checked;
5. some commit in ``git log --all`` is authored by a member email (whole
   address, any case) -> own; none -> external.

Ruling P21 / fix round 1: once the folder is a repo, ANY git failure, signal
or time-out means external (``R_FAILED``) - a broken config, an unsupported
repository format or "dubious ownership" are under the repo's control, and
guessing external only asks for approvals. All git calls share one
``GIT_TIMEOUT`` (5 s) budget and go through ``app.gitsafe.run_git`` (no
transport, no helpers, no lazy fetch, no repo-configured programs), so
suggesting trust never runs code the repo controls.
"""

from __future__ import annotations

import os
import subprocess  # noqa: F401  (tests patch subprocess.run through this module)
import time

from .. import gitsafe

GIT_TIMEOUT = 5  # seconds, for all git calls of one suggestion
MAX_EMAILS = 500

R_NO_GIT = "git is not installed on the server, so trust was not checked"
R_NOT_REPO = "the folder is not a git repository, so it is treated as code made here"
R_NO_REMOTE = "the folder has no git remote, so it is treated as code made here"
R_NO_COMMITS = "the repo has no commits yet, so it is treated as code made here"
R_NOT_CHECKED = ("trust was not checked: no org member has a git email set (Settings → Credentials); "
                 "a maintainer can mark it external")
R_MEMBER = "an org member has commits in this repo"
R_FOREIGN = "the repo has a git remote and no commit is by an org member, so it is treated as someone else's code"
R_FAILED = "git could not read this repo, so it is treated as someone else's code"
R_TIME_LIMIT = "trust was not checked (time limit), so it is treated as someone else's code"

_Result = gitsafe.GitResult


def _git(directory, args, deadline) -> _Result:
    left = deadline - time.monotonic()
    if left <= 0:
        return _Result(False, "", True, "time limit")
    return gitsafe.run_git(directory, *args, timeout=min(GIT_TIMEOUT, left))


def _emails(member_emails) -> list[str]:
    seen: dict[str, None] = {}
    for e in member_emails or ():
        if isinstance(e, str) and e.strip() and "\n" not in e:
            seen.setdefault(e.strip().lower(), None)
    return list(seen)[:MAX_EMAILS]


def suggest_trust(directory, member_emails) -> tuple[str, str]:
    deadline = time.monotonic() + GIT_TIMEOUT
    if not os.path.isdir(directory):
        return "own", R_NOT_REPO
    if not gitsafe.git_available():
        return "own", R_NO_GIT
    has_git = os.path.lexists(os.path.join(directory, ".git"))
    top = _git(directory, ["rev-parse", "--git-dir"], deadline)
    if top.missing:
        return "own", R_NO_GIT
    if not top.ok:
        if not has_git and not top.failed and "not a git repository" in top.err.lower():
            return "own", R_NOT_REPO
        return "external", R_FAILED
    remotes = _git(directory, ["remote"], deadline)
    if not remotes.ok:
        return "external", R_FAILED
    if not remotes.out:
        return "own", R_NO_REMOTE
    refs = _git(directory, ["for-each-ref", "--count=1", "--format=%(objectname)", "refs/"], deadline)
    if not refs.ok:
        return "external", R_FAILED
    if not refs.out:
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
