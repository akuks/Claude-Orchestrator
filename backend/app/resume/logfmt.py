"""One log line per meaningful event, shared by the handoff's recent actions,
the log tail injected into a resumed prompt and the recovery record.

``line(ev)`` gives ``"HH:MM:SS KIND detail"`` (UTC) or None for an event that
is not worth a line. ``ev`` is ``{"type", "payload", "created_at"}`` (a
TaskEvent as a dict). The detail is redacted (token shapes; the payload was
already redacted with the run's secrets when it was stored), cleaned of hidden
characters and then cut to ``DETAIL_MAX``. A helper's (subagent's) line is
prefixed ``[helper] ``."""

from __future__ import annotations

import math
from datetime import datetime

from ..redaction import clean_text, redact
from .gitfacts import own_shas

LOG_TAIL_LINES = 30
LOG_TAIL_CHARS = 4000
DETAIL_MAX = 160

_EDIT = {"Edit", "MultiEdit"}
_WRITE = {"Write", "NotebookEdit"}


def _hhmmss(value) -> str:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            value = None
    return value.strftime("%H:%M:%S") if isinstance(value, datetime) else "--:--:--"


def _detail(text) -> str:
    text = " ".join(str(text or "").split())  # one line
    return clean_text(redact(text, []), DETAIL_MAX)


def bash_what(inp: dict) -> str:
    cmd = str(inp.get("command") or "")
    desc = str(inp.get("description") or "")
    return f"{desc} :: {cmd}" if desc else cmd


def _tool_use(p: dict) -> tuple[str, str] | None:
    name = str(p.get("name") or "")
    inp = p.get("input") if isinstance(p.get("input"), dict) else {}
    if name == "Bash":
        return "BASH", bash_what(inp)
    if name in _EDIT:
        return "EDIT", inp.get("file_path") or ""
    if name in _WRITE:
        return "WRITE", inp.get("file_path") or inp.get("notebook_path") or ""
    if name.startswith("mcp__"):
        return "MCP", name
    if name in ("Agent", "Task"):
        kind = inp.get("subagent_type") or "helper"
        desc = inp.get("description") or ""
        return "AGENT+", f"{kind}: {desc}" if desc else str(kind)
    return None


def _guard(p: dict) -> tuple[str, str] | None:
    decision = p.get("decision")
    what = p.get("summary") or p.get("tool") or p.get("reason") or ""
    if decision == "deny" and p.get("status") == "filed":
        return "WAIT", what
    if decision == "deny":
        return "BLOCK", what
    if decision in ("approved", "granted"):
        return "ALLOW", what
    return None


def _band(p: dict) -> tuple[str, str]:
    band, value = str(p.get("band") or ""), p.get("value")
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return "BAND", band
    if band == "fresh":
        return "BAND", f"{band} {int(value) // 1000}k"
    return "BAND", f"{band} ${value:.2f}"


def _kind(ev: dict) -> tuple[str, str] | None:
    etype = ev.get("type")
    p = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    if etype == "text_output":
        return "SAY", p.get("text") or ""
    if etype == "tool_use":
        return _tool_use(p)
    if etype == "tool_result":
        content = p.get("content")
        content = content if isinstance(content, str) else str(content or "")
        if p.get("is_error"):
            return "FAIL", content
        if own_shas([content]):
            return "COMMIT", content.split("\n", 1)[0]
        return None
    if etype == "guard":
        return _guard(p)
    if etype == "system" and p.get("subtype") == "compact_boundary":
        return "COMPACT", ""
    if etype == "retry":
        return "RETRY", p.get("reason") or ""
    if etype == "error":
        return "ERROR", p.get("message") or ""
    if etype == "completed":
        return "END", p.get("status") or ""
    if etype == "recovery":
        return "CUTOFF", "gave up" if p.get("gave_up") else (p.get("why") or "")
    if etype == "fresh_session":
        return "FRESH", p.get("reason") or ""
    if etype == "usage_band":
        return _band(p)
    if etype == "auto_continue":
        return "AUTO", p.get("text") or ""
    if etype == "summary":  # phase 7d: older history tidied into one event (app.retention)
        n = p.get("events")
        n = n if isinstance(n, int) and not isinstance(n, bool) else 0
        return "TIDIED", f"older run history kept as a summary ({n} events)"
    return None


def line(ev: dict) -> str | None:
    if not isinstance(ev, dict):
        return None
    k = _kind(ev)
    if k is None:
        return None
    kind, detail = k
    p = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    helper = "[helper] " if p.get("parent") else ""
    out = f"{_hhmmss(ev.get('created_at'))} {helper}{kind}"
    detail = _detail(detail)
    return f"{out} {detail}" if detail else out


def all_lines(events) -> list[str]:
    return [ln for ln in (line(e) for e in events or []) if ln]


def tail(events, lines: int = LOG_TAIL_LINES, chars: int = LOG_TAIL_CHARS) -> str:
    """The last ``lines`` formatted lines, oldest first, at most ``chars``
    characters (older lines are dropped first)."""
    out = all_lines(events)[-lines:] if lines > 0 else []
    while out and len("\n".join(out)) > chars:
        out.pop(0)
    return "\n".join(out)
