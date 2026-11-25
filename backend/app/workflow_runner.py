"""Workflow runner: advances workflow runs step by step, branches side by side (phase 7b).

A thin orchestrator (like the scheduler) over the EXISTING task machinery. A step may wait
for several earlier steps (`after`, see flowgraph); each pass watches every step in flight
and starts the steps whose waited-for steps all finished successfully, lowest first, up to
max_parallel at once (approvals waiting do not count). A step failing stops the others the
way Cancel does and fails the run. A plain line (no `after`) runs exactly as before.
current_stage is kept as the lowest step not finished successfully. For each step in
flight the runner:
  * agent stage: the current attempt's tasks
      - all completed            -> start the next stage (or finish the run)
      - any failed/cancelled     -> halt the run (status=failed)
      - any awaiting approval    -> pause the run until a human approves
  * gate stage (phase 5a): the server runs the stage's commands itself; the real
    exit code decides (see `_gate_main`). A retry command, when set, runs once
    after a failed full run, then the full run once more.
  * approval stage (phase 5a): the run pauses until someone approves or rejects.
An agent stage item may declare typed `outputs` (phase 5c): the task's prompt asks for a
final ```json block, and once every task of the attempt has completed the values are
parsed and checked (outside the lock) and stored on tasks.outputs, or the task fails
with "outputs invalid: <reason>". Later prompts read them as {{stageN.name}} and
{{prev.name}}. stage_state[i].expects maps task id -> declared names.
Any stage may carry `before` pre-flight lines; an unmet line holds the stage
(the run stays running) and is checked again on every tick.
Phase 5c also adds:
  * auto-retry once: an agent stage with `auto_retry: true` whose attempt failed (no task
    cancelled; siblings finished) starts one new attempt by itself, through the same
    mechanics as a manual Retry (`_new_attempt`). stage_state[i].auto_retried records it.
  * a per-run budget cap (run.budget_usd): before an agent stage (or auto-retry) starts,
    `_budget_ok` sums total_cost_usd over all of the run's tasks; with less than 0.01 left
    the run pauses (stage status "budget", `_budget_hold`) and resumes on the next advance
    once `raise_budget` lifts the cap. Each new task's cap is min(agent cap, remaining).
  * a fix-it loop: a gate with `fix: {"agent", "rounds"}` whose check failed with a real exit
    code (never a guard refusal, timeout, cancel or internal error) goes `fixing` (fix_round
    k, fix_check = the failed gate row). The next advance builds the fix prompt (gates/fixloop,
    outside the lock) and creates one task for the fixer agent in the gate's project at the
    gate's stage_index and attempt, tagged workflow+fix (fix_task). Completed -> a new hand-out
    runs the gate again; failed/cancelled -> the run fails. Short on budget -> status "budget"
    with fix_pending = k, and the raise starts the fix task, not the gate.

Stage shape (on the workflow / run snapshot):
    [{"name": "Develop", "items": [{"agent": "<id|name>", "prompt": "..."}]},
     {"name": "Test", "kind": "gate", "project": "<id>", "run": ["pytest -q"], ...},
     {"name": "Sign-off", "kind": "approval", "message": "..."}]
Each item prompt may contain {{input}}, replaced with the run's input; if it has
no placeholder, the input is appended.

Per-stage state lives in run.stage_state[str(i)]: attempt, status, reason,
started_at/finished_at, and for gates handout, round, passed_cmds, allowed, hits,
log_seq, flaky; decided_by/decided_at for approvals and allowances; summary is
the stage's {{prev}} text for gate and approval stages.
"""

import asyncio
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, update

from .constants import Status
from .database import SessionLocal
from . import flowgraph, planlimits, tiers
from .gates import codestate, executor, fixloop, outputs as typed_outputs, preflight, runlog, totals
from .gates.spec import stage_kind
from .gates.test_results import TestResult
from .models import Agent, Project, Task, WorkflowGateRun, WorkflowRun
from .redaction import clean_text, env_values
from .task_service import build_task

RUN_PENDING = "pending"
RUN_RUNNING = "running"
RUN_PAUSED = "paused"
RUN_COMPLETED = "completed"
RUN_FAILED = "failed"
ACTIVE_RUN = {RUN_PENDING, RUN_RUNNING, RUN_PAUSED}

ROWS_KEEP = 50  # gate-run rows kept per run stage
LOGS_KEEP = 5  # gate log files kept per run stage
SUMMARY_NAMES = 10  # failing / flaky names in a gate summary
FLAKY_WORDS = "a flaky test is not a fixed test"
GATE_ERROR_WORDS = "the gate could not run (internal error)"
FIXER_GONE_WORDS = "the fixer agent is not available"
FIX_UNFINISHED_WORDS = "the fix did not finish"
UNCHANGED_WORDS = "failed then passed unchanged"
GATE_LIVE = ("waiting", "running")  # gate states that want a live process
# Phase 7b: a step is finished successfully in this state (per kind).
SUCCESS = {"agent": "done", "gate": "passed", "approval": "approved"}
# States that hold one of the run's max_parallel slots (approvals waiting never do).
SLOT_STATES = ("running", "waiting", "fixing")  # a gate waiting for "Allow" does not (7b)
RESERVE_WORDS = "Waiting for other steps to finish so the spending limit is not passed."
BUDGET_MIN = 0.01  # below this remainder a capped run pauses instead of starting work
BUDGET_MAX = 10000.0

log = logging.getLogger(__name__)


_PLACEHOLDER = re.compile(r"\{\{(input|prev|stage(\d+))(?:\.([a-z][a-z0-9_]{0,39}))?\}\}")


def substitute(prompt: str, run_input: str, stage_outputs: dict[int, str],
               named_outputs: dict[int, dict[str, str]] | None = None,
               after: list[int] | None = None, stage_names: dict[int, str] | None = None) -> str:
    """Fill placeholders in a stage prompt:
      {{input}}        -> the run's input
      {{prev}}         -> the previous stage's combined output
      {{stageN}}       -> stage N's combined output (N is 1-based, matching the UI)
      {{prev.name}}    -> the previous stage's typed output `name`
      {{stageN.name}}  -> stage N's typed output `name` (current attempt)
    An unknown stage or name gives "". Single pass: the template is tokenised once, so
    a substituted value that itself holds `{{...}}` is never expanded again.
    If no {{input}} and no stage placeholder exists, the input is appended (back-compat).
    With `after` (a step that declares what it waits for): {{prev}} is the output of those steps in step
    order, each under "From <name>:" when there is more than one; {{prev.name}} takes the first of them
    that has `name`; an empty `after` gives "". Without `after` nothing changes.
    """
    named = named_outputs or {}
    p = prompt or ""
    seen = {idx for idx in stage_outputs} | {idx for idx in named}
    prev_idx = max(seen) if seen else None  # {{prev}} = highest-numbered available stage
    had_input = had_stage = False

    def _sub(m):
        nonlocal had_input, had_stage
        head, num, name = m.group(1), m.group(2), m.group(3)
        if head == "input":
            if name:
                return m.group(0)  # {{input.x}} is not a placeholder
            had_input = True
            return run_input or ""
        had_stage = True
        if head == "prev" and after is not None:
            srcs = sorted(set(after))
            if name:
                for j in srcs:
                    if name in (named.get(j) or {}):
                        return named[j][name]
                return ""
            if len(srcs) == 1:
                return stage_outputs.get(srcs[0], "")
            names = stage_names or {}
            return "\n\n".join(
                f"From {names.get(j) or f'step {j + 1}'}:\n{stage_outputs.get(j, '')}" for j in srcs)
        idx = prev_idx if head == "prev" else int(num) - 1
        if idx is None:
            return ""
        if name:
            return (named.get(idx) or {}).get(name, "")
        return stage_outputs.get(idx, "")

    p = _PLACEHOLDER.sub(_sub, p)
    if not had_input and run_input and not had_stage:
        p = f"{p}\n\n{run_input}"
    return p


# ---------------------------------------------------------------------------
# Stage-state helpers
# ---------------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso() -> str:
    return _now().isoformat()


def _stage_name(run, i: int) -> str:
    stages = run.stages or []
    st = stages[i] if 0 <= i < len(stages) else None
    return (st.get("name") if isinstance(st, dict) else None) or f"Stage {i + 1}"


def get_state(run, i: int) -> dict:
    """A copy of run.stage_state[str(i)] (empty when the stage has none)."""
    return dict((run.stage_state or {}).get(str(i)) or {})


def _put_state(run, i: int, st: dict) -> None:
    # Reassign so SQLAlchemy sees the JSON change.
    run.stage_state = {**(run.stage_state or {}), str(i): st}


def _attempt(run, i: int) -> int:
    try:
        return int(get_state(run, i).get("attempt") or 0)
    except (TypeError, ValueError):
        return 0


def _names(names, cap: int = SUMMARY_NAMES) -> str:
    names = list(names or [])
    out = ", ".join(names[:cap])
    if len(names) > cap:
        out += f" and {len(names) - cap} more"
    return out


def _flaky_text(flaky) -> str:
    return f"flaky ({FLAKY_WORDS}): {_names(flaky) if flaky else UNCHANGED_WORDS}"


def gate_status_text(stage: dict, state: dict, live: dict | None) -> str:
    """Honest state words for a gate stage (F112). `live` is WorkflowRunner.live_gate()'s
    entry for this stage, or None. "running" only while this process has a live child."""
    if live:
        return f"running `{live['command']}` ({live['k']} of {live['n']} commands passed)"
    status = state.get("status") or "waiting"
    if status == "running" or (status == "waiting" and state.get("handout")):
        n = len(stage.get("run") or [])
        k = len(state.get("passed_cmds") or [])
        return f"not yet passed ({k} of {n} commands passed, none running)"
    return status


async def _stage_outputs(s, run: WorkflowRun, up_to_stage: int,
                         named: dict[int, dict[str, str]] | None = None) -> dict[int, str]:
    """Output per stage index for stages < up_to_stage: agent stages give the combined
    result_text of the CURRENT attempt's tasks; gate and approval stages give their summary.
    When `named` is given it is filled with the current attempt's typed outputs per stage
    ({stage index: {name: value}}); several items with the same name are joined with a
    blank line."""
    rows = (
        await s.execute(
            select(Task).where(Task.workflow_run_id == run.id, Task.stage_index < up_to_stage)
        )
    ).scalars().all()
    from .config import settings

    cap = settings.workflow_stage_output_max_chars
    stages = run.stages or []
    out: dict[int, str] = {}
    for t in sorted(rows, key=lambda x: (x.stage_index or 0, x.created_at)):
        idx = t.stage_index
        if idx is None or not (0 <= idx < len(stages)) or stage_kind(stages[idx]) != "agent":
            continue
        if (t.stage_attempt or 0) != _attempt(run, idx):
            continue
        if named is not None and isinstance(t.outputs, dict):
            got = named.setdefault(idx, {})
            for k, v in t.outputs.items():
                if isinstance(k, str) and isinstance(v, str):
                    got[k] = f"{got[k]}\n\n{v}" if k in got else v
        if t.result_text:
            out.setdefault(idx, "")
            out[idx] += (("\n\n" if out[idx] else "") + t.result_text)
    for idx in range(min(up_to_stage, len(stages))):
        if stage_kind(stages[idx]) != "agent":
            summary = get_state(run, idx).get("summary")
            if summary:
                out[idx] = summary
    # Cap each stage's chained output so later-stage input stays bounded.
    for idx, text in out.items():
        if len(text) > cap:
            out[idx] = text[:cap] + f"\n\n…[truncated, {len(text) - cap} chars omitted]"
    return out


async def _spent(s, run) -> float:
    """Sum of total_cost_usd over every task of the run (every attempt); None counts as 0."""
    v = (
        await s.execute(
            select(func.coalesce(func.sum(Task.total_cost_usd), 0.0)).where(Task.workflow_run_id == run.id)
        )
    ).scalar()
    return float(v or 0.0)


async def _budget_ok(s, run) -> tuple[bool, float | None, float | None]:
    """(ok, spent, remaining) for starting new paid work on the run. A run without
    budget_usd is always ok: (True, None, None). Otherwise ok is remaining >= 0.01."""
    budget = run.budget_usd
    if budget is None:
        return True, None, None
    spent = await _spent(s, run)
    remaining = round(float(budget) - spent, 6)
    return remaining >= BUDGET_MIN, spent, remaining


def _budget_cap(own: float | None, remaining: float | None) -> float | None:
    """A new task's max_budget_usd: min(own cap, remaining), or own when the run is uncapped."""
    if remaining is None:
        return own
    return remaining if own is None else min(own, remaining)


async def _reserved(s, run) -> float:
    """What the run's tasks still at work may spend: their caps (max_budget_usd), for the ones
    with no recorded cost yet, of each step's current attempt (a fix task carries its gate's
    attempt); an older attempt's task left waiting does not hold later steps."""
    rows = (
        await s.execute(
            select(Task.stage_index, Task.stage_attempt, Task.max_budget_usd).where(
                Task.workflow_run_id == run.id, Task.status.in_(list(Status.ACTIVE)),
                Task.total_cost_usd.is_(None))
        )
    ).all()
    return float(sum(cap or 0.0 for j, att, cap in rows
                     if j is not None and (att or 0) == _attempt(run, j)))


async def _flow_left(s, run, remaining: float | None) -> float | None:
    """For a run with branches (7b): the remaining amount minus what steps still at work may
    spend, so steps side by side never together pass the limit. A plain line run (one step
    at a time) keeps the old amount."""
    if remaining is None or not _is_flow(run):
        return remaining
    return round(remaining - await _reserved(s, run), 6)


def _budget_hold(run, i: int, st: dict, spent: float, reason: str | None = None) -> None:
    """Pause the run at stage i because the budget is used up: stage status "budget" and the
    fixed log line (once per spent/budget pair). The run resumes on the next advance after
    the cap is raised. `reason` (7b): held while other steps' caps use what is left."""
    reason = reason or f"budget reached (${spent:.2f} of ${float(run.budget_usd or 0):.2f})"
    if st.get("status") != "budget" or st.get("reason") != reason:
        runlog.append(run, f"Step {_stage_name(run, i)} held: {reason}")
    st = {k: v for k, v in st.items() if k not in PLAN_KEYS}  # not a plan hold any more
    st = {**st, "status": "budget", "reason": reason}
    if reason == RESERVE_WORDS:
        st["budget_reserve"] = True  # the UI's flag: not the spending limit, other steps' caps
    else:
        st.pop("budget_reserve", None)
    _put_state(run, i, st)
    if reason != RESERVE_WORDS:
        # Held for other steps' caps: another branch is at work by definition, so the run
        # is not "waiting for you" (7b).
        run.status = RUN_PAUSED


def _unstart(run, i: int) -> None:
    """Back to not started, same attempt (a step that was only held when the run stopped)."""
    old = get_state(run, i)
    _put_state(run, i, {k: old[k] for k in ("attempt", "log_seq", "auto_retried") if k in old})


def _new_attempt(run, i: int, auto: bool = False, log: bool = True) -> int:
    """Begin a new attempt of stage i (manual Retry or auto-retry): bump the attempt, keep
    log_seq and auto_retried, set the run running and log it. Earlier tasks are kept; the
    caller then starts the stage. Returns the new attempt."""
    old = get_state(run, i)
    fresh = {"attempt": _attempt(run, i) + 1}
    for k in ("log_seq", "auto_retried"):
        if k in old:
            fresh[k] = old[k]
    if auto:
        fresh["auto_retried"] = True
    _put_state(run, i, fresh)
    run.status = RUN_RUNNING
    name = _stage_name(run, i)
    if log:
        runlog.append(run, f"Step {name} retried automatically" if auto
                      else f"Step {name} retried (attempt {fresh['attempt'] + 1})")
    return fresh["attempt"]


async def _preflight(lines) -> str | None:
    """None when every pre-flight line is met; otherwise the plain reason. Never raises:
    an unreadable line or a check that cannot answer counts as not met."""
    if not lines:
        return None
    try:
        return await preflight.check(list(lines))
    except ValueError as e:
        return str(e) or "a pre-flight line could not be read"
    except Exception:  # noqa: BLE001 - a failing check holds, never fails the run
        return "the pre-flight check could not answer"


async def _hold(s, run, i: int, st: dict, reason: str) -> None:
    # The run log is visible to everyone who sees the run: fixed words only. The detail
    # stays in stage_state.reason (filtered per viewer).
    if st.get("status") != "held" or st.get("reason") != reason:
        runlog.append(run, f"Step {_stage_name(run, i)} held: waiting for pre-flight")
    for k in PLAN_KEYS:  # not a plan hold any more
        st.pop(k, None)
    st.update(status="held", reason=reason)
    _put_state(run, i, st)
    if run.status == RUN_PAUSED:
        run.status = RUN_RUNNING


PLAN_KEYS = ("plan_wait", "not_before")  # a step waiting for the Claude plan's reset (7c)


def _plan_hold(run, i: int, st: dict, row) -> None:
    """Hold an agent step while the run owner's Claude plan is at its limit (7c): status "held" with the
    paused sentence, plan_wait and not_before (ISO UTC, when it may start), never failed; _schedule tries it
    again and it starts after the hold time."""
    until = planlimits.hold_until(row)
    reason = planlimits.paused_text(until)
    if st.get("status") != "held" or st.get("reason") != reason:
        runlog.append(run, f"Step {_stage_name(run, i)} held: {planlimits.STEP_HELD}")
    _put_state(run, i, {**st, "status": "held", "reason": reason, "plan_wait": True,
                        "not_before": planlimits.start_after(until).isoformat()})
    if run.status == RUN_PAUSED:
        run.status = RUN_RUNNING


def _label(run, i: int) -> str:
    """A step named once in log lines (F128): "Step Test (gate)", but "Step gate" when the
    name is the kind itself (any case). Agent steps carry no kind."""
    name = _stage_name(run, i)
    stages = run.stages or []
    kind = stage_kind(stages[i]) if 0 <= i < len(stages) else "agent"
    if kind == "agent" or name.strip().lower() == kind:
        return f"Step {name}"
    return f"Step {name} ({kind})"


# ---------------------------------------------------------------------------
# Steps side by side (phase 7b)
# ---------------------------------------------------------------------------
def _kind(run, i: int) -> str:
    stages = run.stages or []
    return stage_kind(stages[i]) if 0 <= i < len(stages) else "agent"


def _ok(run, i: int) -> bool:
    """Step i finished successfully (agent done, gate passed, approval approved). Every step
    below current_stage is finished by definition (it is the lowest one that is not)."""
    if i < (run.current_stage or 0):
        return True
    return get_state(run, i).get("status") == SUCCESS[_kind(run, i)]


def _holds_slot(st: dict) -> bool:
    """The step takes one of the run's max_parallel places: working, or a gate between its
    commands (waiting its turn for the project, or held on the budget during a fix)."""
    status = st.get("status")
    if status == "waiting" and st.get("slot_wait"):
        return False  # allowed, but waiting for a free place
    return (status in SLOT_STATES or (status == "held" and bool(st.get("lock_wait")))
            or (status == "budget" and bool(st.get("fix_pending"))))


def _slots_used(run, but: int | None = None) -> int:
    return sum(1 for j in range(run.current_stage or 0, len(run.stages or []))
               if j != but and _holds_slot(get_state(run, j)))


def _is_flow(run) -> bool:
    """A run whose snapshot declares `after` somewhere. Without it the run is a plain line and
    every status decision stays exactly as before 7b."""
    return any(isinstance(st, dict) and "after" in st for st in run.stages or [])


def _move_pointer(run) -> None:
    """Keep current_stage = the lowest step not finished successfully (kept for compatibility;
    nothing new depends on it). Moving on sets the run running, as the old move to the next
    step did; nothing left completes the run (current_stage then stays on the last step)."""
    n = len(run.stages or [])
    cur = run.current_stage or 0
    low = next((i for i in range(cur, n) if not _ok(run, i)), None)
    if low is None:
        run.status = RUN_COMPLETED
        run.current_stage = max(0, n - 1)
    elif low != cur:
        run.current_stage = low
        run.status = RUN_RUNNING


FINISHED_STATES = ("done", "passed", "failed", "approved", "rejected", "stopped")


def _cancel_words(run, i: int, procs: dict, failed_name: str | None = None) -> bool:
    """Mark a step stopped by a cancel: a gate or approval logs the command it still needed
    and fails "cancelled" (agent steps are left; their tasks are cancelled). With
    `failed_name` (7b: another branch failed) every kind of step in flight becomes "stopped",
    reason "Stopped because <name> failed.", with one log line. True when marked."""
    st = get_state(run, i)
    status = st.get("status")
    if status is None or status in FINISHED_STATES:
        return False
    kind = _kind(run, i)
    if kind == "agent" and failed_name is None:
        return False
    if kind != "agent":
        live = procs.get((run.id, i))
        nxt = _next_cmd(run.stages[i], st) if status in GATE_LIVE else None
        cmd = live["cmd"] if live else (nxt[1] if nxt else None)
        if cmd:
            runlog.append(run, f"{_label(run, i)} still needs: {cmd}")
    if failed_name is None:
        st.update(status="failed", reason="cancelled")
    else:
        st.update(status="stopped", reason=f"Stopped because {failed_name} failed.")
        runlog.append(run, f"{_stage_name(run, i)}: stopped because {failed_name} failed")
    _put_state(run, i, st)
    return True


# ---------------------------------------------------------------------------
# Approve ahead (F123)
# ---------------------------------------------------------------------------
AHEAD_REFUSED = "This step can only be approved when the run reaches it."


def _drop_ahead(run, why: str, start: int | None = None, only: set | None = None) -> None:
    """Drop every early approval not applied yet on a step from `start` on (default: the
    current step and the steps not reached yet), one log line each. Used when the run fails
    or is cancelled, and on run again. An approval already applied (status "approved") is a
    record of what happened: kept, never reported as dropped."""
    first = run.current_stage if start is None else start
    state = dict(run.stage_state or {})
    changed = False
    for i in range(max(0, first), len(run.stages or [])):
        st = state.get(str(i))
        if only is not None and i not in only:
            continue
        if isinstance(st, dict) and "ahead" in st and st.get("status") != "approved":
            st = {k: v for k, v in st.items() if k != "ahead"}
            if st:
                state[str(i)] = st
            else:
                state.pop(str(i), None)
            runlog.append(run, f"{_stage_name(run, i)}: early approval dropped ({why})")
            changed = True
    if changed:
        run.stage_state = state


def _fail_run(run, why: str = "the run failed", release: bool = True) -> None:
    """Every failure of a run goes through here: the run is failed, its early approvals are
    dropped (with a log line) and its project check locks are let go (cancel lets go only
    after the gate's process is killed)."""
    run.status = RUN_FAILED
    _drop_ahead(run, why)
    if release:
        # A gate of another branch whose command is still in flight keeps its lock until the
        # runner has killed it (_stop_others), as Cancel does.
        release_project_locks(run.id, keep_in_flight=True)


# ---------------------------------------------------------------------------
# One check at a time per project (F126)
# ---------------------------------------------------------------------------
LOCK_WORDS = "Waiting for checks from another run in this project to finish."
# realpath of a project folder -> (run id, stage index) of the gate stage running checks
# there. In memory: one server process; empty after a restart.
_PROJECT_LOCKS: dict[str, tuple[str, int]] = {}
LOCK_HOLDING = ("waiting", "running", "fixing")  # gate states that keep the lock
# (run id, stage) of gate commands whose process this server has started and not yet seen
# end (the kill of a cancel included): such an owner is live whatever its database row says.
_IN_FLIGHT: set[tuple[str, int]] = set()


def _lock_key(directory: str) -> str:
    return os.path.realpath(directory)


def release_project_locks(run_id: str, i: int | None = None, keep_in_flight: bool = False) -> None:
    """Let go of the project locks the run's gate stage i (any stage when None) holds. With
    `keep_in_flight`, a lock whose gate process is still running is kept."""
    for path, owner in list(_PROJECT_LOCKS.items()):
        if owner[0] == run_id and (i is None or owner[1] == i):
            if keep_in_flight and owner in _IN_FLIGHT:
                continue
            _PROJECT_LOCKS.pop(path, None)


async def _owner_live(s, owner: tuple[str, int]) -> bool:
    """True while the owning run is active and its gate stage still runs or fixes its checks.
    Anything else is a lock nobody let go of (a missed path): free."""
    if owner in _IN_FLIGHT:
        return True  # its process may still be running (a cancel kills it after the commit)
    rid, j = owner
    run = await s.get(WorkflowRun, rid)
    if run is None or run.status not in ACTIVE_RUN or not (0 <= j < len(run.stages or [])):
        return False
    st = get_state(run, j)
    if st.get("status") in LOCK_HOLDING:
        return True
    # The same gate stage continuing after a fix round, held for its pre-flight: still its turn.
    return (st.get("status") == "held" and not st.get("lock_wait")
            and int(st.get("fix_round") or 0) > 0)


async def _take_project_lock(s, directory: str, run_id: str, i: int) -> bool:
    """Take the project folder's check lock for (run_id, i). True when taken or already
    held by this same run and stage (a resumed or next command), False when another live
    gate stage holds it (another run's, or since 7b another branch of this run)."""
    path = _lock_key(directory)
    me = (run_id, i)
    for _ in range(3):
        owner = _PROJECT_LOCKS.get(path)
        if owner is None or owner == me:
            _PROJECT_LOCKS[path] = me
            return True
        if await _owner_live(s, owner):
            return False
        if _PROJECT_LOCKS.get(path) == owner:  # unchanged across the read: take it over
            _PROJECT_LOCKS[path] = me
            return True
    return False


async def _project_lock_free(s, directory: str, run_id: str, i: int) -> bool:
    owner = _PROJECT_LOCKS.get(_lock_key(directory))
    return owner is None or owner == (run_id, i) or not await _owner_live(s, owner)


def _lock_hold(run, i: int, st: dict) -> None:
    """Hold gate stage i until the project's checks are free. Its hand-out and progress are
    kept; the tick hands it out again when the lock is free (never a failure). A fix round
    held here keeps fix_pending, so the fix (not the gate) starts when the lock is free."""
    if not st.get("lock_wait"):
        runlog.append(run, f"{_label(run, i)} held: waiting for checks from another run in this project")
    st.update(status="held", reason=LOCK_WORDS, lock_wait=True)
    _put_state(run, i, st)
    if run.status == RUN_PAUSED:
        run.status = RUN_RUNNING


class _NeedPreflight(Exception):
    """Raised inside a locked section when a stage start needs a pre-flight answer. The
    caller drops the session and the lock, runs the (slow) check, and tries again."""

    def __init__(self, index: int, lines: list):
        super().__init__(index)
        self.index = index
        self.lines = lines


async def _start_stage(s, run: WorkflowRun, stage_index: int, worker, pre: dict | None = None) -> list:
    """Start a stage. Agent stages: create the tasks (items run in parallel) and return
    them to hand to the worker AFTER commit. Gate stages: a new hand-out in `waiting`
    (the runner spawns the gate after commit). Approval stages: pause the run.
    Unmet pre-flight holds the stage instead (nothing starts, the run stays running).

    Pre-flight answers come in `pre` ({stage_index: reason | None}), computed outside
    the run lock; a stage with `before` lines and no answer raises _NeedPreflight."""
    stage = run.stages[stage_index]
    kind = stage_kind(stage)
    old = get_state(run, stage_index)
    attempt = _attempt(run, stage_index)

    remaining = None
    if kind == "agent":
        ok, spent, remaining = await _budget_ok(s, run)
        if not ok:
            _budget_hold(run, stage_index, old, spent)
            return []
        remaining = await _flow_left(s, run, remaining)
        if remaining is not None and remaining < BUDGET_MIN:
            _budget_hold(run, stage_index, old, spent, RESERVE_WORDS)
            return []
        if old.get("status") == "budget" and run.status == RUN_PAUSED:
            run.status = RUN_RUNNING  # the cap was raised: resume
        plan_row = await planlimits.hold_for_user(s, run.created_by)
        if plan_row is not None:
            _plan_hold(run, stage_index, old, plan_row)
            return []

    lines = list(stage.get("before") or [])
    reason = None
    if lines:
        if pre is None or stage_index not in pre:
            raise _NeedPreflight(stage_index, lines)
        reason = pre[stage_index]
    if reason:
        await _hold(s, run, stage_index, old, reason)
        return []

    st: dict = {"attempt": attempt, "started_at": _iso()}
    # fix_round survives a pre-flight hold within the attempt (a new attempt drops it in
    # _new_attempt), so a held re-run after a fix cannot reset the round count.
    for k in ("log_seq", "auto_retried", "fix_round", "fix_task"):
        if k in old:
            st[k] = old[k]
    name = _stage_name(run, stage_index)

    if kind == "gate":
        st.update(status="waiting", handout=uuid.uuid4().hex, round="full", passed_cmds=[],
                  allowed=False, hits=[])
        _put_state(run, stage_index, st)
        return []
    if kind == "approval":
        ahead = old.get("ahead")
        if isinstance(ahead, dict) and stage.get("ahead") is True:
            # Approved ahead (F123): the same effect as Approve, by the early approver.
            who = str(ahead.get("by_name") or "someone")
            st.update(status="approved", handout=uuid.uuid4().hex, ahead=ahead, decided_by=who,
                      decided_at=_iso(), finished_at=_iso(), summary=f"Approved ahead by {who}")
            _put_state(run, stage_index, st)
            runlog.append(run, f"{name}: approved ahead by {who}")
            return []
        st.update(status="waiting_approval", handout=uuid.uuid4().hex)
        _put_state(run, stage_index, st)
        run.status = RUN_PAUSED
        return []

    st["status"] = "running"
    _put_state(run, stage_index, st)
    from .config import settings

    agents = (await s.execute(select(Agent))).scalars().all()
    by_id = {a.id: a for a in agents}
    by_name = {a.name: a for a in agents}
    # Prior stages' outputs so a stage can build on what came before (e.g. a lead
    # planning stage feeding the dev stages via {{prev}}).
    named: dict[int, dict[str, str]] = {}
    outputs = await _stage_outputs(s, run, stage_index, named) if stage_index > 0 else {}
    submits = []
    expects: dict[str, list[str]] = {}  # task id -> declared output names
    for item in stage.get("items") or []:
        agent = by_id.get(item.get("agent")) or by_name.get(item.get("agent"))
        if agent is None:
            # Unknown agent -> fail the run rather than silently skip. A new dict: an
            # earlier item's build_task flushed `st`, so an in-place change would not persist.
            st = {**st, "status": "failed", "reason": f"unknown agent {item.get('agent')}"}
            _put_state(run, stage_index, st)
            runlog.append(run, f"Step {name} failed: unknown agent {item.get('agent')}")
            _fail_run(run)
            return []
        # Only a step that declares `after` gets the new {{prev}} (7b); an old step keeps the
        # old one (the highest earlier step with output), exactly as before.
        after = flowgraph.deps(run.stages, stage_index) if "after" in stage else None
        prompt = substitute(item.get("prompt", ""), run.input, outputs, named, after=after,
                            stage_names={j: _stage_name(run, j) for j in after or []})
        names = [n for n in (item.get("outputs") or []) if isinstance(n, str)]
        if names:
            prompt = f"{prompt}\n\n{typed_outputs.suffix(names)}"
        task = await build_task(
            s,
            prompt=prompt,
            title=f"[{stage.get('name', f'Stage {stage_index + 1}')}] {agent.name}: {prompt.splitlines()[0][:50]}",
            project_id=agent.project_id,
            model=await tiers.agent_model(s, agent),
            max_turns=agent.max_turns,
            max_budget_usd=_budget_cap(
                agent.max_budget_usd if agent.max_budget_usd is not None else settings.default_task_budget_usd,
                remaining,
            ),
            priority=agent.priority,
            tags=(agent.tags or []) + ["workflow"],
            agent_id=agent.id,
            system_prompt=agent.system_prompt or None,
            requires_approval=agent.requires_approval,
            allow_remote=agent.allow_remote,
            read_only=agent.read_only,
            workflow_run_id=run.id,
            stage_index=stage_index,
            created_by=run.created_by,
        )
        task.stage_attempt = attempt
        if names:
            expects[task.id] = names
        submits.append(task)
    if expects:
        # A new dict: build_task flushed the earlier state, so mutating `st` in place
        # would look unchanged to SQLAlchemy.
        _put_state(run, stage_index, {**st, "expects": expects})
    return submits


def _user_name(user) -> str:
    for attr in ("name", "display_name", "username", "email"):
        v = getattr(user, attr, None)
        if v:
            return str(v)
    return "someone"


def _gate_detail(cmd: str, out, res, timeout_min, names: bool = True) -> str:
    if out.exit_code is not None:
        d = f"{cmd}, exit {out.exit_code}"
    elif out.outcome == "timed_out":
        d = f"{cmd}, timed out after {timeout_min} min"
    else:
        d = f"{cmd}, {out.outcome}"
    if res.failed:
        d += f", {res.failed} failed"
        if res.failing and names:
            d += f": {_names(res.failing)}"
    return d


def _counts(res) -> str:
    if res.runner is None:
        return ""
    parts = [f"{res.passed or 0} passed", f"{res.failed or 0} failed"]
    if res.skipped:
        parts.append(f"{res.skipped} skipped")
    return "; " + ", ".join(parts)




def _fix_rounds(stage: dict) -> int:
    fix = stage.get("fix") if isinstance(stage, dict) else None
    if not isinstance(fix, dict):
        return 0
    try:
        return int(fix.get("rounds") or 1)
    except (TypeError, ValueError):
        return 0


def _fix_due(stage: dict, st: dict, out) -> bool:
    """A fix round starts only for a check that failed with a real exit code (not a guard
    refusal, a timeout, a cancel or an internal error) when the gate has `fix` and rounds
    are left."""
    return (out.outcome == "failed" and out.exit_code is not None
            and int(st.get("fix_round") or 0) < _fix_rounds(stage))


async def _fix_agent(s, ref, project_id) -> Agent | None:
    """The gate's fixer agent. `ref` is the agent id (stored since the fix round 1 of 5c
    task 4) or, for an older saved stage, a name looked up ONLY inside the gate's project,
    so a same-named agent elsewhere cannot break the gate. The agent must still be in the
    gate's project. None when missing, ambiguous within the project, or moved."""
    if not isinstance(ref, str) or not ref or not project_id:
        return None
    agent = await s.get(Agent, ref)
    if agent is not None:
        return agent if agent.project_id == project_id else None
    inside = (
        await s.execute(select(Agent).where(Agent.name == ref, Agent.project_id == project_id))
    ).scalars().all()
    return inside[0] if len(inside) == 1 else None


@dataclass
class _Fix:
    """A gate stage in fix round `round` whose fix task is not created yet. The prompt (log
    tail read) is built outside the run lock; the task is created under it after re-checking
    that the run, stage, hand-out and round are unchanged."""

    index: int
    handout: str | None
    round: int


@dataclass
class _Post:
    """Work to do after a locked section has committed and released the lock."""

    results: list = field(default_factory=list)  # (task id, priority, created_at, status)
    spawns: list = field(default_factory=list)  # (stage index, hand-out)
    verifies: list = field(default_factory=list)  # _Verify to check outside the lock
    fixes: list = field(default_factory=list)  # _Fix to start outside the lock
    need: "_NeedPreflight | None" = None  # a step start that needs a pre-flight answer
    failed: bool = False  # the run failed in this pass: stop the other branches


@dataclass
class _Verify:
    """A stage whose tasks all completed but whose typed outputs are not checked yet. The
    checks (parse, file system, git) run outside the run lock; the result is written
    under it after re-checking the stage and attempt."""

    index: int
    attempt: int
    items: list = field(default_factory=list)  # (task id, names, result_text, project dir)


def _next_cmd(stage: dict, st: dict):
    """(index, command, kind) the gate runs next in this hand-out, or None."""
    cmds = list(stage.get("run") or [])
    if (st.get("round") or "full") == "retry":
        cmd = stage.get("retry")
        return (0, cmd, "retry") if cmd else None
    passed = list(st.get("passed_cmds") or [])
    todo = [j for j in range(len(cmds)) if j not in passed]
    if not todo or not cmds[todo[0]]:
        return None
    return todo[0], cmds[todo[0]], "full"


class WorkflowRunner:
    """Background loop that advances active workflow runs.

    Every read-modify-write of a WorkflowRun (tick/_advance, cancel, retry, approve,
    reject, allow, and the gate prepare/record/error paths) runs under a per-run
    asyncio.Lock and re-reads the run inside it. Slow probes (pre-flight, guard,
    code state) run outside the lock and are re-validated inside it. Worker submits
    and gate spawns happen after the lock is released.
    """

    def __init__(self, worker, interval_seconds: int | None = None):
        from .config import settings

        self.worker = worker
        self.interval = interval_seconds or settings.workflow_runner_interval_seconds
        self._task = None
        self._stop = asyncio.Event()
        # Live gates, in this process only (single uvicorn process).
        self._gates: dict[tuple[str, int], asyncio.Task] = {}
        # (run_id, stage) -> the step in flight: {proc, cmd, k, n, kind, attempt, handout,
        # log_name, code_state, started_at, recorded, ...}
        self._procs: dict[tuple[str, int], dict] = {}
        # Per-run write locks (and the loop each belongs to; tests drive one runner from
        # several loops, production has one).
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_loops: dict[str, object] = {}
        # (run_id, stage) -> hand-out of a gate task that ended in this process without
        # finishing its stage, so a respawn is not reported as a server restart.
        self._ended: dict[tuple[str, int], str] = {}
        # Runs a gate failed while another step was in flight (7b): the gate's task stops the
        # others when it ends. A plain line run never has another step in flight.
        self._stop_after_gate: set[str] = set()

    async def start(self):
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self._stop.set()
        if self._task:
            await asyncio.wait([self._task])
        gates = list(self._gates.values())
        for t in gates:
            t.cancel()
        if gates:
            await asyncio.gather(*gates, return_exceptions=True)

    async def _loop(self):
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:  # never let the loop die
                log.exception("workflow runner tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass

    # ------------------------------------------------------------------
    # Locking
    # ------------------------------------------------------------------
    def _lock(self, run_id: str) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lk = self._locks.get(run_id)
        if lk is None or self._lock_loops.get(run_id) is not loop:
            lk = asyncio.Lock()
            self._locks[run_id] = lk
            self._lock_loops[run_id] = loop
        return lk

    async def _locked(self, run_id: str, fn, pre: dict | None = None):
        """Run `fn(pre)` under the run's lock. When it needs a pre-flight answer, drop the
        lock, run the check, and call it again with the answer."""
        pre = dict(pre or {})
        for _ in range(8):
            try:
                async with self._lock(run_id):
                    return await fn(pre)
            except _NeedPreflight as e:
                pre[e.index] = await _preflight(e.lines)
        raise RuntimeError("pre-flight answers kept changing")

    async def _run_post(self, run_id: str, post: "_Post | None") -> None:
        if not post:
            return
        await self._submit(post.results)
        for i, handout in post.spawns:
            self._spawn_gate(run_id, i, handout)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def start_run(self, run_id: str):
        """Kick off a freshly created run (start stage 0) immediately."""
        await self._advance(run_id, first=True)

    def live_gates(self, run_id: str) -> list[dict]:
        """Every gate command this process is running for the run, lowest step first:
        [{stage_index, command, k, n, log_name}] (several when branches run side by side)."""
        out = [{"stage_index": i, "command": p["cmd"], "k": p["k"], "n": p["n"],
                "log_name": p.get("log_name")}
               for (rid, i), p in list(self._procs.items()) if rid == run_id]
        return sorted(out, key=lambda x: x["stage_index"])

    def live_gate(self, run_id: str) -> dict | None:
        """The lowest-numbered gate command this process is running for the run, or None."""
        lives = self.live_gates(run_id)
        return lives[0] if lives else None

    async def cancel_run(self, run_id: str) -> bool:
        """Halt a run AND cancel its in-flight stage tasks and live gate."""

        async def fn(pre):
            async with SessionLocal() as s:
                run = await s.get(WorkflowRun, run_id)
                if run is None or run.status not in ACTIVE_RUN:
                    return None
                active_tasks = (
                    await s.execute(
                        select(Task.id).where(
                            Task.workflow_run_id == run.id,
                            Task.status.in_(list(Status.ACTIVE)),
                        )
                    )
                ).scalars().all()
                _fail_run(run, "the run was cancelled", release=False)
                # Every step in flight (several when branches run side by side).
                for i in range(len(run.stages or [])):
                    _cancel_words(run, i, self._procs)
                await s.commit()
                return list(active_tasks)

        active_tasks = await self._locked(run_id, fn)
        if active_tasks is None:
            return False
        await self._kill_gates(run_id, record=True)
        release_project_locks(run_id)  # after the gate's process is gone
        # Actually stop the subprocesses (terminates running, dequeues queued).
        for tid in active_tasks:
            try:
                await self.worker.cancel(tid)
            except Exception:
                pass
        return True

    async def retry_run(self, run_id: str) -> bool:
        """Re-run the failed step(s) as a new attempt and continue the run: every step that
        started and did not finish successfully (the failed one and the branches stopped with
        it), or the current step when none did. Earlier tasks and gate rows are kept; each
        step gets a new hand-out, so a late result from the old one is dropped."""
        async with SessionLocal() as s:
            run = await s.get(WorkflowRun, run_id)
            if run is None or run.status != RUN_FAILED:
                return False
        # A failed run should have no live gate; stop any straggler (its result would be
        # dropped by the hand-out check anyway).
        await self._kill_gates(run_id, record=False)

        async def fn(pre):
            async with SessionLocal() as s:
                run = await s.get(WorkflowRun, run_id)
                if run is None or run.status != RUN_FAILED:
                    return None
                i = run.current_stage
                if not (0 <= i < len(run.stages or [])):
                    return None
                left = [j for j in range(i, len(run.stages)) if not _ok(run, j)
                        and get_state(run, j).get("status") is not None
                        and not await self._finished_agent(s, run, j)]
                # Only a step that started gets a new attempt; one only held (pre-flight,
                # budget, cancelled before it started) just goes back to not started.
                todo = [j for j in left if get_state(run, j).get("started_at")]
                targets = todo or [i]
                for j in targets:
                    _new_attempt(run, j)
                for j in left:
                    if j not in targets:
                        _unstart(run, j)
                await s.commit()
                return True

        if await self._locked(run_id, fn) is None:
            return False
        await self._advance(run_id)
        return True

    @staticmethod
    async def _finished_agent(s, run, j: int) -> bool:
        """An agent step still marked running (or stopped) whose tasks of this attempt all
        completed (it finished as another branch failed): not retried; the next advance marks
        it done."""
        if _kind(run, j) != "agent" or get_state(run, j).get("status") not in ("running", "stopped"):
            return False
        rows = (await s.execute(select(Task.status).where(
            Task.workflow_run_id == run.id, Task.stage_index == j,
            Task.stage_attempt == _attempt(run, j)))).scalars().all()
        return bool(rows) and all(x == Status.COMPLETED for x in rows)

    async def approve_stage(self, run_id: str, i: int, user) -> None:
        async def fn(pre):
            async with SessionLocal() as s:
                run, st = await self._decision_target(s, run_id, i, "approval", "waiting_approval",
                                                      "this step is not waiting for approval")
                who = _user_name(user)
                name = _stage_name(run, i)
                st.update(status="approved", decided_by=who, decided_at=_iso(), finished_at=_iso(),
                          summary=f"Step {name} was approved by {who}.")
                _put_state(run, i, st)
                run.status = RUN_RUNNING
                t = await totals.compute(s, run)
                runlog.append(run, f"{_label(run, i)} approved by {who}. {totals.line(t)}")
                await s.commit()

        await self._locked(run_id, fn)
        await self._advance(run_id)

    async def reject_stage(self, run_id: str, i: int, user, reason: str) -> None:
        reason = clean_text(reason or "").strip()
        if not 1 <= len(reason) <= 500:
            raise ValueError("a reason of 1 to 500 characters is required")

        async def fn(pre):
            async with SessionLocal() as s:
                run, st = await self._decision_target(s, run_id, i, "approval", "waiting_approval",
                                                      "this step is not waiting for approval")
                who = _user_name(user)
                name = _stage_name(run, i)
                st.update(status="rejected", reason=reason, decided_by=who, decided_at=_iso(),
                          summary=f"Step {name} was rejected by {who}: {reason}")
                _put_state(run, i, st)
                runlog.append(run, f"{_label(run, i)} rejected by {who}: {reason}")
                _fail_run(run)
                await s.commit()

        await self._locked(run_id, fn)
        await self._stop_others(run_id)

    async def _ahead_target(self, s, run_id: str, i: int, withdraw: bool = False):
        run = await s.get(WorkflowRun, run_id)
        if run is None:
            raise ValueError("run not found")
        if run.status not in (RUN_RUNNING, RUN_PAUSED):
            raise ValueError("the run is not active")
        if not (0 <= i < len(run.stages or [])) or stage_kind(run.stages[i]) != "approval":
            raise ValueError(f"step {i + 1} is not an approval step")
        if run.stages[i].get("ahead") is not True:
            # Opt-in per step: only a step saved with "ahead": true (runs keep the snapshot
            # they started with, so runs started before that flag existed never allow it).
            raise ValueError(AHEAD_REFUSED)
        status = get_state(run, i).get("status")
        if i < run.current_stage or status is not None:
            # Reached (7b: a branch may reach it before a lower step finishes). A step
            # reached but held (pre-flight) is still undecided: its early approval may be
            # withdrawn, never given.
            held = status == "held"
            if not (withdraw and held):
                raise ValueError("this step has already been reached")
        return run

    async def approve_ahead(self, run_id: str, i: int, user) -> None:
        """Approve approval step i before the run reaches it (F123). Idempotent: a step
        already approved ahead keeps its first approval."""

        async def fn(pre):
            async with SessionLocal() as s:
                run = await self._ahead_target(s, run_id, i)
                st = get_state(run, i)
                if isinstance(st.get("ahead"), dict):
                    return
                who = _user_name(user)
                st["ahead"] = {"by": getattr(user, "id", None), "by_name": who, "at": _iso()}
                _put_state(run, i, st)
                runlog.append(run, f"{_stage_name(run, i)}: early approval given by {who}")
                await s.commit()

        await self._locked(run_id, fn)

    async def withdraw_ahead(self, run_id: str, i: int, user) -> None:
        """Withdraw the early approval of step i (no-op when there is none)."""

        async def fn(pre):
            async with SessionLocal() as s:
                run = await self._ahead_target(s, run_id, i, withdraw=True)
                st = get_state(run, i)
                if "ahead" not in st:
                    return
                st.pop("ahead", None)
                state = dict(run.stage_state or {})
                if st:
                    state[str(i)] = st
                else:
                    state.pop(str(i), None)
                run.stage_state = state
                runlog.append(run, f"{_stage_name(run, i)}: early approval withdrawn by {_user_name(user)}")
                await s.commit()

        await self._locked(run_id, fn)

    async def rerun_from(self, run_id: str, n: int, user) -> None:
        """Run a completed or failed run again from step n (F66): step n and every step that
        waits for it, directly or not (7b), are reset to not started (each keeps its attempt
        count, one higher, so old tasks and gate rows stay history), step n starts as a new
        attempt and the run is running again. Other steps keep their results; another step
        that had not finished (a branch that failed or was stopped) is reset too, so the run
        can finish. Raises ValueError (409) otherwise."""
        async with SessionLocal() as s:
            run = await s.get(WorkflowRun, run_id)
            if run is None:
                raise ValueError("run not found")
            if run.status not in (RUN_COMPLETED, RUN_FAILED):
                raise ValueError("the run is still going; it can run again once it has finished")
        await self._kill_gates(run_id, record=False)  # a finished run has none; stop any straggler

        async def fn(pre):
            async with SessionLocal() as s:
                run = await s.get(WorkflowRun, run_id)
                if run is None:
                    raise ValueError("run not found")
                if run.status not in (RUN_COMPLETED, RUN_FAILED):
                    raise ValueError("the run is still going; it can run again once it has finished")
                stages = run.stages or []
                if not (0 <= n < len(stages)):
                    raise ValueError(f"step {n + 1} does not exist")
                st_n = get_state(run, n)
                reached = n < run.current_stage or run.status == RUN_COMPLETED or bool(
                    st_n.get("started_at") or st_n.get("status"))
                if not reached:
                    raise ValueError("this step was never reached")
                reset = flowgraph.dependants(stages, n)
                for j in range(run.current_stage, len(stages)):
                    # Another unfinished branch of a failed run is reset too (or the run fails
                    # again at once), unless its tasks all completed: the next advance marks it done.
                    if (j != n and j not in reset and not _ok(run, j) and get_state(run, j).get("status") is not None
                            and not await self._finished_agent(s, run, j)):
                        reset.add(j)
                _drop_ahead(run, f"run again from {_stage_name(run, n)}", start=n, only=reset | {n})
                state = dict(run.stage_state or {})
                for j in sorted(reset):
                    old = state.get(str(j))
                    if not isinstance(old, dict) or not (old.get("started_at") or old.get("status")
                                                         or old.get("attempt")):
                        state.pop(str(j), None)
                        continue
                    fresh = {"attempt": _attempt(run, j) + 1}
                    if "log_seq" in old:
                        fresh["log_seq"] = old["log_seq"]
                    state[str(j)] = fresh
                run.stage_state = state
                # The lowest step not finished: n, unless an earlier unfinished step was reset.
                run.current_stage = min([n, run.current_stage, *reset])
                runlog.append(run, f"Run again from {_stage_name(run, n)} by {_user_name(user)}")
                _new_attempt(run, n, log=False)
                st = get_state(run, n)
                st.pop("auto_retried", None)
                _put_state(run, n, st)
                await s.commit()

        await self._locked(run_id, fn)
        await self._advance(run_id)

    async def raise_budget(self, run_id: str, budget_usd: float, user) -> None:
        """Set the run's budget cap to `budget_usd` (must be more than spent, 0.01..10000,
        run active). Under the run lock, so it cannot interleave a tick or a cancel; the
        run then advances (a run held on "budget" resumes)."""
        try:
            new = float(budget_usd)
        except (TypeError, ValueError):
            raise ValueError("the budget must be a number") from None
        if not BUDGET_MIN <= new <= BUDGET_MAX:
            raise ValueError("the budget must be between 0.01 and 10000")

        async def fn(pre):
            async with SessionLocal() as s:
                run = await s.get(WorkflowRun, run_id)
                if run is None:
                    raise ValueError("run not found")
                if run.status not in ACTIVE_RUN:
                    raise ValueError("the run is not active")
                spent = await _spent(s, run)
                if not new > spent:
                    raise ValueError(f"the budget must be more than the ${spent:.2f} already spent")
                if new - spent < BUDGET_MIN:
                    raise ValueError("the new limit must leave at least $0.01 to spend")
                run.budget_usd = new
                runlog.append(run, f"Budget raised to ${new:.2f} by {_user_name(user)}")
                await s.commit()

        await self._locked(run_id, fn)
        await self._advance(run_id)

    async def allow_gate(self, run_id: str, i: int, user) -> None:
        """Allow this hand-out's guard-flagged commands to run ("Allow and run")."""

        async def fn(pre):
            async with SessionLocal() as s:
                run, st = await self._decision_target(s, run_id, i, "gate", "needs_approval",
                                                      "this gate is not waiting for an allowance")
                who = _user_name(user)
                st.update(status="waiting", allowed=True, decided_by=who, decided_at=_iso(), reason=None)
                # Waiting for "Allow" freed its place (7b): with every place taken now it runs
                # when one is free (the next advance hands it out).
                full = _slots_used(run, but=i) >= flowgraph.get_max_parallel(run.stages)
                if full:
                    st["slot_wait"] = True
                _put_state(run, i, st)
                runlog.append(run, f"{_label(run, i)} allowed")
                await s.commit()
                return None if full else st.get("handout")

        handout = await self._locked(run_id, fn)
        self._spawn_gate(run_id, i, handout)

    # ------------------------------------------------------------------
    # Tick / advance
    # ------------------------------------------------------------------
    async def _tick(self):
        async with SessionLocal() as s:
            run_ids = (
                await s.execute(
                    select(WorkflowRun.id).where(WorkflowRun.status.in_(list(ACTIVE_RUN)))
                )
            ).scalars().all()
        for rid in run_ids:
            try:
                await self._advance(rid)
            except Exception:  # one bad run must not starve the others
                log.exception("workflow runner: advancing run %s failed", rid)
        active = set(run_ids)
        for rid in list(self._locks):
            if rid not in active and not self._locks[rid].locked():
                self._locks.pop(rid, None)
                self._lock_loops.pop(rid, None)
        for key in list(self._ended):
            if key[0] not in active:
                self._ended.pop(key, None)

    async def _advance(self, run_id: str, first: bool = False):
        """One or more locked passes over the run (_advance_locked); between them, outside the
        lock: typed-output checks, fix-task starts and pre-flight answers."""
        pre: dict = {}
        post = None
        for _ in range(16):
            post = await self._locked(run_id, lambda p: self._advance_locked(run_id, p), pre)
            if post is None or post.failed:
                break
            if post.need is not None:
                need, post.need = post.need, None
                await self._run_post(run_id, post)
                pre[need.index] = await _preflight(need.lines)
                post = None
                continue
            if post.verifies:
                verifies, post.verifies = post.verifies, []
                await self._run_post(run_id, post)
                for v in verifies:
                    await self._check_outputs(run_id, v)
                post = None
                continue
            if post.fixes:
                fixes, post.fixes = post.fixes, []
                await self._run_post(run_id, post)
                for fx in fixes:
                    await self._run_post(run_id, await self._start_fix(run_id, fx))
                post = None
                await self._stop_others(run_id)  # a fix that could not start fails the run
            break
        await self._run_post(run_id, post)
        if post is not None and post.failed:
            await self._stop_others(run_id)

    async def _stop_others(self, run_id: str) -> None:
        """After a step failed (7b): stop every other step still in flight exactly as Cancel
        does (its gate process killed with a `cancelled` row, its tasks cancelled, its project
        locks let go after the kill). The failed step's own tasks are left as before. A plain
        line run has nothing else in flight: nothing happens."""
        async with SessionLocal() as s:
            run = await s.get(WorkflowRun, run_id)
            if run is None or run.status != RUN_FAILED:
                return

        async def fn(pre):
            async with SessionLocal() as s:
                run = await s.get(WorkflowRun, run_id)
                if run is None or run.status != RUN_FAILED:
                    return None
                stages = run.stages or []
                bad = [j for j in range(len(stages))
                       if get_state(run, j).get("status") in ("failed", "rejected")]
                who = _stage_name(run, bad[0]) if bad else "another step"
                # An agent branch whose tasks all completed finished, it was not stopped: it is
                # left as it is and marked done by the next advance (Retry / Run again).
                done_agents = {j for j in range(len(stages)) if await self._finished_agent(s, run, j)}
                stopped = {i for i in range(len(stages))
                           if i not in done_agents and _cancel_words(run, i, self._procs, who)}
                rows = (await s.execute(select(Task.id, Task.stage_index, Task.stage_attempt).where(
                    Task.workflow_run_id == run.id, Task.status.in_(list(Status.ACTIVE))))).all()
                tids = []
                for tid, j, att in rows:
                    if j is None or not (0 <= j < len(stages)) or (att or 0) != _attempt(run, j):
                        continue
                    if j in stopped or (_kind(run, j) == "agent"
                                        and get_state(run, j).get("status") == "running"):
                        tids.append(tid)
                if not stopped and not tids:
                    return None
                await s.commit()
                return stopped, tids

        res = await self._locked(run_id, fn)
        if not res:
            return
        stopped, tids = res
        await self._kill_gates(run_id, record=True, stages=stopped)
        release_project_locks(run_id)  # after the gates' processes are gone
        for tid in tids:
            try:
                await self.worker.cancel(tid)
            except Exception:  # noqa: BLE001
                pass

    async def _check_outputs(self, run_id: str, v: _Verify) -> None:
        """Parse and verify the typed outputs of a finished agent stage (outside the run
        lock), then store them, or fail the task with "outputs invalid: <reason>"
        (under the lock, only when the stage and attempt are unchanged)."""
        results = []
        for tid, names, text, project_dir in v.items:
            try:
                values = typed_outputs.parse(text, names)
                await typed_outputs.verify(values, project_dir)
            except ValueError as e:
                results.append((tid, None, str(e) or "invalid"))
            except Exception:  # noqa: BLE001 - a check that cannot answer fails the outputs
                log.exception("workflow runner: checking outputs of task %s failed", tid)
                results.append((tid, None, "the outputs could not be checked"))
            else:
                results.append((tid, values, None))

        async def fn(pre):
            flipped: list[tuple[str, str]] = []
            async with SessionLocal() as s:
                run = await s.get(WorkflowRun, run_id)
                if (run is None or run.status not in ACTIVE_RUN
                        or get_state(run, v.index).get("status") not in ("running", "stopped")
                        or _attempt(run, v.index) != v.attempt):
                    return flipped
                for tid, values, reason in results:
                    t = await s.get(Task, tid)
                    if (t is None or t.status != Status.COMPLETED or t.outputs is not None
                            or t.stage_index != v.index or (t.stage_attempt or 0) != v.attempt):
                        continue
                    if reason is None:
                        t.outputs = values
                    else:
                        t.status = Status.FAILED
                        t.error = f"outputs invalid: {reason}"
                        t.outputs = None
                        flipped.append((tid, t.error))
                await s.commit()
                return flipped

        flipped = await self._locked(run_id, fn) or []
        # After the lock: tell live views the task ended failed, as the worker does at
        # its own terminal points (an `error`, then `completed` with the status).
        emit = getattr(self.worker, "emit_offline", None)
        if emit is None:
            return
        for tid, error in flipped:
            for etype, payload in (("error", {"message": error}), ("completed", {"status": Status.FAILED})):
                try:
                    await emit(tid, etype, payload)
                except Exception:  # noqa: BLE001 - an event failure never blocks advancing
                    log.exception("workflow runner: emitting %s for task %s failed", etype, tid)

    async def _advance_locked(self, run_id: str, pre: dict) -> "_Post | None":
        """One pass over the run under its lock (7b): watch every step in flight, then start
        the steps whose waited-for steps all finished successfully, lowest first, while fewer
        than max_parallel steps hold a place (approvals waiting never do) and the budget allows.
        A plain line run has one step in flight at a time and behaves exactly as before."""
        async with SessionLocal() as s:
            run = await s.get(WorkflowRun, run_id)
            if run is None or run.status not in ACTIVE_RUN:
                return None
            if not run.stages:
                run.status = RUN_COMPLETED
                await s.commit()
                return None
            post = _Post()

            if run.status == RUN_PENDING:
                # Claim the run atomically: start_run (request path) and the
                # background tick can both see a fresh run as pending; only the
                # one whose conditional UPDATE matches may start stage 0, or the
                # stage's tasks are created (and billed) twice.
                claimed = await s.execute(
                    update(WorkflowRun)
                    .where(WorkflowRun.id == run_id, WorkflowRun.status == RUN_PENDING)
                    .values(status=RUN_RUNNING, current_stage=0)
                    .execution_options(synchronize_session=False)
                )
                if claimed.rowcount != 1:
                    await s.rollback()
                    return None
                await s.refresh(run)
            else:
                if not (0 <= run.current_stage < len(run.stages)):
                    return None
                await self._adopt_started_agents(s, run)
                for i in range(run.current_stage, len(run.stages)):
                    if _ok(run, i) or get_state(run, i).get("status") is None:
                        continue
                    await self._watch(s, run, i, post)
                    if run.status == RUN_FAILED:
                        await s.commit()
                        post.failed = True
                        return post
                _move_pointer(run)
                if run.status == RUN_COMPLETED:
                    await s.commit()
                    return post

            await self._schedule(s, run, pre, post)
            if run.status == RUN_FAILED:
                await s.commit()
                post.failed = True
                return post
            if post.need is None and _is_flow(run):
                await self._flow_status(s, run)
            await s.commit()
            return post

    @staticmethod
    async def _adopt_started_agents(s, run) -> None:
        """An agent step that has tasks of its current attempt but no status (a run active
        from before agent steps got one) is running: mark it so it is watched, never started
        a second time."""
        idle = [i for i in range(run.current_stage or 0, len(run.stages))
                if stage_kind(run.stages[i]) == "agent" and not _ok(run, i)
                and get_state(run, i).get("status") is None]
        if not idle:
            return
        have = set((await s.execute(select(Task.stage_index, Task.stage_attempt).where(
            Task.workflow_run_id == run.id, Task.stage_index.in_(idle)))).all())
        for i in idle:
            if (i, _attempt(run, i)) in have or (_attempt(run, i) == 0 and (i, None) in have):
                st = get_state(run, i)
                st.update(status="running", started_at=st.get("started_at") or _iso())
                _put_state(run, i, st)

    async def _watch(self, s, run, i: int, post: _Post) -> None:
        """Follow step i, which is in flight (started, not finished successfully). Writes
        go to the session; slow work is queued on `post` (spawns, fixes, output checks)."""
        run_id = run.id
        kind = stage_kind(run.stages[i])
        st = get_state(run, i)
        status = st.get("status")

        if kind == "gate" and status == "budget" and st.get("fix_pending"):
            # A fix round held on the budget: once it is raised, start the FIX TASK
            # (not the gate again). While still short, stay held without re-reading logs.
            ok, _, _ = await _budget_ok(s, run)
            if ok:
                post.fixes.append(_Fix(i, st.get("handout"), int(st.get("fix_pending") or 0)))
            return
        if kind == "gate" and status == "held" and st.get("lock_wait"):
            # Held for the project's check lock: hand the same hand-out out again once
            # the lock is free (the gate takes it before its next command).
            project = await s.get(Project, run.stages[i].get("project")) if run.stages[i].get("project") else None
            if project is not None and (project.directory or "").strip() and not await _project_lock_free(
                    s, project.directory, run_id, i):
                return
            if st.get("fix_pending"):
                post.fixes.append(_Fix(i, st.get("handout"), int(st.get("fix_pending") or 0)))
                return
            st.update(status="waiting", reason=None)
            _put_state(run, i, st)
            await s.commit()
            post.spawns.append((i, st.get("handout")))
            return
        if status in ("held", "budget"):
            return  # started again by _schedule (pre-flight / budget checked again)

        if kind == "gate":
            if status == "failed":
                if run.status != RUN_FAILED:
                    _fail_run(run)
            elif status in GATE_LIVE:
                t = self._gates.get((run_id, i))
                if t is not None and not t.done():
                    return
                if st.get("slot_wait"):
                    return  # allowed, waiting for a free place: _schedule hands it out
                lost = self._ended.pop((run_id, i), None) == st.get("handout")
                if status == "running" and not lost:
                    # This process has no child for it: the server restarted mid-command.
                    runlog.append(run, f"{_label(run, i)} resumed after a server restart")
                    await s.commit()
                post.spawns.append((i, st.get("handout")))
            elif status == "fixing":
                r = await self._fix_watch(s, run, i, st)
                if isinstance(r, _Fix):
                    post.fixes.append(r)
                elif isinstance(r, _Post):
                    post.spawns.extend(r.spawns)
            return

        if kind == "approval":
            if status == "rejected":
                if run.status != RUN_FAILED:
                    _fail_run(run)
            elif status == "waiting_approval":
                if run.status != RUN_PAUSED:
                    run.status = RUN_PAUSED
            return

        # Agent stage: inspect the current attempt's tasks only.
        tasks = (
            await s.execute(
                select(Task).where(
                    Task.workflow_run_id == run.id,
                    Task.stage_index == i,
                    Task.stage_attempt == _attempt(run, i),
                )
            )
        ).scalars().all()
        if not tasks:
            return
        statuses = [t.status for t in tasks]
        bad = any(x in (Status.FAILED, Status.CANCELLED) for x in statuses)
        if bad and self._auto_retry_due(run, i, st, statuses):
            if not any(x in Status.ACTIVE for x in statuses):
                _new_attempt(run, i, auto=True)  # no status now: _schedule starts it again
                return
            bad = False  # wait for the siblings to end, then retry
        if bad:
            if status != "failed":
                n = sum(1 for x in statuses if x in (Status.FAILED, Status.CANCELLED))
                st.update(status="failed", reason=f"{n} task{'s' if n != 1 else ''} failed")
                _put_state(run, i, st)
                runlog.append(run, f"Step {_stage_name(run, i)} failed: {st['reason']}")
            _fail_run(run)
            return
        if any(x == Status.AWAITING_APPROVAL for x in statuses):
            run.status = RUN_PAUSED  # gated stage waiting for a human
            return
        if all(x == Status.COMPLETED for x in statuses):
            expects = st.get("expects") or {}
            todo = [t for t in tasks if t.id in expects and t.outputs is None]
            if todo:
                pids = {t.project_id for t in todo if t.project_id}
                dirs = {p.id: p.directory for p in (
                    await s.execute(select(Project).where(Project.id.in_(pids)))
                ).scalars().all()} if pids else {}
                post.verifies.append(_Verify(i, _attempt(run, i), [
                    (t.id, list(expects[t.id]), (t.result_text or "")[-typed_outputs.SCAN_BYTES:],
                     dirs.get(t.project_id)) for t in todo
                ]))
                return
            if st.pop("plan_wait", None):
                st.pop("reason", None)
                st.pop("not_before", None)
            st.update(status="done", finished_at=_iso())
            _put_state(run, i, st)
            _move_pointer(run)
            t = await totals.compute(s, run)
            runlog.append(run, f"Step {_stage_name(run, i)} done. {totals.line(t)}")
            return
        # A task waiting for its Claude plan's reset (7c): the step shows the paused sentence, still at work.
        waits = [t for t in tasks if t.status == Status.QUEUED and t.wait_reason]
        if waits:
            nb = waits[0].not_before
            nb = (nb.replace(tzinfo=timezone.utc) if nb.tzinfo is None else nb).isoformat() if nb else None
            if (st.get("reason"), st.get("plan_wait"), st.get("not_before")) != (waits[0].wait_reason, True, nb):
                _put_state(run, i, {**st, "reason": waits[0].wait_reason, "plan_wait": True, "not_before": nb})
        elif st.get("plan_wait"):
            _put_state(run, i, {k: v for k, v in st.items() if k not in ("reason", *PLAN_KEYS)})
        # Otherwise still running — make sure the run reflects that.
        if run.status == RUN_PAUSED:
            run.status = RUN_RUNNING

    async def _schedule(self, s, run, pre: dict, post: _Post) -> None:
        """Start every step that may start now, lowest first: its waited-for steps finished
        successfully and it has not started (or it is held on pre-flight / the budget and is
        tried again). Agent and gate steps need a free place (max_parallel); approvals do
        not. Each start is committed on its own; an approval approved ahead finishes at once
        and may let its dependants start in the same pass. A start that needs a pre-flight
        answer stops the pass (post.need): the caller asks outside the lock and comes back."""
        stages = run.stages
        limit = flowgraph.get_max_parallel(stages)
        tried: set[int] = set()
        while run.status in ACTIVE_RUN:
            cur = run.current_stage or 0
            used = _slots_used(run)
            pick = None
            for i in range(cur, len(stages)):
                if i in tried or _ok(run, i):
                    continue
                st = get_state(run, i)
                status = st.get("status")
                if not (status is None or (status in ("held", "budget") and not st.get("lock_wait")
                                           and not st.get("fix_pending"))
                        or (status == "waiting" and st.get("slot_wait"))):
                    continue
                if not all(_ok(run, j) for j in flowgraph.deps(stages, i)):
                    continue
                if stage_kind(stages[i]) != "approval" and used >= limit:
                    continue
                pick = i
                break
            if pick is None:
                return
            tried.add(pick)
            st = get_state(run, pick)
            if st.get("slot_wait"):
                # An allowed gate that waited for a place: hand out the same hand-out now.
                st.pop("slot_wait", None)
                _put_state(run, pick, st)
                await s.commit()
                post.spawns.append((pick, st.get("handout")))
                continue
            try:
                submits = await _start_stage(s, run, pick, self.worker, pre)
            except _NeedPreflight as e:
                await s.commit()  # what this pass decided so far stands
                post.need = e
                return
            await s.commit()
            for t in submits:
                await s.refresh(t)
            post.results.extend((t.id, t.priority, t.created_at, t.status) for t in submits)
            if run.status == RUN_FAILED:
                return
            if _ok(run, pick):  # approved ahead when reached: move on at once
                _move_pointer(run)
                await s.commit()
                continue
            spawn = self._want_spawn(run, pick)
            if spawn:
                post.spawns.append((pick, spawn))

    async def _flow_status(self, s, run) -> None:
        """Run status of a run with branches (7b): running while any step works (or is held
        to start again); paused ("Waiting for you") only when nothing else runs and a step
        waits for a person (an approval, a task approval, a fix task approval) or the budget."""
        if run.status not in (RUN_RUNNING, RUN_PAUSED):
            return
        rows = (await s.execute(select(Task.stage_index, Task.stage_attempt, Task.status, Task.id).where(
            Task.workflow_run_id == run.id))).all()
        work = wait = False
        for i in range(run.current_stage or 0, len(run.stages)):
            if _ok(run, i):
                continue
            st = get_state(run, i)
            status = st.get("status")
            kind = stage_kind(run.stages[i])
            if status is None:
                continue
            if status in ("budget", "waiting_approval", "needs_approval"):
                wait = True
            elif kind == "agent" and status == "running":
                mine = [x for j, a, x, _ in rows if j == i and (a or 0) == _attempt(run, i)]
                if Status.AWAITING_APPROVAL in mine:
                    wait = True
                else:
                    work = True
            elif kind == "gate" and status == "fixing":
                fix = [x for _, _, x, tid in rows if tid == st.get("fix_task")]
                if fix and fix[0] == Status.AWAITING_APPROVAL:
                    wait = True
                else:
                    work = True
            elif status in ("running", "waiting", "held"):
                work = True
        if work:
            run.status = RUN_RUNNING
        elif wait:
            run.status = RUN_PAUSED

    async def _fix_watch(self, s, run, i: int, st: dict) -> "_Fix | _Post | None":
        """A gate stage in `fixing`: create the round's fix task (via _Fix) or follow it.
        Completed -> a new hand-out and the gate runs again; failed/cancelled (or gone) ->
        the run fails; awaiting approval -> the run pauses; otherwise wait."""
        tid = st.get("fix_task")
        if not tid:
            return _Fix(i, st.get("handout"), int(st.get("fix_round") or 0))
        t = await s.get(Task, tid)
        tstatus = t.status if t is not None else Status.FAILED
        if tstatus == Status.COMPLETED:
            handout = uuid.uuid4().hex
            st.update(status="waiting", handout=handout, round="full", passed_cmds=[], allowed=False,
                      hits=[], reason=None)
            _put_state(run, i, st)
            run.status = RUN_RUNNING
            await s.commit()
            return _Post(spawns=[(i, handout)])
        if tstatus in (Status.FAILED, Status.CANCELLED):
            name = _stage_name(run, i)
            st.update(status="failed", reason=FIX_UNFINISHED_WORDS,
                      summary=f"Gate {name} failed: {FIX_UNFINISHED_WORDS}.")
            _put_state(run, i, st)
            runlog.append(run, f"{_label(run, i)} failed: {FIX_UNFINISHED_WORDS}")
            _fail_run(run)
            await s.commit()
            return None
        want = RUN_PAUSED if tstatus == Status.AWAITING_APPROVAL else RUN_RUNNING
        if run.status != want:
            run.status = want
            await s.commit()
        return None

    async def _start_fix(self, run_id: str, fx: "_Fix") -> "_Post | None":
        """Create (and hand back for submit) the fix task of round `fx.round`. The prompt is
        built here, outside the run lock (the log tail is read in a thread); the task is
        created under the lock only after re-checking that the run is still active, on the
        same stage, hand-out and round, with no fix task yet. A cancel that got the lock
        first therefore wins: no task is created on a failed run."""

        def same(run, st) -> bool:
            if run is None or run.status not in ACTIVE_RUN:
                return False
            if not (0 <= fx.index < len(run.stages or [])) or stage_kind(run.stages[fx.index]) != "gate":
                return False
            if st.get("handout") != fx.handout or int(st.get("fix_round") or 0) != fx.round or fx.round < 1:
                return False
            if st.get("status") == "fixing":
                return not st.get("fix_task")
            return st.get("status") in ("budget", "held") and int(st.get("fix_pending") or 0) == fx.round

        # Phase 1: read, no lock.
        async with SessionLocal() as s:
            run = await s.get(WorkflowRun, run_id)
            st = get_state(run, fx.index) if run is not None else {}
            if not same(run, st):
                return None
            stage = run.stages[fx.index]
            g = await s.get(WorkflowGateRun, st["fix_check"]) if st.get("fix_check") else None
            project = await s.get(Project, stage.get("project")) if stage.get("project") else None
        secrets = env_values([project.env]) if project is not None else []
        path = None
        if g is not None and g.log_name:
            try:
                path = executor._run_dir(run_id) / g.log_name
            except ValueError:
                path = None
        tail = await asyncio.to_thread(fixloop.read_tail, path) if path is not None else ""
        if g is not None:
            res = TestResult(g.runner, g.passed, g.failed, g.skipped, None, tuple(g.failing or ()))
            text = fixloop.prompt(g.command, g.exit_code, res, tail, secrets)
        else:
            # The gate row was pruned or lost: the stage's commands are all that is known.
            text = fixloop.prompt(", ".join(stage.get("run") or []), None, None, tail, secrets)

        # Phase 2: create under the lock, on a fresh read.
        async def fn(pre):
            async with SessionLocal() as s:
                run = await s.get(WorkflowRun, run_id)
                st = get_state(run, fx.index) if run is not None else {}
                if not same(run, st):
                    return None
                i = fx.index
                stage = run.stages[i]
                name = _stage_name(run, i)
                agent = await _fix_agent(s, (stage.get("fix") or {}).get("agent"), stage.get("project"))
                if agent is None:
                    st.pop("fix_pending", None)
                    st.update(status="failed", reason=FIXER_GONE_WORDS,
                              summary=f"Gate {name} failed: {FIXER_GONE_WORDS}.")
                    _put_state(run, i, st)
                    runlog.append(run, f"{_label(run, i)} failed: {FIXER_GONE_WORDS}")
                    _fail_run(run)
                    await s.commit()
                    return None
                ok, spent, remaining = await _budget_ok(s, run)
                # Remember what failed: a hold's own reason replaces it on screen.
                fr = st.get("fail_reason") if st.get("status") in ("budget", "held") else st.get("reason")
                left = await _flow_left(s, run, remaining) if ok else remaining
                if ok and left is not None and left < BUDGET_MIN:
                    ok, why = False, RESERVE_WORDS
                else:
                    why = None
                remaining = left
                if not ok:
                    _budget_hold(run, i, {**st, "fix_pending": fx.round, "fail_reason": fr}, spent, why)
                    await s.commit()
                    release_project_locks(run_id, i)  # a person must raise the limit: free the folder
                    return None
                # The fix round works in the folder: it holds the project's check lock (taken
                # again after a budget hold let it go; busy -> held until it is free).
                project = await s.get(Project, stage.get("project")) if stage.get("project") else None
                if project is not None and (project.directory or "").strip() and not await _take_project_lock(
                        s, project.directory, run_id, i):
                    hold = {**st, "fix_pending": fx.round, "fail_reason": fr}
                    _lock_hold(run, i, hold)
                    await s.commit()
                    return None
                from .config import settings

                task = await build_task(
                    s,
                    prompt=text,
                    title=f"[{name}] {agent.name}: fix round {fx.round}",
                    project_id=stage.get("project"),
                    model=await tiers.agent_model(s, agent),
                    max_turns=agent.max_turns,
                    max_budget_usd=_budget_cap(
                        agent.max_budget_usd if agent.max_budget_usd is not None
                        else settings.default_task_budget_usd,
                        remaining,
                    ),
                    priority=agent.priority,
                    tags=[x for x in (agent.tags or []) if x not in ("workflow", "fix")] + ["workflow", "fix"],
                    agent_id=agent.id,
                    system_prompt=agent.system_prompt or None,
                    requires_approval=agent.requires_approval,
                    allow_remote=agent.allow_remote,
                    read_only=agent.read_only,
                    workflow_run_id=run.id,
                    stage_index=i,
                    created_by=run.created_by,
                    # The fixed head's own "do not merge or push" must not gate the task;
                    # the check's output (data) is still classified.
                    risk_text=text[len(fixloop.HEAD):] if text.startswith(fixloop.HEAD) else text,
                )
                task.stage_attempt = _attempt(run, i)
                st.pop("fix_pending", None)
                # A new dict: build_task flushed, so an in-place change would look unchanged.
                # Drop "budget reached" but keep the stage's failure reason ("What failed").
                reason = st.get("reason")
                if st.get("status") in ("budget", "held"):
                    reason = st.get("fail_reason")
                st.pop("fail_reason", None)
                st.pop("lock_wait", None)
                st.pop("budget_reserve", None)
                _put_state(run, i, {**st, "status": "fixing", "fix_task": task.id, "reason": reason})
                if run.status == RUN_PAUSED:
                    run.status = RUN_RUNNING  # the cap was raised: resume
                await s.commit()
                await s.refresh(task)
                return _Post(results=[(task.id, task.priority, task.created_at, task.status)])

        return await self._locked(run_id, fn)

    @staticmethod
    def _auto_retry_due(run, i: int, st: dict, statuses: list) -> bool:
        """An agent stage with auto_retry that has not used it yet, whose current attempt has
        a failed task (an "outputs invalid" one included) and no cancelled one."""
        stage = run.stages[i] if 0 <= i < len(run.stages or []) else None
        return (isinstance(stage, dict) and stage.get("auto_retry") is True
                and not st.get("auto_retried") and st.get("status") != "failed"
                and Status.FAILED in statuses and Status.CANCELLED not in statuses)

    async def _submit(self, results):
        for tid, prio, created, status in results:
            if status != Status.AWAITING_APPROVAL:
                await self.worker.submit(tid, prio, created)

    async def _decision_target(self, s, run_id, i, kind, want, why):
        run = await s.get(WorkflowRun, run_id)
        if run is None:
            raise ValueError("run not found")
        if run.status not in ACTIVE_RUN:
            raise ValueError("the run is not active")
        if not (0 <= i < len(run.stages or [])) or stage_kind(run.stages[i]) != kind:
            raise ValueError(f"step {i + 1} is not {'an' if kind == 'approval' else 'a'} {kind} step")
        st = get_state(run, i)
        if _ok(run, i) or st.get("status") is None:
            # Finished or not reached. Any step in flight may be decided (7b: branches).
            raise ValueError("this step is not the current step")
        if st.get("status") != want:
            raise ValueError(why)
        return run, st

    # ------------------------------------------------------------------
    # Gates
    # ------------------------------------------------------------------
    def _flag_others(self, run, i: int) -> None:
        """A gate failed the run: when another step is in flight (only with branches), the
        gate's task stops it once it ends (_gate_main)."""
        if any(j != i and not _ok(run, j) and get_state(run, j).get("status") not in (None, *FINISHED_STATES)
               for j in range(run.current_stage or 0, len(run.stages or []))):
            self._stop_after_gate.add(run.id)

    def _want_spawn(self, run, i: int) -> str | None:
        """The hand-out to spawn after commit, when stage i is a gate waiting to run."""
        if run.status not in ACTIVE_RUN:
            return None
        if not (0 <= i < len(run.stages or [])) or stage_kind(run.stages[i]) != "gate":
            return None
        st = get_state(run, i)
        return st.get("handout") if st.get("status") == "waiting" else None

    def _spawn_gate(self, run_id: str, i: int, handout: str | None) -> None:
        if not handout:
            return
        key = (run_id, i)
        t = self._gates.get(key)
        if t is not None and not t.done():
            return
        self._gates[key] = asyncio.create_task(self._gate_main(run_id, i, handout))

    async def _kill_gates(self, run_id: str, record: bool, stages: set | None = None) -> None:
        """Cancel the run's live gate tasks (only those of `stages` when given; run_command
        kills the process group). With `record`, a command that was in flight and not yet
        recorded gets a `cancelled` row."""
        for key, task in list(self._gates.items()):
            if key[0] != run_id or (stages is not None and key[1] not in stages):
                continue
            info = self._procs.get(key)
            task.cancel()
            try:
                await task
            except BaseException:  # noqa: BLE001 - CancelledError or the gate's own error
                pass
            if self._gates.get(key) is task:
                self._gates.pop(key, None)
            if record and info and not info.get("recorded"):
                await self._record_cancelled(run_id, key[1], info)

    async def _record_cancelled(self, run_id: str, i: int, info: dict) -> None:
        async def fn(pre):
            async with SessionLocal() as s:
                run = await s.get(WorkflowRun, run_id)
                if run is None or get_state(run, i).get("handout") != info["handout"] or info.get("recorded"):
                    return
                started = info.get("started_at") or _now()
                fin = _now()
                s.add(WorkflowGateRun(
                    run_id=run_id, stage_index=i, attempt=info["attempt"], handout=info["handout"],
                    command=info["cmd"], kind=info["kind"], started_at=started, finished_at=fin,
                    duration_ms=int((fin - started).total_seconds() * 1000), exit_code=None,
                    outcome="cancelled", failing=[], flaky=None, code_state=info.get("code_state"),
                    log_name=info.get("log_name"),
                ))
                await s.flush()
                await self._prune_rows(s, run_id, i)
                await s.commit()
                info["recorded"] = True

        await self._locked(run_id, fn)

    async def _gate_main(self, run_id: str, i: int, handout: str) -> None:
        key = (run_id, i)
        advance = False
        try:
            while True:
                step = await self._gate_prepare(run_id, i, handout)
                if step is None:
                    break
                step.update(proc=None, started_at=_now(), recorded=False)
                self._procs[key] = step

                def on_spawn(proc, _step=step):
                    _step["proc"] = proc

                try:
                    _IN_FLIGHT.add(key)
                    try:
                        out = await executor.run_command(
                            step["cmd"], cwd=step["cwd"], env=step["env"], log_path=step["log_path"],
                            timeout_s=step["timeout_s"], on_spawn=on_spawn,
                        )
                    finally:
                        # run_command returns (or re-raises a cancel) only after the process ended
                        _IN_FLIGHT.discard(key)
                    try:
                        await asyncio.to_thread(executor.prune_logs, run_id, i, LOGS_KEEP)
                    except Exception:  # noqa: BLE001
                        pass
                    more = await self._gate_record(run_id, i, handout, step, out)
                finally:
                    # Kept until recorded, so a cancel in between still gets its row.
                    if self._procs.get(key) is step:
                        self._procs.pop(key, None)
                if more is None:
                    break  # stale hand-out: dropped
                if not more:
                    advance = True
                    break
        except asyncio.CancelledError:
            self._stop_after_gate.discard(run_id)  # killed: whoever killed it stops the rest
            raise
        except Exception as e:  # noqa: BLE001 - a broken gate fails its stage, never the loop
            log.exception("gate %s stage %s failed", run_id, i)
            await self._gate_error(run_id, i, handout, e)
        finally:
            if self._gates.get(key) is asyncio.current_task():
                self._gates.pop(key, None)
        if not advance:
            # Ended here (dropped, held, refused or errored), not by a restart.
            self._ended[key] = handout
        if advance:
            try:
                await self._advance(run_id)
            except Exception:  # noqa: BLE001 - the tick tries again; the gate result stands
                log.exception("workflow runner: advancing run %s after its gate failed", run_id)
        try:
            # This gate failed the run while other steps were in flight (7b): stop them.
            if run_id in self._stop_after_gate:
                self._stop_after_gate.discard(run_id)
                await self._stop_others(run_id)
        except Exception:  # noqa: BLE001
            log.exception("workflow runner: stopping the other steps of run %s failed", run_id)

    async def _gate_error(self, run_id: str, i: int, handout: str, err: Exception) -> None:
        async def fn(pre):
            async with SessionLocal() as s:
                run = await s.get(WorkflowRun, run_id)
                if run is None or run.status not in ACTIVE_RUN or not (0 <= i < len(run.stages or [])):
                    return
                st = get_state(run, i)
                if st.get("handout") != handout or st.get("status") not in GATE_LIVE:
                    return
                name = _stage_name(run, i)
                log.error("gate %s stage %s: %s: %s", run_id, i, type(err).__name__, err,
                          exc_info=(type(err), err, err.__traceback__))
                reason = GATE_ERROR_WORDS
                st.update(status="failed", reason=reason, summary=f"Gate {name} failed: {reason}.")
                _put_state(run, i, st)
                runlog.append(run, f"{_label(run, i)} failed: {reason}")
                _fail_run(run)
                self._flag_others(run, i)
                await s.commit()

        try:
            await self._locked(run_id, fn)
        except Exception:  # noqa: BLE001
            log.exception("gate %s stage %s: could not record the failure", run_id, i)

    async def _gate_prepare(self, run_id: str, i: int, handout: str) -> dict | None:
        """Pick the next command of this hand-out. Pre-flight (before the first command)
        and the guard check run OUTSIDE the run lock; the decision is written under the
        lock after re-checking that the stage, hand-out and next command are unchanged.
        None when nothing should run now."""
        # Phase 1: read and probe, no lock.
        async with SessionLocal() as s:
            run = await s.get(WorkflowRun, run_id)
            if run is None or run.status not in ACTIVE_RUN or not (0 <= i < len(run.stages or [])):
                return None
            stage = run.stages[i]
            st = get_state(run, i)
            if st.get("handout") != handout or st.get("status") not in GATE_LIVE:
                return None
            plan = _next_cmd(stage, st)
            project = await s.get(Project, stage.get("project")) if stage.get("project") else None
            has_dir = project is not None and bool((project.directory or "").strip())
            first = ((st.get("round") or "full") == "full" and not st.get("passed_cmds")
                     and st.get("status") == "waiting")
            pre_reason = None
            decision, reasons = None, []  # no verdict from this pass = no run (fail closed)
            if plan and has_dir:
                if first:
                    pre_reason = await _preflight(stage.get("before") or [])
                if not pre_reason:
                    try:
                        verdict = await executor.guard_verdict(s, project, plan[1])
                        decision = verdict.decision
                        reasons = [str(getattr(h, "reason", h)) for h in (verdict.hits or [])]
                    except Exception as e:  # noqa: BLE001 - fail closed
                        decision, reasons = "deny", [f"the guard check failed ({type(e).__name__})"]

        # Phase 2: decide under the lock, on a fresh read.
        async def fn(pre):
            async with SessionLocal() as s:
                run = await s.get(WorkflowRun, run_id)
                if run is None or run.status not in ACTIVE_RUN or not (0 <= i < len(run.stages or [])):
                    return None
                stage = run.stages[i]
                st = get_state(run, i)
                if st.get("handout") != handout or st.get("status") not in GATE_LIVE:
                    return None
                if _next_cmd(stage, st) != plan:
                    return None  # moved on while we probed; the caller drops this
                name = _stage_name(run, i)

                async def fail(reason, line, summary):
                    st.update(status="failed", reason=reason, summary=summary)
                    _put_state(run, i, st)
                    runlog.append(run, line)
                    _fail_run(run)  # lets go of the project lock too
                    self._flag_others(run, i)
                    await s.commit()

                if plan is None:
                    reason = "the gate has no command to run"
                    await fail(reason, f"{_label(run, i)} failed: {reason}", f"Gate {name} failed: {reason}.")
                    return None
                idx, cmd, kind = plan
                if not has_dir:
                    reason = "the gate's project is missing or has no directory"
                    await fail(reason, f"{_label(run, i)} failed: {reason}", f"Gate {name} failed: {reason}.")
                    return None
                if first and st.get("status") == "waiting" and pre_reason:
                    await _hold(s, run, i, st, pre_reason)
                    await s.commit()
                    return None
                if decision is None:
                    return None  # the guard did not answer in this pass; try again later
                if decision not in ("allow", "approve"):
                    why = "; ".join(reasons) or "denied"
                    fin = _now()
                    s.add(WorkflowGateRun(
                        run_id=run_id, stage_index=i, attempt=_attempt(run, i), handout=handout,
                        command=cmd, kind=kind, started_at=fin, finished_at=fin, duration_ms=0,
                        exit_code=None, outcome="refused", failing=[], flaky=None,
                    ))
                    await s.flush()
                    await self._prune_rows(s, run_id, i)
                    reason = f"blocked by the guard: {why}"
                    st["hits"] = reasons
                    await fail(reason, f"{_label(run, i)} failed: {cmd}, blocked by the guard",
                               f"Gate {name} failed: {cmd} was {reason}.")
                    return None
                if decision == "approve" and not st.get("allowed"):
                    st.update(status="needs_approval", hits=reasons,
                              reason=f"the guard asks a person to allow `{cmd}`: {'; '.join(reasons)}")
                    _put_state(run, i, st)
                    runlog.append(run, f"{_label(run, i)} needs approval to run: {cmd}")
                    await s.commit()
                    release_project_locks(run_id, i)  # a person decides now: free the folder
                    return None

                project = await s.get(Project, stage.get("project"))
                if not await _take_project_lock(s, project.directory, run_id, i):
                    _lock_hold(run, i, st)
                    await s.commit()
                    return None
                st.pop("lock_wait", None)
                seq = int(st.get("log_seq") or 0) + 1
                st.update(status="running", log_seq=seq, reason=None)
                _put_state(run, i, st)
                try:
                    await s.commit()
                except BaseException:
                    release_project_locks(run_id, i)
                    raise
                return {
                    "cmd": cmd, "idx": idx, "kind": kind,
                    "k": len(st.get("passed_cmds") or []), "n": len(stage.get("run") or []),
                    "attempt": _attempt(run, i), "handout": handout, "seq": seq,
                    "env": executor.gate_env(project), "cwd": project.directory,
                    "timeout_min": int(stage.get("timeout_min") or 60),
                }

        step = await self._locked(run_id, fn)
        if step is None:
            return None
        # Phase 3: slow, no lock.
        log_path = executor.log_file(run_id, i, step["seq"])
        step.update(log_path=log_path, log_name=log_path.name, timeout_s=step["timeout_min"] * 60,
                    code_state=await codestate.code_state(step["cwd"]))
        return step

    async def _prune_rows(self, s, run_id: str, i: int) -> None:
        old = (
            await s.execute(
                select(WorkflowGateRun.id)
                .where(WorkflowGateRun.run_id == run_id, WorkflowGateRun.stage_index == i)
                .order_by(WorkflowGateRun.started_at.desc(), WorkflowGateRun.id.desc())
                .offset(ROWS_KEEP)
            )
        ).scalars().all()
        if old:
            await s.execute(delete(WorkflowGateRun).where(WorkflowGateRun.id.in_(old)))

    async def _gate_record(self, run_id: str, i: int, handout: str, step: dict, out) -> bool | None:
        """Record one command's result under the run lock. Returns True when the round has
        more to run, False when the gate finished, None when the result is stale (dropped)."""
        return await self._locked(run_id, lambda pre: self._gate_record_locked(run_id, i, handout, step, out))

    async def _gate_record_locked(self, run_id, i, handout, step, out) -> bool | None:
        async with SessionLocal() as s:
            run = await s.get(WorkflowRun, run_id)
            if run is None or run.status not in ACTIVE_RUN or not (0 <= i < len(run.stages or [])):
                return None
            st = get_state(run, i)
            if st.get("handout") != handout or st.get("status") != "running" or step.get("recorded"):
                return None
            stage = run.stages[i]
            name = _stage_name(run, i)
            res = out.result
            cmd = step["cmd"]

            # Flaky (F118): a full pass after an earlier full failure of the same command
            # (any attempt) that had a real exit code and the same non-null code state.
            flaky = None
            if step["kind"] == "full" and out.outcome == "passed" and step.get("code_state"):
                prior = (
                    await s.execute(
                        select(WorkflowGateRun)
                        .where(
                            WorkflowGateRun.run_id == run_id, WorkflowGateRun.stage_index == i,
                            WorkflowGateRun.kind == "full", WorkflowGateRun.command == cmd,
                            WorkflowGateRun.outcome == "failed",
                            WorkflowGateRun.exit_code.is_not(None),
                            WorkflowGateRun.code_state == step["code_state"],
                        )
                        .order_by(WorkflowGateRun.started_at)
                    )
                ).scalars().all()
                if prior:
                    flaky = []
                    for r in prior:
                        for fname in r.failing or []:
                            if fname not in flaky:
                                flaky.append(fname)

            row = WorkflowGateRun(
                run_id=run_id, stage_index=i, attempt=step["attempt"], handout=handout, command=cmd,
                kind=step["kind"], started_at=out.started_at, finished_at=out.finished_at,
                duration_ms=out.duration_ms, exit_code=out.exit_code, outcome=out.outcome,
                runner=res.runner, passed=res.passed, failed=res.failed, skipped=res.skipped,
                failing=list(res.failing or ()), flaky=flaky, code_state=step.get("code_state"),
                log_name=step["log_name"],
            )
            s.add(row)
            await s.flush()
            await self._prune_rows(s, run_id, i)
            if out.log_error:
                runlog.append(run, f"{_label(run, i)}: the log of `{cmd}` could not be written")

            rnd = st.get("round") or "full"
            passed = list(st.get("passed_cmds") or [])
            n = len(stage.get("run") or [])
            more, result = False, None
            if out.outcome == "passed":
                if step["kind"] == "retry":
                    rnd, passed, more = "full2", [], True
                else:
                    passed.append(step["idx"])
                    if rnd == "full" and len(passed) < n:
                        more = True
                    else:
                        result = "passed"
            elif rnd == "full" and stage.get("retry"):
                rnd, passed, more = "retry", [], True
            else:
                result = "failed"
            st.update(round=rnd, passed_cmds=passed)

            if result == "passed":
                text = f"Gate {name} passed: {', '.join(stage.get('run') or [])}{_counts(res)}."
                fixes = int(st.get("fix_round") or 0)
                line = f"{_label(run, i)} passed" + (f" after {fixes} fix round(s)" if fixes else "")
                if flaky is not None:
                    st["flaky"] = flaky
                    text += f" {_flaky_text(flaky)}."
                    line += f", flaky ({FLAKY_WORDS})"
                st.update(status="passed", finished_at=_iso(), reason=None, summary=text)
                _put_state(run, i, st)
                release_project_locks(run_id, i)
                t = await totals.compute(s, run)
                runlog.append(run, f"{line}. {totals.line(t)}")
            elif result == "failed" and _fix_due(stage, st, out):
                # A real exit code and rounds left: hand the check to the fixer agent. The
                # fix task is created by the next advance (outside this lock), which then
                # watches it; `fix_check` points at the gate row the prompt is built from.
                detail = _gate_detail(cmd, out, res, step.get("timeout_min"))
                k, n = int(st.get("fix_round") or 0) + 1, _fix_rounds(stage)
                st.pop("fix_task", None)
                st.pop("fix_pending", None)
                st.update(status="fixing", fix_round=k, fix_check=row.id, reason=detail,
                          summary=f"Gate {name} failed: {detail}.")
                _put_state(run, i, st)
                runlog.append(run, f"{_label(run, i)} failed, fixing (round {k} of {n})")
            elif result == "failed":
                detail = _gate_detail(cmd, out, res, step.get("timeout_min"))
                st.update(status="failed", reason=detail, summary=f"Gate {name} failed: {detail}.")
                _put_state(run, i, st)
                runlog.append(run, f"{_label(run, i)} failed: "
                              f"{_gate_detail(cmd, out, res, step.get('timeout_min'), names=False)}")
                _fail_run(run)
                self._flag_others(run, i)
            else:
                _put_state(run, i, st)
            await s.commit()
            step["recorded"] = True
        return more
