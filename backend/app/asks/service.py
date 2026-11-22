"""Ask about this run (phase 3c): service functions."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update

from ..access import effective_project_id
from ..agent_env import agent_env_or_none
from ..config import settings
from ..database import SessionLocal
from ..models import Project, RunAsk, Task
from ..redaction import clean_text, redact, secrets_from_env
from .record import build_record
from .runner import run_ask

log = logging.getLogger(__name__)

NO_CREDENTIAL = "Add your Claude credential to ask about runs"
QUESTION_MAX = 2000
ANSWER_MAX = 8000
ERROR_MAX = 200
_tasks: set[asyncio.Task] = set()
_sem: asyncio.Semaphore | None = None
_sem_loop = None


def _semaphore() -> asyncio.Semaphore:
    global _sem, _sem_loop
    loop = asyncio.get_running_loop()
    if _sem is None or _sem_loop is not loop:
        _sem, _sem_loop = asyncio.Semaphore(max(settings.ask_concurrency, 1)), loop
    return _sem


async def _project_env(s, task: Task) -> dict | None:
    pid = await effective_project_id(s, task)
    project = await s.get(Project, pid) if pid else None
    return project.env if project is not None else None


async def start_ask(task_id: str, user_id: str, question: str) -> RunAsk:
    """File a pending ask and run it in the background. The caller has already checked
    visibility and the question length; 422 without a credential, 409 with a pending ask, 429 past the hourly limit."""
    async with SessionLocal() as s:
        task = await s.get(Task, task_id)
        if task is None:
            raise HTTPException(404, "Task not found")
        env = await agent_env_or_none(s, user_id, project_env=await _project_env(s, task), purpose="ask")
        if env is None:
            raise HTTPException(422, NO_CREDENTIAL)
        pending = select(func.count()).select_from(RunAsk).where(
            RunAsk.task_id == task_id, RunAsk.user_id == user_id, RunAsk.status == "pending")
        if (await s.execute(pending)).scalar_one():
            raise HTTPException(409, "You already have a question being answered on this run")
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        recent = (await s.execute(select(func.count()).select_from(RunAsk).where(
            RunAsk.user_id == user_id, RunAsk.created_at >= since))).scalar_one()
        if recent >= settings.ask_hourly_limit:
            raise HTTPException(429, "Too many questions this hour, try again later")
        ask = RunAsk(task_id=task_id, user_id=user_id, question=question, status="pending",
                     model=settings.ask_model)
        s.add(ask)
        await s.commit()
        ask_id = ask.id
        # A double-click: two pendings can both commit. The lowest id stays, the other withdraws.
        ids = (await s.execute(select(RunAsk.id).where(
            RunAsk.task_id == task_id, RunAsk.user_id == user_id, RunAsk.status == "pending")
            .order_by(RunAsk.id))).scalars().all()
        if len(ids) > 1 and ids[0] != ask_id:
            await s.execute(delete(RunAsk).where(RunAsk.id == ask_id))
            await s.commit()
            raise HTTPException(409, "You already have a question being answered on this run")
        await s.refresh(ask)
        s.expunge(ask)
    t = asyncio.create_task(_execute(ask_id))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return ask


async def _finish(ask_id: str, **values) -> None:
    async with SessionLocal() as s:
        await s.execute(update(RunAsk).where(RunAsk.id == ask_id)
                        .values(answered_at=datetime.now(timezone.utc), model=settings.ask_model, **values))
        await s.commit()


async def _execute(ask_id: str) -> None:
    """Background: build the record, make the call, store the outcome. Never raises."""
    try:
        async with _semaphore():
            async with SessionLocal() as s:
                ask = await s.get(RunAsk, ask_id)
                task = await s.get(Task, ask.task_id) if ask else None
                if ask is None or task is None:
                    return
                env = await agent_env_or_none(s, ask.user_id, project_env=await _project_env(s, task),
                                              purpose="ask")
                if env is None:
                    await _finish(ask_id, status="failed", error=NO_CREDENTIAL[:ERROR_MAX])
                    return
                secrets = secrets_from_env(env)
                prior_rows = (await s.execute(
                    select(RunAsk).where(RunAsk.task_id == ask.task_id, RunAsk.user_id == ask.user_id,
                                         RunAsk.status == "answered", RunAsk.id != ask_id)
                    .order_by(RunAsk.answered_at.desc()).limit(3))).scalars().all()
                prior = [(r.question, r.answer or "") for r in reversed(prior_rows)]  # oldest first
                prompt = await build_record(s, task, ask.question, prior, secrets)
            res = await run_ask(prompt, env)
        if res.answer:
            answer = clean_text(redact(res.answer, secrets), ANSWER_MAX)
            await _finish(ask_id, status="answered", answer=answer, cost_usd=res.cost_usd, error=None)
        else:
            err = clean_text(redact(res.error or "no answer", secrets), ERROR_MAX)
            await _finish(ask_id, status="failed", error=err, cost_usd=res.cost_usd)
    except Exception:
        log.exception("ask %s failed", ask_id)
        try:
            await _finish(ask_id, status="failed", error="failed")
        except Exception:
            pass


async def fail_interrupted_asks() -> int:
    """Startup sweep: an ask still pending belonged to a process that is gone; mark it failed."""
    async with SessionLocal() as s:
        res = await s.execute(
            update(RunAsk).where(RunAsk.status == "pending")
            .values(status="failed", error="interrupted", answered_at=datetime.now(timezone.utc)))
        await s.commit()
        return res.rowcount or 0
