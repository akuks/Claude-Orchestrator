"""The usage report (phase 3b): the step's figures, the thread's "so far" totals and up to three
tips, built from stored rows (``Task``, ``TaskUsage``) by ``GET /tasks/{id}/usage``."""

from ..constants import Status
from .tracker import reread_per_step

TIP_NARROW = "A tool result over 20,000 characters came back; read narrower ranges or filter output."
TIP_FRESH = "Context got large; split long work across fresh sessions (set a fresh-session threshold on the project)."
TIP_FRESH_SET = "Context got large; this project's fresh-session threshold will hand long work to a fresh session."
TIP_CACHE = "Under half of the input came from the cache; keep the start of prompts stable."

# phase 7e (F96)
TIP_REREAD = "Each step re-reads about {n} tokens of earlier conversation."
TIP_HELPER = "Helpers of type {kind} average {n} steps each — give them a narrower brief."
FRESH_SAVING = "A fresh session would save about {m} tokens over the next 50 steps."
REREAD_TIP_MIN = 20_000
REREAD_SAVING_MIN = 150_000  # above it the fresh-session tip says what a fresh session would save
REREAD_FRESH_BASE = 30_000  # what a fresh session re-reads per step
SAVING_STEPS = 50
HELPER_STEPS_MAX = 40

BIG_RESULT_CHARS = 20_000
DEFAULT_FRESH_TOKENS = 150_000
CACHE_MIN_MESSAGES = 20


def _buckets(breakdown: dict) -> list[dict]:
    bd = breakdown or {}
    return [bd.get("main") or {}, *((bd.get("helpers") or {}).values())]


def breakdown_tokens(breakdown: dict) -> int:
    """input + cache write + cache read + output over the main and helper buckets: the basis of the stored
    Task.input_tokens (cache included) + Task.output_tokens, so a step's figure does not jump when it ends."""
    return sum(int(b.get(f) or 0) for b in _buckets(breakdown)
               for f in ("input", "cache_write", "cache_read", "output"))


def _thousands(n: int) -> str:
    return f"{int(round(n, -3)):,}"


def reread_tip(n: int) -> str:
    return TIP_REREAD.format(n=_thousands(n))


def helper_tip(kind: str, n: int) -> str:
    return TIP_HELPER.format(kind=kind, n=n)


def step_reread(usages: list) -> int:
    """The main thread's re-read per step (the largest over the rows), from the stored figures, so a breakdown
    stored before phase 7e works too."""
    return max((reread_per_step((u.breakdown or {}).get("main") or {}) for u in usages), default=0)


def _long_helper(usages: list) -> tuple[str, int] | None:
    """The helper kind with the most steps per start, when over HELPER_STEPS_MAX (a breakdown without
    ``starts`` - stored before phase 7e - gives none)."""
    best = None
    for u in usages:
        for kind, b in ((u.breakdown or {}).get("helpers") or {}).items():
            starts = b.get("starts") if isinstance(b, dict) else None
            if not isinstance(starts, int) or isinstance(starts, bool) or starts <= 0:
                continue
            avg = round(int(b.get("messages") or 0) / starts)
            if avg > HELPER_STEPS_MAX and (best is None or avg > best[1]):
                best = (str(kind), avg)
    return best


def tips(usages: list, fresh_tokens: int | None) -> list[str]:
    """The re-read tip (always, when a step re-reads 20k+ tokens) then up to three others over the given
    TaskUsage rows (the asked step's)."""
    out: list[str] = []
    reread = step_reread(usages)
    if any((r.get("chars") or 0) > BIG_RESULT_CHARS
           for u in usages for r in ((u.breakdown or {}).get("largest_results") or [])):
        out.append(TIP_NARROW)
    if any((u.peak_context or 0) > (fresh_tokens or DEFAULT_FRESH_TOKENS) for u in usages):
        tip = TIP_FRESH if fresh_tokens is None else TIP_FRESH_SET
        if reread > REREAD_SAVING_MIN:
            tip += " " + FRESH_SAVING.format(m=_thousands((reread - REREAD_FRESH_BASE) * SAVING_STEPS))
        out.append(tip)
    if (long := _long_helper(usages)) is not None:
        out.append(helper_tip(*long))
    for u in usages:
        bd = u.breakdown or {}
        if (bd.get("messages") or 0) < CACHE_MIN_MESSAGES:
            continue
        bs = _buckets(bd)
        read = sum(int(b.get("cache_read") or 0) for b in bs)
        total = sum(int(b.get("input") or 0) + int(b.get("cache_write") or 0) + int(b.get("cache_read") or 0)
                    for b in bs)
        if total > 0 and read < 0.5 * total:
            out.append(TIP_CACHE)
            break
    return ([reread_tip(reread)] if reread >= REREAD_TIP_MIN else []) + out[:3]


def build(task, usage, steps: list, usages: list, fresh_tokens: int | None) -> dict:
    """``task``/``usage``: the asked step and its TaskUsage (or None); ``steps``: the thread's steps
    oldest first; ``usages``: the thread's TaskUsage rows."""
    by_id = {u.task_id: u for u in usages}
    live = task.status in Status.ACTIVE
    est = usage.est_cost_usd if usage is not None else None
    # a finished step's stored result cost wins over the running estimate
    cost = est if live and est is not None else (task.total_cost_usd if task.total_cost_usd is not None else est)
    # an estimate while the step runs, or when the finished step reported no result cost
    estimate = live or task.total_cost_usd is None
    pct = None
    if task.max_budget_usd and cost is not None:
        pct = round(cost / task.max_budget_usd * 100, 1)
    step = {
        "context_tokens": usage.context_tokens if usage else None,
        "peak_context": usage.peak_context if usage else None,
        "est_cost_usd": cost,
        "cost_is_estimate": estimate,
        "max_budget_usd": task.max_budget_usd,
        "budget_pct": pct,
        "fresh_session_tokens": fresh_tokens,
        "breakdown": (usage.breakdown if usage else None) or None,
        "reread_per_step": step_reread([usage]) if usage else None,
    }
    dur = cost_sum = tokens = 0
    last = None
    for t in steps:
        u = by_id.get(t.id)
        if t.status in Status.ACTIVE:  # a running step has no stored totals yet: its live estimate stands in
            c = (u.est_cost_usd if u and u.est_cost_usd is not None else t.total_cost_usd) or 0.0
            tk = breakdown_tokens(u.breakdown) if u else 0
        else:
            c = t.total_cost_usd if t.total_cost_usd is not None else ((u.est_cost_usd or 0.0) if u else 0.0)
            tk = (t.input_tokens or 0) + (t.output_tokens or 0)
        d = t.duration_ms or 0
        dur += d
        cost_sum += c
        tokens += tk
        last = {"title": t.title, "duration_ms": d, "cost_usd": c}
    thread = {"steps": len(steps), "duration_ms": dur, "cost_usd": round(cost_sum, 6), "tokens": tokens,
              "last": last}
    return {"step": step, "thread": thread, "tips": tips([usage] if usage else [], fresh_tokens)}
