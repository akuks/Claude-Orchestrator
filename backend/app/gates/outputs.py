"""Typed stage outputs (phase 5c).

An agent stage item may declare ``outputs`` (names checked by ``spec.validate_stages``).
The runner appends ``suffix(names)`` to that task's prompt; when the task completes,
``parse`` reads the LAST fenced json block of its result and ``verify`` checks the
``*_path`` and ``*_commit`` values against the task's project folder. Both raise
``ValueError`` with a plain reason, which becomes the task error
"outputs invalid: <reason>".
"""

from __future__ import annotations

import asyncio
import json
import os
import re

from .. import gitsafe
from ..redaction import clean_text

SCAN_BYTES = 64 * 1024  # only the tail of result_text is scanned
MAX_VALUE = 2000  # chars per value after clean_text
GIT_TIMEOUT = 10.0
FENCE_OPEN = "```json"
FENCE_CLOSE = "```"
_COMMIT_RE = re.compile(r"[0-9a-f]{7,40}")


def suffix(names: list[str]) -> str:
    """The exact instruction appended to a task prompt that declares outputs."""
    return (
        "When you finish, end your final message with a fenced ```json block holding exactly these keys, "
        f"each a short string: {', '.join(names)}. A key ending in _path is a path relative to the project "
        "folder that exists; a key ending in _commit is a commit id in this repository."
    )


def parse(result_text: str | None, names: list[str]) -> dict:
    """The declared values from the last ```json block of ``result_text`` (cleaned, in
    declared order). Only the final 64 KB is scanned, with ``rfind``/``find``: linear."""
    text = result_text or ""
    tail = text[-SCAN_BYTES:]
    start = tail.rfind(FENCE_OPEN)
    if start < 0:
        raise ValueError("no ```json block at the end of the result")
    body_start = start + len(FENCE_OPEN)
    end = tail.find(FENCE_CLOSE, body_start)
    if end < 0:
        raise ValueError("the last ```json block is not closed")
    body = tail[body_start:end]
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):
        raise ValueError("the ```json block is not valid JSON") from None
    if not isinstance(data, dict):
        raise ValueError("the ```json block is not an object")
    for k in names:
        if k not in data:
            raise ValueError(f"missing key {k}")
    for k in data:
        if k not in names:
            raise ValueError(f"unexpected key {clean_text(str(k), 40)}")
    out: dict[str, str] = {}
    for k in names:
        v = data[k]
        if not isinstance(v, str):
            raise ValueError(f"{k} is not a string")
        v = clean_text(v).strip()
        if len(v) > MAX_VALUE:
            raise ValueError(f"{k} is longer than {MAX_VALUE} characters")
        out[k] = v
    return out


def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def _check_path(k: str, v: str, root: str) -> None:
    if not v or "\0" in v:
        raise ValueError(f"{k} is empty")
    if os.path.isabs(v) or v.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", v):
        raise ValueError(f"{k} must be relative to the project folder")
    if ".." in re.split(r"[/\\]", v):
        raise ValueError(f"{k} must not contain ..")
    full = os.path.join(root, v)
    try:
        os.lstat(full)
    except OSError:
        raise ValueError(f"{k} does not exist in the project folder") from None
    real = os.path.realpath(full)
    if not _inside(real, root):
        raise ValueError(f"{k} points outside the project folder")
    if not os.path.exists(real):
        raise ValueError(f"{k} does not exist in the project folder")


def _check_commit(k: str, v: str, root: str) -> None:
    if not _COMMIT_RE.fullmatch(v):
        raise ValueError(f"{k} is not a commit id (7 to 40 lowercase hex characters)")
    try:
        r = gitsafe.run_git(root, "rev-parse", "--verify", "--quiet", f"{v}^{{commit}}", timeout=GIT_TIMEOUT)
    except ValueError:
        raise ValueError(f"{k} is not a commit id") from None
    if not r.ok:
        raise ValueError(f"{k} is not a commit in this repository")


def _verify_sync(values: dict, project_dir: str | None) -> None:
    keys = [k for k in values if k.endswith(("_path", "_commit"))]
    if not keys:
        return
    if not project_dir or not os.path.isdir(project_dir):
        raise ValueError(f"{keys[0]} cannot be checked: the task has no project folder")
    root = os.path.realpath(project_dir)
    for k in keys:
        if k.endswith("_path"):
            _check_path(k, values[k], root)
        else:
            _check_commit(k, values[k], root)


async def verify(values: dict, project_dir: str | None) -> None:
    """ValueError(reason) when a ``*_path`` value is not an existing path inside the
    project folder, or a ``*_commit`` value is not a commit of its repository. The
    file system and git work runs in a thread."""
    await asyncio.to_thread(_verify_sync, values, project_dir)
