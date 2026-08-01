import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from . import mcp_manager
from .config import settings
from .constants import Status
from .database import SessionLocal, init_db
from .models import Task
from .routers import (
    approvals,
    mcp,
    projects,
    schedules,
    stream,
    tasks,
    templates,
    usage,
)
from .scheduler import Scheduler
from .worker import WorkerManager


async def _mcp_health_loop():
    while True:
        await asyncio.sleep(settings.mcp_health_interval_seconds)
        try:
            await mcp_manager.health_check_all()
        except Exception:  # noqa: BLE001
            continue


async def _approval_timeout_loop():
    """Auto-reject approvals left pending longer than the configured timeout."""
    while True:
        await asyncio.sleep(settings.approval_check_interval_seconds)
        if settings.approval_timeout_seconds <= 0:
            continue
        cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=settings.approval_timeout_seconds
        )
        try:
            async with SessionLocal() as s:
                stale = (
                    await s.execute(
                        select(Task).where(
                            Task.status == Status.AWAITING_APPROVAL,
                            Task.created_at < cutoff,
                        )
                    )
                ).scalars().all()
                for t in stale:
                    t.status = Status.CANCELLED
                    t.decided_at = datetime.now(timezone.utc)
                    t.completed_at = datetime.now(timezone.utc)
                    t.decision_reason = "Auto-rejected (approval timed out)"
                await s.commit()
        except Exception:  # noqa: BLE001
            continue


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    worker = WorkerManager()
    app.state.worker = worker
    await worker.start()
    scheduler = Scheduler(worker)
    app.state.scheduler = scheduler
    await scheduler.start()
    health_task = asyncio.create_task(_mcp_health_loop())
    approval_task = asyncio.create_task(_approval_timeout_loop())
    try:
        yield
    finally:
        health_task.cancel()
        approval_task.cancel()
        await scheduler.stop()
        await worker.stop()


app = FastAPI(title="Claude Orchestrator", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tasks.router)
app.include_router(stream.router)
app.include_router(mcp.router)
app.include_router(projects.router)
app.include_router(templates.router)
app.include_router(schedules.router)
app.include_router(approvals.router)
app.include_router(usage.router)


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok", "version": app.version}
