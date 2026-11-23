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
Any stage may carry `before` pre-flight lines; an unmet line holds the stage
(the run stays running) and is checked again on every tick.

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
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select, update

from .constants import Status
from .database import SessionLocal
from . import tiers
from .gates import codestate, executor, preflight, runlog, totals
from .gates.spec import stage_kind
from .models import Agent, Project, Task, WorkflowGateRun, WorkflowRun
from .redaction import clean_text
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
UNCHANGED_WORDS = "failed then passed unchanged"
GATE_LIVE = ("waiting", "running")  # gate states that want a live process


def substitute(prompt: str, run_input: str, stage_outputs: dict[int, str]) -> str:
    """Fill placeholders in a stage prompt:
      {{input}}   -> the run's input
      {{prev}}    -> the previous stage's combined output
      {{stageN}}  -> stage N's combined output (N is 1-based, matching the UI)
    If no {{input}} placeholder exists, the input is appended (back-compat).
    """
    import re

    p = prompt or ""
    had_input = "{{input}}" in p
    p = p.replace("{{input}}", run_input or "")
    # {{prev}} = highest-numbered available stage output.
    prev = ""
    if stage_outputs:
        prev = stage_outputs[max(stage_outputs)]
    p = p.replace("{{prev}}", prev)
    # {{stageN}} (1-based)
    def _stage_sub(m):
        n = int(m.group(1))
        return stage_outputs.get(n - 1, "")
    p = re.sub(r"\{\{stage(\d+)\}\}", _stage_sub, p)
    if not had_input and run_input and "{{prev}}" not in (prompt or "") and not re.search(r"\{\{stage\d+\}\}", prompt or ""):
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


async def _stage_outputs(s, run: WorkflowRun, up_to_stage: int) -> dict[int, str]:
    """Output per stage index for stages < up_to_stage: agent stages give the combined
    result_text of the CURRENT attempt's tasks; gate and approval stages give their summary."""
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


async def _others_using(s, run) -> int:
    """How many other active runs touch one of this run's projects (pre-flight is host-wide)."""
    mine = set(run.project_ids or [])
    if not mine:
        return 0
    others = (
        await s.execute(
            select(WorkflowRun.project_ids).where(
                WorkflowRun.status.in_(list(ACTIVE_RUN)), WorkflowRun.id != run.id
            )
        )
    ).scalars().all()
    return sum(1 for pids in others if mine & set(pids or []))


async def _hold(s, run, i: int, st: dict, reason: str) -> None:
    others = await _others_using(s, run)
    if others:
        reason += f" ({others} other active run{'s use' if others != 1 else ' uses'} the same project)"
    if st.get("status") != "held" or st.get("reason") != reason:
        runlog.append(run, f"Step {_stage_name(run, i)} held: {reason}")
    st.update(status="held", reason=reason)
    _put_state(run, i, st)
    if run.status == RUN_PAUSED:
        run.status = RUN_RUNNING


async def _start_stage(s, run: WorkflowRun, stage_index: int, worker) -> list:
    """Start a stage. Agent stages: create the tasks (items run in parallel) and return
    them to hand to the worker AFTER commit. Gate stages: a new hand-out in `waiting`
    (the runner spawns the gate after commit). Approval stages: pause the run.
    Unmet pre-flight holds the stage instead (nothing starts, the run stays running)."""
    stage = run.stages[stage_index]
    kind = stage_kind(stage)
    old = get_state(run, stage_index)
    attempt = _attempt(run, stage_index)

    reason = await _preflight(stage.get("before") or [])
    if reason:
        await _hold(s, run, stage_index, old, reason)
        return []

    st: dict = {"attempt": attempt, "started_at": _iso()}
    if "log_seq" in old:
        st["log_seq"] = old["log_seq"]
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
    agents = (await s.execute(select(Agent))).scalars().all()
    by_id = {a.id: a for a in agents}
    by_name = {a.name: a for a in agents}
    # Prior stages' outputs so a stage can build on what came before (e.g. a lead
    # planning stage feeding the dev stages via {{prev}}).
    outputs = await _stage_outputs(s, run, stage_index) if stage_index > 0 else {}
    submits = []
    for item in stage.get("items") or []:
        agent = by_id.get(item.get("agent")) or by_name.get(item.get("agent"))
        if agent is None:
            # Unknown agent -> fail the run rather than silently skip.
            st.update(status="failed", reason=f"unknown agent {item.get('agent')}")
            _put_state(run, stage_index, st)
            runlog.append(run, f"Step {name} failed: unknown agent {item.get('agent')}")
            run.status = RUN_FAILED
            return []
        prompt = substitute(item.get("prompt", ""), run.input, outputs)
        task = await build_task(
            s,
            prompt=prompt,
            title=f"[{stage.get('name', f'Stage {stage_index + 1}')}] {agent.name}: {prompt.splitlines()[0][:50]}",
            project_id=agent.project_id,
            model=await tiers.agent_model(s, agent),
            max_turns=agent.max_turns,
            max_budget_usd=agent.max_budget_usd,
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
        submits.append(task)
    return submits


def _user_name(user) -> str:
    for attr in ("name", "display_name", "username", "email"):
        v = getattr(user, attr, None)
        if v:
            return str(v)
    return "someone"


def _gate_detail(cmd: str, out, res, timeout_min) -> str:
    if out.exit_code is not None:
        d = f"{cmd}, exit {out.exit_code}"
    elif out.outcome == "timed_out":
        d = f"{cmd}, timed out after {timeout_min} min"
    else:
        d = f"{cmd}, {out.outcome}"
    if res.failed:
        d += f", {res.failed} failed"
        if res.failing:
            d += f": {_names(res.failing)}"
    return d


def _counts(res) -> str:
    if res.runner is None:
        return ""
    parts = [f"{res.passed or 0} passed", f"{res.failed or 0} failed"]
    if res.skipped:
        parts.append(f"{res.skipped} skipped")
    return "; " + ", ".join(parts)


class WorkflowRunner:
    """Background loop that advances active workflow runs."""

    def __init__(self, worker, interval_seconds: int | None = None):
        from .config import settings

        self.worker = worker
        self.interval = interval_seconds or settings.workflow_runner_interval_seconds
        self._task = None
        self._stop = asyncio.Event()
        # Live gates, in this process only (single uvicorn process).
        self._gates: dict[tuple[str, int], asyncio.Task] = {}
        # (run_id, stage) -> the command in flight: {proc, cmd, k, n, kind, attempt,
        # handout, log_name, code_state, started_at, ...}
        self._procs: dict[tuple[str, int], dict] = {}

    async def start(self):
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self._stop.set()
        if self._task:
            await asyncio.wait([self._task])
        for t in list(self._gates.values()):
            t.cancel()

    async def _loop(self):
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:  # never let the loop die
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass

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
        async with SessionLocal() as s:
            run = await s.get(WorkflowRun, run_id)
            if run is None or run.status not in ACTIVE_RUN:
                return False
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
                if st.get("status") not in ("passed", "failed", "approved", "rejected", None):
                    live = self._procs.get((run_id, i))
                    if live:
                        runlog.append(run, f"Step {_stage_name(run, i)} (gate) still needs: {live['cmd']}")
                    st.update(status="failed", reason="cancelled")
                    _put_state(run, i, st)
            await s.commit()
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
        async with SessionLocal() as s:
            run = await s.get(WorkflowRun, run_id)
            if run is None or run.status != RUN_FAILED:
                return False
            i = run.current_stage
            if not (0 <= i < len(run.stages or [])):
                return False
            old = get_state(run, i)
            fresh = {"attempt": _attempt(run, i) + 1}
            if "log_seq" in old:
                fresh["log_seq"] = old["log_seq"]
            _put_state(run, i, fresh)
            run.status = RUN_RUNNING
            runlog.append(run, f"Step {_stage_name(run, i)} retried (attempt {fresh['attempt'] + 1})")
            submits = await _start_stage(s, run, i, self.worker)
            await s.commit()
            for t in submits:
                await s.refresh(t)
            results = [(t.id, t.priority, t.created_at, t.status) for t in submits]
            spawn = self._want_spawn(run, i)
        await self._submit(results)
        if spawn:
            self._spawn_gate(run_id, i, spawn)
        return True

    async def approve_stage(self, run_id: str, i: int, user) -> None:
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
        await self._advance(run_id)

    async def reject_stage(self, run_id: str, i: int, user, reason: str) -> None:
        reason = clean_text(reason or "").strip()
        if not 1 <= len(reason) <= 500:
            raise ValueError("a reason of 1 to 500 characters is required")
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

    async def allow_gate(self, run_id: str, i: int, user) -> None:
        """Allow this hand-out's guard-flagged commands to run ("Allow and run")."""
        async with SessionLocal() as s:
            run, st = await self._decision_target(s, run_id, i, "gate", "needs_approval",
                                                  "this gate is not waiting for an allowance")
            who = _user_name(user)
            st.update(status="waiting", allowed=True, decided_by=who, decided_at=_iso(), reason=None)
            _put_state(run, i, st)
            runlog.append(run, f"Step {_stage_name(run, i)} (gate) allowed by {who}")
            await s.commit()
            handout = st.get("handout")
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
            await self._advance(rid)

    async def _advance(self, run_id: str, first: bool = False):
        submits = []
        async with SessionLocal() as s:
            run = await s.get(WorkflowRun, run_id)
            if run is None or run.status not in ACTIVE_RUN:
                return
            if not run.stages:
                run.status = RUN_COMPLETED
                await s.commit()
                return

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
                    return
                await s.refresh(run)
                submits = await _start_stage(s, run, 0, self.worker)
                await s.commit()
                for t in submits:
                    await s.refresh(t)
                results = [(t.id, t.priority, t.created_at, t.status) for t in submits]
                spawn = self._want_spawn(run, 0)
                await self._submit(results)
                if spawn:
                    self._spawn_gate(run_id, 0, spawn)
                return

            i = run.current_stage
            if not (0 <= i < len(run.stages)):
                return
            kind = stage_kind(run.stages[i])
            st = get_state(run, i)
            status = st.get("status")

            if status == "held":
                await self._restart_held(s, run, i)
                return

            if kind == "gate":
                if status == "passed":
                    await self._next_stage(s, run, i)
                elif status == "failed":
                    if run.status != RUN_FAILED:
                        run.status = RUN_FAILED
                        await s.commit()
                elif status in GATE_LIVE:
                    t = self._gates.get((run_id, i))
                    if t is not None and not t.done():
                        return
                    if status == "running":
                        # This process has no child for it: the server restarted mid-command.
                        runlog.append(run, f"Step {_stage_name(run, i)} (gate) resumed after a server restart")
                        await s.commit()
                    self._spawn_gate(run_id, i, st.get("handout"))
                elif status is None:
                    await self._restart_held(s, run, i)
                return

            if kind == "approval":
                if status == "approved":
                    await self._next_stage(s, run, i)
                elif status == "rejected":
                    if run.status != RUN_FAILED:
                        run.status = RUN_FAILED
                        await s.commit()
                elif status == "waiting_approval":
                    if run.status != RUN_PAUSED:
                        run.status = RUN_PAUSED
                        await s.commit()
                elif status is None:
                    await self._restart_held(s, run, i)
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
            if any(x in (Status.FAILED, Status.CANCELLED) for x in statuses):
                run.status = RUN_FAILED
                if status != "failed":
                    n = sum(1 for x in statuses if x in (Status.FAILED, Status.CANCELLED))
                    st.update(status="failed", reason=f"{n} task{'s' if n != 1 else ''} failed")
                    _put_state(run, i, st)
                    runlog.append(run, f"Step {_stage_name(run, i)} failed: {st['reason']}")
                await s.commit()
                return
            if any(x == Status.AWAITING_APPROVAL for x in statuses):
                run.status = RUN_PAUSED  # gated stage waiting for a human
                await s.commit()
                return
            if all(x == Status.COMPLETED for x in statuses):
                await self._next_stage(s, run, i, finish_agent=True)
                return
            # Otherwise still running — make sure the run reflects that.
            if run.status == RUN_PAUSED:
                run.status = RUN_RUNNING
                await s.commit()

    async def _restart_held(self, s, run, i: int) -> None:
        """Start stage i in this session and commit; a held stage is checked again."""
        submits = await _start_stage(s, run, i, self.worker)
        await s.commit()
        for t in submits:
            await s.refresh(t)
        results = [(t.id, t.priority, t.created_at, t.status) for t in submits]
        spawn = self._want_spawn(run, i)
        await self._submit(results)
        if spawn:
            self._spawn_gate(run.id, i, spawn)

    async def _next_stage(self, s, run, i: int, finish_agent: bool = False) -> None:
        """Move from stage i to i + 1 (or complete the run). A conditional UPDATE claims
        the move so a tick and a finishing gate cannot both start the next stage."""
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
            return
        await s.refresh(run)
        if finish_agent:
            st = get_state(run, i)
            st.update(status="done", finished_at=_iso())
            _put_state(run, i, st)
            t = await totals.compute(s, run)
            runlog.append(run, f"Step {_stage_name(run, i)} done. {totals.line(t)}")
        if last:
            await s.commit()
            return
        await self._restart_held(s, run, i + 1)

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
        `record`, a command that was in flight gets a `cancelled` row."""
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
            if record and info:
                await self._record_cancelled(run_id, key[1], info)

    async def _record_cancelled(self, run_id: str, i: int, info: dict) -> None:
        async with SessionLocal() as s:
            run = await s.get(WorkflowRun, run_id)
            if run is None or get_state(run, i).get("handout") != info["handout"]:
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

    async def _gate_main(self, run_id: str, i: int, handout: str) -> None:
        key = (run_id, i)
        try:
            while True:
                step = await self._gate_prepare(run_id, i, handout)
                if step is None:
                    return
                self._procs[key] = {**step, "proc": None, "started_at": _now()}

                def on_spawn(proc, _key=key):
                    if _key in self._procs:
                        self._procs[_key]["proc"] = proc

                try:
                    out = await executor.run_command(
                        step["cmd"], cwd=step["cwd"], env=step["env"], log_path=step["log_path"],
                        timeout_s=step["timeout_s"], on_spawn=on_spawn,
                    )
                finally:
                    self._procs.pop(key, None)
                try:
                    await asyncio.to_thread(executor.prune_logs, run_id, i, LOGS_KEEP)
                except Exception:  # noqa: BLE001
                    pass
                more = await self._gate_record(run_id, i, handout, step, out)
                if more is None:
                    return  # stale hand-out: dropped
                if not more:
                    break
            await self._advance(run_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - a broken gate fails its stage, never the loop
            await self._gate_error(run_id, i, handout, e)
        finally:
            if self._gates.get(key) is asyncio.current_task():
                self._gates.pop(key, None)

    async def _gate_error(self, run_id: str, i: int, handout: str, err: Exception) -> None:
        try:
            async with SessionLocal() as s:
                run = await s.get(WorkflowRun, run_id)
                if run is None or run.status not in ACTIVE_RUN or run.current_stage != i:
                    return
                st = get_state(run, i)
                if st.get("handout") != handout:
                    return
                name = _stage_name(run, i)
                reason = f"the gate could not run: {type(err).__name__}: {err}"[:500]
                st.update(status="failed", reason=reason, summary=f"Gate {name} failed: {reason}.")
                _put_state(run, i, st)
                run.status = RUN_FAILED
                runlog.append(run, f"Step {name} (gate) failed: {reason}")
                await s.commit()
        except Exception:  # noqa: BLE001
            pass

    async def _gate_prepare(self, run_id: str, i: int, handout: str) -> dict | None:
        """Pick the next command of this hand-out, run pre-flight (before the first
        command) and the guard check. None when nothing should run now."""
        async with SessionLocal() as s:
            run = await s.get(WorkflowRun, run_id)
            if run is None or run.status not in ACTIVE_RUN or run.current_stage != i:
                return None
            stage = run.stages[i]
            st = get_state(run, i)
            if st.get("handout") != handout or st.get("status") not in GATE_LIVE:
                return None
            name = _stage_name(run, i)
            cmds = list(stage.get("run") or [])
            rnd = st.get("round") or "full"
            passed = list(st.get("passed_cmds") or [])
            if rnd == "retry":
                idx, cmd, kind = 0, stage.get("retry"), "retry"
            else:
                todo = [j for j in range(len(cmds)) if j not in passed]
                if not todo:
                    return None
                idx, cmd, kind = todo[0], cmds[todo[0]], "full"
            if not cmd:
                return None

            project = await s.get(Project, stage.get("project")) if stage.get("project") else None
            if project is None or not (project.directory or "").strip():
                reason = "the gate's project is missing or has no directory"
                st.update(status="failed", reason=reason, summary=f"Gate {name} failed: {reason}.")
                _put_state(run, i, st)
                run.status = RUN_FAILED
                runlog.append(run, f"Step {name} (gate) failed: {reason}")
                await s.commit()
                return None

            if rnd == "full" and not passed and st.get("status") == "waiting":
                # Pre-flight again just before the gate's first command.
                reason = await _preflight(stage.get("before") or [])
                if reason:
                    await _hold(s, run, i, st, reason)
                    await s.commit()
                    return None

            try:
                verdict = await executor.guard_verdict(s, project, cmd)
                decision = verdict.decision
                reasons = [str(getattr(h, "reason", h)) for h in (verdict.hits or [])]
            except Exception as e:  # noqa: BLE001 - fail closed
                decision, reasons = "deny", [f"the guard check failed ({type(e).__name__})"]
            if decision not in ("allow", "approve"):
                why = "; ".join(reasons) or "denied"
                fin = _now()
                s.add(WorkflowGateRun(
                    run_id=run_id, stage_index=i, attempt=_attempt(run, i), handout=handout, command=cmd,
                    kind=kind, started_at=fin, finished_at=fin, duration_ms=0, exit_code=None,
                    outcome="refused", failing=[], flaky=None,
                ))
                await s.flush()
                await self._prune_rows(s, run_id, i)
                reason = f"blocked by the guard: {why}"
                st.update(status="failed", reason=reason, hits=reasons,
                          summary=f"Gate {name} failed: {cmd} was {reason}.")
                _put_state(run, i, st)
                run.status = RUN_FAILED
                runlog.append(run, f"Step {name} (gate) failed: {cmd}, {reason}")
                await s.commit()
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
            attempt = _attempt(run, i)
            env = executor.gate_env(project)
            directory = project.directory
            timeout_min = int(stage.get("timeout_min") or 60)
        log_path = executor.log_file(run_id, i, seq)
        state = await codestate.code_state(directory)
        return {
            "cmd": cmd, "idx": idx, "kind": kind, "k": len(passed), "n": len(cmds),
            "attempt": attempt, "handout": handout, "cwd": directory, "env": env,
            "log_path": log_path, "log_name": log_path.name, "timeout_s": timeout_min * 60,
            "timeout_min": timeout_min, "code_state": state,
        }

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
        """Record one command's result. Returns True when the round has more to run,
        False when the gate finished, None when the result is stale (dropped)."""
        async with SessionLocal() as s:
            run = await s.get(WorkflowRun, run_id)
            if run is None or run.status not in ACTIVE_RUN or run.current_stage != i:
                return None
            st = get_state(run, i)
            if st.get("handout") != handout or st.get("status") != "running":
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

            s.add(WorkflowGateRun(
                run_id=run_id, stage_index=i, attempt=step["attempt"], handout=handout, command=cmd,
                kind=step["kind"], started_at=out.started_at, finished_at=out.finished_at,
                duration_ms=out.duration_ms, exit_code=out.exit_code, outcome=out.outcome,
                runner=res.runner, passed=res.passed, failed=res.failed, skipped=res.skipped,
                failing=list(res.failing or ()), flaky=flaky, code_state=step.get("code_state"),
                log_name=step["log_name"],
            ))
            await s.flush()
            await self._prune_rows(s, run_id, i)
            if out.log_error:
                runlog.append(run, f"Step {name} (gate): the log of `{cmd}` could not be written: {out.log_error}")

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
                line = f"Step {name} (gate) passed"
                if flaky is not None:
                    st["flaky"] = flaky
                    text += f" {_flaky_text(flaky)}."
                    line += f", {_flaky_text(flaky)}"
                st.update(status="passed", finished_at=_iso(), reason=None, summary=text)
                _put_state(run, i, st)
                t = await totals.compute(s, run)
                runlog.append(run, f"{line}. {totals.line(t)}")
            elif result == "failed":
                detail = _gate_detail(cmd, out, res, step.get("timeout_min"))
                text = f"Gate {name} failed: {detail}."
                st.update(status="failed", reason=detail, summary=text)
                _put_state(run, i, st)
                run.status = RUN_FAILED
                runlog.append(run, f"Step {name} (gate) failed: {detail}")
            else:
                _put_state(run, i, st)
            await s.commit()
        return more
