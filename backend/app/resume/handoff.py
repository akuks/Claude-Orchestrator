"""Where a thread stands: one handoff per thread (``TaskHandoff``, unique on
root_id), built by the server from the thread's steps, the described step's
events, git facts and the thread's live state.

* ``build(HandoffInput) -> content`` uses no DB and no git (it only reads a
  plan or ledger file the step wrote, and resolves paths): every text is redacted (the run's
  secrets plus token shapes), cleaned of hidden characters and then cut to
  ``FIELD_MAX``. ``render(content, level)`` gives the injected text (at most
  ``HANDOFF_MAX_WORDS`` words, ``HANDOFF_HEAD`` first).
* ``snapshot(task_id, reason)`` gathers the inputs, builds, renders and stores
  them; the version is bumped only when the content changed apart from
  ``as_of``, keeping the previous pair in ``prev_*``.

The described step is the thread's latest main-line step (origin not
``agent``); dispatched children appear only as counts."""

from __future__ import annotations

import asyncio
import json
import os
import posixpath
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select

from ..guard import rules
from ..redaction import clean_text, redact, strip_query
from . import gitfacts, logfmt
from .texts import HANDOFF_HEAD

HANDOFF_MAX_WORDS = 600
FIELD_MAX = 200
FILES_MAX = 50
FILES_TRIMMED = 5
FILES_SHOWN = 5  # newest files the injected text names when there are more (forge v0.8.0 F129); the card shows all
CAUTIONS_MAX = 6  # caution notes shown under "Re-check before acting" (3 from trim level 3)
CAUTIONS_TRIMMED = 3
CAUTION_TEXT_MAX = 160
CAUTION_MEASURED_MAX = 120
RECENT_ACTIONS = 8
OTHERS_MAX = 2
BEFORE_MAX = 4
LAST_REPLY_WORDS = 70
LAST_REPLY_TRIMMED = 35  # from trim level 3
NOTES_TRIMMED = 60  # words, from trim level 3
NOTES_MAX_WORDS = 120
NOTES_MAX_CHARS = 1000
NOTES_BODY_MAX = 4096  # bytes of a notes PUT
EVENTS_SCAN_MAX = 2000
PLAN_FILE_MAX = 262144
HANDOFF_EVERY_SECONDS = 60
HEARTBEAT_SECONDS = 30
DONE_MAX = 5
ELSEWHERE_MAX = 10
RUNNING_MAX = 10
SOURCES_MAX = 5
HANDOFF_FILE = "handoff.md"
# snapshots of a run: only they move the outside-commit base (a Refresh or notes save by a person never does)
RUN_REASONS = ("run ended", "cut off", "working", "recovered")

_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
_HELPER_TOOLS = {"Agent", "Task"}
_DOC_EXT = {".html", ".htm", ".md", ".pdf"}
_BOX = re.compile(r"^\s*[-*] \[([ xX])\] +(.+?)\s*$", re.M)
_SP_PLAN = re.compile(r"(?:^|/)docs/superpowers/plans/([^/]+)\.md$")
_SP_LEDGER = re.compile(r"(?:^|/)\.superpowers/sdd/([^/]+)/progress\.md$")
_SP_TASK = re.compile(r"^###\s+Task\s+(\d+):\s*(.+?)\s*$", re.M)
_SP_DONE = re.compile(r"^\s*(?:[-*]\s*)?Task\s+(\d+):\s*complete\b", re.M | re.I)
_NOTE_STEP = re.compile(r"^\s*Step:\s*(\d+)\s+of\s+(\d+)\s*:?\s*(.*?)\s*$", re.M | re.I)
_NOTE_NEXT = re.compile(r"^\s*Next:\s*(.+?)\s*$", re.M | re.I)
_COMMIT = re.compile(r"^\[(?:[^\]\s]+|detached HEAD)(?: \(root-commit\))? ([0-9a-f]{7,40})\] ?(.*)$", re.M)


# ---- small helpers -------------------------------------------------------------------------


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


def _payload(e) -> dict:
    p = e.get("payload") if isinstance(e, dict) else None
    return p if isinstance(p, dict) else {}


def _input_of(p) -> dict:
    return p.get("input") if isinstance(p.get("input"), dict) else {}


def _tool_uses(events, main_only=False):
    for e in events or []:
        if isinstance(e, dict) and e.get("type") == "tool_use":
            p = _payload(e)
            if main_only and p.get("parent"):
                continue
            yield e, p


def _within(path, folder):
    return path == folder or path.startswith(folder.rstrip("/") + "/")


def _realabs(path, base=None) -> str:
    path = str(path)
    if base is not None and not posixpath.isabs(path):
        path = posixpath.join(str(base), path)
    return os.path.realpath(posixpath.normpath(path))


def _written_paths(events, workspace):
    """(shown path, real path) of each file a tool wrote, newest first, once each."""
    seen, out = set(), []
    for _e, p in reversed(list(_tool_uses(events))):
        if p.get("name") not in _WRITE_TOOLS:
            continue
        inp = _input_of(p)
        raw = inp.get("file_path") or inp.get("notebook_path")
        if not isinstance(raw, str) or not raw or "\0" in raw:
            continue
        shown = posixpath.normpath(raw if posixpath.isabs(raw) else posixpath.join(str(workspace), raw))
        real = _realabs(shown)
        if real not in seen:
            seen.add(real)
            out.append((shown, real))
    return out


def _read_small(path: Path) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > PLAN_FILE_MAX:
            return None
        return path.read_text(errors="replace")
    except OSError:
        return None


def _mtime(path: Path):
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return None


# ---- step derivation -------------------------------------------------------------------------


def _progress(items, source):
    """items: [(text shown while current, name, status)] -> the step dict."""
    if not items:
        return None
    idx = next((i for i, it in enumerate(items) if it[2] == "in_progress"), None)
    if idx is None:
        idx = next((i for i, it in enumerate(items) if it[2] != "completed"), len(items) - 1)
    nxt = next((it[1] for it in items[idx + 1:] if it[2] != "completed"), None)
    done = [it[1] for it in items if it[2] == "completed"][-DONE_MAX:]
    finished = all(it[2] == "completed" for it in items)
    text = "all steps done" if finished else (items[idx][0] or items[idx][1])
    return {"n": idx + 1, "m": len(items), "text": text, "next": nxt, "done": done, "source": source}


def _from_todos(events):
    last = None
    for _e, p in _tool_uses(events, main_only=True):
        todos = _input_of(p).get("todos")
        if p.get("name") == "TodoWrite" and isinstance(todos, list) and todos:
            last = todos
    if last is None:
        return None
    items = [(str(t.get("activeForm") or ""), str(t.get("content") or ""), str(t.get("status") or ""))
             for t in last if isinstance(t, dict)]
    return _progress(items, "todo")


def _from_tasks(events):
    tasks: dict[str, list] = {}
    for _e, p in _tool_uses(events, main_only=True):
        inp = _input_of(p)
        if p.get("name") == "TaskCreate":
            tid = str(len(tasks) + 1)  # best effort: the CLI numbers them in creation order
            tasks[tid] = [str(inp.get("activeForm") or ""), str(inp.get("subject") or ""), "pending"]
        elif p.get("name") == "TaskUpdate" and str(inp.get("taskId")) in tasks:
            t = tasks[str(inp.get("taskId"))]
            if inp.get("status"):
                t[2] = str(inp["status"])
            if inp.get("subject"):
                t[1] = str(inp["subject"])
    items = [tuple(t) for t in tasks.values() if t[2] != "deleted"]
    return _progress(items, "tasks")


def _from_plan_file(events, workspace):
    for shown, real in _written_paths(events, workspace):
        if _SP_PLAN.search(shown) or _SP_LEDGER.search(shown):
            continue  # superpowers plans have their own ledger (priority 4)
        text = _read_small(Path(real))
        boxes = _BOX.findall(text or "")
        if len(boxes) >= 2:
            items = [("", t, "completed" if mark in "xX" else "pending") for mark, t in boxes]
            return _progress(items, "plan"), _mtime(Path(real))
    return None


def _from_superpowers(events, workspace, roots=()):
    allowed = [_realabs(d) for d in (workspace, *roots) if d]
    for shown, _real in _written_paths(events, workspace):
        m = _SP_PLAN.search(shown) or _SP_LEDGER.search(shown)
        if not m:
            continue
        name = m.group(1)
        # the tree the step wrote it in, when that is the workspace, the project dir or a source dir (or
        # inside one); anywhere else: the workspace, as before
        tree = _realabs(shown[: m.start()] or workspace)
        root = Path(tree if any(_within(tree, a) for a in allowed) else workspace)
        plan = root / "docs" / "superpowers" / "plans" / f"{name}.md"
        ledger = root / ".superpowers" / "sdd" / name / "progress.md"
        tasks = _SP_TASK.findall(_read_small(plan) or "")
        if not tasks:
            continue
        complete = set(_SP_DONE.findall(_read_small(ledger) or ""))
        items = [("", t, "completed" if n in complete else "pending") for n, t in tasks]
        times = [t for t in (_mtime(plan), _mtime(ledger)) if t]
        return _progress(items, "superpowers"), max(times) if times else None
    return None


def _from_notes(notes_text):
    m = _NOTE_STEP.search(notes_text or "")
    if not m:
        return None
    nxt = _NOTE_NEXT.search(notes_text)
    return {"n": int(m.group(1)), "m": int(m.group(2)), "text": m.group(3), "next": nxt.group(1) if nxt else None,
            "done": [], "source": "notes"}


def step_progress(events, workspace, notes_text, notes_at, roots=()):
    """Step n of m, in priority order: TodoWrite, TaskCreate/TaskUpdate, a
    checkbox plan file the step wrote, a superpowers plan with its ledger, the
    notes' ``Step:`` header (it wins over a plan file when newer). None: no
    source (the caller falls back to the last user prompt). ``roots``: the
    project dir and source dirs a superpowers plan may also live in."""
    for derive in (_from_todos, _from_tasks):
        got = derive(events)
        if got:
            return got
    noted = _from_notes(notes_text)
    for derive in (_from_plan_file, lambda ev, ws: _from_superpowers(ev, ws, roots)):
        got = derive(events, workspace)
        if got:
            plan, at = got
            notes_at = _aware(notes_at)
            if noted and notes_at and at and notes_at > at:
                return noted
            return plan
    return noted


# ---- files and running ---------------------------------------------------------------------


def files_touched(events, workspace, project_dir, policy) -> dict:
    """Files the step wrote: in the workspace or project dir (``files``,
    newest first), temp-folder documents that can vanish (``outside_docs``),
    and the folders of every other write (``elsewhere``)."""
    ws, pd = _realabs(workspace), _realabs(project_dir)
    files, docs, elsewhere = [], [], []
    for shown, real in _written_paths(events, workspace):
        if _within(real, ws):
            files.append(posixpath.relpath(real, ws))
        elif _within(real, pd):
            files.append(shown)
        elif posixpath.splitext(real)[1].lower() in _DOC_EXT and rules.path_in_temp(real, policy):
            docs.append(shown)
        else:
            folder = posixpath.dirname(shown)
            if folder not in elsewhere:
                elsewhere.append(folder)
    return {"files": files[:FILES_MAX], "elsewhere": elsewhere[:ELSEWHERE_MAX], "outside_docs": docs[:FILES_MAX]}


def running_at_cutoff(events) -> list[str]:
    """Helpers (Agent calls) with no result, background shells started after
    the last Stop, and the background tasks that Stop listed."""
    events = list(events or [])
    results = {str(_payload(e).get("tool_use_id")) for e in events if e.get("type") == "tool_result"}
    stops = [i for i, e in enumerate(events) if e.get("type") == "guard" and _payload(e).get("event") == "Stop"]
    last_stop = stops[-1] if stops else -1
    out = []
    for i, e in enumerate(events):
        if e.get("type") != "tool_use":
            continue
        p, inp = _payload(e), _input_of(_payload(e))
        if p.get("name") in _HELPER_TOOLS and str(p.get("id")) not in results:
            kind, desc = inp.get("subagent_type") or "helper", inp.get("description") or ""
            out.append(f"helper {kind}: {desc}" if desc else f"helper {kind}")
        elif p.get("name") == "Bash" and inp.get("run_in_background") and i > last_stop:
            out.append(f"background shell: {logfmt.bash_what(inp)}")
    if last_stop >= 0:
        bg = _payload(events[last_stop]).get("background")
        for b in bg if isinstance(bg, list) else []:
            if isinstance(b, dict) and b.get("what"):
                out.append(f"background: {b['what']}")
    return out[:RUNNING_MAX]


# ---- build -----------------------------------------------------------------------------------


@dataclass
class HandoffInput:
    steps: list[dict]  # main-line steps, oldest first; the last one is described
    step_events: list[dict]
    facts: Any  # gitfacts.Facts | None
    outside: dict  # gitfacts.outside_commits
    sources: list[dict]  # [{"path", "head"}]
    waiting: list[str]
    children: dict  # {status: count}
    others: list[str]
    trust: str
    workspace: str
    project_dir: str
    policy: dict
    now: datetime
    reason: str
    secrets: list[str] = field(default_factory=list)


def build(inp: HandoffInput) -> dict:
    secrets = list(inp.secrets or [])

    def f(text, limit=FIELD_MAX):
        """Redact first, then clean, then cut."""
        if text is None:
            return None
        return " ".join(clean_text(redact(strip_query(text), secrets)).split())[:limit]

    def fl(items):
        return [x for x in (f(i) for i in items or []) if x]

    steps = list(inp.steps or [])
    step = steps[-1] if steps else {}
    events = list(inp.step_events or [])[-EVENTS_SCAN_MAX:]
    prompts = [s.get("prompt") for s in steps if s.get("origin") == "user" and s.get("prompt")]
    last_request = prompts[-1] if prompts else None

    noted = [s for s in steps if s.get("notes") and _aware(s.get("notes_updated_at"))]
    noted.sort(key=lambda s: _aware(s["notes_updated_at"]))
    notes = noted[-1] if noted else None
    notes_text = clean_text(redact(strip_query(notes["notes"]), secrets)) if notes else None
    cautions: list[dict] = []
    if notes_text:
        notes_text, cautions = split_cautions(notes_text)
        cautions = [{"text": f(c["text"], CAUTION_TEXT_MAX), "measured": f(c["measured"], CAUTION_MEASURED_MAX)}
                    for c in cautions]

    roots = [inp.project_dir, *(s.get("path") for s in inp.sources or [] if isinstance(s, dict))]
    sp = step_progress(events, inp.workspace, notes_text, notes["notes_updated_at"] if notes else None, roots)
    if sp is None:
        sp = {"n": None, "m": None, "text": last_request, "next": None, "done": [], "source": "prompt"}
    done = list(sp["done"])
    commits = [m for e in events if e.get("type") == "tool_result" and not _payload(e).get("is_error")
               for m in _COMMIT.findall(str(_payload(e).get("content") or ""))]
    if commits:
        done.append(f"commit {commits[-1][0]} {commits[-1][1]}".strip())

    reply = step.get("result_text")
    if not reply:
        says = [_payload(e).get("text") for e in events
                if e.get("type") == "text_output" and not _payload(e).get("parent") and _payload(e).get("text")]
        reply = says[-1] if says else None
    if reply:
        words = " ".join(clean_text(redact(strip_query(reply), secrets)).split()).split(" ")
        reply = " ".join(words[-LAST_REPLY_WORDS:])[-FIELD_MAX:].lstrip()

    facts = inp.facts if inp.facts is not None and getattr(inp.facts, "is_repo", False) else None
    status = step.get("status")
    ended = "cut off" if inp.reason == "cut off" else (
        f"session ended ({status})" if status in ("completed", "failed", "cancelled") else None)
    touched = files_touched(events, inp.workspace, inp.project_dir, inp.policy)
    outside = inp.outside or {}
    out_commits = []
    for c in outside.get("commits") or []:
        sha = getattr(c, "sha", None) or (c.get("sha") if isinstance(c, dict) else None)
        subject = getattr(c, "subject", None) or (c.get("subject") if isinstance(c, dict) else "")
        if sha:
            out_commits.append({"sha": str(sha)[:12], "subject": f(subject) or ""})
    sid = step.get("id")
    return {
        "as_of": _iso(inp.now),
        "reason": inp.reason,
        "step_task_id": sid,
        "step_status": status,
        "ended": ended,
        "branch": f(facts.branch) if facts else None,
        "head": facts.head if facts else None,
        "dirty": facts.dirty if facts else None,
        "dirty_capped": bool(facts.dirty_capped) if facts else False,
        "no_commits": bool(facts.no_commits) if facts else False,
        "review": (inp.trust or "own") != "own",
        "sources": [{"path": f(s.get("path")), "head": s.get("head")} for s in (inp.sources or [])[:SOURCES_MAX]],
        "task": f(last_request),
        "before": fl(prompts[:-1][-BEFORE_MAX:]),
        "step": {"n": sp["n"], "m": sp["m"], "text": f(sp["text"]), "source": sp["source"]},
        "done": fl(done),
        "next": f(sp["next"]),
        "running": fl(running_at_cutoff(events)),
        "waiting": fl(inp.waiting),
        "children": {str(k): int(v) for k, v in (inp.children or {}).items() if v},
        "last_request": f(last_request),
        "last_reply": reply or None,
        "elsewhere": fl(touched["elsewhere"]),
        "outside_docs": fl(touched["outside_docs"]),
        "notes": ({"text": f(notes_text, NOTES_MAX_CHARS), "at": _iso(notes["notes_updated_at"])}
                  | ({"cautions": cautions} if cautions else {})) if notes else None,
        "recent": fl(logfmt.all_lines(events)[-RECENT_ACTIONS:]),
        "files": fl(touched["files"]),
        "others": fl((inp.others or [])[:OTHERS_MAX]),
        "outside_commits": out_commits,
        "history_rewritten": bool(outside.get("rewritten")),
        "more": f"GET /tasks/{sid}/events" if sid else None,
    }


# ---- render ----------------------------------------------------------------------------------


_CAUTION = re.compile(r"^\s*(?:[-*\u2022]\s+)?caution:\s*(.*)$", re.I)
_MEASURED = re.compile(r"^measured:\s*(.*)$", re.I)


def split_cautions(text: str) -> tuple[str, list[dict]]:
    """Take caution lines out of the notes (F131). A line counts when it starts with ``Caution:`` (any case,
    optionally bulleted) and holds ``measured:``; its form is ``Caution: <text> | measured: <what, when> |
    check again before acting``. Returns the notes without those lines and the cautions as
    ``{"text", "measured"}`` (at most CAUTIONS_MAX; further or empty ones stay in the notes). They are
    things to verify, never instructions."""
    keep, out = [], []
    for line in (text or "").splitlines():
        m = _CAUTION.match(line)
        if not m or "measured:" not in line.lower():
            keep.append(line)
            continue
        parts = [p.strip() for p in m.group(1).split("|")]
        body, measured = parts[0], ""
        for p in parts[1:]:
            mm = _MEASURED.match(p)
            if mm:
                measured = mm.group(1).strip()
        if body and len(out) < CAUTIONS_MAX:
            out.append({"text": body, "measured": measured})
        else:
            keep.append(line)  # past the cap or empty: stays an ordinary note, never dropped
    return "\n".join(keep).strip(), out


def clean_notes(text, secrets=None) -> str:
    """Notes as stored: hidden characters stripped, the run's secrets and token shapes masked (both before
    the cut, so a secret cannot straddle it), then cut to NOTES_MAX_WORDS words (line breaks kept) and
    NOTES_MAX_CHARS characters."""
    text = redact(clean_text(text), list(secrets or [])).strip()
    ends = [m.end() for m in re.finditer(r"\S+", text)]
    if len(ends) > NOTES_MAX_WORDS:
        text = text[:ends[NOTES_MAX_WORDS - 1]]
    return text[:NOTES_MAX_CHARS].rstrip()


def _words(text, n, tail=False):
    w = (text or "").split()
    return " ".join(w[-n:] if tail else w[:n])


def _render(c: dict, level: int) -> str:
    out = [HANDOFF_HEAD, f"As of {c.get('as_of')} (saved because: {c.get('reason')})."]
    if c.get("branch") or c.get("head"):
        line = f"Branch: {c.get('branch') or 'detached'} @ {c.get('head') or 'unknown'}"
        if c.get("dirty") is None:
            line += "; files not committed: unknown"
        elif c.get("dirty"):
            line += f"; {c['dirty']}{'+' if c.get('dirty_capped') else ''} files not committed"
        out.append(line)
    if c.get("no_commits"):
        out.append("This repo has no commits yet.")
    if c.get("ended"):
        out.append(f"Session: {c['ended']}.")
    if c.get("review"):
        out.append("This is a review of someone else's repo.")
    for s in c.get("sources") or []:
        out.append(f"Source (read-only): {s.get('path')} @ {s.get('head') or 'unknown'}")

    def field_(label, value):
        if value:
            out.append(f"{label}: {value}")

    field_("Task", c.get("task"))
    st = c.get("step") or {}
    if st.get("n") and st.get("m"):
        out.append(f"Step {st['n']} of {st['m']}: {st.get('text') or ''}".rstrip())
    else:
        field_("Step", st.get("text"))
    field_("Done", "; ".join(c.get("done") or []))
    field_("Next action", c.get("next"))
    field_("Still running at cutoff", "; ".join(c.get("running") or []))
    field_("Waiting on you", "; ".join(c.get("waiting") or []))
    if c.get("last_request") != c.get("task"):
        field_("Your last request", c.get("last_request"))
    field_("Requests before it", " | ".join(c.get("before") or []))
    reply = c.get("last_reply")
    field_("Claude's last reply", _words(reply, LAST_REPLY_TRIMMED, tail=True) if level >= 3 else reply)
    field_("Also worked in", ", ".join(c.get("elsewhere") or []))
    notes = c.get("notes") or {}
    if notes.get("text"):
        text = _words(notes["text"], NOTES_TRIMMED) if level >= 3 else notes["text"]
        out.append(f"Claude's notes (written {notes.get('at')}; re-check any caution before acting on it): {text}")
    cautions = [x for x in notes.get("cautions") or [] if isinstance(x, dict) and x.get("text")]
    if cautions:
        out.append("Re-check before acting:")
        for x in cautions[:CAUTIONS_TRIMMED if level >= 3 else CAUTIONS_MAX]:
            out.append(f"- {x['text']}" + (f" (measured: {x['measured']})" if x.get("measured") else ""))
    children = c.get("children") or {}
    if children:
        field_("Child tasks", ", ".join(f"{n} {k}" for k, n in sorted(children.items())))

    def items(head, values):
        if values:
            out.append(head)
            out.extend(f"- {v}" for v in values)

    if level < 1:
        items("Recent actions:", c.get("recent"))
    files = c.get("files") or []
    if len(files) > FILES_SHOWN:
        items(f"{len(files)} files touched. Newest {FILES_SHOWN}:", files[:FILES_SHOWN])
        out.append("The full list is on the task page.")
    else:
        items("Files touched (newest first):", files)
    items("Files outside the workspace that may vanish:", c.get("outside_docs"))
    if c.get("history_rewritten"):
        out.append("Commits made outside this run: unknown (history changed since the last save).")
    else:
        items("Commits made outside this run (up to 50 listed):",
              [f"{x.get('sha')} {x.get('subject')}".strip() for x in c.get("outside_commits") or []])
    if level < 4:
        items("Also active in this directory:", c.get("others"))
    if c.get("more"):
        out.append(f"More detail: {c['more']}")
    return "\n".join(out)


def _cut_words(text: str, n: int) -> str:
    out, left = [], n
    for line in text.split("\n"):
        w = line.split()
        if len(w) > left:
            if left:
                out.append(" ".join(w[:left]))
            break
        out.append(line)
        left -= len(w)
    return "\n".join(out)


def render(content: dict, level: int = 0) -> str:
    """The injected text at trim ``level``. Files: all when at most FILES_SHOWN (5), else
    "N files touched. Newest 5:" and where the full list is. Levels are cumulative: 1 drops
    recent actions, 2 (kept for callers; files are already 5), 3 notes to 60 words, the last
    reply to 35 and cautions to 3, 4 drops also active). Over HANDOFF_MAX_WORDS, the next level
    is tried, and the last one is cut to the limit."""
    text = ""
    for lv in range(max(0, level), 5):
        text = _render(content, lv)
        if len(text.split()) <= HANDOFF_MAX_WORDS:
            return text
    return _cut_words(text, HANDOFF_MAX_WORDS)


# ---- snapshot --------------------------------------------------------------------------------


def _same(a: dict | None, b: dict | None) -> bool:
    def norm(x):
        return json.dumps({k: v for k, v in (x or {}).items() if k != "as_of"}, sort_keys=True, default=str)

    return a is not None and norm(a) == norm(b)


def _step_dict(t) -> dict:
    return {k: getattr(t, k) for k in ("id", "title", "prompt", "origin", "status", "created_at", "started_at",
                                       "completed_at", "result_text", "notes", "notes_updated_at")}


async def boundary(task_id: str) -> str | None:
    """The outside-commit base of a run, fixed at its spawn (F9): the stored
    handoff's base_head (the head a run's own snapshot saw), else its head,
    else the step's head_at_start."""
    from ..database import SessionLocal
    from ..models import Task, TaskHandoff

    async with SessionLocal() as s:
        task = await s.get(Task, task_id)
        if task is None:
            return None
        row = (await s.execute(select(TaskHandoff).where(TaskHandoff.root_id == (task.root_id or task.id)))
               ).scalar_one_or_none()
        c = (row.content or {}) if row is not None else {}
        return c.get("base_head") or c.get("head") or task.head_at_start


async def stored(task_id: str):
    """The thread's stored TaskHandoff (or None)."""
    from ..database import SessionLocal
    from ..models import Task, TaskHandoff

    async with SessionLocal() as s:
        task = await s.get(Task, task_id)
        if task is None:
            return None
        return (await s.execute(select(TaskHandoff).where(TaskHandoff.root_id == (task.root_id or task.id)))
                ).scalar_one_or_none()


def write_file(task_id: str, text: str, secrets=None) -> bool:
    """``${CO_TASK_DIR}/handoff.md`` (0600), redacted again for injection;
    False when the task dir is gone."""
    from ..guard import runtime as guard_runtime

    d = guard_runtime.task_dir(task_id)
    if not d.is_dir():
        return False
    tmp, path = d / (HANDOFF_FILE + ".tmp"), d / HANDOFF_FILE
    try:  # a fresh file (mode 0600), never through a planted link
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(redact(strip_query(text or ""), list(secrets or [])))
    os.replace(tmp, path)
    return True


async def _gather(s, task, since, ending=None):
    from ..access import can_see_task, effective_project_id
    from ..auth import Principal
    from ..config import settings
    from ..constants import Status
    from ..guard import runtime as guard_runtime
    from ..models import ActionApproval, Project, Task, TaskEvent, TaskHandoff, User

    root = task.root_id or task.id
    thread = (await s.execute(select(Task).where(Task.root_id == root).order_by(Task.created_at))).scalars().all()
    if task not in thread:
        thread = [*thread, task]
    main = [t for t in thread if t.origin != "agent"]
    if task.origin != "agent":
        described = task
    else:
        described = ([t for t in main if t.started_at] or main or [task])[-1]
    steps = main[: main.index(described) + 1] if described in main else [*main, described]
    ending = ending or {}

    def step(t):  # a finishing step's terminal fields, not committed yet
        return {**_step_dict(t), **ending} if t.id == task.id else _step_dict(t)

    children = Counter(ending.get("status", t.status) if t.id == task.id else t.status
                       for t in thread if t.origin == "agent")
    evs = (await s.execute(select(TaskEvent).where(TaskEvent.task_id == described.id)
                           .order_by(TaskEvent.seq.desc()).limit(EVENTS_SCAN_MAX))).scalars().all()
    events = [{"type": e.type, "payload": e.payload or {}, "created_at": e.created_at, "seq": e.seq}
              for e in reversed(evs)]
    pid = await effective_project_id(s, described)
    proj = await s.get(Project, pid) if pid else None
    workspace = described.workspace_dir or str(settings.workspaces_dir / described.id)
    project_dir = str(Path((proj.directory if proj is not None else None) or workspace).resolve())
    source_dirs = []
    if proj is not None:
        for e in proj.readonly_paths or []:
            if isinstance(e, dict) and isinstance(e.get("path"), str):
                source_dirs.append(posixpath.join(project_dir, e["path"]))
        ref_ids = [r for r in (proj.reference_project_ids or []) if isinstance(r, str)]
        if ref_ids:
            refs = (await s.execute(select(Project.directory).where(Project.id.in_(ref_ids)))).scalars().all()
            source_dirs += [d for d in refs if d]
    now = datetime.now(timezone.utc)
    pend = (await s.execute(select(ActionApproval).where(
        ActionApproval.task_id.in_([t.id for t in thread]), ActionApproval.status == "pending",
        or_(ActionApproval.expires_at > now, ActionApproval.resume_until > now)))).scalars().all()
    waiting = [f"{a.tool}: {a.summary}" if a.summary else a.tool for a in pend]
    others = []
    if described.workspace_dir:
        rows = (await s.execute(select(Task).where(
            Task.status == Status.RUNNING, Task.workspace_dir == described.workspace_dir,
            or_(Task.root_id.is_(None), Task.root_id != root),  # a legacy row has no root_id
            Task.id.notin_([t.id for t in thread]))  # never the thread's own steps
            .order_by(Task.started_at.desc()).limit(OTHERS_MAX + 1))).scalars().all()
        owner = await s.get(User, described.created_by) if described.created_by else None
        for t in rows[:OTHERS_MAX]:
            seen = owner is not None and await can_see_task(s, Principal(owner), t)
            others.append(t.title if seen else "a run you cannot see")
    row = (await s.execute(select(TaskHandoff).where(TaskHandoff.root_id == root))).scalar_one_or_none()
    trust = (proj.trust if proj is not None else None) or "own"
    prev = (row.content or {}) if row is not None else {}
    base = since or prev.get("base_head") or prev.get("head") or described.head_at_start
    policy = {"project_dir": project_dir, "home": guard_runtime.real_home(), "tmpdir": os.environ.get("TMPDIR")}
    own_texts = [str(e["payload"].get("content") or "") for e in events if e["type"] == "tool_result"]
    return {
        "root": root, "described": described, "steps": [step(t) for t in steps], "events": events,
        "children": dict(children), "workspace": workspace, "project_dir": project_dir, "sources": source_dirs,
        "waiting": waiting, "others": others, "trust": trust, "base": base, "policy": policy, "own": own_texts,
        "prev": prev,
        # the thread's latest run start (Continue, Retry and follow-ups set it when their run starts)
        "started": max((a for a in (_aware(t.started_at) for t in thread) if a), default=None),
    }


async def snapshot(task_id: str, reason: str, *, secrets=None, since=None, ending=None):
    """Build and store the thread's handoff; the stored TaskHandoff, or None
    when the task is gone. ``since``: the run's outside-commit base (else the
    stored handoff's base_head, else its head, else head_at_start). ``ending``:
    the finishing step's terminal fields (status, completed_at, result_text),
    which the finish snapshot records before they are committed."""
    from sqlalchemy.exc import IntegrityError

    from ..database import SessionLocal
    from ..models import Task, TaskHandoff

    async with SessionLocal() as s:
        task = await s.get(Task, task_id)
        if task is None:
            return None
        g = await _gather(s, task, since, ending)

    budget = gitfacts.Budget()
    facts = await asyncio.to_thread(gitfacts.facts, g["workspace"], budget)
    outside = {"commits": [], "rewritten": False}
    if facts.is_repo and facts.head:
        outside = await asyncio.to_thread(
            lambda: gitfacts.outside_commits(g["workspace"], since=g["base"], head=facts.head,
                                             own=gitfacts.own_shas(g["own"]), budget=budget))
    sources = []
    for d in g["sources"][:SOURCES_MAX]:
        sf = await asyncio.to_thread(gitfacts.facts, d, budget)
        sources.append({"path": d, "head": sf.head})
    now = datetime.now(timezone.utc)
    content = build(HandoffInput(
        steps=g["steps"], step_events=g["events"], facts=facts, outside=outside, sources=sources,
        waiting=g["waiting"], children=g["children"], others=g["others"], trust=g["trust"],
        workspace=g["workspace"], project_dir=g["project_dir"], policy=g["policy"], now=now, reason=reason,
        secrets=list(secrets or [])))
    prev = g["prev"]
    # the outside-commit base: a run's own snapshot moves it to the head it saw; any other keeps the stored one
    content["base_head"] = (facts.head if reason in RUN_REASONS or not prev
                            else prev.get("base_head") or prev.get("head"))
    text = render(content)

    for attempt in (1, 2):  # a concurrent first insert of the thread's row: update it instead
        try:
            async with SessionLocal() as s:
                row = (await s.execute(select(TaskHandoff).where(TaskHandoff.root_id == g["root"]))
                       ).scalar_one_or_none()
                if row is None:
                    row = TaskHandoff(root_id=g["root"], version=1)
                    s.add(row)
                elif not _same(row.content, content):
                    row.prev_content, row.prev_text = row.content, row.text
                    row.version = (row.version or 0) + 1
                row.task_id = g["described"].id
                row.content, row.text, row.reason, row.saved_at = content, text, reason, now
                if _reopens(row, reason, g["started"]):
                    row.status, row.closed_at, row.closed_by = "open", None, None
                await s.commit()
                return row
        except IntegrityError:
            if attempt == 2:
                raise
    return None


def _reopens(row, reason: str, started) -> bool:
    """A closed handoff stays closed (its content still updates) until a new run of the thread: a run that
    started after the close (a person's Continue reopens it with ``reopen`` once its step is queued)."""
    if row.status != "closed":
        return True
    closed_at = _aware(row.closed_at)
    return closed_at is None or (started is not None and started > closed_at)


async def reopen(task_id: str) -> bool:
    """Reopen the thread's closed handoff (a Continue's step was just queued). True when it reopened."""
    from ..database import SessionLocal
    from ..models import Task, TaskHandoff

    async with SessionLocal() as s:
        task = await s.get(Task, task_id)
        if task is None:
            return False
        row = (await s.execute(select(TaskHandoff).where(TaskHandoff.root_id == (task.root_id or task.id)))
               ).scalar_one_or_none()
        if row is None or row.status != "closed":
            return False
        row.status, row.closed_at, row.closed_by = "open", None, None
        await s.commit()
        return True


async def auto_close(task_id: str) -> bool:
    """Close the thread's handoff (closed_by "system") when this main-line step completed and nothing is left
    open: no open question in the thread, no active step, no open todo for the step. Notes are kept. True when
    it closed the handoff; a handoff already closed (by a person or earlier) is left as it is."""
    from ..constants import Status
    from ..database import SessionLocal
    from ..models import Task, TaskHandoff, TaskQuestion

    async with SessionLocal() as s:
        task = await s.get(Task, task_id)
        if task is None or task.status != Status.COMPLETED or task.origin == "agent":
            return False
        root = task.root_id or task.id
        row = (await s.execute(select(TaskHandoff).where(TaskHandoff.root_id == root))).scalar_one_or_none()
        if row is None or row.status == "closed":
            return False
        asked = (await s.execute(select(TaskQuestion.id).where(
            TaskQuestion.root_id == root, TaskQuestion.status == "open").limit(1))).first()
        if asked is not None:
            return False
        active = (await s.execute(select(Task.id).where(
            or_(Task.root_id == root, Task.id == root), Task.status.in_(Status.ACTIVE)).limit(1))).first()
        if active is not None or open_todo(row.content, task_id):
            return False
        row.status, row.closed_at, row.closed_by = "closed", datetime.now(timezone.utc), "system"
        await s.commit()
        return True


def open_todo(content: dict, task_id: str) -> bool:
    """(Phase 3b: the automatic Continue and Home "Open handoffs".) The stored handoff describes this step and its progress has an item not completed: a ``next``
    one, a current one that is not "all steps done", or (from the notes' header) n below m."""
    if not isinstance(content, dict) or content.get("step_task_id") != task_id:
        return False
    st = content.get("step") if isinstance(content.get("step"), dict) else {}
    if st.get("source") in (None, "prompt") or not st.get("m"):
        return False
    if content.get("next"):
        return True
    if st.get("source") == "notes":
        return isinstance(st.get("n"), int) and st["n"] < st["m"]
    return st.get("text") != "all steps done"
