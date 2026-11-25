"""Decision track record (phase 8a): which tasks were shown which recorded decision, what happened to those
tasks afterwards, and a score per decision computed on read.

* ``record_shown`` - the worker's spool ingest: one row per task and decision number (the first showing kept),
  under the task's effective project (an ad-hoc thread step: its thread root's project).
* ``settle_task`` / ``settle_run`` - the outcome of the tasks' showings (on task finish; when a workflow stage
  settles). Settles of one run are serialized (one in flight, at most one waiting; more are dropped - the waiting
  one reads the newest state). Background jobs through ``spawn_settle_task`` / ``spawn_settle_run``; they never
  raise.
* ``track_for`` - the decisions list's ``track`` per decision (one grouped query over the last 90 days).
* ``backfill`` - an admin's "Build from past runs": rows from the ``decision_note`` events still present, read in
  batches.

Outcomes: ``failed`` (the task failed, or the gate after its step finally failed on a real check); ``fixed`` (the
gate after its step needed a fix-it round); ``passed`` (that gate passed with no fix round); ``completed`` (a task
outside a workflow, or a workflow step with no gate after it, that completed). A fix task (tagged ``fix``) is
judged by its gate's final result: ``passed`` or ``failed``, never ``fixed``. No outcome (not counted): a
cancelled task, a gate that has not settled, a gate stopped by cancel or by its own infrastructure (internal
error, guard refusal, missing command/folder, fixer gone), and a task of an older stage attempt. A settled
outcome is never cleared back to none."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, exists, func, or_, select, update
from sqlalchemy.orm import aliased

from .. import flowgraph
from ..access import effective_project_id
from ..constants import Status
from ..database import SessionLocal
from ..gates.spec import stage_kind
from ..models import DecisionShowing, Task, TaskEvent, WorkflowRun

log = logging.getLogger(__name__)

MAX_IDS = 20  # decision numbers kept from one note
WINDOW = timedelta(days=90)
TROUBLE_MIN = 5  # showings with an outcome before a decision can be flagged
TROUBLE_RATE = 0.5
TROUBLE_FACTOR = 1.5  # times the project's overall rate over the same window
OUTCOMES = ("passed", "completed", "fixed", "failed")
BATCH = 1000  # backfill: events per read, rows per insert
SETTLE_CHUNK = 500

_JOBS: set = set()
_RUN_LOCKS: dict[str, asyncio.Lock] = {}
_RUN_QUEUED: dict[str, int] = {}  # settles of a run in flight + waiting (at most 2)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def clean_ids(ids) -> list[int]:
    """Decision numbers from a note (positive ints, no bools), first seen order, at most MAX_IDS."""
    if not isinstance(ids, list):
        return []
    out: list[int] = []
    for i in ids:
        if isinstance(i, int) and not isinstance(i, bool) and i > 0 and i not in out:
            out.append(i)
    return out[:MAX_IDS]


def _insert(s):
    if s.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert(DecisionShowing)


async def _add_rows(s, rows: list[dict]) -> int:
    """Insert showings in multi-row statements of at most BATCH rows, skipping (task, number) pairs already
    there (the first showing kept). Returns how many were added. Caller commits."""
    added = 0
    for k in range(0, len(rows), BATCH):
        stmt = _insert(s).values(rows[k:k + BATCH]).on_conflict_do_nothing(
            index_elements=["task_id", "decision_number"])
        res = await s.execute(stmt)
        added += max(res.rowcount or 0, 0)
    return added


async def record_shown(task_id: str, numbers: list[int], shown_at: datetime | None = None) -> None:
    """The task was shown these decision numbers: one row each (kept if already there), under its effective
    project. Never raises."""
    numbers = clean_ids(numbers)
    if not numbers:
        return
    try:
        async with SessionLocal() as s:
            task = await s.get(Task, task_id)
            project_id = await effective_project_id(s, task) if task is not None else None
            if not project_id:
                return
            at = shown_at or _now()
            await _add_rows(s, [{"project_id": project_id, "decision_number": n, "task_id": task_id,
                                 "shown_at": at, "retried": False} for n in numbers])
            await s.commit()
    except Exception as e:  # noqa: BLE001 - the track record never stops a run
        log.warning("decision track: recording a showing failed for %s: %s", task_id, e)


# ---- outcomes ---------------------------------------------------------------------------------------------


def _infra_reasons() -> tuple[set[str], tuple[str, ...]]:
    """Gate `failed` reasons that are not a real check failing: (exact reasons, prefixes)."""
    from .. import workflow_runner as wr

    exact = {"cancelled", wr.GATE_ERROR_WORDS, wr.FIXER_GONE_WORDS, "the gate has no command to run",
             "the gate's project is missing or has no directory"}
    return exact, ("blocked by the guard",)


def _real_failure(st: dict) -> bool:
    if st.get("status") != "failed":
        return False
    reason = str(st.get("reason") or "")
    exact, prefixes = _infra_reasons()
    return reason not in exact and not reason.startswith(prefixes)


def _gates_for(stages: list, j: int) -> list[int]:
    """The gate stages that decide a step's outcome: itself when it is a gate (its fix tasks), else the gates
    that wait for it directly."""
    if not (0 <= j < len(stages)):
        return []
    if stage_kind(stages[j]) == "gate":
        return [j]
    return [k for k in range(j + 1, len(stages)) if stage_kind(stages[k]) == "gate" and j in flowgraph.deps(stages, k)]


def _state(run: WorkflowRun, i: int) -> dict:
    return dict((run.stage_state or {}).get(str(i)) or {})


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def outcome_of(t, run: WorkflowRun | None, fix_tasks: set[tuple[int, int]]) -> str | None:
    """The outcome of a finished task (None: not counted / not settled yet). ``t`` needs status,
    workflow_run_id, stage_index, stage_attempt, tags. ``fix_tasks``: the run's (stage index, stage attempt)
    pairs that have a fix-tagged task."""
    if t.status == Status.FAILED:
        return "failed"
    if t.status != Status.COMPLETED:
        return None  # cancelled or not finished
    if not t.workflow_run_id or run is None or t.stage_index is None:
        return "completed"
    stages = list(run.stages or [])
    j = t.stage_index
    if not (0 <= j < len(stages)):
        return "completed"
    if _int(t.stage_attempt) != _int(_state(run, j).get("attempt")):
        return None  # an older attempt of its step: the current gate result is not about it
    gates = _gates_for(stages, j)
    if not gates:
        return "completed"
    states = [_state(run, k) for k in gates]
    if any(_real_failure(st) for st in states):
        return "failed"
    is_fix = isinstance(t.tags, list) and "fix" in t.tags and stage_kind(stages[j]) == "gate"
    if is_fix:  # the fixer is judged by whether its fix worked: the gate's final result
        return "passed" if all(st.get("status") == "passed" for st in states) else None
    if any(st.get("status") == "fixing" for st in states):
        return "fixed"
    if all(st.get("status") == "passed" for st in states):
        fixed = any(_int(st.get("fix_round")) >= 1 or (k, _int(st.get("attempt"))) in fix_tasks
                    for k, st in zip(gates, states))
        return "fixed" if fixed else "passed"
    return None  # the gate has not settled (or stopped on cancel / its own infrastructure)


_COLS = (Task.id, Task.status, Task.attempt, Task.workflow_run_id, Task.stage_index, Task.stage_attempt,
         Task.tags)


async def _write(s, rows, run: WorkflowRun | None, fix_tasks: set) -> None:
    now = _now()
    for t in sorted(rows, key=lambda r: r.id):  # one order: concurrent writers never deadlock
        out = outcome_of(t, run, fix_tasks)
        values = {"retried": _int(t.attempt) > 0}
        if out is not None:
            values.update(outcome=out, settled_at=now)
        await s.execute(update(DecisionShowing).where(DecisionShowing.task_id == t.id).values(**values)
                        .execution_options(synchronize_session=False))


def _has_showing():
    return exists().where(DecisionShowing.task_id == Task.id)


async def _settle_run_now(run_id: str) -> None:
    async with SessionLocal() as s:
        run = await s.get(WorkflowRun, run_id)
        rows = (await s.execute(select(*_COLS).where(Task.workflow_run_id == run_id).order_by(Task.id))).all()
        fix_tasks = {(r.stage_index, _int(r.stage_attempt)) for r in rows
                     if r.stage_index is not None and isinstance(r.tags, list) and "fix" in r.tags}
        shown = set((await s.execute(select(DecisionShowing.task_id).join(Task, Task.id == DecisionShowing.task_id)
                                     .where(Task.workflow_run_id == run_id))).scalars().all())
        await _write(s, [r for r in rows if r.id in shown], run, fix_tasks)
        await s.commit()


async def settle_run(run_id: str) -> None:
    """Settle every showing task of a workflow run. Serialized per run: one in flight, at most one waiting
    (it reads the newest state after the one in flight); further calls meanwhile are dropped. Never raises."""
    if _RUN_QUEUED.get(run_id, 0) >= 2:
        return
    _RUN_QUEUED[run_id] = _RUN_QUEUED.get(run_id, 0) + 1
    try:
        lock = _RUN_LOCKS.setdefault(run_id, asyncio.Lock())
        async with lock:
            await _settle_run_now(run_id)
    except Exception as e:  # noqa: BLE001 - the track record never breaks a finish
        log.warning("decision track: settling run %s failed: %s", run_id, e)
    finally:
        n = _RUN_QUEUED.get(run_id, 1) - 1
        if n <= 0:
            _RUN_QUEUED.pop(run_id, None)
            _RUN_LOCKS.pop(run_id, None)
        else:
            _RUN_QUEUED[run_id] = n


async def settle_tasks(task_ids: list[str]) -> None:
    """Settle the showings of these tasks (a workflow task: its whole run, once per run). Never raises."""
    if not task_ids:
        return
    run_ids: set[str] = set()
    try:
        async with SessionLocal() as s:
            for k in range(0, len(task_ids), SETTLE_CHUNK):
                rows = (await s.execute(select(*_COLS).where(Task.id.in_(task_ids[k:k + SETTLE_CHUNK]),
                                                             _has_showing()).order_by(Task.id))).all()
                run_ids |= {r.workflow_run_id for r in rows if r.workflow_run_id}
                await _write(s, [r for r in rows if not r.workflow_run_id], None, set())
            await s.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("decision track: settling failed for %s: %s", task_ids[:3], e)
    for rid in sorted(run_ids):
        await settle_run(rid)


async def settle_task(task_id: str) -> None:
    await settle_tasks([task_id])


def _spawn(coro) -> None:
    try:
        job = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:  # no loop: nothing to run it on
        coro.close()
        return
    _JOBS.add(job)
    job.add_done_callback(_JOBS.discard)


def spawn_settle_task(task_id: str) -> None:
    """Fire-and-forget ``settle_task`` (a finishing task never waits for it; nothing happens without showings)."""
    _spawn(settle_task(task_id))


def spawn_settle_run(run_id: str) -> None:
    _spawn(settle_run(run_id))


async def drain() -> None:
    """Wait for the background settle jobs (tests)."""
    while _JOBS:
        await asyncio.gather(*list(_JOBS), return_exceptions=True)


# ---- score ------------------------------------------------------------------------------------------------


def _empty() -> dict:
    return {"shown": 0, "passed": 0, "completed": 0, "fixed": 0, "failed": 0, "last_shown_at": None,
            "trouble": False}


async def track_for(s, project_id: str, numbers: list[int]) -> dict[int, dict]:
    """``{number: track}`` for these decision numbers: showings over the last 90 days, by outcome, the last
    showing, and ``trouble`` (>= 5 showings with an outcome, (fixed + failed) / those >= 50%, and >= 1.5x the
    project's overall rate over the same window). One grouped query."""
    cutoff = _now() - WINDOW
    cols = [func.count(DecisionShowing.id)] + [
        func.sum(case((DecisionShowing.outcome == o, 1), else_=0)) for o in OUTCOMES]
    rows = (await s.execute(
        select(DecisionShowing.decision_number, *cols, func.max(DecisionShowing.shown_at))
        .where(DecisionShowing.project_id == project_id, DecisionShowing.shown_at >= cutoff)
        .group_by(DecisionShowing.decision_number))).all()
    by: dict[int, dict] = {}
    total_bad = total_with = 0
    for n, shown, passed, completed, fixed, failed, last in rows:
        tr = {"shown": int(shown or 0), "passed": int(passed or 0), "completed": int(completed or 0),
              "fixed": int(fixed or 0), "failed": int(failed or 0), "last_shown_at": last, "trouble": False}
        if last is not None and last.tzinfo is None:
            tr["last_shown_at"] = last.replace(tzinfo=timezone.utc)
        by[n] = tr
        total_with += tr["passed"] + tr["completed"] + tr["fixed"] + tr["failed"]
        total_bad += tr["fixed"] + tr["failed"]
    overall = total_bad / total_with if total_with else 0.0
    out = {}
    for n in numbers:
        tr = by.get(n) or _empty()
        with_outcome = tr["passed"] + tr["completed"] + tr["fixed"] + tr["failed"]
        if with_outcome >= TROUBLE_MIN:
            rate = (tr["fixed"] + tr["failed"]) / with_outcome
            tr["trouble"] = rate >= TROUBLE_RATE and rate >= TROUBLE_FACTOR * overall
        out[n] = tr
    return out


# ---- backfill ---------------------------------------------------------------------------------------------


async def backfill(project_id: str) -> int:
    """Showings from the project's ``decision_note`` events still present (its own tasks and the ad-hoc steps
    of its threads), read by event id in batches of BATCH, one multi-row insert and commit per batch; pairs
    already recorded are skipped (the earliest showing wins), so running it twice adds nothing. Then settles
    the touched tasks (each run once). Returns how many rows were added."""
    root = aliased(Task)
    in_project = or_(Task.project_id == project_id,
                     and_(Task.project_id.is_(None), root.project_id == project_id))
    added, last_id = 0, 0
    touched: set[str] = set()
    while True:
        async with SessionLocal() as s:
            events = (await s.execute(
                select(TaskEvent.id, TaskEvent.task_id, TaskEvent.payload, TaskEvent.created_at)
                .join(Task, Task.id == TaskEvent.task_id)
                .outerjoin(root, root.id == Task.root_id)
                .where(TaskEvent.type == "decision_note", TaskEvent.id > last_id, in_project)
                .order_by(TaskEvent.id).limit(BATCH))).all()
            if not events:
                break
            last_id = events[-1].id
            first: dict[tuple[str, int], datetime] = {}
            for _, tid, payload, at in events:
                for n in clean_ids(payload.get("ids") if isinstance(payload, dict) else None):
                    if (tid, n) not in first:  # ordered by id: the earliest of this batch
                        first[(tid, n)] = at
            touched |= {t for t, _ in first}
            if first:
                added += await _add_rows(s, [
                    {"project_id": project_id, "decision_number": n, "task_id": t, "shown_at": at or _now(),
                     "retried": False} for (t, n), at in first.items()])
            await s.commit()
        if len(events) < BATCH:
            break
    await settle_tasks(sorted(touched))
    return added
