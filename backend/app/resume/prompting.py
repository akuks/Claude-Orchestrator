"""The one composer of a step's prompt, and the resume-or-fresh decision.

``decide_session`` runs before the step's new ``credential_ref`` is stored
(F1). ``compose`` implements the spec's table (with row 8, F17):

| # | Case                                 | Prompt                                                   |
|---|--------------------------------------|----------------------------------------------------------|
| 1 | recovery, resumed                    | record + log tail + RECOVERED_CONTINUE                   |
| 2 | recovery, fresh                      | handoff + record + log tail + ``# Task`` + prompt        |
| 3 | ``continue``, resumed                | handoff + log tail + the Continue prompt                 |
| 4 | ``continue``, fresh                  | project context + handoff + log tail + the Continue      |
|   |                                      | prompt + REQUEST_LINE                                    |
| 5 | ``user`` follow-up, resumed          | the prompt (+ OUTSIDE_LINE when there are outside commits)|
| 6 | wanted to resume but fresh           | project context + handoff + ``# Task`` + prompt          |
| 7 | ``guard`` follow-up                  | the prompt, byte for byte                                |
| 8 | new thread                           | project context + others + no-commits + ``# Task`` +     |
|   |                                      | prompt; no handoff                                       |

A recovery (rows 1-2) comes first, also for a guard step: its record says
what to check before anything is repeated. A step that fits no row (a
dispatched child) keeps today's project context +
``# Task`` + prompt. Every injected block is framed as a record (its head
text), redacted with the run's secrets plus token shapes and cleaned of hidden
characters before it is joined; the person's own prompt is passed unchanged."""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select

from ..redaction import clean_text, redact
from . import gitfacts, logfmt
from .texts import (
    CRED_NOTE,
    HISTORY_CHANGED,
    LOG_HEAD,
    MAYBE_MORE,
    NO_COMMITS_LINE,
    NO_SESSION_NOTE,
    OTHER_SENDER_NOTE,
    OTHERS_HIDDEN,
    OTHERS_LINE,
    OUTSIDE_LINE,
    RECOVERED_CONTINUE,
    REQUEST_LINE,
)

RESUME_ORIGINS = ("user", "continue", "guard")
SEP = "\n\n---\n\n"
OUTSIDE_SHOWN = 5
COMMITS_CAP = 50  # gitfacts.commits_between's limit: a full list may be longer
OTHERS_SCAN = 50
REQUEST_MAX = 4000
TITLE_MAX = 120


def recovering(task) -> bool:
    return bool(isinstance(task.recovery, dict) and task.recovery.get("pending"))


def wanted_resume(task) -> bool:
    """A step of an existing thread (F3): it has a parent step, a resuming
    origin, and joined that thread (a duplicate starts its own). A recovered
    step wants its own session (``decide_session``)."""
    return (task.parent_task_id is not None and task.origin in RESUME_ORIGINS
            and task.root_id is not None and task.root_id != task.id)


def _session_file_exists(config_dir: str | None, session_id: str) -> bool:
    """Whether ``<config_dir>/projects/**/<session_id>.jsonl`` exists."""
    # The id becomes a glob pattern: refuse separators and glob characters.
    if not config_dir or not session_id or any(c in session_id for c in "/\\*?[]"):
        return False
    projects = Path(config_dir) / "projects"
    if not projects.is_dir():
        return False
    name = f"{session_id}.jsonl"
    return any(p.is_file() for p in projects.rglob(name))


async def decide_session(s, task, cred_ref: str | None, config_dir: str | None) -> tuple[str | None, str | None]:
    """``(resume_id, None)`` to resume, ``(None, reason)`` when the step wanted
    to resume and cannot, ``(None, None)`` when it never wanted to. Call it
    before ``task.credential_ref`` is set to ``cred_ref`` (F1): a recovered
    step compares with its own earlier ref."""
    from ..models import Task

    if recovering(task):
        owner_step = task
    elif wanted_resume(task):
        owner_step = await s.get(Task, task.parent_task_id)
        # the follow-up router already cleared the resume id for another sender
        if owner_step is not None and owner_step.created_by != task.created_by:
            return None, OTHER_SENDER_NOTE
    else:
        return None, None
    resume_id = task.resume_session_id
    if not resume_id:
        return None, None
    if not _session_file_exists(config_dir, resume_id):
        return None, NO_SESSION_NOTE
    if owner_step is not None and owner_step.credential_ref and owner_step.credential_ref != cred_ref:
        return None, CRED_NOTE
    return resume_id, None


# ---- blocks --------------------------------------------------------------------------------------


def _clean(text: str, secrets, limit: int | None = None) -> str:
    """Redact first (the run's secrets plus token shapes), then clean and cut."""
    return clean_text(redact(str(text or ""), list(secrets or [])), limit)


async def _handoff_text(s, task) -> str | None:
    from ..models import TaskHandoff

    row = (await s.execute(select(TaskHandoff).where(TaskHandoff.root_id == (task.root_id or task.id)))
           ).scalar_one_or_none()
    return row.text if row is not None and row.text else None


async def _log_tail(s, step_id: str | None) -> str | None:
    from ..models import TaskEvent
    from .handoff import EVENTS_SCAN_MAX

    if not step_id:
        return None
    rows = (await s.execute(select(TaskEvent).where(TaskEvent.task_id == step_id)
                            .order_by(TaskEvent.seq.desc()).limit(EVENTS_SCAN_MAX))).scalars().all()
    events = [{"type": e.type, "payload": e.payload or {}, "created_at": e.created_at} for e in reversed(rows)]
    tail = logfmt.tail(events)
    return f"{LOG_HEAD}\n{tail}" if tail else None


async def _request(s, task) -> str | None:
    """The thread's last request from a person (a ``user`` step's prompt)."""
    from ..models import Task

    root = task.root_id or task.id
    row = (await s.execute(select(Task).where(Task.root_id == root, Task.origin == "user", Task.id != task.id)
                           .order_by(Task.created_at.desc()).limit(1))).scalar_one_or_none()
    return row.prompt if row is not None and row.prompt else None


async def _others(s, task, secrets) -> str | None:
    """Other runs working in this directory now: titles of the runs the
    owner can see, a count of the rest."""
    from ..access import can_see_task
    from ..auth import Principal
    from ..constants import Status
    from ..models import Task, User
    from .handoff import OTHERS_MAX

    if not task.workspace_dir:
        return None
    root = task.root_id or task.id
    rows = (await s.execute(select(Task).where(
        Task.status == Status.RUNNING, Task.workspace_dir == task.workspace_dir, Task.root_id != root)
        .order_by(Task.started_at.desc()).limit(OTHERS_SCAN))).scalars().all()
    if not rows:
        return None
    owner = await s.get(User, task.created_by) if task.created_by else None
    shown, hidden = [], 0
    for t in rows:
        if len(shown) < OTHERS_MAX and owner is not None and await can_see_task(s, Principal(owner), t):
            shown.append('"' + " ".join(_clean(t.title, secrets).split())[:TITLE_MAX] + '"')
        else:
            hidden += 1
    if hidden:
        shown.append(OTHERS_HIDDEN.format(n=hidden))
    return OTHERS_LINE.format(list=", ".join(shown))


def _git_facts(directory):
    return gitfacts.facts(directory, gitfacts.Budget())


async def _no_commits(task) -> str | None:
    if not task.workspace_dir:
        return None
    f = await asyncio.to_thread(_git_facts, task.workspace_dir)
    return NO_COMMITS_LINE if f.is_repo and f.no_commits else None


async def _outside(task) -> str | None:
    """Commits made outside the thread since its last saved head (none of
    them are this new step's own yet)."""
    from . import handoff

    if not task.workspace_dir:
        return None
    since = await handoff.boundary(task.id)
    if not since:
        return None

    def go():
        budget = gitfacts.Budget()
        f = gitfacts.facts(task.workspace_dir, budget)
        if not (f.is_repo and f.head):
            return None
        return gitfacts.outside_commits(task.workspace_dir, since=since, head=f.head, own=set(), budget=budget)

    out = await asyncio.to_thread(go)
    if not out:
        return None
    if out.get("rewritten"):
        return OUTSIDE_LINE.format(list=HISTORY_CHANGED)
    commits = out.get("commits") or []
    if not commits:
        return None
    items = [f"{c.sha[:7]} {c.subject}".strip() for c in commits[:OUTSIDE_SHOWN]]
    if len(commits) > OUTSIDE_SHOWN:
        items.append(f"{len(commits) - OUTSIDE_SHOWN} more")
    if len(commits) >= COMMITS_CAP:  # the list is capped: never present it as complete
        items.append(MAYBE_MORE)
    return OUTSIDE_LINE.format(list="; ".join(items))


async def _project_context(task, prompt: str) -> tuple[str | None, dict]:
    from .. import memory

    if not task.project_id:
        return None, {}
    return await memory.assemble_context(task.project_id, prompt)


# ---- compose -------------------------------------------------------------------------------------


async def compose(s, task, *, base_prompt: str, resumed: bool, wanted_resume: bool,
                  recovery_block: str | None, secrets) -> tuple[str, dict]:
    """``(prompt, log)``: the step's prompt for its table row, and
    ``{"blocks": [...], "project": <assemble_context log>}``."""
    origin = task.origin
    log: dict = {"blocks": [], "project": {}}
    if origin == "guard" and recovery_block is None:  # row 7: the Phase 2 text, unchanged
        return base_prompt, log

    blocks: list[tuple[str, str]] = []  # (name, text), in prompt order

    def add(name, text):
        if text:
            blocks.append((name, _clean(text, secrets)))

    async def project(prompt_for):
        pre, ctx = await _project_context(task, prompt_for)
        log["project"] = ctx or {}
        add("project_context", pre)

    tail_text = base_prompt
    if recovery_block is not None:
        if resumed:  # row 1
            add("record", recovery_block)
            add("log_tail", await _log_tail(s, task.id))
            tail_text = RECOVERED_CONTINUE
        else:  # row 2
            add("handoff", await _handoff_text(s, task))
            add("record", recovery_block)
            add("log_tail", await _log_tail(s, task.id))
            tail_text = f"# Task\n\n{base_prompt}"
    elif origin == "continue":
        if resumed:  # row 3
            add("handoff", await _handoff_text(s, task))
            add("log_tail", await _log_tail(s, task.parent_task_id))
        else:  # row 4
            request = await _request(s, task)
            await project(request or base_prompt)
            add("handoff", await _handoff_text(s, task))
            add("log_tail", await _log_tail(s, task.parent_task_id))
            if request:
                tail_text = f"{base_prompt}\n\n{_clean(REQUEST_LINE.format(request=request), secrets, REQUEST_MAX)}"
                log["blocks"].append("request")
    elif wanted_resume:
        if resumed:  # row 5
            outside = await _outside(task)
            if not outside:
                return base_prompt, log
            log["blocks"] = ["outside"]
            return f"{base_prompt}\n\n{_clean(outside, secrets)}", log
        await project(base_prompt)  # row 6
        add("handoff", await _handoff_text(s, task))
        tail_text = f"# Task\n\n{base_prompt}"
    elif task.parent_task_id is None or task.root_id == task.id:  # row 8: a new thread
        await project(base_prompt)
        add("others", await _others(s, task, secrets))
        add("no_commits", await _no_commits(task))
        tail_text = f"# Task\n\n{base_prompt}"
    else:  # a dispatched child: today's project context only
        await project(base_prompt)
        tail_text = f"# Task\n\n{base_prompt}"

    log["blocks"] = [name for name, _ in blocks] + log["blocks"]
    if not blocks and tail_text.startswith("# Task\n\n"):
        return base_prompt, log  # nothing to inject: the prompt as it came
    return SEP.join([text for _, text in blocks] + [tail_text]), log
