"""Run totals and the "So far" cost line (F120).

compute(s, run) rules:
- Wall time per stage comes from run.stage_state[str(i)] {"started_at", "finished_at"} (iso).
- stages_done: stages with a finished_at. stages: len(run.stages).
- minutes: finished stages' wall time plus the current stage (run.current_stage, started and
  not finished, run not completed/failed) up to now; rounded to one decimal.
- cost_usd / tokens: sums over every task of the run (Task.workflow_run_id), every attempt;
  total_cost_usd and input_tokens + output_tokens, None counts as 0.
- last: the finished stage with the latest finished_at: {"name", "minutes" (its wall time),
  "cost_usd", "tokens" (sums over that stage's tasks)}, or None.

line(t) renders exactly:
  So far: {d} of {n} steps done, {M} min, ${C}, {T} tokens (last: {stage}, {m} min, ${c})
- minutes: 0 decimals when >= 10, else 1 decimal (of the one-decimal value).
- cost: always 2 decimals.
- tokens: < 1000 as is (900), < 1,000,000 in thousands rounded (41k), else millions with one
  decimal (1.2M).
- with no last stage the parenthetical is dropped.
"""
from datetime import datetime, timezone

from sqlalchemy import select

from ..models import Task


def _parse(v):
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v)
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _mins(a, b) -> float:
    return max(0.0, (b - a).total_seconds() / 60.0)


def _tok(t) -> int:
    return (t.input_tokens or 0) + (t.output_tokens or 0)


async def compute(s, run) -> dict:
    stages = run.stages or []
    state = run.stage_state or {}
    now = datetime.now(timezone.utc)
    tasks = (await s.execute(select(Task).where(Task.workflow_run_id == run.id))).scalars().all()
    per: dict[int, list] = {}
    for t in tasks:
        if t.stage_index is not None:
            per.setdefault(t.stage_index, [0.0, 0])
            per[t.stage_index][0] += t.total_cost_usd or 0.0
            per[t.stage_index][1] += _tok(t)

    done = 0
    minutes = 0.0
    last_at = None
    last_i = None
    last_min = 0.0
    live = run.status not in ("completed", "failed")
    for key, st in state.items():
        try:
            i = int(key)
        except ValueError:
            continue
        a, b = _parse((st or {}).get("started_at")), _parse((st or {}).get("finished_at"))
        if a and b:
            done += 1
            m = _mins(a, b)
            minutes += m
            if last_at is None or b > last_at:
                last_at, last_i, last_min = b, i, m
        elif a and live and i == run.current_stage:
            minutes += _mins(a, now)

    # Runs from before 0014 have no stage_state: count their finished stages from the position.
    for i in range(len(stages)):
        st = state.get(str(i))
        if isinstance(st, dict) and (st.get("started_at") or st.get("finished_at")):
            continue
        if run.status == "completed" or i < (run.current_stage or 0):
            done += 1

    last = None
    if last_i is not None:
        sg = stages[last_i] if 0 <= last_i < len(stages) else {}
        name = (sg.get("name") if isinstance(sg, dict) else None) or f"Stage {last_i + 1}"
        c, tk = per.get(last_i, [0.0, 0])
        last = {"name": name, "minutes": round(last_min, 1), "cost_usd": round(c, 4), "tokens": tk}
    return {
        "stages_done": done,
        "stages": len(stages),
        "minutes": round(minutes, 1),
        "cost_usd": round(sum(t.total_cost_usd or 0.0 for t in tasks), 4),
        "tokens": sum(_tok(t) for t in tasks),
        "last": last,
    }


def _fmt_min(m: float) -> str:
    m = round(m, 1)
    return f"{m:.0f}" if m >= 10 else f"{m:.1f}"


def _fmt_tokens(n: int) -> str:
    if n < 1000:
        return str(n)
    k = round(n / 1000)
    if k < 1000:
        return f"{k}k"
    return f"{n / 1_000_000:.1f}M"


def line(t: dict) -> str:
    out = (
        f"So far: {t['stages_done']} of {t['stages']} steps done, {_fmt_min(t['minutes'])} min, "
        f"${t['cost_usd']:.2f}, {_fmt_tokens(t['tokens'])} tokens"
    )
    last = t.get("last")
    if last:
        out += f" (last: {last['name']}, {_fmt_min(last['minutes'])} min, ${last['cost_usd']:.2f})"
    return out


async def per_step(s, run, gate_rows=None) -> list[dict | None]:
    """Time and spend per step for its newest attempt (F66), computed on read:
    {"cost_usd", "input_tokens", "output_tokens", "seconds"}, or None for a step that has not
    started and has no tasks. Cost and tokens sum the step's tasks of that attempt (None counts
    as 0). Seconds: started_at -> finished_at; while the run is on the step, up to now; for a
    step that ended without a finish time (a failure), its gate runs' durations or its tasks'
    last completion. `gate_rows` are the run's WorkflowGateRun rows when already loaded."""
    from ..models import WorkflowGateRun

    stages = run.stages or []
    state = run.stage_state or {}
    now = datetime.now(timezone.utc)
    tasks = (await s.execute(select(Task).where(Task.workflow_run_id == run.id))).scalars().all()
    if gate_rows is None:
        gate_rows = (
            await s.execute(select(WorkflowGateRun).where(WorkflowGateRun.run_id == run.id))
        ).scalars().all()
    live = run.status not in ("completed", "failed")
    out: list[dict | None] = []
    for i in range(len(stages)):
        st = state.get(str(i)) if isinstance(state.get(str(i)), dict) else {}
        try:
            attempt = int(st.get("attempt") or 0)
        except (TypeError, ValueError):
            attempt = 0
        mine = [t for t in tasks if t.stage_index == i and (t.stage_attempt or 0) == attempt]
        a, b = _parse(st.get("started_at")), _parse(st.get("finished_at"))
        if not a and not mine:
            out.append(None)
            continue
        seconds = 0.0
        if a and b:
            seconds = (b - a).total_seconds()
        elif a and live and i == run.current_stage:
            seconds = (now - a).total_seconds()
        else:
            rows = [g for g in gate_rows if g.stage_index == i and (g.attempt or 0) == attempt]
            if rows:
                seconds = sum((g.duration_ms or 0) for g in rows) / 1000.0
            elif a:
                ends = [_aware(t.completed_at) for t in mine if t.completed_at]
                if ends:
                    seconds = (max(ends) - a).total_seconds()
        out.append({
            "cost_usd": round(sum(t.total_cost_usd or 0.0 for t in mine), 4),
            "input_tokens": sum(t.input_tokens or 0 for t in mine),
            "output_tokens": sum(t.output_tokens or 0 for t in mine),
            "seconds": max(0, int(seconds)),
        })
    return out


def _aware(d):
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)
