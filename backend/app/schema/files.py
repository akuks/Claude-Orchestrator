"""Find and safely read a project's SQL migration files.

Roots (each kept on its own): migrations/, db/migrations/, supabase/migrations/, prisma/migrations/.

Nothing here re-opens a file by its path: every folder is opened with ``O_NOFOLLOW|O_DIRECTORY`` relative to its
parent's file descriptor and every file with ``O_NOFOLLOW|O_NONBLOCK`` relative to its folder's descriptor (then
``fstat`` must say regular file), so a folder swapped for a link while we work is never followed.
Two steps: ``scan`` lists (and gives the (path, size, mtime) signature), ``read`` reads what the scan listed.
"""

from __future__ import annotations

import os
import re
import stat
import time
from dataclasses import dataclass, field

ROOTS = ("migrations", "db/migrations", "supabase/migrations", "prisma/migrations")
MAX_FILES = 5000
MAX_BYTES = 5 * 1024 * 1024
MAX_DEPTH = 6
LIMIT_FILES = "More than 5,000 migration files were found: only the first 5,000 were read."
LIMIT_BYTES = "The migration files add up to more than 5 MB: later files were not read."
_UNDO = re.compile(r"\.down\.|_down\.|\.undo\.|_undo\.", re.IGNORECASE)
_UNDO_U = re.compile(r"^U\d+__")  # Flyway undo: capital U only
_NUM = re.compile(r"(\d+)")
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def natural_key(path: str):
    """Numeric-aware path order: 2_x.sql before 10_x.sql."""
    return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in _NUM.split(path.replace(os.sep, "/")) if p != ""]


def is_undo(name: str) -> bool:
    return bool(_UNDO.search(name) or _UNDO_U.match(name))


@dataclass
class RootFiles:
    root: str
    files: list[tuple[str, str]] = field(default_factory=list)  # (rel_path, text)


@dataclass
class Scan:
    base: str = ""
    roots: list[tuple[str, list[tuple[str, int, int]]]] = field(default_factory=list)  # root -> (rel, size, mtime_ns)
    limits: list[str] = field(default_factory=list)
    signature: tuple = ()
    base_fd: int = -1  # the project folder, opened once; scan and read both work from it

    def close(self) -> None:
        if self.base_fd >= 0:
            try:
                os.close(self.base_fd)
            except OSError:
                pass
            self.base_fd = -1


@dataclass
class Collected:
    roots: list[RootFiles] = field(default_factory=list)
    limits: list[str] = field(default_factory=list)
    signature: tuple = ()
    changed: list[str] = field(default_factory=list)  # files whose size differs from the listing


def _check(deadline):
    if deadline is not None and time.monotonic() > deadline:
        raise TimeoutError("schema scan over budget")


def _open_chain(base_fd: int, parts: list[str]) -> int:
    """Open the folder ``parts`` below ``base_fd`` one step at a time; the caller closes the result."""
    fd = os.dup(base_fd)
    try:
        for part in parts:
            nxt = os.open(part, _DIR, dir_fd=fd)
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd


def _list(dirfd: int, rel: str, depth: int, prisma: bool, out: list, deadline) -> None:
    _check(deadline)
    try:
        entries = list(os.scandir(dirfd))
    except OSError:
        return
    for e in entries:
        try:
            if e.is_symlink():
                continue
            if e.is_dir(follow_symlinks=False):
                if depth < MAX_DEPTH and not e.name.startswith("."):
                    try:
                        sub = os.open(e.name, _DIR, dir_fd=dirfd)
                    except OSError:
                        continue
                    try:
                        _list(sub, f"{rel}/{e.name}", depth + 1, prisma, out, deadline)
                    finally:
                        os.close(sub)
            elif e.is_file(follow_symlinks=False) and e.name.lower().endswith(".sql") and not is_undo(e.name):
                if prisma and not (depth == 1 and e.name == "migration.sql"):
                    continue
                st = e.stat(follow_symlinks=False)
                out.append((f"{rel}/{e.name}", st.st_size, st.st_mtime_ns))
        except OSError:
            continue


def _after_list() -> None:
    """Test hook: runs between listing and reading."""


def scan(project_dir: str, deadline: float | None = None) -> Scan:
    res = Scan()
    try:
        base = os.path.realpath(project_dir)
        base_fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
    except (OSError, ValueError, TypeError):
        return res
    res.base, res.base_fd = base, base_fd
    n_files = n_bytes = 0
    sig: list[tuple] = []
    full = False
    ok = False
    try:
        for root in ROOTS:
            _check(deadline)
            try:
                top = _open_chain(base_fd, root.split("/"))  # a link anywhere on the way fails here
            except OSError:
                continue
            found: list[tuple[str, int, int]] = []
            try:
                _list(top, root, 0, root.startswith("prisma"), found, deadline)
            finally:
                os.close(top)
            found.sort(key=lambda f: natural_key(f[0]))
            kept = []
            for rel, size, mtime in found:
                if full:
                    break
                if n_files >= MAX_FILES:
                    if LIMIT_FILES not in res.limits:
                        res.limits.append(LIMIT_FILES)
                    full = True
                    break
                if n_bytes + size > MAX_BYTES:
                    if LIMIT_BYTES not in res.limits:
                        res.limits.append(LIMIT_BYTES)
                    full = True  # stop reading: no gaps in the middle
                    break
                n_files += 1
                n_bytes += size
                kept.append((rel, size, mtime))
                sig.append((rel, size, mtime))
            if kept:
                res.roots.append((root, kept))
        ok = True
    finally:
        if not ok:
            res.close()
    res.signature = tuple(sig)
    return res


def _read_one(base_fd: int, rel: str, cap: int):
    """(text, size) of a regular file below ``base_fd`` (size from fstat); (None, size) when over the cap;
    None when it cannot be opened as a regular file."""
    parts = rel.split("/")
    try:
        parent = _open_chain(base_fd, parts[:-1])
    except OSError:
        return None
    try:
        fd = os.open(parts[-1], _FILE, dir_fd=parent)
    except OSError:
        return None
    finally:
        os.close(parent)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None
        if st.st_size > cap:
            return (None, st.st_size)
        with os.fdopen(fd, "rb", closefd=False) as f:
            data = f.read(cap + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    if len(data) > cap:
        return (None, len(data))
    return (data.decode("utf-8", errors="replace"), st.st_size)


def read(sc: Scan, deadline: float | None = None, accept_changed: bool = False) -> Collected:
    """Read what the scan listed, through the scan's own folder descriptor (never reopened by path).

    A file whose size no longer matches the listing is named in ``changed``; with ``accept_changed`` it is read
    as it is now (capped at 5 MB) and a ``limits`` line says so; otherwise the caller rescans."""
    res = Collected(limits=list(sc.limits), signature=sc.signature)
    if not sc.roots or sc.base_fd < 0:
        return res
    _after_list()
    for root, entries in sc.roots:
        rf = RootFiles(root)
        for rel, size, _ in entries:
            _check(deadline)
            got = _read_one(sc.base_fd, rel, MAX_BYTES)
            if got is None:
                continue
            text, now = got
            if now != size:
                res.changed.append(rel)
                if not accept_changed:
                    continue
                res.limits.append(f"{rel} changed while it was being read.")
            if text is not None:
                rf.files.append((rel, text))
        if rf.files:
            res.roots.append(rf)
    return res


def collect(project_dir: str, deadline: float | None = None) -> Collected:
    """Scan and read; a changed file triggers one rescan, then is read as it is."""
    sc = scan(project_dir, deadline)
    try:
        res = read(sc, deadline)
        if res.changed:
            sc.close()
            sc = scan(project_dir, deadline)
            res = read(sc, deadline, accept_changed=True)
        return res
    finally:
        sc.close()
