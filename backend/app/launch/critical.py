"""Critical paths (phase 5b, F64): which files a run changed, and which of the project's critical globs they hit.
Shell-command edits are not seen, only the Edit/Write/MultiEdit/NotebookEdit tool calls."""

from __future__ import annotations

import os

from ..decisions import globmatch

EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
MAX_PATHS = 10


def changed_paths(events: list[dict], project_dir: str) -> list[str]:
    """Relative, de-duplicated paths (in order) of the files the edit tool calls targeted. Each event is a
    ``tool_use`` payload ``{name, input}``. Paths outside the project directory are dropped."""
    if not project_dir:
        return []
    root = os.path.realpath(project_dir)
    out: list[str] = []
    seen: set[str] = set()
    for ev in events:
        if not isinstance(ev, dict) or ev.get("name") not in EDIT_TOOLS:
            continue
        inp = ev.get("input")
        if not isinstance(inp, dict):
            continue
        raw = inp.get("notebook_path") if ev["name"] == "NotebookEdit" else inp.get("file_path")
        if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
            continue
        full = os.path.realpath(raw if os.path.isabs(raw) else os.path.join(root, raw))
        rel = os.path.relpath(full, root) if full == root or full.startswith(root + os.sep) else None
        if rel is None or rel == "." or rel == ".." or rel.startswith(".." + os.sep):
            continue
        rel = rel.replace(os.sep, "/")
        if rel not in seen:
            seen.add(rel)
            out.append(rel)
    return out


def hits(paths: list[str], globs: list[str]) -> tuple[list[str], list[str]]:
    """``(paths that match some glob, globs that matched some path)``, both in input order."""
    hit_paths = [p for p in paths if any(globmatch.matches(g, p) for g in globs)]
    hit_globs = [g for g in globs if any(globmatch.matches(g, p) for p in paths)]
    return hit_paths, hit_globs


PREFIX_MIN = 3


def prefixes(critical_paths) -> list[str]:
    """The literal prefix of each glob (up to its first wildcard, trailing ``/`` stripped), lower-cased,
    unique, in order; prefixes shorter than 3 characters are dropped."""
    out: list[str] = []
    for g in critical_paths or []:
        if not isinstance(g, str):
            continue
        cut = min((g.find(c) for c in "*?[{" if c in g), default=len(g))
        pre = g[:cut].rstrip("/").lower()
        if len(pre) >= PREFIX_MIN and pre not in out:
            out.append(pre)
    return out
