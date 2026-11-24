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
DIRS = ("", "src", "frontend", os.path.join("frontend", "src"))
BRAND_FILES = (os.path.join(".claude", "skills", "brand", "SKILL.md"), os.path.join("skills", "brand", "SKILL.md"))
BRAND_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
MAX_ENTRIES = 2000  # directory entries looked at per folder


@dataclass
class Detected:
    has_ui: bool = False
    react: bool = False
    brand_skill_found: bool = False


def valid_brand(name) -> bool:
    return isinstance(name, str) and BRAND_RE.fullmatch(name) is not None


def _read(path: str) -> bytes | None:
    """A regular, non-link file read up to the cap; None when missing, a link, or over the cap."""
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
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


def _is_dir(path: str) -> bool:
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def _deps(path: str) -> set[str]:
    data = _read(path)
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


def _has_ui_file(folder: str) -> bool:
    try:
        with os.scandir(folder) as it:
            for n, entry in enumerate(it):
                if n >= MAX_ENTRIES:
                    return False
                if entry.name.lower().endswith(UI_ENDINGS) and entry.is_file(follow_symlinks=False):
                    return True
    except OSError:
        pass
    return False


def _detect(root: str) -> Detected:
    deps: set[str] = set()
    for pkg in ("package.json", os.path.join("frontend", "package.json")):
        deps |= _deps(os.path.join(root, pkg))
    has_ui = bool(deps & UI_DEPS)
    if not has_ui:
        for d in DIRS:
            folder = os.path.join(root, d) if d else root
            if d and not _is_dir(folder):
                continue
            if _has_ui_file(folder):
                has_ui = True
                break
    brand = any(_read(os.path.join(root, f)) is not None for f in BRAND_FILES)
    return Detected(has_ui=has_ui, react=bool(deps & REACT_DEPS), brand_skill_found=brand)


def detect(project_dir: str | None) -> Detected:
    """What the folder says about a user interface; all false when there is no usable folder. Never raises."""
    try:
        if not project_dir or not os.path.isdir(project_dir):
            return Detected()
        return _detect(project_dir)
    except Exception:
        return Detected()
