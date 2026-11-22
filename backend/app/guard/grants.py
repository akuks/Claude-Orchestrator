"""co-guard grants and deny-and-resume follow-ups (Phase 2, Task 6).

A grant (``ActionGrant``) is a person's approval that covers later calls:

* ``run`` - every step of one thread (``Task.root_id``, or the task's own id
  when it has none) for ``RUN_GRANT_TTL`` seconds, one row per rule id, keyed
  by ``scope_keys`` (the same keys the hook computes: ``co_hook.scope_keys``).
* ``once`` - one call of one task (a resume follow-up), keyed by the call's
  ``grant_key``; it is spent when the hook spools a ``granted`` record
  (``used_at``, set from ``/guard/report`` and, durably, from the worker's
  spool ingestion - ``mark_spool_used``).

A call is covered only when EVERY confirmable rule id has a live grant of
that rule with the right key. Hard-only rules (``rules.RULES`` with no
confirmable reason) are never grantable, nor are org/project rules that are
not stored ``confirm`` rules (``grantable_ids``); repo rules are (a repo never
rule that timed out is confirmable). A ``command`` key is only ever the
request's VERIFIED grant_key (Ruling P23, ``routers/guard._verified_key``):
a request without one is approvable once, inline, and never resumes.

Deny-and-resume: a person approving a request in its resume phase gets a
follow-up task of the request's thread (``create_resume_followup``): it runs
on the thread owner's credentials, resumes the thread's latest session (only
when that session is the owner's: Claude sessions belong to an account), and
its prompt carries one ``followup_block`` per approved request plus a once
grant per request. At most one follow-up per thread at a time:

* a queued follow-up of the thread (one an approval created, or the thread
  owner's queued Continue step - which runs in the session the Continue
  chose: the continued step's, or a fresh one with the handoff, not
  necessarily the owner's latest) is joined:
  one more block appended to its prompt (only while it is still queued) and
  one more once grant;
* while any other step of the thread is active the approval is deferred
  (``followup_task_id`` stays empty) and ``after_task_finished`` - called by
  the worker's ``_finish`` - spawns one follow-up for every deferred approval
  once no step is active; a step that ended cancelled drops them
  (``delivery`` ``dropped``: no follow-up runs on a cancelled thread), and
  so does a deferred approval past its ``resume_until`` (Ruling P24). Every
  path that ends or cancels a step calls it: the worker's ``_finish`` and
  cancelled-while-queued shortcut, the task reject and auto-reject, and the
  cancel endpoint.

The follow-up resumes the latest finished step the thread owner created that
has a session; with none it starts a fresh session on the owner's
credentials.

``followup_lock()`` serialises the join / defer / spawn choice in this server
process; the join is also a conditional ``UPDATE ... WHERE status='queued'``
and the worker re-reads the prompt after it marks a task running, so a join
that loses the race to the worker falls back to deferring.
"""

from __future__ import annotations

import asyncio
import json
import re
import weakref
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select, update

from ..constants import Status
from ..models import ActionApproval, ActionGrant, ActionRule, Task
from ..redaction import redact
from . import co_hook
from . import rules as guard_rules
from . import runtime as guard_runtime

RUN_GRANT_TTL = 43200  # s: "for this run" = this thread, 12 h
RESUME_WINDOW = 86400  # s: a deny_and_resume request stays decidable for 24 h
FOLLOWUP_SUMMARY_MAX = 4000
MAX_GRANT_IDS = co_hook.MAX_GRANT_IDS

_LOCKS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def followup_lock() -> asyncio.Lock:
    """The process's follow-up lock (one per event loop: the server has one)."""
    loop = asyncio.get_running_loop()
    lock = _LOCKS.get(loop)
    if lock is None:
        lock = _LOCKS[loop] = asyncio.Lock()
    return lock

_HEX64 = re.compile(r"[0-9a-f]{64}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # SQLite: naive UTC


def is_hex_key(key) -> bool:
    return isinstance(key, str) and bool(_HEX64.fullmatch(key))


def grantable(rule_id: str) -> bool:
    """Custom rules (``org.``/``project.``/``repo.``) and built-ins with a
    confirmable part; never a hard-only rule or an unknown built-in."""
    if not isinstance(rule_id, str) or not rule_id:
        return False
    if "." in rule_id:
        return True
    entry = guard_rules.RULES.get(rule_id)
    return entry is not None and entry[0] is not None


async def grantable_ids(s, rule_ids, project_id) -> set[str]:
    """The rule ids a person may grant: ``grantable`` built-ins and repo
    rules, plus org/project rules that exist as ``confirm`` rules (an org or
    project ``never`` rule, or one since removed, is not grantable)."""
    out = set()
    for rid in dict.fromkeys(r for r in (rule_ids or []) if isinstance(r, str)):
        if not grantable(rid):
            continue
        scope, _, slug = rid.partition(".")
        if scope in ("org", "project") and slug:
            where = [ActionRule.scope == scope, ActionRule.slug == slug, ActionRule.kind == "confirm"]
            if scope == "project":
                if not project_id:
                    continue
                where.append(ActionRule.project_id == project_id)
            else:
                where.append(ActionRule.project_id.is_(None))
            if (await s.execute(select(ActionRule.id).where(*where).limit(1))).first() is None:
                continue
        elif "." in rid and scope != "repo":
            continue
        out.add(rid)
    return out


def scope_keys(rule_ids, tool, grant_key) -> list[tuple[str, str, str]]:
    """(rule_id, key_kind, key) a RUN grant must carry per rule id, in order,
    without repeats (``co_hook.scope_keys`` per rule)."""
    out = []
    for rid in dict.fromkeys(r for r in (rule_ids or []) if isinstance(r, str)):
        kind, key = co_hook.scope_keys(rid, tool or "", grant_key or "")
        out.append((rid, kind, key))
    return out


def run_keys(approval: ActionApproval) -> list[dict]:
    """What "approve for this run" would cover (the card's ``run_keys``)."""
    return [{"rule_id": rid, "key_kind": kind, "key": key}
            for rid, kind, key in scope_keys(approval.rule_ids, approval.tool, approval.grant_key)
            if grantable(rid)]


def _live(now: datetime):
    return and_(ActionGrant.revoked_at.is_(None), ActionGrant.expires_at > now)


async def thread_cover(s, *, root_id, rule_ids, tool, grant_key, now) -> list[ActionGrant] | None:
    """The thread's live RUN grants covering every rule id (one per rule), or
    None (no rule ids, a hard/unknown rule, a missing key, or a rule with no
    matching live grant)."""
    want = scope_keys(rule_ids, tool, grant_key)
    if not want or not root_id:
        return None
    for rid, kind, key in want:
        if not grantable(rid) or (kind == "command" and not is_hex_key(key)):
            return None
    rows = (await s.execute(
        select(ActionGrant)
        .where(ActionGrant.root_id == root_id, ActionGrant.scope == "run", _live(now),
               ActionGrant.rule_id.in_([w[0] for w in want]))
        .order_by(ActionGrant.created_at)
    )).scalars().all()
    out = []
    for rid, kind, key in want:
        g = next((g for g in rows if g.rule_id == rid and g.key_kind == kind and g.key == key), None)
        if g is None:
            return None
        out.append(g)
    return out


RUN_REFUSED = "this request can't be approved for the whole run"
ONCE_ONLY_REASON = ("This request can't be approved for the whole run (the agent filed it itself, or the command is "
                    "too long to show in full), so only Approve once is offered.")


async def run_grant_problem(s, approval: ActionApproval, cache: dict | None = None) -> str | None:
    """Why "approve for this run" cannot work for this request (the card's
    ``run_reason``), or None when it can: every rule id must be grantable
    (``grantable_ids``: never a hard-only, unknown or org/project never
    rule) and every command-keyed rule needs the request's verified
    ``grant_key`` (Ruling P23). ``cache`` memoises ``grantable_ids`` per
    (rule ids, project) across the cards of one listing."""
    ids = list(dict.fromkeys(r for r in (approval.rule_ids or []) if isinstance(r, str)))
    if not ids:
        return "This request names no rule, so only Approve once is offered."
    ck = (tuple(ids), approval.project_id)
    ok = cache.get(ck) if cache is not None else None
    if ok is None:
        ok = await grantable_ids(s, ids, approval.project_id)
        if cache is not None:
            cache[ck] = ok
    bad = [r for r in ids if r not in ok]
    if bad:
        return (f"{', '.join(bad)} can't be approved for the whole run (a hard or never rule, or one since "
                f"removed), so only Approve once is offered.")
    for _rid, kind, key in scope_keys(ids, approval.tool, approval.grant_key):
        if kind == "command" and not is_hex_key(key):
            return ONCE_ONLY_REASON
    return None


async def create_run_grants(s, approval: ActionApproval, task: Task, decider_id) -> list[ActionGrant]:
    """One run grant per rule id of the request (flushed, not committed).
    ValueError (``RUN_REFUSED``) when any rule id is not grantable or a
    command key is missing (``run_grant_problem``) - nothing is skipped."""
    if await run_grant_problem(s, approval) is not None:
        # Ruling P23 / final wave: never a partial grant, never an unverified command key
        raise ValueError(RUN_REFUSED)
    now = _now()
    out = []
    for rid, kind, key in scope_keys(approval.rule_ids, approval.tool, approval.grant_key):
        g = ActionGrant(root_id=task.root_id or task.id, task_id=task.id, approval_id=approval.id, scope="run",
                        rule_id=rid, key_kind=kind, key=key, created_by=decider_id, created_at=now,
                        expires_at=now + timedelta(seconds=RUN_GRANT_TTL))
        s.add(g)
        out.append(g)
    if not out:
        raise ValueError(RUN_REFUSED)
    await s.flush()
    return out


# ---- grant use (the hook's granted records) -------------------------------------------------------


async def valid_use(s, task: Task, grant_ids, now: datetime | None = None) -> list[ActionGrant] | None:
    """The grants named by a ``granted`` record, when every one is a live run
    grant of the task's thread or a live once grant of this task; else None."""
    ids = [g for g in grant_ids if isinstance(g, str) and g] if isinstance(grant_ids, list) else []
    ids = list(dict.fromkeys(ids))
    if not ids or len(ids) > MAX_GRANT_IDS:
        return None
    now = now or _now()
    root = task.root_id or task.id
    rows = (await s.execute(select(ActionGrant).where(ActionGrant.id.in_(ids), _live(now)))).scalars().all()
    by_id = {g.id: g for g in rows}
    out = []
    for gid in ids:
        g = by_id.get(gid)
        if g is None:
            return None
        if g.scope == "run" and g.root_id == root:
            out.append(g)
        elif g.scope == "once" and g.task_id == task.id:
            out.append(g)
        else:
            return None
    return out


async def spend_once(s, grants: list[ActionGrant], now: datetime | None = None) -> bool:
    """Spend each once grant on its own (``used_at`` from NULL); False when
    one was already spent (the record re-uses it). Not committed."""
    now = now or _now()
    fresh = True
    for g in grants:
        if g.scope == "once":
            res = await s.execute(update(ActionGrant).where(ActionGrant.id == g.id, ActionGrant.used_at.is_(None))
                                  .values(used_at=now))
            fresh = fresh and res.rowcount == 1
    return fresh


async def record_use(s, grants: list[ActionGrant], *, count: bool, now: datetime | None = None) -> None:
    """Spend once grants (``used_at``, first use only - idempotent) and, with
    ``count``, add one use to every grant. Not committed."""
    await spend_once(s, grants, now)
    if count:
        ids = [g.id for g in grants]
        await s.execute(update(ActionGrant).where(ActionGrant.id.in_(ids))
                        .values(use_count=ActionGrant.use_count + 1))


def spool_granted(task_id: str) -> list[list]:
    """The grant ids of every ``granted`` record in the task's spool (every
    line; the event reader's cap does not apply)."""
    out = []
    try:
        with open(guard_runtime.task_dir(task_id) / guard_runtime.SPOOL_NAME, encoding="utf-8",
                  errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and rec.get("decision") == "granted" \
                        and isinstance(rec.get("grant_ids"), list):
                    out.append(rec["grant_ids"])
    except OSError:
        pass
    return out


async def mark_spool_used(s, task_id: str) -> int:
    """Durably spend the once grants this task's spool says the hook used
    (before a retry or requeue wipes the spool). Committed; returns how many
    records named valid grants."""
    task = await s.get(Task, task_id)
    if task is None:
        return 0
    n = 0
    for ids in spool_granted(task_id):
        # a grant used just before it expired was still used
        grants = await valid_use(s, task, ids, now=_now() - timedelta(seconds=RESUME_WINDOW))
        if grants:
            await record_use(s, grants, count=False)
            n += 1
    await s.commit()
    return n


# ---- deny-and-resume follow-ups ---------------------------------------------------------------------


def _fence(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    mark = f"… (truncated, {len(text)} chars)"
    return text[: limit - len(mark)] + mark


def followup_block(a: ActionApproval) -> str:
    """The fixed follow-up prompt text for one approved request."""
    rules = ", ".join(str(r) for r in (a.rule_ids or [])) or "co-guard"
    summary = _cut(redact(a.summary or a.tool or "", []), FOLLOWUP_SUMMARY_MAX)
    fence = _fence(summary)
    return (f"co-guard: a person approved request {a.id} ({rules}). You may now run this once, exactly as before:\n"
            f"{fence}\n{summary}\n{fence}\nThen continue the task.")


async def _thread_root(s, task: Task) -> Task:
    root_id = task.root_id or task.id
    root = await s.get(Task, root_id) if root_id != task.id else task
    return root or task


async def _queued_followup(s, root_id: str, owner=None) -> Task | None:
    """The thread's queued follow-up an approval may join: one an approval
    created, or a queued Continue step of the thread owner (it runs on the
    owner's credentials, as a follow-up must; another person's Continue is
    an active step, so the approval is deferred). A step that already ran (a
    recovered step queued again) is never joined: its resumed prompt is
    RECOVERED_CONTINUE, so the approval is deferred and spawned after it."""
    made = select(ActionApproval.followup_task_id).where(ActionApproval.followup_task_id.is_not(None))
    joinable = Task.id.in_(made)
    if owner is not None:
        joinable = or_(joinable, and_(Task.origin == "continue", Task.created_by == owner))
    return (await s.execute(
        select(Task).where(Task.root_id == root_id, Task.status == Status.QUEUED, Task.started_at.is_(None), joinable)
        .order_by(Task.created_at.desc()).limit(1)
    )).scalar_one_or_none()


async def _active_step(s, root_id: str) -> bool:
    row = (await s.execute(
        select(Task.id).where(or_(Task.root_id == root_id, Task.id == root_id), Task.status.in_(Status.ACTIVE))
        .limit(1)
    )).first()
    return row is not None


async def _once_grants(s, a: ActionApproval, followup_id: str, root_id: str, decider_id) -> list[ActionGrant]:
    """One once grant per grantable rule id of the request (the hook needs a
    grant whose rule_id equals each hit), all keyed by the call's grant_key."""
    now = _now()
    ok = await grantable_ids(s, a.rule_ids, a.project_id)
    if not is_hex_key(a.grant_key):
        return []
    return [ActionGrant(root_id=root_id, task_id=followup_id, approval_id=a.id, scope="once", rule_id=rid,
                        key_kind="command", key=a.grant_key, created_by=decider_id, created_at=now,
                        expires_at=now + timedelta(seconds=RESUME_WINDOW))
            for rid in dict.fromkeys(r for r in (a.rule_ids or []) if isinstance(r, str)) if rid in ok]


async def _attach(s, a: ActionApproval, followup_id: str, root_id: str, decider_id) -> None:
    grants = await _once_grants(s, a, followup_id, root_id, decider_id)
    for g in grants:
        s.add(g)
    await s.flush()
    a.followup_task_id = followup_id
    a.grant_ids = list(dict.fromkeys(list(a.grant_ids or []) + [g.id for g in grants]))


async def _latest_session_step(s, root_id: str, owner) -> Task | None:
    """The latest finished step of the thread the owner created that has a
    session (Claude sessions belong to an account)."""
    return (await s.execute(
        select(Task).where(or_(Task.root_id == root_id, Task.id == root_id), Task.status.in_(Status.TERMINAL),
                           Task.created_by == owner, Task.session_id.is_not(None))
        .order_by(Task.created_at.desc()).limit(1)
    )).scalar_one_or_none()


async def _spawn(s, approvals: list[ActionApproval], root: Task, decider_id) -> Task:
    owner = root.created_by
    parent = await _latest_session_step(s, root.id, owner) or root
    blocks = "\n\n".join(followup_block(a) for a in approvals)
    child = Task(
        title=_cut(f"co-guard follow-up: approved request {approvals[0].id}"
                   + (f" (+{len(approvals) - 1})" if len(approvals) > 1 else ""), 200),
        prompt=blocks,
        project=parent.project,
        project_id=parent.project_id,
        priority=parent.priority,
        tags=list(parent.tags or []),
        model=parent.model,
        max_turns=parent.max_turns,
        max_budget_usd=parent.max_budget_usd,  # a thread step keeps the budget cut and bands
        parent_task_id=parent.id,
        origin="guard",
        root_id=root.id,
        # the owner's own session only (Claude sessions belong to an account)
        resume_session_id=parent.session_id if parent.created_by == owner else None,
        workspace_dir=parent.workspace_dir,
        status=Status.QUEUED,
        created_by=owner,
    )
    s.add(child)
    await s.flush()
    for a in approvals:
        await _attach(s, a, child.id, root.id, a.decided_by or decider_id)
    return child


async def thread_cancelled(s, task: Task) -> bool:
    """The thread's latest step was cancelled: no follow-up may start."""
    root_id = task.root_id or task.id
    latest = (await s.execute(
        select(Task.status).where(or_(Task.root_id == root_id, Task.id == root_id))
        .order_by(Task.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    return latest == Status.CANCELLED


async def create_resume_followup(s, approval: ActionApproval, decider_id, *, spawned: list | None = None
                                 ) -> str | None:
    """Join the thread's queued follow-up, defer (None) while a step is
    active, or create a new queued follow-up (appended to ``spawned`` - the
    caller submits it to the worker after committing). Call under
    ``followup_lock()``; not committed."""
    task = await s.get(Task, approval.task_id)
    if task is None:
        return None
    root = await _thread_root(s, task)
    queued = await _queued_followup(s, root.id, root.created_by)
    if queued is not None:
        res = await s.execute(
            update(Task).where(Task.id == queued.id, Task.status == Status.QUEUED)
            .values(prompt=Task.prompt.concat("\n\n" + followup_block(approval)))
            .execution_options(synchronize_session=False)
        )
        if res.rowcount == 1:
            await _attach(s, approval, queued.id, root.id, decider_id)
            return queued.id
    if await _active_step(s, root.id):
        return None  # after_task_finished spawns it
    child = await _spawn(s, [approval], root, decider_id)
    if spawned is not None:
        spawned.append(child)
    return child.id


async def _deferred(s, root_id: str) -> list[ActionApproval]:
    """The thread's deferred resume approvals still inside their window;
    older ones are dropped (Ruling P24). Not committed."""
    steps = select(Task.id).where(or_(Task.root_id == root_id, Task.id == root_id))
    rows = list((await s.execute(
        select(ActionApproval)
        .where(ActionApproval.task_id.in_(steps), ActionApproval.status == "approved",
               ActionApproval.delivery == "resume", ActionApproval.followup_task_id.is_(None))
        .order_by(ActionApproval.decided_at, ActionApproval.created_at)
    )).scalars().all())
    now = _now()
    live = []
    for a in rows:
        if a.resume_until is None or _aware(a.resume_until) <= now:
            a.delivery = "dropped"
        else:
            live.append(a)
    return live


async def after_task_finished(task_id: str, submit=None) -> str | None:
    """A step ended (worker ``_finish``): spawn one follow-up for the
    thread's deferred approvals once no step is active; drop them when the
    step was cancelled. ``submit(task_id, priority, created_at)`` queues the
    new task. Returns the new follow-up's id, if any."""
    from ..database import SessionLocal

    async with followup_lock():
        async with SessionLocal() as s:
            task = await s.get(Task, task_id)
            if task is None:
                return None
            root = await _thread_root(s, task)
            pending = await _deferred(s, root.id)
            if not pending:
                await s.commit()  # stale ones dropped
                return None
            if task.status == Status.CANCELLED:
                for a in pending:
                    a.delivery = "dropped"
                await s.commit()
                return None
            if await _active_step(s, root.id):
                await s.commit()
                return None
            decider = pending[0].decided_by
            child = await _spawn(s, pending, root, decider)
            child_id, prio, created = child.id, child.priority, child.created_at
            await s.commit()
    if submit is not None:
        await submit(child_id, prio, created)
    return child_id
