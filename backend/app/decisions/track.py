"""Decision track record (phase 8a): which tasks were shown which recorded decision, what happened to those
tasks afterwards, and a score per decision computed on read.

* ``record_shown`` - the worker's spool ingest: one row per task and decision number (the first showing kept).
* ``settle_task`` / ``settle_run`` - the outcome of the tasks' showings (on task finish; when a workflow stage
  settles). Background jobs through ``spawn_settle_task`` / ``spawn_settle_run``; they never raise.
* ``track_for`` - the decisions list's ``track`` per decision (one grouped query over the last 90 days).
* ``backfill`` - an admin's "Build from past runs": rows from the ``decision_note`` events still present.

Outcomes: ``failed`` (the task failed, or its stage's gate finally failed); ``fixed`` (its stage needed a fix-it
round); ``passed`` (its stage's gate passed with no fix round); ``completed`` (a task outside a workflow, or a
workflow step with no gate after it, that completed). A cancelled task, or one whose gate has not settled,
has no outcome (not counted). A settled outcome is never cleared back to none."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, or_, select, update

from .. import flowgraph
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

_JOBS: set = set()


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
    """Insert showings, skipping (task, number) pairs already there (the first showing kept). Returns how many
    were added. Caller commits."""
    added = 0
    for r in rows:
        stmt = _insert(s).values(**r).on_conflict_do_nothing(index_elements=["task_id", "decision_number"])
        res = await s.execute(stmt)
        added += max(res.rowcount or 0, 0)
    return added


async def record_shown(task_id: str, numbers: list[int], shown_at: datetime | None = None) -> None:
    """The task was shown these decision numbers: one row each (kept if already there). Never raises."""
    numbers = clean_ids(numbers)
    if not numbers:
        return
    try:
        async with SessionLocal() as s:
            project_id = (await s.execute(select(Task.project_id).where(Task.id == task_id))).scalar_one_or_none()
            if not project_id:
                return
            at = shown_at or _now()
            await _add_rows(s, [{"project_id": project_id, "decision_number": n, "task_id": task_id,
                                 "shown_at": at, "retried": False} for n in numbers])
            await s.commit()
    except Exception as e:  # noqa: BLE001 - the track record never stops a run
        log.warning("decision track: recording a showing failed for %s: %s", task_id, e)


# ---- outcomes ---------------------------------------------------------------------------------------------


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


def outcome_of(t: Task, run: WorkflowRun | None, fix_tasks: set[tuple[int, int]]) -> str | None:
    """The outcome of a finished task (None: not counted / not settled yet). ``fix_tasks``: the run's
    (stage index, stage attempt) pairs that have a fix-tagged task."""
    if t.status == Status.FAILED:
        return "failed"
    if t.status != Status.COMPLETED:
        return None  # cancelled or not finished
    if not t.workflow_run_id or run is None or t.stage_index is None:
        return "completed"
    stages = list(run.stages or [])
    gates = _gates_for(stages, t.stage_index)
    if not gates:
        return "completed"
    states = []
    for k in gates:
        st = _state(run, k)
        fixed = _int(st.get("fix_round")) >= 1 or (k, _int(st.get("attempt"))) in fix_tasks
        states.append((st.get("status"), st.get("reason"), fixed))
    if any(status == "failed" and reason != "cancelled" for status, reason, _ in states):
        return "failed"
    if any(status == "fixing" for status, _, _ in states):
        return "fixed"
    if all(status == "passed" for status, _, _ in states):
        return "fixed" if any(f for _, _, f in states) else "passed"
    return None  # the gate has not settled (or the run was cancelled)


async def _settle(s, tasks: list[Task]) -> None:
    runs: dict[str, WorkflowRun | None] = {}
    fixes: dict[str, set] = {}
    now = _now()
    for t in tasks:
        rid = t.workflow_run_id
        if rid and rid not in runs:
            runs[rid] = await s.get(WorkflowRun, rid)
            rows = (await s.execute(select(Task.stage_index, Task.stage_attempt, Task.tags).where(
                Task.workflow_run_id == rid))).all()
            fixes[rid] = {(si, _int(sa)) for si, sa, tags in rows
                          if si is not None and isinstance(tags, list) and "fix" in tags}
        out = outcome_of(t, runs.get(rid) if rid else None, fixes.get(rid, set()))
        retried = _int(t.attempt) > 0
        if out is None:
            await s.execute(update(DecisionShowing).where(DecisionShowing.task_id == t.id)
                            .values(retried=retried).execution_options(synchronize_session=False))
            continue
        await s.execute(update(DecisionShowing).where(DecisionShowing.task_id == t.id)
                        .values(outcome=out, retried=retried, settled_at=now)
                        .execution_options(synchronize_session=False))


async def settle_tasks(task_ids: list[str]) -> None:
    """Settle the showings of these tasks (a workflow task: every showing task of its run). Never raises."""
    if not task_ids:
        return
    try:
        async with SessionLocal() as s:
            rows = (await s.execute(select(Task.id, Task.workflow_run_id).where(Task.id.in_(task_ids)))).all()
            run_ids = {r for _, r in rows if r}
            plain = [i for i, r in rows if not r]
            await _settle_in(s, plain, run_ids)
            await s.commit()
    except Exception as e:  # noqa: BLE001 - the track record never breaks a finish
        log.warning("decision track: settling failed for %s: %s", task_ids[:3], e)


async def _settle_in(s, task_ids: list[str], run_ids: set[str]) -> None:
    shown = select(DecisionShowing.task_id)
    q = select(Task).where(Task.id.in_(shown))
    conds = []
    if task_ids:
        conds.append(Task.id.in_(task_ids))
    if run_ids:
        conds.append(Task.workflow_run_id.in_(run_ids))
    if not conds:
        return
    tasks = (await s.execute(q.where(or_(*conds)))).scalars().all()
    if tasks:
        await _settle(s, list(tasks))


async def settle_task(task_id: str) -> None:
    await settle_tasks([task_id])


async def settle_run(run_id: str) -> None:
    """Settle every showing task of a workflow run (its stage settled). Never raises."""
    try:
        async with SessionLocal() as s:
            await _settle_in(s, [], {run_id})
            await s.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("decision track: settling run %s failed: %s", run_id, e)


def _spawn(coro) -> None:
    try:
        job = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:  # no loop: nothing to run it on
        coro.close()
        return
    _JOBS.add(job)
    job.add_done_callback(_JOBS.discard)


def spawn_settle_task(task_id: str) -> None:
    """Fire-and-forget ``settle_task`` (a finishing task never waits for it)."""
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
    """Showings from the project's ``decision_note`` events still present (the first per task and number);
    pairs already recorded are skipped, so running it twice adds nothing. Then settles those tasks. Returns
    how many rows were added."""
    async with SessionLocal() as s:
        events = (await s.execute(
            select(TaskEvent.task_id, TaskEvent.payload, TaskEvent.created_at)
            .join(Task, Task.id == TaskEvent.task_id)
            .where(Task.project_id == project_id, TaskEvent.type == "decision_note")
            .order_by(TaskEvent.id))).all()
        first: dict[tuple[str, int], datetime] = {}
        for tid, payload, at in events:
            ids = clean_ids((payload or {}).get("ids") if isinstance(payload, dict) else None)
            for n in ids:
                key = (tid, n)
                if key not in first or (at is not None and first[key] is not None and at < first[key]):
                    first[key] = at
        if not first:
            return 0
        tids = sorted({t for t, _ in first})
        have = set()
        for k in range(0, len(tids), 500):
            have |= set((await s.execute(select(DecisionShowing.task_id, DecisionShowing.decision_number).where(
                DecisionShowing.task_id.in_(tids[k:k + 500])))).all())
        rows = [{"project_id": project_id, "decision_number": n, "task_id": t, "shown_at": at or _now(),
                 "retried": False} for (t, n), at in first.items() if (t, n) not in have]
        added = await _add_rows(s, rows)
        await s.commit()
    for k in range(0, len(tids), 500):
        await settle_tasks(tids[k:k + 500])
    return added
