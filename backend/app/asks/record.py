"""The run record an ask is answered from: one text, every part redacted (the
known secrets, and token shapes always) and cleaned of hidden characters. The
record is data for the model; the question goes last, quoted."""

from __future__ import annotations

import json

from sqlalchemy import func, select

from ..models import Task, TaskEvent, TaskHandoff, TaskUsage
from ..redaction import clean_text, redact
from ..resume.handoff import render

RECORD_EVENTS_CHARS = 40000
RECORD_MAX_CHARS = 60000
PROMPT_MAX = 4000
TEXT_MAX = 600
TOOL_INPUT_MAX = 200
TOOL_RESULT_MAX = 300
PRIOR_ASKS = 3
_PAGE = 500


def _txt(value, secrets, limit=None, keep_newlines=False) -> str:
    out = redact(str(value if value is not None else ""), secrets)
    if not keep_newlines:
        out = " ".join(out.split())
    return clean_text(out, limit)


def _event_line(ev: TaskEvent, secrets) -> str | None:
    p = ev.payload if isinstance(ev.payload, dict) else {}
    t = ev.type
    helper = "[helper] " if p.get("parent") else ""
    if t == "text_output":
        return f"{helper}assistant: {_txt(p.get('text'), secrets, TEXT_MAX)}"
    if t == "tool_use":
        inp = p.get("input")
        raw = inp if isinstance(inp, str) else json.dumps(inp, default=str, ensure_ascii=False) if inp else ""
        return f"{helper}tool {_txt(p.get('name'), secrets, 80)}: {_txt(raw, secrets, TOOL_INPUT_MAX)}"
    if t == "tool_result":
        err = " (error)" if p.get("is_error") else ""
        return f"{helper}result{err}: {_txt(p.get('content'), secrets, TOOL_RESULT_MAX)}"
    if t == "error":
        return f"error: {_txt(p.get('message'), secrets, TEXT_MAX)}"
    if t == "guard":
        what = p.get("summary") or p.get("tool") or p.get("reason") or ""
        return f"guard {_txt(p.get('decision') or p.get('event'), secrets, 40)}: {_txt(what, secrets, TOOL_INPUT_MAX)}"
    if t == "completed":
        return f"completed: {_txt(p.get('status'), secrets, 40)}"
    if t == "started":
        return f"started: model {_txt(p.get('model'), secrets, 60)}"
    if t == "usage_band":
        return f"budget band: {_txt(p.get('band'), secrets, 20)}"
    return None  # other event types are not worth a line


def _when(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC") if dt else "-"


async def _events(s, task_id: str, budget: int, secrets) -> tuple[list[str], int]:
    """Newest lines within `budget` chars (oldest first) and the count of older events left out."""
    total = (await s.execute(select(func.count()).select_from(TaskEvent).where(TaskEvent.task_id == task_id))).scalar() or 0
    lines: list[str] = []
    used = 0
    seen = 0
    offset = 0
    full = False
    while not full and offset < total:
        rows = (await s.execute(
            select(TaskEvent).where(TaskEvent.task_id == task_id)
            .order_by(TaskEvent.seq.desc(), TaskEvent.id.desc()).offset(offset).limit(_PAGE)
        )).scalars().all()
        if not rows:
            break
        offset += len(rows)
        for ev in rows:
            line = _event_line(ev, secrets)
            if line is None:
                seen += 1
                continue
            if used + len(line) + 1 > budget:
                full = True
                break
            lines.append(line)
            used += len(line) + 1
            seen += 1
    lines.reverse()
    return lines, max(total - seen, 0)


async def build_record(s, task: Task, question: str, prior: list[tuple[str, str]],
                       secrets: list[str] | None = None) -> str:
    secrets = list(secrets or [])
    head = [
        "## Step",
        f"Title: {_txt(task.title, secrets, 200)}",
        f"Status: {_txt(task.status, secrets, 40)}",
        f"Started: {_when(task.started_at)}",
        f"Ended: {_when(task.completed_at)}",
        f"Model: {_txt(task.model_used or task.model or 'default', secrets, 64)}",
        "Prompt:",
        _txt(task.prompt, secrets, PROMPT_MAX, keep_newlines=True),
    ]
    parts = ["\n".join(head)]

    root = task.root_id or task.id
    handoff = (await s.execute(select(TaskHandoff).where(TaskHandoff.root_id == root))).scalar_one_or_none()
    if handoff is not None and isinstance(handoff.content, dict) and handoff.content:
        parts.append("## Where it stands\n" + _txt(render(handoff.content, level=1), secrets, keep_newlines=True))

    usage = await s.get(TaskUsage, task.id)
    if usage is not None:
        ul = []
        if usage.context_tokens is not None:
            ul.append(f"Context tokens now: {usage.context_tokens}")
        if usage.peak_context is not None:
            ul.append(f"Peak context tokens: {usage.peak_context}")
        cost = task.total_cost_usd if task.total_cost_usd is not None else usage.est_cost_usd
        if cost is not None:
            ul.append(f"Cost{'' if task.total_cost_usd is not None else ' so far (estimate)'}: ${cost:.4f}")
        if task.max_budget_usd is not None:
            ul.append(f"Budget: ${task.max_budget_usd:.2f}")
        if ul:
            parts.append("## Usage\n" + "\n".join(ul))

    tail = []
    if prior:
        pl = ["## Earlier questions you asked"]
        for q, a in prior[-PRIOR_ASKS:]:
            pl.append(f"Q: {_txt(q, secrets, 2000)}\nA: {_txt(a, secrets, 8000, keep_newlines=True)}")
        tail.append("\n".join(pl))
    quoted = "\n".join("> " + ln for ln in _txt(question, secrets, 2000, keep_newlines=True).split("\n"))
    tail.append("Question from the person:\n" + quoted)

    fixed = len("\n\n".join(parts + tail)) + len("## Run log (newest last)\n") + 80
    budget = max(min(RECORD_EVENTS_CHARS, RECORD_MAX_CHARS - fixed), 0)
    lines, dropped = await _events(s, task.id, budget, secrets)
    log = ["## Run log (newest last)"]
    if dropped:
        log.append(f"({dropped} earlier events not shown)")
    log.extend(lines)
    parts.append("\n".join(log))
    return "\n\n".join(parts + tail)
