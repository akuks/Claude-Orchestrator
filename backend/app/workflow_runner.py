"""Workflow runner: advances workflow runs stage by stage.

A thin orchestrator (like the scheduler) over the EXISTING task machinery. For
each active run it watches the current stage and:
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
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, update

from .constants import Status
from .database import SessionLocal
from . import tiers
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
BUDGET_MIN = 0.01  # below this remainder a capped run pauses instead of starting work
BUDGET_MAX = 10000.0

log = logging.getLogger(__name__)


_PLACEHOLDER = re.compile(r"\{\{(input|prev|stage(\d+))(?:\.([a-z][a-z0-9_]{0,39}))?\}\}")


def substitute(prompt: str, run_input: str, stage_outputs: dict[int, str],
               named_outputs: dict[int, dict[str, str]] | None = None) -> str:
    """Fill placeholders in a stage prompt:
      {{input}}        -> the run's input
      {{prev}}         -> the previous stage's combined output
      {{stageN}}       -> stage N's combined output (N is 1-based, matching the UI)
      {{prev.name}}    -> the previous stage's typed output `name`
      {{stageN.name}}  -> stage N's typed output `name` (current attempt)
    An unknown stage or name gives "". Single pass: the template is tokenised once, so
    a substituted value that itself holds `{{...}}` is never expanded again.
    If no {{input}} and no stage placeholder exists, the input is appended (back-compat).
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


def _budget_hold(run, i: int, st: dict, spent: float) -> None:
    """Pause the run at stage i because the budget is used up: stage status "budget" and the
    fixed log line (once per spent/budget pair). The run resumes on the next advance after
    the cap is raised."""
    reason = f"budget reached (${spent:.2f} of ${float(run.budget_usd or 0):.2f})"
    if st.get("status") != "budget" or st.get("reason") != reason:
        runlog.append(run, f"Step {_stage_name(run, i)} held: {reason}")
    _put_state(run, i, {**st, "status": "budget", "reason": reason})
    run.status = RUN_PAUSED


def _new_attempt(run, i: int, auto: bool = False) -> int:
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
    st.update(status="held", reason=reason)
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
        if old.get("status") == "budget" and run.status == RUN_PAUSED:
            run.status = RUN_RUNNING  # the cap was raised: resume

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
            run.status = RUN_FAILED
            return []
        prompt = substitute(item.get("prompt", ""), run.input, outputs, named)
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

    def live_gate(self, run_id: str) -> dict | None:
        """The gate command this process is running for the run, or None:
        {stage_index, command, k, n, log_name}."""
        for (rid, i), p in list(self._procs.items()):
            if rid == run_id:
                return {"stage_index": i, "command": p["cmd"], "k": p["k"], "n": p["n"],
                        "log_name": p.get("log_name")}
        return None

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
                run.status = RUN_FAILED
                i = run.current_stage
                if 0 <= i < len(run.stages or []) and stage_kind(run.stages[i]) != "agent":
                    st = get_state(run, i)
                    status = st.get("status")
                    if status not in ("passed", "failed", "approved", "rejected", None):
                        live = self._procs.get((run_id, i))
                        nxt = _next_cmd(run.stages[i], st) if status in GATE_LIVE else None
                        cmd = live["cmd"] if live else (nxt[1] if nxt else None)
                        if cmd:
                            runlog.append(run, f"Step {_stage_name(run, i)} (gate) still needs: {cmd}")
                        st.update(status="failed", reason="cancelled")
                        _put_state(run, i, st)
                await s.commit()
                return list(active_tasks)

        active_tasks = await self._locked(run_id, fn)
        if active_tasks is None:
            return False
        await self._kill_gates(run_id, record=True)
        # Actually stop the subprocesses (terminates running, dequeues queued).
        for tid in active_tasks:
            try:
                await self.worker.cancel(tid)
            except Exception:
                pass
        return True

    async def retry_run(self, run_id: str) -> bool:
        """Re-run the current (failed) stage as a new attempt and continue the pipeline.
        Earlier tasks and gate rows are kept; the stage gets a new hand-out, so a late
        result from the old one is dropped."""
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
                _new_attempt(run, i)
                return await self._start_and_commit(s, run, i, pre)

        post = await self._locked(run_id, fn)
        if post is None:
            return False
        await self._run_post(run_id, post)
        return True

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
                runlog.append(run, f"Step {name} (approval) approved by {who}. {totals.line(t)}")
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
                run.status = RUN_FAILED
                runlog.append(run, f"Step {name} (approval) rejected by {who}: {reason}")
                await s.commit()

        await self._locked(run_id, fn)

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
                _put_state(run, i, st)
                runlog.append(run, f"Step {_stage_name(run, i)} (gate) allowed")
                await s.commit()
                return st.get("handout")

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
        post = None
        for _ in range(4):
            post = await self._locked(run_id, lambda pre: self._advance_locked(run_id, pre))
            if isinstance(post, _Fix):
                post = await self._start_fix(run_id, post)
                break
            if not isinstance(post, _Verify):
                break
            await self._check_outputs(run_id, post)
            post = None
        await self._run_post(run_id, post)

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
                if (run is None or run.status not in ACTIVE_RUN or run.current_stage != v.index
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
        async with SessionLocal() as s:
            run = await s.get(WorkflowRun, run_id)
            if run is None or run.status not in ACTIVE_RUN:
                return None
            if not run.stages:
                run.status = RUN_COMPLETED
                await s.commit()
                return None

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
                return await self._start_and_commit(s, run, 0, pre)

            i = run.current_stage
            if not (0 <= i < len(run.stages)):
                return None
            kind = stage_kind(run.stages[i])
            st = get_state(run, i)
            status = st.get("status")

            if kind == "gate" and status == "budget" and st.get("fix_pending"):
                # A fix round held on the budget: once it is raised, start the FIX TASK
                # (not the gate again). While still short, stay held without re-reading logs.
                ok, _, _ = await _budget_ok(s, run)
                if not ok:
                    return None
                return _Fix(i, st.get("handout"), int(st.get("fix_pending") or 0))
            if status in ("held", "budget"):
                return await self._start_and_commit(s, run, i, pre)

            if kind == "gate":
                if status == "passed":
                    return await self._next_stage(s, run, i, pre)
                if status == "failed":
                    if run.status != RUN_FAILED:
                        run.status = RUN_FAILED
                        await s.commit()
                elif status in GATE_LIVE:
                    t = self._gates.get((run_id, i))
                    if t is not None and not t.done():
                        return None
                    lost = self._ended.pop((run_id, i), None) == st.get("handout")
                    if status == "running" and not lost:
                        # This process has no child for it: the server restarted mid-command.
                        runlog.append(run, f"Step {_stage_name(run, i)} (gate) resumed after a server restart")
                        await s.commit()
                    return _Post(spawns=[(i, st.get("handout"))])
                elif status == "fixing":
                    return await self._fix_watch(s, run, i, st)
                elif status is None:
                    return await self._start_and_commit(s, run, i, pre)
                return None

            if kind == "approval":
                if status == "approved":
                    return await self._next_stage(s, run, i, pre)
                if status == "rejected":
                    if run.status != RUN_FAILED:
                        run.status = RUN_FAILED
                        await s.commit()
                elif status == "waiting_approval":
                    if run.status != RUN_PAUSED:
                        run.status = RUN_PAUSED
                        await s.commit()
                elif status is None:
                    return await self._start_and_commit(s, run, i, pre)
                return None

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
                return None
            statuses = [t.status for t in tasks]
            bad = any(x in (Status.FAILED, Status.CANCELLED) for x in statuses)
            if bad and self._auto_retry_due(run, i, st, statuses):
                if not any(x in Status.ACTIVE for x in statuses):
                    _new_attempt(run, i, auto=True)
                    return await self._start_and_commit(s, run, i, pre)
                bad = False  # wait for the siblings to end, then retry
            if bad:
                run.status = RUN_FAILED
                if status != "failed":
                    n = sum(1 for x in statuses if x in (Status.FAILED, Status.CANCELLED))
                    st.update(status="failed", reason=f"{n} task{'s' if n != 1 else ''} failed")
                    _put_state(run, i, st)
                    runlog.append(run, f"Step {_stage_name(run, i)} failed: {st['reason']}")
                await s.commit()
                return None
            if any(x == Status.AWAITING_APPROVAL for x in statuses):
                run.status = RUN_PAUSED  # gated stage waiting for a human
                await s.commit()
                return None
            if all(x == Status.COMPLETED for x in statuses):
                expects = st.get("expects") or {}
                todo = [t for t in tasks if t.id in expects and t.outputs is None]
                if todo:
                    pids = {t.project_id for t in todo if t.project_id}
                    dirs = {p.id: p.directory for p in (
                        await s.execute(select(Project).where(Project.id.in_(pids)))
                    ).scalars().all()} if pids else {}
                    return _Verify(i, _attempt(run, i), [
                        (t.id, list(expects[t.id]), (t.result_text or "")[-typed_outputs.SCAN_BYTES:],
                         dirs.get(t.project_id)) for t in todo
                    ])
                return await self._next_stage(s, run, i, pre, finish_agent=True)
            # Otherwise still running — make sure the run reflects that.
            if run.status == RUN_PAUSED:
                run.status = RUN_RUNNING
                await s.commit()
            return None

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
            run.status = RUN_FAILED
            runlog.append(run, f"Step {name} (gate) failed: {FIX_UNFINISHED_WORDS}")
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
            if run is None or run.status not in ACTIVE_RUN or run.current_stage != fx.index:
                return False
            if not (0 <= fx.index < len(run.stages or [])) or stage_kind(run.stages[fx.index]) != "gate":
                return False
            if st.get("handout") != fx.handout or int(st.get("fix_round") or 0) != fx.round or fx.round < 1:
                return False
            if st.get("status") == "fixing":
                return not st.get("fix_task")
            return st.get("status") == "budget" and int(st.get("fix_pending") or 0) == fx.round

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
                    run.status = RUN_FAILED
                    runlog.append(run, f"Step {name} (gate) failed: {FIXER_GONE_WORDS}")
                    await s.commit()
                    return None
                ok, spent, remaining = await _budget_ok(s, run)
                if not ok:
                    # Remember what failed: the hold's own reason replaces it on screen.
                    fr = st.get("fail_reason") if st.get("status") == "budget" else st.get("reason")
                    _budget_hold(run, i, {**st, "fix_pending": fx.round, "fail_reason": fr}, spent)
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
                if st.get("status") == "budget":
                    reason = st.get("fail_reason")
                st.pop("fail_reason", None)
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

    async def _start_and_commit(self, s, run, i: int, pre: dict) -> _Post:
        """Start stage i in this session and commit (a held stage is checked again).
        Returns the submits and gate spawn to do after the lock is released."""
        submits = await _start_stage(s, run, i, self.worker, pre)
        await s.commit()
        for t in submits:
            await s.refresh(t)
        post = _Post(results=[(t.id, t.priority, t.created_at, t.status) for t in submits])
        spawn = self._want_spawn(run, i)
        if spawn:
            post.spawns.append((i, spawn))
        return post

    async def _next_stage(self, s, run, i: int, pre: dict, finish_agent: bool = False) -> "_Post | None":
        """Move from stage i to i + 1 (or complete the run). A conditional UPDATE claims
        the move so two advances cannot both start the next stage."""
        last = i + 1 >= len(run.stages)
        values = {"status": RUN_COMPLETED} if last else {"status": RUN_RUNNING, "current_stage": i + 1}
        claimed = await s.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == run.id, WorkflowRun.current_stage == i,
                   WorkflowRun.status.in_(list(ACTIVE_RUN)))
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            await s.rollback()
            return None
        await s.refresh(run)
        if finish_agent:
            st = get_state(run, i)
            st.update(status="done", finished_at=_iso())
            _put_state(run, i, st)
            t = await totals.compute(s, run)
            runlog.append(run, f"Step {_stage_name(run, i)} done. {totals.line(t)}")
        if last:
            await s.commit()
            return None
        return await self._start_and_commit(s, run, i + 1, pre)

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
        if run.current_stage != i:
            raise ValueError("this step is not the current step")
        st = get_state(run, i)
        if st.get("status") != want:
            raise ValueError(why)
        return run, st

    # ------------------------------------------------------------------
    # Gates
    # ------------------------------------------------------------------
    def _want_spawn(self, run, i: int) -> str | None:
        """The hand-out to spawn after commit, when stage i is a gate waiting to run."""
        if run.status not in ACTIVE_RUN or run.current_stage != i:
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

    async def _kill_gates(self, run_id: str, record: bool) -> None:
        """Cancel the run's live gate tasks (run_command kills the process group). With
        `record`, a command that was in flight and not yet recorded gets a `cancelled` row."""
        for key, task in list(self._gates.items()):
            if key[0] != run_id:
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
                    out = await executor.run_command(
                        step["cmd"], cwd=step["cwd"], env=step["env"], log_path=step["log_path"],
                        timeout_s=step["timeout_s"], on_spawn=on_spawn,
                    )
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

    async def _gate_error(self, run_id: str, i: int, handout: str, err: Exception) -> None:
        async def fn(pre):
            async with SessionLocal() as s:
                run = await s.get(WorkflowRun, run_id)
                if run is None or run.status not in ACTIVE_RUN or run.current_stage != i:
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
                run.status = RUN_FAILED
                runlog.append(run, f"Step {name} (gate) failed: {reason}")
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
            if run is None or run.status not in ACTIVE_RUN or run.current_stage != i:
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
                if run is None or run.status not in ACTIVE_RUN or run.current_stage != i:
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
                    run.status = RUN_FAILED
                    runlog.append(run, line)
                    await s.commit()

                if plan is None:
                    reason = "the gate has no command to run"
                    await fail(reason, f"Step {name} (gate) failed: {reason}", f"Gate {name} failed: {reason}.")
                    return None
                idx, cmd, kind = plan
                if not has_dir:
                    reason = "the gate's project is missing or has no directory"
                    await fail(reason, f"Step {name} (gate) failed: {reason}", f"Gate {name} failed: {reason}.")
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
                    await fail(reason, f"Step {name} (gate) failed: {cmd}, blocked by the guard",
                               f"Gate {name} failed: {cmd} was {reason}.")
                    return None
                if decision == "approve" and not st.get("allowed"):
                    st.update(status="needs_approval", hits=reasons,
                              reason=f"the guard asks a person to allow `{cmd}`: {'; '.join(reasons)}")
                    _put_state(run, i, st)
                    runlog.append(run, f"Step {name} (gate) needs approval to run: {cmd}")
                    await s.commit()
                    return None

                seq = int(st.get("log_seq") or 0) + 1
                st.update(status="running", log_seq=seq, reason=None)
                _put_state(run, i, st)
                await s.commit()
                project = await s.get(Project, stage.get("project"))
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
            if run is None or run.status not in ACTIVE_RUN or run.current_stage != i:
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
                runlog.append(run, f"Step {name} (gate): the log of `{cmd}` could not be written")

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
                line = f"Step {name} (gate) passed" + (f" after {fixes} fix round(s)" if fixes else "")
                if flaky is not None:
                    st["flaky"] = flaky
                    text += f" {_flaky_text(flaky)}."
                    line += f", flaky ({FLAKY_WORDS})"
                st.update(status="passed", finished_at=_iso(), reason=None, summary=text)
                _put_state(run, i, st)
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
                runlog.append(run, f"Step {name} (gate) failed, fixing (round {k} of {n})")
            elif result == "failed":
                detail = _gate_detail(cmd, out, res, step.get("timeout_min"))
                st.update(status="failed", reason=detail, summary=f"Gate {name} failed: {detail}.")
                _put_state(run, i, st)
                run.status = RUN_FAILED
                runlog.append(run, f"Step {name} (gate) failed: "
                              f"{_gate_detail(cmd, out, res, step.get('timeout_min'), names=False)}")
            else:
                _put_state(run, i, st)
            await s.commit()
            step["recorded"] = True
        return more
