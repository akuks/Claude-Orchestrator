"""Crash recovery: the verified orphan kill and the record a requeued step
carries (spec "Crash recovery", F15a, F19, F79-F82).

``WorkerManager.recover`` drives the steps; this module holds the parts that
need no database: the process start-time check, the record built from the
crashed run's events and raw spool, and its rendering. Every text field is
redacted (token shapes: no run secrets exist at boot) BEFORE it is cleaned of
hidden characters and cut."""

from __future__ import annotations

import os
import posixpath
import signal
import stat
import subprocess
from datetime import datetime, timedelta, timezone

from ..redaction import clean_text, redact
from . import logfmt
from .handoff import running_at_cutoff
from .texts import GONE_LINE, GUEST_TAIL, IN_FLIGHT_ADVICE, RECORD_HEAD, RECORD_TAIL

STALE_SECONDS = 120
SWEEP_SECONDS = 60
MAX_RECOVERIES = 2
RECENT_WRITE_SECONDS = 120
IN_FLIGHT_MAX = 10
SINCE_SAVE_MAX = 12
LAST_WORDS = 60  # words
LAST_WORDS_CHARS = 600
WHAT_MAX_SHELL = 200
WHAT_MAX_PATH = 300
GONE_MAX = 10
WRITES_MAX = 10
TAIL_BYTES = 64
PS_TIMEOUT = 2.0

GAVE_UP_ERROR = "interrupted {n} times by server restarts; use Continue to resume it"

_SHELL = {"Bash", "PowerShell"}
_FILE = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
_AGENT = {"Agent", "Task"}

MARK_MISSING = "missing"
MARK_EMPTY = "empty (0 bytes)"
MARK_NUL = "ends in NUL bytes"
MARK_OK = "ok"


# ---- the orphan --------------------------------------------------------------------------------


def proc_start(pid) -> str | None:
    """The process's start time as ``ps -o lstart=`` prints it (portable
    across Linux and BSD), or None (no such process, bad pid, ps failed)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 1:
        return None
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=PS_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    text = " ".join((out.stdout or "").split())
    return text or None


def _norm(start) -> str:
    return " ".join(str(start or "").split())


def orphan_alive(pgid, start) -> bool:
    """True only when the recorded process still runs: ``pgid`` names a
    process whose start time equals ``start`` (pid reuse never matches)."""
    want = _norm(start)
    if not pgid or not want:
        return False
    got = proc_start(pgid)
    return bool(got) and _norm(got) == want


def kill_orphan(pgid, start) -> bool:
    """SIGKILL the recorded claude process group, only when its start time
    still matches. True when a signal was sent."""
    if not orphan_alive(pgid, start):
        return False
    try:
        os.killpg(int(pgid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


# ---- the record --------------------------------------------------------------------------------


def _text(value, limit: int | None = None) -> str:
    """Redact first (token shapes), then one line, clean and cut."""
    return clean_text(" ".join(redact(str(value or ""), []).split()), limit)


def aware(dt):
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return None
    if isinstance(dt, datetime):
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _payload(e) -> dict:
    p = e.get("payload") if isinstance(e, dict) else None
    return p if isinstance(p, dict) else {}


def _kind(tool: str) -> str:
    if tool in _SHELL:
        return "shell"
    if tool in _FILE:
        return "file"
    if tool in _AGENT:
        return "Agent"
    return "other"


def _what_of_use(p: dict) -> str:
    inp = p.get("input") if isinstance(p.get("input"), dict) else {}
    name = str(p.get("name") or "")
    if name in _SHELL:
        return logfmt.bash_what(inp)
    if name in _FILE:
        return str(inp.get("file_path") or inp.get("notebook_path") or "")
    if name in _AGENT:
        return str(inp.get("subagent_type") or inp.get("description") or "")
    return name


def _in_flight(events, spool_raw) -> list[dict]:
    """``seen`` calls with no ``done`` line and no ``tool_result`` event."""
    done = {str(r.get("tool_use_id")) for r in spool_raw if r.get("done") is True and r.get("tool_use_id")}
    results = {str(_payload(e).get("tool_use_id")) for e in events if e.get("type") == "tool_result"}
    uses = {str(_payload(e).get("id")): _payload(e) for e in events if e.get("type") == "tool_use"}
    out, listed = [], set()
    for r in spool_raw:
        tid = r.get("tool_use_id")
        if r.get("seen") is not True or not tid:
            continue
        tid = str(tid)
        if tid in done or tid in results or tid in listed:
            continue
        listed.add(tid)
        use = uses.get(tid, {})
        tool = str(r.get("tool") or use.get("name") or "a tool")
        what = r.get("what") if isinstance(r.get("what"), str) and r.get("what") else _what_of_use(use)
        kind = _kind(tool)
        out.append({"tool": _text(tool, 64),
                    "what": _text(what, WHAT_MAX_PATH if kind == "file" else WHAT_MAX_SHELL),
                    "advice": IN_FLIGHT_ADVICE[kind]})
        if len(out) >= IN_FLIGHT_MAX:
            break
    return out


def _with_spool_stops(events, spool_raw) -> list[dict]:
    """The events plus the spool's Stop lines (not ingested yet), each put
    after the last event that is not newer than its ``ts``."""
    stops = []
    for r in spool_raw:
        if r.get("event") != "Stop":
            continue
        try:
            at = datetime.fromtimestamp(float(r.get("ts")), timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            at = None
        stops.append({"type": "guard", "payload": {"event": "Stop", "background": r.get("background")},
                      "created_at": at})
    if not stops:
        return list(events)
    merged = list(events)
    for st in stops:
        at = st["created_at"]
        pos = len(merged)
        if at is not None:
            for i in range(len(merged) - 1, -1, -1):
                t = aware(merged[i].get("created_at"))
                if t is None or t <= at:
                    pos = i + 1
                    break
            else:
                pos = 0
        merged.insert(pos, st)
    return merged


def _within(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip("/") + "/")


def _mark(real: str) -> str | None:
    """The file's mark, or None when it is not a regular file."""
    try:
        st = os.stat(real)
    except FileNotFoundError:
        return MARK_MISSING
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    if st.st_size == 0:
        return MARK_EMPTY
    try:
        fd = os.open(real, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        os.lseek(fd, max(0, st.st_size - TAIL_BYTES), os.SEEK_SET)
        tail = os.read(fd, TAIL_BYTES)
    except OSError:
        return None
    finally:
        os.close(fd)
    return MARK_NUL if tail.endswith(b"\0") else MARK_OK


def _recent_writes(events, workspace, project_dir) -> list[dict]:
    """Files written in the RECENT_WRITE_SECONDS before the last event,
    newest first. Only regular files inside the workspace or project dir
    are stat-ed; other paths are left out."""
    times = [t for t in (aware(e.get("created_at")) for e in events) if t is not None]
    if not times:
        return []
    since = max(times) - timedelta(seconds=RECENT_WRITE_SECONDS)
    roots = [os.path.realpath(str(d)) for d in (workspace, project_dir) if d]
    out, seen = [], set()
    for e in reversed(events):
        if e.get("type") != "tool_use":
            continue
        p = _payload(e)
        if p.get("name") not in _FILE:
            continue
        at = aware(e.get("created_at"))
        if at is None or at < since:
            continue
        inp = p.get("input") if isinstance(p.get("input"), dict) else {}
        raw = inp.get("file_path") or inp.get("notebook_path")
        if not isinstance(raw, str) or not raw or "\0" in raw:
            continue
        shown = posixpath.normpath(raw if posixpath.isabs(raw) else posixpath.join(str(workspace), raw))
        real = os.path.realpath(shown)
        if real in seen:
            continue
        seen.add(real)
        if not any(_within(real, r) for r in roots):
            continue  # never stat a path outside the step's own folders
        mark = _mark(real)
        if mark is None:
            continue
        out.append({"path": _text(shown, WHAT_MAX_PATH), "mark": mark})
        if len(out) >= WRITES_MAX:
            break
    return out


def _since_save(events, last_save_at) -> list[str]:
    cut = aware(last_save_at)
    after = [e for e in events if cut is None or (aware(e.get("created_at")) or cut) > cut]
    return logfmt.all_lines(after)[-SINCE_SAVE_MAX:]


def _last_words(events) -> str:
    for e in reversed(events):
        if e.get("type") == "text_output" and _payload(e).get("text"):
            whole = redact(str(_payload(e)["text"]), [])  # redacted whole, then cut
            words = clean_text(whole).split()[-LAST_WORDS:]
            return " ".join(words)[-LAST_WORDS_CHARS:]
    return ""


def build_record(task, events, spool_raw, workspace, last_save_at, now, *, why: str = "",
                 project_dir=None) -> dict:
    """The record of a run cut off at ``now``: what was in flight, what is
    gone, the files written just before the cut (a hint, not proof), the log
    lines after the last handoff save and Claude's last words. ``task`` is
    not read (everything comes from the events and the spool; it may be
    None); ``project_dir`` widens the folders whose files may be stat-ed."""
    events = [e for e in (events or []) if isinstance(e, dict)]
    spool_raw = [r for r in (spool_raw or []) if isinstance(r, dict)]
    gone = [_text(g, 200) for g in running_at_cutoff(_with_spool_stops(events, spool_raw))]
    return {
        "at": aware(now).isoformat() if aware(now) else datetime.now(timezone.utc).isoformat(),
        "why": _text(why, 100),
        "in_flight": _in_flight(events, spool_raw),
        "gone": [g for g in gone if g][:GONE_MAX],
        "recent_writes": _recent_writes(events, workspace, project_dir),
        "since_save": _since_save(events, last_save_at),
        "last_words": _last_words(events),
        "pending": True,
    }


def render_record(record: dict, external: bool) -> str:
    """The record as the injected text: RECORD_HEAD ... RECORD_TAIL
    (+ GUEST_TAIL for an external or quarantined project)."""
    r = record if isinstance(record, dict) else {}
    at = aware(r.get("at"))
    lines = [RECORD_HEAD.format(hhmm=at.astimezone(timezone.utc).strftime("%H:%M") if at else "--:--",
                                why=_text(r.get("why"), 100) or "unknown")]
    flight = [f for f in r.get("in_flight") or [] if isinstance(f, dict)]
    if flight:
        lines.append("In flight when it stopped:")
        for f in flight[:IN_FLIGHT_MAX]:
            what = _text(f.get("what"), WHAT_MAX_PATH)
            lines.append(f"- {_text(f.get('tool'), 64)}{': ' + what if what else ''} - {_text(f.get('advice'), 100)}")
    gone = [_text(g, 200) for g in r.get("gone") or []]
    gone = [g for g in gone if g]
    if gone:
        lines.append(GONE_LINE.format(list="; ".join(gone[:GONE_MAX])))
    writes = [w for w in r.get("recent_writes") or [] if isinstance(w, dict)]
    if writes:
        lines.append("Files written just before the cut (a hint, not proof):")
        for w in writes[:WRITES_MAX]:
            lines.append(f"- {_text(w.get('path'), WHAT_MAX_PATH)}: {_text(w.get('mark'), 40)}")
    since = [_text(s, 400) for s in r.get("since_save") or []]
    since = [s for s in since if s]
    if since:
        lines.append("Log since the last save:")
        lines += since[-SINCE_SAVE_MAX:]
    words = _text(r.get("last_words"), LAST_WORDS_CHARS)
    if words:
        lines.append(f'Last words: "{words}"')
    lines.append(RECORD_TAIL)
    if external:
        lines.append(GUEST_TAIL)
    return "\n".join(lines)
