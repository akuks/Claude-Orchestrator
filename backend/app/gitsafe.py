"""Hardened git for server-side reads of a user's directory (Ruling P21).

The server reads repo metadata (remotes, refs, authors) of directories it does
not control. A repo's own config can make git run programs (core.sshCommand,
credential.helper, ext:: remotes, fsmonitor, hooks, pagers, a promisor
remote's lazy fetch of a missing object), so every such call goes through
``run_git``:

* no transport at all: ``GIT_ALLOW_PROTOCOL=none``, ``GIT_NO_LAZY_FETCH=1``,
  ``GIT_SSH_COMMAND=false`` and ``-c core.sshCommand=false``;
* no helpers or prompts: ``-c credential.helper=``, ``-c core.askPass=``,
  ``GIT_TERMINAL_PROMPT=0``, empty askpass variables;
* no local programs: ``-c core.fsmonitor=false``, ``-c core.hooksPath=/dev/null``,
  ``-c core.pager=cat``, ``--no-pager``, ``GIT_PAGER=cat``,
  ``-c log.showSignature=false``, ``--no-optional-locks``;
* only the repo itself: system and global config are skipped
  (``GIT_CONFIG_NOSYSTEM=1``, ``GIT_CONFIG_GLOBAL=/dev/null``), every
  inherited ``GIT_*`` variable is dropped (``GIT_DIR``, ``GIT_WORK_TREE``,
  ``GIT_INDEX_FILE``, ``GIT_OBJECT_DIRECTORY``, ``GIT_CONFIG_*`` ...), and
  ``GIT_CEILING_DIRECTORIES`` is the directory's parent, so git never climbs
  into an enclosing repo;
* stdin is /dev/null, ``LC_ALL=C`` (stable messages), a timeout always.

* signature and diff programs off: ``-c gpg.program=false``,
  ``gpg.ssh.program``/``gpg.x509.program`` too, ``-c diff.external=``,
  ``-c core.attributesFile=/dev/null``;
* a subcommand allowlist (``ValueError`` otherwise): ``rev-parse``, plain
  ``remote`` (or ``-v``), ``for-each-ref``, ``log``/``rev-list`` with only
  ``ALLOWED_LOG_OPTIONS`` (no patches or diffs, no ``%G`` signature
  formats), ``config --get <key>`` and ``--version``;
* the resume snapshot's read-only extras, each exact: ``status`` with exactly
  ``--porcelain=v1 -z --untracked-files=all --no-renames`` (any order, nothing
  else), ``merge-base --is-ancestor <hex> <hex>``, ``symbolic-ref -q --short
  HEAD``, and for ``log``/``rev-list`` positional revisions that are ``HEAD``,
  a hex sha or ``<hex>..<hex>`` (hex is 7-40 lowercase digits);
* the decision health check's two reads: ``ls-files`` with exactly ``-co
  --exclude-standard -z`` (any order, nothing else), and ``log`` with
  ``--name-only`` and ``--since=<date>`` (both in ``ALLOWED_LOG_OPTIONS``);
* repo-defined filter drivers off: before every command the repo's
  ``filter.<name>.*`` config is listed (one internal read, ``config
  --get-regexp -z ^filter\\.``) and each driver's ``clean``, ``smudge``,
  ``process`` is set empty and ``required=false`` with ``-c``, so a
  ``.gitattributes`` ``filter=`` line cannot make git run a program; more
  than 50 drivers, or a listing that fails, refuses the command;
* submodule internals are never entered: ``status`` and ``diff`` get
  ``--ignore-submodules=dirty`` from ``run_git`` itself (a submodule's own
  config could define filter drivers), so dirty submodule content is not
  reported, only a changed gitlink; submodule diffs are pinned to the short
  form (``-c diff.submodule=short``, ``-c status.submoduleSummary=false`` and
  ``diff --submodule=short``), so no child ``git diff`` runs inside one; a driver name that is not printable ASCII
  (or holds ``=``) refuses the command ("unusual filter driver name");
* the gate code-state hash's two reads: ``diff`` with exactly
  ``--no-ext-diff --no-textconv --binary HEAD`` and ``ls-files`` with exactly
  ``--others --exclude-standard -z`` (each any order, nothing else);
* the code map freshness check (phase 7d): ``diff --name-only <hex> HEAD``, exactly that order (the caller
  bounds it with its timeout and reads only what it needs), and the repository's first commit: ``rev-list
  --max-parents=0 HEAD``, exactly;
* the typed stage outputs' commit check: ``rev-parse --verify --quiet
  <hex>^{commit}``, exactly that order (hex is 7-40 lowercase digits).

It only reads metadata. Commands that need the network or write (clone,
fetch, push, checkout ...) are refused; they are out of scope here.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SAFE_ARGS = (
    "-c", "core.sshCommand=false",
    "-c", "credential.helper=",
    "-c", "core.askPass=",
    "-c", "core.fsmonitor=false",
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.pager=cat",
    "-c", "log.showSignature=false",
    "-c", "protocol.allow=never",
    "-c", "gpg.program=false",
    "-c", "gpg.ssh.program=false",
    "-c", "gpg.x509.program=false",
    "-c", "diff.external=",
    "-c", "core.attributesFile=/dev/null",
    "-c", "diff.submodule=short",
    "-c", "status.submoduleSummary=false",
    "--no-pager",
    "--no-optional-locks",
)
_FIXED_ENV = {
    "GIT_ALLOW_PROTOCOL": "none",
    "GIT_NO_LAZY_FETCH": "1",
    "GIT_SSH_COMMAND": "false",
    "GIT_SSH": "false",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_PAGER": "cat",
    "GIT_ASKPASS": "",
    "SSH_ASKPASS": "",
    "GIT_OPTIONAL_LOCKS": "0",
    "LC_ALL": "C",
}


@dataclass
class GitResult:
    ok: bool  # exit 0
    out: str
    failed: bool  # git missing, could not start, timed out or was killed by a signal
    err: str = ""
    code: int | None = None  # exit code; None when git did not finish
    missing: bool = False  # no git executable


# log / rev-list options a caller may pass (exact, or a prefix ending in "=").
ALLOWED_LOG_OPTIONS = ("--all", "-i", "--fixed-strings", "--no-merges", "--count", "--max-count=", "--format=",
                       "--author=", "--name-only", "--since=")

LS_FILES_OPTIONS = ("-co", "--exclude-standard", "-z")
LS_FILES_OTHERS_OPTIONS = ("--others", "--exclude-standard", "-z")
DIFF_OPTIONS = ("--no-ext-diff", "--no-textconv", "--binary", "HEAD")
STATUS_OPTIONS = ("--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
_HEX = r"[0-9a-f]{7,40}"
_REV_RE = re.compile(rf"^(HEAD|{_HEX}(\.\.{_HEX})?)$")
_SHA_RE = re.compile(rf"^{_HEX}$")
_COMMIT_REV_RE = re.compile(rf"{_HEX}\^\{{commit\}}")  # used with fullmatch


def check_args(args) -> None:
    """ValueError unless ``args`` is an allowed read-only git command."""
    if not args or not all(isinstance(a, str) for a in args):
        raise ValueError("git: no command")
    cmd, rest = args[0], list(args[1:])
    if cmd == "--version" and not rest:
        return
    if cmd == "rev-parse" and all(a in ("--git-dir", "--show-toplevel", "--verify", "-q", "HEAD") for a in rest):
        return
    if cmd == "rev-parse" and len(rest) == 3 and rest[:2] == ["--verify", "--quiet"] \
            and _COMMIT_REV_RE.fullmatch(rest[2]):
        return
    if cmd == "remote" and rest in ([], ["-v"]):
        return
    if cmd == "for-each-ref" and all(a.startswith(("--count=", "--format=", "refs/")) for a in rest):
        return
    if cmd == "status" and sorted(rest) == sorted(STATUS_OPTIONS):
        return
    if cmd == "ls-files" and sorted(rest) == sorted(LS_FILES_OPTIONS):
        return
    if cmd == "ls-files" and sorted(rest) == sorted(LS_FILES_OTHERS_OPTIONS):
        return
    if cmd == "diff" and sorted(rest) == sorted(DIFF_OPTIONS):
        return
    if cmd == "diff" and len(rest) == 3 and rest[0] == "--name-only" and _SHA_RE.fullmatch(rest[1]) \
            and rest[2] == "HEAD":
        return
    if cmd == "rev-list" and rest == ["--max-parents=0", "HEAD"]:
        return
    if cmd == "merge-base" and len(rest) == 3 and rest[0] == "--is-ancestor" and all(_SHA_RE.match(a) for a in rest[1:]):
        return
    if cmd == "symbolic-ref" and rest == ["-q", "--short", "HEAD"]:
        return
    if cmd == "config" and len(rest) == 2 and rest[0] == "--get":
        return
    if cmd in ("log", "rev-list"):
        for a in rest:
            if _REV_RE.match(a):
                continue
            exact = a in ALLOWED_LOG_OPTIONS and not a.endswith("=")
            valued = any(o.endswith("=") and a.startswith(o) for o in ALLOWED_LOG_OPTIONS)
            if not (exact or valued):
                raise ValueError(f"git {cmd}: option not allowed: {a[:80]!r}")
            if a.startswith("--format=") and "%G" in a:
                raise ValueError(f"git {cmd}: signature formats are not allowed")
        return
    raise ValueError(f"git command not allowed: {' '.join(args)[:120]!r}")


def git_env(directory) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(_FIXED_ENV)
    env["GIT_CEILING_DIRECTORIES"] = os.path.dirname(os.path.realpath(directory))
    return env


def git_available() -> bool:
    return shutil.which("git") is not None


MAX_FILTER_DRIVERS = 50
_DRIVER_NAME_RE = re.compile(r"^[\x21-\x3c\x3e-\x7e]+$")  # printable ASCII, no space, no "="


def _filter_overrides(exe: str, directory, timeout: float) -> list[str] | GitResult:
    """``-c`` arguments that disable every filter driver the repo defines, or a failing GitResult."""
    try:
        r = subprocess.run(
            [exe, *SAFE_ARGS, "config", "--get-regexp", "-z", r"^filter\."], cwd=str(Path(directory)),
            stdin=subprocess.DEVNULL, capture_output=True, timeout=max(0.01, timeout), env=git_env(directory),
        )
    except subprocess.TimeoutExpired:
        return GitResult(False, "", True, "filter driver listing timed out")
    except (OSError, ValueError) as e:
        return GitResult(False, "", True, str(e))
    out = r.stdout.decode("latin-1")  # byte-for-byte: a name is checked, never mangled
    if r.returncode == 1 and not out:
        return []  # no matches
    if r.returncode != 0:
        return GitResult(False, "", r.returncode < 0, r.stderr.decode("utf-8", "replace").strip(), r.returncode)
    names: list[str] = []
    for entry in out.split("\0"):
        key = entry.split("\n", 1)[0]
        if not key.startswith("filter.") or "." not in key[7:]:
            continue
        name = key[7:key.rindex(".")]
        if name not in names:
            names.append(name)
    if len(names) > MAX_FILTER_DRIVERS:
        return GitResult(False, "", True, "too many filter drivers")
    if any(not _DRIVER_NAME_RE.match(n) for n in names):
        return GitResult(False, "", True, "unusual filter driver name")
    over: list[str] = []
    for n in names:
        for k, v in (("clean", ""), ("smudge", ""), ("process", ""), ("required", "false")):
            over += ["-c", f"filter.{n}.{k}={v}"]
    return over


def run_git(directory, *args: str, timeout: float) -> GitResult:
    """Run ``git <SAFE_ARGS> <args>`` in ``directory`` (see the module doc);
    ValueError for a command outside the allowlist."""
    check_args(args)
    exe = shutil.which("git")
    if exe is None:
        return GitResult(False, "", True, "git not found", missing=True)
    over = _filter_overrides(exe, directory, timeout)
    if isinstance(over, GitResult):
        return over
    if args[0] in ("status", "diff"):
        # a submodule's own config could define filter or diff drivers that git's child processes would run
        extra = ("--ignore-submodules=dirty", "--submodule=short") if args[0] == "diff" else ("--ignore-submodules=dirty",)
        args = (args[0], *extra, *args[1:])
    try:
        r = subprocess.run(
            [exe, *SAFE_ARGS, *over, *args], cwd=str(Path(directory)), stdin=subprocess.DEVNULL,
            capture_output=True, timeout=max(0.01, timeout), env=git_env(directory),
        )
    except subprocess.TimeoutExpired:
        return GitResult(False, "", True, "timed out")
    except (OSError, ValueError) as e:
        return GitResult(False, "", True, str(e))
    return GitResult(
        r.returncode == 0, r.stdout.decode("utf-8", "replace").strip(), r.returncode < 0,
        r.stderr.decode("utf-8", "replace").strip(), r.returncode,
    )
