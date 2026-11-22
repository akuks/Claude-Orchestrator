"""The usage report (phase 3b): the step's figures, the thread's "so far" totals and up to three
tips, built from stored rows (``Task``, ``TaskUsage``) by ``GET /tasks/{id}/usage``."""

from ..constants import Status

TIP_NARROW = "A tool result over 20,000 characters came back; read narrower ranges or filter output."
TIP_FRESH = "Context got large; split long work across fresh sessions (set a fresh-session threshold on the project)."
TIP_CACHE = "Under half of the input came from the cache; keep the start of prompts stable."

BIG_RESULT_CHARS = 20_000
DEFAULT_FRESH_TOKENS = 150_000
CACHE_MIN_MESSAGES = 20


def _buckets(breakdown: dict) -> list[dict]:
    bd = breakdown or {}
    return [bd.get("main") or {}, *((bd.get("helpers") or {}).values())]


def breakdown_tokens(breakdown: dict) -> int:
    """input + output over the main and helper buckets (what Task.input/output_tokens count)."""
    return sum(int(b.get("input") or 0) + int(b.get("output") or 0) for b in _buckets(breakdown))


def tips(usages: list, fresh_tokens: int | None) -> list[str]:
    """Up to three tips over the thread's steps' TaskUsage rows."""
    out: list[str] = []
    if any((r.get("chars") or 0) > BIG_RESULT_CHARS
           for u in usages for r in ((u.breakdown or {}).get("largest_results") or [])):
        out.append(TIP_NARROW)
    if any((u.peak_context or 0) > (fresh_tokens or DEFAULT_FRESH_TOKENS) for u in usages):
        out.append(TIP_FRESH)
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
    return out


def build(task, usage, steps: list, usages: list, fresh_tokens: int | None) -> dict:
    """``task``/``usage``: the asked step and its TaskUsage (or None); ``steps``: the thread's steps
    oldest first; ``usages``: the thread's TaskUsage rows."""
    by_id = {u.task_id: u for u in usages}
    running = task.status in (Status.QUEUED, Status.RUNNING)
    cost = usage.est_cost_usd if usage is not None and usage.est_cost_usd is not None else task.total_cost_usd
    # an estimate while the step runs, or when the finished step reported no result cost
    estimate = running or task.total_cost_usd is None
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
    }
    dur = cost_sum = tokens = 0
    last = None
    for t in steps:
        u = by_id.get(t.id)
        live = t.status in Status.ACTIVE
        if live:  # a running step has no stored totals yet: its live estimate stands in
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
    return {"step": step, "thread": thread, "tips": tips(usages, fresh_tokens)[:3]}
