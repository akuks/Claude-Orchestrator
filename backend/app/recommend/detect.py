"""Does a project have a user interface? A bounded, link-safe look at its folder (never raises)."""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass

PACKAGE_CAP = 256 * 1024
UI_DEPS = frozenset({"react", "next", "vue", "nuxt", "svelte", "@sveltejs/kit", "@angular/core", "solid-js",
                     "preact", "astro", "vite"})
REACT_DEPS = frozenset({"react", "next", "preact"})
UI_ENDINGS = (".html", ".tsx", ".jsx", ".vue", ".svelte")
DIRS = ((), ("src",), ("frontend",), ("frontend", "src"))
BRAND_FILES = ((".claude", "skills", "brand", "SKILL.md"), ("skills", "brand", "SKILL.md"))
BRAND_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
TIMEOUT = 2.0  # seconds a caller waits for detect()
MAX_ENTRIES = 5000  # directory entries looked at per folder; a UI file listed later is not seen


@dataclass
class Detected:
    has_ui: bool = False
    react: bool = False
    brand_skill_found: bool = False


def valid_brand(name) -> bool:
    return isinstance(name, str) and BRAND_RE.fullmatch(name) is not None


_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def _open_dir(parts: tuple[str, ...], root_fd: int) -> int | None:
    """A descriptor for the folder `parts` below the root, every step opened with O_NOFOLLOW relative to its
    parent's descriptor (a link anywhere on the way means None). The caller closes it."""
    fd = os.dup(root_fd)
    for part in parts:
        try:
            nxt = os.open(part, _DIR, dir_fd=fd)
        except OSError:
            os.close(fd)
            return None
        os.close(fd)
        fd = nxt
    return fd


def _read_in(dirfd: int, name: str) -> bytes | None:
    """A regular, non-link file of the folder `dirfd`, read up to the cap; None when missing, a link, or over it."""
    try:
        fd = os.open(name, _FILE, dir_fd=dirfd)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb", closefd=False) as f:
            data = f.read(PACKAGE_CAP + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    return None if len(data) > PACKAGE_CAP else data


def _file_at(root_fd: int, parts: tuple[str, ...]) -> bytes | None:
    d = _open_dir(parts[:-1], root_fd)
    if d is None:
        return None
    try:
        return _read_in(d, parts[-1])
    finally:
        os.close(d)


def _deps(root_fd: int, parts: tuple[str, ...]) -> set[str]:
    data = _file_at(root_fd, parts)
    if data is None:
        return set()
    try:
        doc = json.loads(data.decode("utf-8", errors="replace"))
    except ValueError:
        return set()
    out: set[str] = set()
    if isinstance(doc, dict):
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            block = doc.get(key)
            if isinstance(block, dict):
                out.update(k for k in block if isinstance(k, str))
    return out


def _has_ui_file(root_fd: int, parts: tuple[str, ...]) -> bool:
    d = _open_dir(parts, root_fd)
    if d is None:
        return False
    try:
        with os.scandir(d) as it:
            for n, entry in enumerate(it):
                if n >= MAX_ENTRIES:
                    return False  # beyond the cap the rest of the folder is not looked at
                if entry.name.lower().endswith(UI_ENDINGS) and entry.is_file(follow_symlinks=False):
                    return True
    except OSError:
        pass
    finally:
        os.close(d)
    return False


def _detect(root_fd: int) -> Detected:
    deps = _deps(root_fd, ("package.json",)) | _deps(root_fd, ("frontend", "package.json"))
    has_ui = bool(deps & UI_DEPS)
    if not has_ui:
        has_ui = any(_has_ui_file(root_fd, parts) for parts in DIRS)
    brand = any(_file_at(root_fd, parts) is not None for parts in BRAND_FILES)
    return Detected(has_ui=has_ui, react=bool(deps & REACT_DEPS), brand_skill_found=brand)


def detect(project_dir: str | None) -> Detected:
    """What the folder says about a user interface; all false when there is no usable folder. Never raises."""
    try:
        if not project_dir:
            return Detected()
        root_fd = os.open(project_dir, os.O_RDONLY | os.O_DIRECTORY)  # the project folder itself may be a link
        try:
            return _detect(root_fd)
        finally:
            os.close(root_fd)
    except Exception:
        return Detected()
