"""Validation and inheritance of a task brief (phase 4c).

A brief says what a task is for and where it may work: a goal, the paths it CAN and CANNOT change, when it
is done and when it must stop. ``clean_brief`` checks and normalises one (ValueError with a plain reason,
which the routes return as a 422); ``child_scope`` combines a parent's brief with a child's own, keeping the
parent's scope as a bound the child cannot widen."""

from __future__ import annotations

import re

from ..decisions.validate import one_line, path_list
from ..redaction import clean_text, redact

FIELDS = ("goal", "can_change", "cannot_change", "done", "stop", "setup", "log", "retries", "tie_break", "outputs")
REQUIRED = ("goal", "can_change", "cannot_change", "done", "stop")
MAX_BOUNDS = 4
MAX_GLOBS, MAX_WILDCARDS = 20, 30
MAX_OUTPUTS, MAX_OUTPUT = 10, 60
_TEXT_LIMITS = {"goal": 2000, "done": 1000, "stop": 1000, "setup": 1000, "log": 500, "tie_break": 500}
_GLOBS = ("can_change", "cannot_change")


def _text(raw, field: str) -> str:
    if not isinstance(raw, str):
        raise ValueError(f"{field} must be text")
    text = "\n".join(redact(clean_text(raw), []).strip().splitlines())
    if len(text) > _TEXT_LIMITS[field]:
        raise ValueError(f"{field} may be at most {_TEXT_LIMITS[field]} characters")
    return text


def _outputs(raw) -> list[str]:
    if not isinstance(raw, (list, tuple)):
        raise ValueError("outputs must be a list of names")
    if len(raw) > MAX_OUTPUTS:
        raise ValueError(f"outputs may have at most {MAX_OUTPUTS} names")
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError("outputs entries must be text")
        name = one_line(redact(clean_text(item), []))
        if len(name) > MAX_OUTPUT:
            raise ValueError(f"an outputs name may be at most {MAX_OUTPUT} characters")
        if name and name not in out:
            out.append(name)
    return out


def clean_brief(raw) -> dict | None:
    """The brief, validated and normalised (every field present; optional ones None or []), or None."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("the brief must be an object")
    for key in raw:
        if key not in FIELDS:
            raise ValueError(f"unknown brief field: {key}")
    out: dict = {}
    for f in FIELDS:
        value = raw.get(f)
        if f in _GLOBS:
            out[f] = None if value is None else path_list(
                value, f"a {f} entry", MAX_GLOBS, max_wildcards=MAX_WILDCARDS)
        elif f == "outputs":
            out[f] = [] if value is None else _outputs(value)
        elif f == "retries":
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)
                                      or not 0 <= value <= 10):
                raise ValueError("retries must be a whole number from 0 to 10")
            out[f] = value
        else:
            out[f] = None if value is None else (_text(value, f) or None)
    for f in REQUIRED:
        if out[f] is None:
            raise ValueError(f"the brief needs {f}")
    if re.sub(r"[\W_]+", "", out["stop"]).lower() == "never":
        raise ValueError('stop must say when to stop, not "never"')
    return out


def _scope(task_id: str, brief: dict) -> dict:
    return {"task_id": task_id, "can_change": list(brief.get("can_change") or []),
            "cannot_change": list(brief.get("cannot_change") or [])}


def child_scope(parent_brief: dict | None, parent_bounds: list | None, parent_id: str,
                own: dict | None) -> tuple[dict | None, list]:
    """(brief, bounds) for a child of a task. No parent brief: the child's own, unbounded. No own brief: the
    parent's. Both: the child's own, bound by the parent's scope plus the parent's bounds (an identical
    scope once, at most MAX_BOUNDS)."""
    if not parent_brief:
        return own, []
    if own is None:
        return parent_brief, list(parent_bounds or [])
    bounds = [dict(b) for b in (parent_bounds or [])]
    mine = _scope(parent_id, parent_brief)
    key = (mine["can_change"], mine["cannot_change"])
    if all((b.get("can_change"), b.get("cannot_change")) != key for b in bounds):
        bounds.append(mine)
    if len(bounds) > MAX_BOUNDS:
        raise ValueError("briefs nest at most 5 deep")
    return own, bounds
