"""Runs the health checks: concurrently, each under its own budget, scrubbed,
cached for 30 s per process."""

from __future__ import annotations

import asyncio
import os
import re
import time
from datetime import datetime, timezone

from ..config import settings
from ..guard.runtime import real_home
from ..redaction import redact, secrets_from_env
from . import checks as checks_mod
from .checks import ATTENTION, OK, PROBLEM, Result

CHECK_BUDGET = 10.0  # seconds per check
TOTAL_BUDGET = 60.0  # seconds for the whole call
CACHE_SECONDS = 30.0
SCRUB_CAP = 500
COULD_NOT_CHECK = "could not check"
_ORDER = {OK: 0, ATTENTION: 1, PROBLEM: 2}
_HOME_RE = re.compile(r"(?:/Users|/home)/[^/\s\"']+")

_clock = time.monotonic  # tests replace it
_cache: tuple[float, dict] | None = None
_lock: asyncio.Lock | None = None
_lock_loop = None


def overall(checks: list[dict]) -> str:
    return max((c["status"] for c in checks), key=_ORDER.__getitem__, default=OK)


def overall_line(checks: list[dict]) -> str:
    status = overall(checks)
    n = sum(1 for c in checks if c["status"] == status)
    if status == OK:
        return "Everything looks good"
    if status == ATTENTION:
        return f"{n} thing{'s' if n != 1 else ''} need{'' if n != 1 else 's'} attention"
    return f"{n} problem{'s' if n != 1 else ''}"


def scrub(text) -> str:
    """No secrets, env values or home paths: known env secrets and token shapes
    are masked, the home path becomes ``~``, the result is cut to 500 chars."""
    text = "" if text is None else str(text)
    secrets = secrets_from_env(dict(os.environ))
    if settings.secret_key:
        secrets.append(settings.secret_key)
    text = redact(text, secrets)
    homes = {h for h in (real_home(), os.path.expanduser("~")) if h and h != "/"}
    for h in sorted(homes, key=len, reverse=True):
        text = text.replace(h, "~")
    text = _HOME_RE.sub("~", text)
    return text if len(text) <= SCRUB_CAP else text[: SCRUB_CAP - 1] + "…"


def _scrub_obj(obj):
    if isinstance(obj, str):
        return scrub(obj)
    if isinstance(obj, dict):
        return {str(k): _scrub_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_scrub_obj(v) for v in obj]
    return obj


def _entry(check, result: Result) -> dict:
    out = {"id": check.id, "title": check.title, "status": result.status, "summary": scrub(result.summary)}
    if result.detail:
        out["detail"] = _scrub_obj(result.detail)
    if result.fix:
        out["fix"] = scrub(result.fix)
    return out


def _failed(check, why: str) -> dict:
    return _entry(check, Result(PROBLEM, COULD_NOT_CHECK, {"why": why},
                                "Try again; if it keeps failing, look at the server log."))


async def _run_one(check, s) -> dict:
    try:
        result = await asyncio.wait_for(check.fn(s), CHECK_BUDGET)
        if not isinstance(result, Result) or result.status not in _ORDER:
            return _failed(check, "the check gave an unusable answer")
        return _entry(check, result)
    except asyncio.TimeoutError:
        return _failed(check, f"it did not answer within {CHECK_BUDGET:g} seconds")
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 - a broken check must not break the page
        return _failed(check, type(e).__name__)


async def _run_fresh(s) -> dict:
    items = list(checks_mod.CHECKS)
    tasks = [asyncio.ensure_future(_run_one(c, s)) for c in items]
    try:
        await asyncio.wait(tasks, timeout=TOTAL_BUDGET)
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
    entries = [t.result() if t.done() and not t.cancelled() and t.exception() is None
               else _failed(c, "it did not finish in time") for c, t in zip(items, tasks)]
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "overall": overall(entries), "checks": entries}


async def run_all(s=None, *, force: bool = False) -> dict:
    """The spec's response shape. ``s``: a session factory (default SessionLocal).
    ``force`` skips the 30 s cache."""
    global _cache, _lock, _lock_loop
    if s is None:
        from ..database import SessionLocal

        s = SessionLocal
    loop = asyncio.get_running_loop()
    if _lock is None or _lock_loop is not loop:
        _lock, _lock_loop = asyncio.Lock(), loop
    async with _lock:
        if not force and _cache is not None and _clock() - _cache[0] < CACHE_SECONDS:
            return _cache[1]
        data = await _run_fresh(s)
        _cache = (_clock(), data)
        return data


def clear_cache() -> None:
    global _cache
    _cache = None
