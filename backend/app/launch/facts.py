"""Launch facts (phase 5b): plain numbers for the task form and the agent, no judgement. The estimate rule
(at least 10 finished items) and the median-of-days idea come from forge's `launch facts`."""

from __future__ import annotations

import statistics
from datetime import timezone

from ..decisions import globmatch
from . import store

ESTIMATE_MIN = 10
MAX_FILES = 50
_DAY = 86400.0


def clean_files(files: list[str]) -> list[str]:
    """Relative paths only, no `..` segment, at most 50. Raises ValueError with a plain reason."""
    if len(files) > MAX_FILES:
        raise ValueError(f"files may have at most {MAX_FILES} entries")
    out: list[str] = []
    for f in files:
        f = f.strip()
        if not f:
            continue
        if len(f) > 300 or "\x00" in f or "\\" in f:
            raise ValueError("files must be relative paths of at most 300 characters")
        if f.startswith(("/", "~")) or (len(f) > 1 and f[1] == ":"):
            raise ValueError("files must be relative paths")
        if ".." in f.split("/"):
            raise ValueError("files may not contain '..'")
        out.append(f)
    return out


def _aware(d):
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


def estimate(items) -> dict | None:
    spans = []
    for i in items:
        if i.started_at is None or i.done_at is None:
            continue
        days = round((_aware(i.done_at) - _aware(i.started_at)).total_seconds() / _DAY)
        if days >= 0:
            spans.append(int(days))
    if len(spans) < ESTIMATE_MIN:
        return None
    return {"median_days": statistics.median(spans), "min_days": min(spans), "max_days": max(spans), "n": len(spans)}


async def compute(s, project_id: str, files: list[str]) -> dict:
    row = await store.get_or_none(s, project_id)
    items = await store.launch_items_for(s, project_id)
    before = [i for i in items if i.section == "before"]
    globs = list(row.critical_paths or []) if row is not None else []
    touched = [g for g in globs if any(globmatch.matches(g, f) for f in files)]
    return {
        "launch_date": row.launch_date if row is not None else None,
        "open": sum(1 for i in before if i.status in ("open", "started")),
        "done": sum(1 for i in before if i.status == "done"),
        "would_be": max((i.number for i in items), default=0) + 1,
        "after": [f"#{i.number} {i.title}" for i in items if i.section == "after" and i.status in ("open", "started")],
        "critical_touched": touched,
        "estimate": estimate(items),
    }
