"""Workflow runner: advances workflow runs stage by stage.

A thin orchestrator (like the scheduler) over the EXISTING task machinery — it
does not execute anything itself. For each active run it watches the current
stage's tasks and:
  * all completed            -> start the next stage (or finish the run)
  * any failed/cancelled     -> halt the run (status=failed)
  * any still active/waiting  -> leave it (a gated stage pauses here until the
                                human approves, then it resumes on the next tick)

Stage shape (on the workflow / run snapshot):
    [{"name": "Develop", "items": [{"agent": "<id|name>", "prompt": "..."}]}, ...]
Each item prompt may contain {{input}}, replaced with the run's input; if it has
no placeholder, the input is appended.
"""

import asyncio

from sqlalchemy import select

from .constants import Status
from .database import SessionLocal
from .models import Agent, Task, WorkflowRun
from .task_service import build_task

RUN_PENDING = "pending"
RUN_RUNNING = "running"
RUN_PAUSED = "paused"
RUN_COMPLETED = "completed"
RUN_FAILED = "failed"
ACTIVE_RUN = {RUN_PENDING, RUN_RUNNING, RUN_PAUSED}


def apply_input(prompt: str, run_input: str) -> str:
    if "{{input}}" in (prompt or ""):
        return prompt.replace("{{input}}", run_input or "")
    if run_input:
        return f"{prompt}\n\n{run_input}"
    return prompt


async def _start_stage(s, run: WorkflowRun, stage_index: int, worker) -> list[str]:
    """Create + submit the tasks for a stage (items run in parallel). Returns the
    submitted (id, priority, created_at) tuples to hand to the worker AFTER commit."""
    stage = run.stages[stage_index]
    agents = (await s.execute(select(Agent))).scalars().all()
    by_id = {a.id: a for a in agents}
    by_name = {a.name: a for a in agents}
    submits = []
    for item in stage.get("items", []):
        agent = by_id.get(item.get("agent")) or by_name.get(item.get("agent"))
        if agent is None:
            # Unknown agent -> fail the run rather than silently skip.
            run.status = RUN_FAILED
            return []
        prompt = apply_input(item.get("prompt", ""), run.input)
        task = await build_task(
            s,
            prompt=prompt,
            title=f"[{stage.get('name', f'Stage {stage_index + 1}')}] {agent.name}: {prompt.splitlines()[0][:50]}",
            project_id=agent.project_id,
            model=agent.model or None,
            max_turns=agent.max_turns,
            max_budget_usd=agent.max_budget_usd,
            priority=agent.priority,
            tags=(agent.tags or []) + ["workflow"],
            agent_id=agent.id,
            system_prompt=agent.system_prompt or None,
            requires_approval=agent.requires_approval,
            allow_remote=agent.allow_remote,
            workflow_run_id=run.id,
            stage_index=stage_index,
        )
        submits.append(task)
    return submits


class WorkflowRunner:
    """Background loop that advances active workflow runs."""

    def __init__(self, worker, interval_seconds: int | None = None):
        from .config import settings

        self.worker = worker
        self.interval = interval_seconds or settings.workflow_runner_interval_seconds
        self._task = None
        self._stop = asyncio.Event()

    async def start(self):
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self._stop.set()
        if self._task:
            await asyncio.wait([self._task])

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

    async def start_run(self, run_id: str):
        """Kick off a freshly created run (start stage 0) immediately."""
        await self._advance(run_id, first=True)

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

            if first or run.status == RUN_PENDING:
                run.status = RUN_RUNNING
                run.current_stage = 0
                submits = await _start_stage(s, run, 0, self.worker)
                await s.commit()
                for t in submits:
                    await s.refresh(t)
                results = [(t.id, t.priority, t.created_at, t.status) for t in submits]
                await self._submit(results)
                return

            # Inspect the current stage's tasks.
            tasks = (
                await s.execute(
                    select(Task).where(
                        Task.workflow_run_id == run.id,
                        Task.stage_index == run.current_stage,
                    )
                )
            ).scalars().all()
            if not tasks:
                return
            statuses = [t.status for t in tasks]
            if any(st in (Status.FAILED, Status.CANCELLED) for st in statuses):
                run.status = RUN_FAILED
                await s.commit()
                return
            if any(st == Status.AWAITING_APPROVAL for st in statuses):
                run.status = RUN_PAUSED  # gated stage waiting for a human
                await s.commit()
                return
            if all(st == Status.COMPLETED for st in statuses):
                # Advance to the next stage, or finish.
                if run.current_stage + 1 >= len(run.stages):
                    run.status = RUN_COMPLETED
                    await s.commit()
                    return
                run.status = RUN_RUNNING
                run.current_stage += 1
                submits = await _start_stage(s, run, run.current_stage, self.worker)
                await s.commit()
                for t in submits:
                    await s.refresh(t)
                results = [(t.id, t.priority, t.created_at, t.status) for t in submits]
                await self._submit(results)
                return
            # Otherwise still running — make sure the run reflects that.
            if run.status == RUN_PAUSED:
                run.status = RUN_RUNNING
                await s.commit()

    async def _submit(self, results):
        for tid, prio, created, status in results:
            if status != Status.AWAITING_APPROVAL:
                await self.worker.submit(tid, prio, created)
