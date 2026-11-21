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

Not for commands that need the network on the agent's behalf (clone, fetch,
push): those are out of scope here.
"""

from __future__ import annotations

import os
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


def git_env(directory) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(_FIXED_ENV)
    env["GIT_CEILING_DIRECTORIES"] = os.path.dirname(os.path.realpath(directory))
    return env


def git_available() -> bool:
    return shutil.which("git") is not None


def run_git(directory, *args: str, timeout: float) -> GitResult:
    """Run ``git <SAFE_ARGS> <args>`` in ``directory`` (see the module doc)."""
    exe = shutil.which("git")
    if exe is None:
        return GitResult(False, "", True, "git not found", missing=True)
    try:
        r = subprocess.run(
            [exe, *SAFE_ARGS, *args], cwd=str(Path(directory)), stdin=subprocess.DEVNULL, capture_output=True,
            timeout=max(0.01, timeout), env=git_env(directory),
        )
    except subprocess.TimeoutExpired:
        return GitResult(False, "", True, "timed out")
    except (OSError, ValueError) as e:
        return GitResult(False, "", True, str(e))
    return GitResult(
        r.returncode == 0, r.stdout.decode("utf-8", "replace").strip(), r.returncode < 0,
        r.stderr.decode("utf-8", "replace").strip(), r.returncode,
    )
