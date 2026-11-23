"""A brief's scope in the co-guard policy (phase 4c).

``layers`` is the task's own brief plus every enclosing bound (a path must be inside every non-empty CAN and
outside every CANNOT); ``apply`` puts them under the policy's ``brief`` key (read by the hook for file tools)
and turns the CANNOT globs that name a folder or file into readonly roots, which the rules engine's
readonly-source rule applies to shell commands."""

from __future__ import annotations

import os


def layers(task_id: str, brief: dict, bounds: list) -> list[dict]:
    """[{"task", "can", "cannot"}], the task's own brief first."""
    out = [{"task": task_id, "can": list(brief.get("can_change") or []),
            "cannot": list(brief.get("cannot_change") or [])}]
    for b in bounds or []:
        out.append({"task": b.get("task_id"), "can": list(b.get("can_change") or []),
                    "cannot": list(b.get("cannot_change") or [])})
    return out


def shell_root_prefix(glob: str) -> str | None:
    """The path a shell command can be checked against: the glob itself when it has no wildcard, or the
    literal part of ``<literal>/**``; None when the glob is for file tools only."""
    if not isinstance(glob, str):
        return None
    prefix = glob[:-3] if glob.endswith("/**") else glob
    prefix = prefix.rstrip("/")
    if not prefix or any(c in prefix for c in "*?"):
        return None
    return prefix


def apply(policy: dict, layers: list[dict]) -> dict:
    policy["brief"] = {"layers": layers}
    ro = policy.setdefault("readonly_paths", [])
    base = policy.get("project_dir") or ""
    seen = {e.get("path") for e in ro if isinstance(e, dict)}
    counted: set[str] = set()  # distinct roots in force, whether added here or already there
    only, outside = [], []
    project_real = os.path.realpath(base)
    done: set[str] = set()
    for layer in layers:
        for glob in layer.get("cannot") or []:
            if glob in done:
                continue
            done.add(glob)
            prefix = shell_root_prefix(glob)
            if prefix is None:
                only.append(glob)
                continue
            path = os.path.realpath(os.path.join(base, prefix))
            if path != project_real and not path.startswith(project_real.rstrip("/") + "/"):
                only.append(glob)  # a symlink out of the project: not ours to make readonly
                outside.append(glob)
                continue
            counted.add(path)
            if path in seen:
                continue
            seen.add(path)
            ro.append({"path": path, "reason": f"the task's brief says CANNOT change {glob}", "source": "brief"})
    ev = {"event": "brief_scope", "shell_roots": len(counted), "file_tools_only": only}
    if outside:
        ev["outside_project"] = outside
    return ev
