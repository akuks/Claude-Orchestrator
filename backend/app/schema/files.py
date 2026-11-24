"""Find and safely read a project's SQL migration files.

Roots (each kept on its own): migrations/, db/migrations/, supabase/migrations/, prisma/migrations/.
Every file is opened without following links and its real path must stay inside the project folder.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field

ROOTS = ("migrations", "db/migrations", "supabase/migrations", "prisma/migrations")
MAX_FILES = 5000
MAX_BYTES = 5 * 1024 * 1024
MAX_DEPTH = 6
LIMIT_FILES = "More than 5,000 migration files were found: only the first 5,000 were read."
LIMIT_BYTES = "The migration files add up to more than 5 MB: later files were not read."
_UNDO = re.compile(r"(\.down\.|_down\.|\.undo\.|_undo\.)|^U\d+__", re.IGNORECASE)
_NUM = re.compile(r"(\d+)")


def natural_key(path: str):
    """Numeric-aware path order: 2_x.sql before 10_x.sql."""
    return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in _NUM.split(path.replace(os.sep, "/")) if p != ""]


def is_undo(name: str) -> bool:
    return bool(_UNDO.search(name))


@dataclass
class RootFiles:
    root: str
    files: list[tuple[str, str]] = field(default_factory=list)  # (rel_path, text)


@dataclass
class Collected:
    roots: list[RootFiles] = field(default_factory=list)
    limits: list[str] = field(default_factory=list)
    signature: tuple = ()  # (rel_path, size, mtime_ns) of every file read


def _inside(real: str, base: str) -> bool:
    return real == base or real.startswith(base.rstrip(os.sep) + os.sep)


def _is_plain_dir(path: str) -> bool:
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def _read(path: str, cap: int):
    """(text, size, mtime_ns) of a regular, non-link file, or None; text None when over the cap."""
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None
        if st.st_size > cap:
            return (None, st.st_size, st.st_mtime_ns)
        with os.fdopen(fd, "rb", closefd=False) as f:
            data = f.read(cap + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    if len(data) > cap:
        return (None, len(data), st.st_mtime_ns)
    return (data.decode("utf-8", errors="replace"), len(data), st.st_mtime_ns)


def _list(base: str, root: str, prisma: bool) -> list[str]:
    """Relative (to the project) paths of the .sql files under a root, links skipped, unordered."""
    out: list[str] = []
    stack = [(os.path.join(base, *root.split("/")), 0)]
    while stack:
        d, depth = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_symlink():
                    continue
                if e.is_dir(follow_symlinks=False):
                    if depth < MAX_DEPTH and not e.name.startswith("."):
                        stack.append((e.path, depth + 1))
                elif e.is_file(follow_symlinks=False) and e.name.lower().endswith(".sql") and not is_undo(e.name):
                    if prisma and not (depth == 1 and e.name == "migration.sql"):
                        continue
                    out.append(os.path.relpath(e.path, base))
            except OSError:
                continue
    return out


def collect(project_dir: str) -> Collected:
    res = Collected()
    try:
        base = os.path.realpath(project_dir)
    except (OSError, ValueError, TypeError):
        return res
    if not os.path.isdir(base):
        return res
    n_files = n_bytes = 0
    sig: list[tuple] = []
    for root in ROOTS:
        top = os.path.join(base, *root.split("/"))
        if not _is_plain_dir(top) or not _inside(os.path.realpath(top), base):
            continue  # a link, or a path that leaves the project
        rels = sorted(_list(base, root, root.startswith("prisma")), key=natural_key)
        rf = RootFiles(root)
        for rel in rels:
            if n_files >= MAX_FILES:
                if LIMIT_FILES not in res.limits:
                    res.limits.append(LIMIT_FILES)
                break
            full = os.path.join(base, rel)
            if not _inside(os.path.realpath(full), base):
                continue
            got = _read(full, MAX_BYTES - n_bytes)
            if got is None:
                continue
            text, size, mtime = got
            if text is None:
                if LIMIT_BYTES not in res.limits:
                    res.limits.append(LIMIT_BYTES)
                continue
            n_files += 1
            n_bytes += size
            sig.append((rel, size, mtime))
            rf.files.append((rel.replace(os.sep, "/"), text))
        if rf.files:
            res.roots.append(rf)
    res.signature = tuple(sig)
    return res
