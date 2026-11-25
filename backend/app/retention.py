"""Tidying old run history (phase 7d, spec §2) - OFF until an admin switches it on.

The switch lives in ``GuardOrgSettings.retention`` (``{"enabled", "days", "last_run", "last_count"}``).
When on, the daily job (``main._retention_loop``) takes the tasks that **finished** (a terminal status with a
``completed_at``) more than ``days`` ago and still have run events other than a summary, and in batches of
``BATCH`` tasks (a short pause between batches):

* replaces their task events with ONE ``summary`` event (``SUMMARY``), whose payload is
  ``{"events", "counts", "first_at", "last_at", "lines", "kept_days", "tidied_at"}``: the number of events it
  stands for, counts by event type, the first and last event time (ISO), the last ``LINES_KEPT`` lines of the
  run's Live Output as plain text (each at most ``LINE_MAX`` characters) and when / with which setting it was
  made. A task that gained events after its summary (a Retry) is folded again: the old summary's figures are
  added in. It takes the seq of the newest event it replaces, so later events still come after it.
* for closed handoffs not saved for ``days`` (so every step of the thread finished before then) and with no
  active step: drops the previous version (``prev_content``, ``prev_text``), keeping the current one.

It never touches a task that is not finished, and never deletes a task, handoff, note or decision."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import Text, cast, delete, exists, func, or_, select

from .constants import Status
from .database import SessionLocal
from .models import GuardOrgSettings, Task, TaskEvent, TaskHandoff
from .redaction import clean_text, redact

log = logging.getLogger(__name__)

SUMMARY = "summary"
CHOICES = (30, 60, 90)
DEFAULT_DAYS = 60
BATCH = 200
PAUSE_SECONDS = 0.5
LINES_KEPT = 20
LINE_MAX = 300
LINE_SCAN = 400  # newest Live Output events read per task for its lines
ROW_OVERHEAD = 64  # rough bytes per event row beyond its payload (the preview's estimate)
DUE_HOURS = 23  # the daily job runs when the last run is at least this old
PREVIEW_CACHE_SECONDS = 60

_LIVE_TYPES = ("text_output", "tool_use", "tool_result", "error", "completed", "started")
_state = {"running": False}
_preview_cache: dict = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt):
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return None
    if isinstance(dt, datetime) and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt if isinstance(dt, datetime) else None


def _iso(dt) -> str | None:
    dt = _aware(dt)
    return dt.isoformat() if dt else None


def is_running() -> bool:
    return _state["running"]


# ---- the switch ---------------------------------------------------------------------------------------


def _normalized(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    days = raw.get("days")
    count = raw.get("last_count")
    return {
        "enabled": raw.get("enabled") is True,
        "days": days if days in CHOICES and not isinstance(days, bool) else DEFAULT_DAYS,
        "last_run": raw.get("last_run") if isinstance(raw.get("last_run"), str) else None,
        "last_count": count if isinstance(count, int) and not isinstance(count, bool) else None,
    }


async def _row(s) -> GuardOrgSettings:
    row = await s.get(GuardOrgSettings, 1)
    if row is None:
        row = GuardOrgSettings(id=1, disabled_rules=[], dev_db_patterns=[])
        s.add(row)
    return row


async def load_settings() -> dict:
    async with SessionLocal() as s:
        row = await s.get(GuardOrgSettings, 1)
        return _normalized(row.retention if row is not None else None)


async def save_settings(*, enabled: bool | None = None, days: int | None = None, by: str | None = None) -> dict:
    """Change the switch and/or the days (30, 60 or 90; ValueError otherwise); the last run is kept."""
    if days is not None and (isinstance(days, bool) or days not in CHOICES):
        raise ValueError("days must be 30, 60 or 90")
    async with SessionLocal() as s:
        row = await _row(s)
        cur = _normalized(row.retention)
        if enabled is not None:
            cur["enabled"] = bool(enabled)
        if days is not None:
            cur["days"] = days
        row.retention = {**cur, "updated_by": by}  # a new dict: the JSON column sees the change
        await s.commit()
    _preview_cache.clear()
    return cur


async def _record_run(count: int) -> None:
    async with SessionLocal() as s:
        row = await _row(s)
        raw = row.retention if isinstance(row.retention, dict) else {}
        row.retention = {**raw, "last_run": _now().isoformat(), "last_count": count}
        await s.commit()


# ---- what would be tidied ---------------------------------------------------------------------------


def _eligible(cutoff: datetime, only=None):
    """Finished before ``cutoff`` and still holding events other than a summary."""
    raw = exists().where(TaskEvent.task_id == Task.id, TaskEvent.type != SUMMARY)
    q = select(Task.id).where(Task.status.in_(Status.TERMINAL), Task.completed_at.is_not(None),
                              Task.completed_at < cutoff, raw)
    if only is not None:
        q = q.where(Task.id.in_(list(only)))
    return q


async def preview(days: int, only=None) -> dict:
    """``{"tasks", "bytes"}``: how many tasks would be tidied and roughly how much their events take."""
    key = (days, None if only is None else tuple(sorted(only)))
    hit = _preview_cache.get(key)
    if hit is not None and time.monotonic() - hit[0] < PREVIEW_CACHE_SECONDS:
        return dict(hit[1])
    cutoff = _now() - timedelta(days=days)
    ids = _eligible(cutoff, only).subquery()
    async with SessionLocal() as s:
        tasks = (await s.execute(select(func.count()).select_from(ids))).scalar() or 0
        size = (await s.execute(
            select(func.coalesce(func.sum(func.length(cast(TaskEvent.payload, Text))), 0), func.count())
            .where(TaskEvent.task_id.in_(select(ids.c.id))))).one()
    out = {"tasks": int(tasks), "bytes": int(size[0] or 0) + ROW_OVERHEAD * int(size[1] or 0)}
    _preview_cache[key] = (time.monotonic(), out)
    return dict(out)


# ---- the summary ----------------------------------------------------------------------------------------


def live_line(etype: str, p: dict) -> str | None:
    """One Live Output line as plain text (as the run view renders it), or None for an event without text."""
    p = p if isinstance(p, dict) else {}
    if etype == "text_output":
        text = p.get("text")
    elif etype == "tool_use":
        inp = p.get("input")
        text = f"{p.get('name') or ''}({json.dumps(inp, default=str, ensure_ascii=False) if inp else ''})"
    elif etype == "tool_result":
        text = p.get("content")
        text = text if isinstance(text, str) else json.dumps(text, default=str, ensure_ascii=False)
    elif etype == "error":
        text = p.get("message")
    elif etype == "completed":
        text = f"completed — {p.get('status') or ''}"
    elif etype == "started":
        text = f"started ({p.get('model') or ''})"
    else:
        return None
    text = " ".join(str(text or "").split())
    if not text:
        return None
    return clean_text(redact(text, []), LINE_MAX)


async def _summarise(s, tid: str, cutoff: datetime, days: int) -> bool:
    groups = (await s.execute(
        select(TaskEvent.type, func.count(), func.min(TaskEvent.created_at), func.max(TaskEvent.created_at),
               func.max(TaskEvent.seq))
        .where(TaskEvent.task_id == tid).group_by(TaskEvent.type))).all()
    if not any(g[0] != SUMMARY for g in groups):
        return False
    top = max(g[4] or 0 for g in groups)
    prev = (await s.execute(select(TaskEvent.payload).where(TaskEvent.task_id == tid, TaskEvent.type == SUMMARY)
                            .order_by(TaskEvent.seq))).scalars().all()
    counts: dict[str, int] = {}
    total = 0
    firsts, lasts, lines = [], [], []
    for p in prev:  # an earlier summary's figures (a Retry added events after it)
        p = p if isinstance(p, dict) else {}
        for k, v in (p.get("counts") or {}).items():
            if isinstance(v, int) and not isinstance(v, bool):
                counts[str(k)] = counts.get(str(k), 0) + v
        if isinstance(p.get("events"), int):
            total += p["events"]
        firsts.append(_aware(p.get("first_at")))
        lasts.append(_aware(p.get("last_at")))
        lines += [x for x in (p.get("lines") or []) if isinstance(x, str)]
    for etype, n, first, last, _ in groups:
        if etype == SUMMARY:
            continue
        counts[etype] = counts.get(etype, 0) + int(n)
        total += int(n)
        firsts.append(_aware(first))
        lasts.append(_aware(last))
    recent = (await s.execute(
        select(TaskEvent.type, TaskEvent.payload).where(TaskEvent.task_id == tid, TaskEvent.type.in_(_LIVE_TYPES))
        .order_by(TaskEvent.seq.desc()).limit(LINE_SCAN))).all()
    new_lines = [ln for ln in (live_line(t, p) for t, p in reversed(recent)) if ln]
    lines = (lines + new_lines)[-LINES_KEPT:]
    firsts, lasts = [d for d in firsts if d], [d for d in lasts if d]
    payload = {
        "events": total,
        "counts": dict(sorted(counts.items())),
        "first_at": _iso(min(firsts)) if firsts else None,
        "last_at": _iso(max(lasts)) if lasts else None,
        "lines": lines,
        "kept_days": days,
        "tidied_at": _now().isoformat(),
    }
    # still finished (a Retry since the batch was chosen leaves it alone); only the events read above
    still = (await s.execute(select(Task.id).where(
        Task.id == tid, Task.status.in_(Status.TERMINAL), Task.completed_at.is_not(None),
        Task.completed_at < cutoff))).first()
    if still is None:
        return False
    await s.execute(delete(TaskEvent).where(TaskEvent.task_id == tid, TaskEvent.seq <= top))
    s.add(TaskEvent(task_id=tid, seq=top, type=SUMMARY, payload=payload))
    return True


async def _prune_handoffs(cutoff: datetime, only=None) -> int:
    active = exists().where(or_(Task.root_id == TaskHandoff.root_id, Task.id == TaskHandoff.root_id),
                            Task.status.in_(Status.ACTIVE))
    # prev_text (Text) is set with prev_content and is a real NULL once dropped (a JSON None may be stored
    # as JSON null), so each row leaves the set when committed
    q = select(TaskHandoff).where(TaskHandoff.status == "closed", TaskHandoff.saved_at < cutoff,
                                  TaskHandoff.prev_text.is_not(None), ~active).order_by(TaskHandoff.id)
    if only is not None:
        q = q.where(TaskHandoff.root_id.in_(list(only)))
    n = 0
    while True:
        async with SessionLocal() as s:
            rows = (await s.execute(q.limit(BATCH))).scalars().all()
            for row in rows:
                row.prev_content = None
                row.prev_text = None
            await s.commit()
        n += len(rows)
        if len(rows) < BATCH:
            return n
        await asyncio.sleep(PAUSE_SECONDS)


async def tidy(days: int, only=None) -> dict:
    """Tidy now with ``days`` (no switch check: the callers check it). ``only``: limit to these task ids
    (and handoffs of these thread roots). ``{"tasks", "batches", "handoffs"}``."""
    cutoff = _now() - timedelta(days=days)
    async with SessionLocal() as s:
        ids = (await s.execute(_eligible(cutoff, only).order_by(Task.completed_at, Task.id))).scalars().all()
    done = batches = 0
    for i in range(0, len(ids), BATCH):
        if i:
            await asyncio.sleep(PAUSE_SECONDS)
        batches += 1
        async with SessionLocal() as s:
            for tid in ids[i:i + BATCH]:
                if await _summarise(s, tid, cutoff, days):
                    done += 1
            await s.commit()
    handoffs = await _prune_handoffs(cutoff, only)
    _preview_cache.clear()
    return {"tasks": done, "batches": batches, "handoffs": handoffs}


# ---- the job ---------------------------------------------------------------------------------------------


async def run_if_enabled() -> dict | None:
    """One tidying pass with the stored days when the switch is on (else None, touching nothing); one at a
    time. Records the last run."""
    cfg = await load_settings()
    if not cfg["enabled"] or _state["running"]:
        return None
    _state["running"] = True
    try:
        out = await tidy(cfg["days"])
        await _record_run(out["tasks"])
        log.info("retention: tidied %s task(s), %s handoff(s)", out["tasks"], out["handoffs"])
        return out
    finally:
        _state["running"] = False


async def run_if_due() -> dict | None:
    """The daily job: a pass when the switch is on and the last one is at least DUE_HOURS old."""
    cfg = await load_settings()
    if not cfg["enabled"]:
        return None
    last = _aware(cfg["last_run"])
    if last is not None and _now() - last < timedelta(hours=DUE_HOURS):
        return None
    return await run_if_enabled()
