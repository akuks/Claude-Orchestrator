"""Copy a temp-folder document (one a step recorded as "may vanish") into the project (phase 7e).

``copy_doc`` is blocking file work (run it in a thread). Every failure is a ``CopyError`` with a plain
reason and an HTTP status. The source is read with no links followed (a regular file, at most 5 MB); the
destination is a new file: relative, no ``..``, its folder exists and really lies inside the workspace or
the project folder, and nothing is overwritten."""

from __future__ import annotations

import os
import posixpath
import stat

from ..guard import rules
from .handoff import _DOC_EXT

MAX_BYTES = 5 * 1024 * 1024
EXISTS = "A file with that name already exists."


class CopyError(Exception):
    def __init__(self, status: int, text: str):
        super().__init__(text)
        self.status, self.text = status, text


def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def _read_source(path: str, recorded, policy) -> bytes:
    if not isinstance(path, str) or not path or "\0" in path:
        raise CopyError(400, "Say which document to copy.")
    norm = posixpath.normpath(path)
    if not any(isinstance(r, str) and posixpath.normpath(r) == norm for r in recorded or []):
        raise CopyError(404, "That document is not one this task wrote to a temp folder.")
    if os.path.splitext(norm)[1].lower() not in _DOC_EXT or not rules.path_in_temp(norm, policy):
        raise CopyError(400, "That file is no longer in a temp folder, so it cannot be copied.")
    try:
        fd = os.open(norm, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        raise CopyError(404, "That document is gone or cannot be read.") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise CopyError(400, "That is not a plain file.")
        if st.st_size > MAX_BYTES:
            raise CopyError(400, "That document is larger than 5 MB.")
        data = b""
        while len(data) <= MAX_BYTES:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            data += chunk
    finally:
        os.close(fd)
    if len(data) > MAX_BYTES:
        raise CopyError(400, "That document is larger than 5 MB.")
    return data


def copy_doc(path, dest, recorded, policy, base: str, roots: list[str]) -> str:
    """Copy ``path`` to ``dest`` (relative to ``base``); returns ``dest`` as given."""
    if not isinstance(dest, str) or not dest.strip() or "\0" in dest:
        raise CopyError(400, "Say where to put the copy.")
    if os.path.isabs(dest) or dest.startswith(("/", "\\")) or ":" in dest.split("/")[0]:
        raise CopyError(400, "The new name must be a path inside the project, like docs/plan.md.")
    parts = dest.replace("\\", "/").split("/")
    if ".." in parts or parts[-1] in ("", "."):
        raise CopyError(400, "The new name must be a path inside the project, like docs/plan.md.")
    data = _read_source(path, recorded, policy)
    real_roots = [os.path.realpath(r) for r in roots if r]
    full = os.path.join(base, *[p for p in parts if p not in ("", ".")])
    parent = os.path.dirname(full)
    if not os.path.isdir(parent):
        raise CopyError(400, "The folder for the copy does not exist yet. Pick an existing folder.")
    if not any(_inside(os.path.realpath(parent), r) for r in real_roots):
        raise CopyError(400, "The new name points outside the project.")
    try:
        fd = os.open(full, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    except FileExistsError:
        raise CopyError(409, EXISTS) from None
    except OSError:
        raise CopyError(400, "The copy could not be written there.") from None
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except OSError:
        raise CopyError(400, "The copy could not be written there.") from None
    return dest
