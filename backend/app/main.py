import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from . import mcp_manager
from . import auth as auth_mod
from .auth import current_principal
from .config import settings
from .constants import Status
from .database import SessionLocal, init_db
from .models import Task
from .routers import (
    agents,
    approvals,
    auth,
    context_docs,
    findings,
    mcp,
    projects,
    schedules,
    skills,
    stream,
    tasks,
    templates,
    usage,
    users,
    webhooks,
    workflows,
)
from .scheduler import Scheduler
from .worker import WorkerManager
from .workflow_runner import WorkflowRunner


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
    await auth.bootstrap_admin()
    await auth.assign_orphans_to_first_admin()
    worker = WorkerManager()
    app.state.worker = worker
    await worker.start()
    scheduler = Scheduler(worker)
    app.state.scheduler = scheduler
    await scheduler.start()
    workflow_runner = WorkflowRunner(worker)
    app.state.workflow_runner = workflow_runner
    await workflow_runner.start()
    health_task = asyncio.create_task(_mcp_health_loop())
    approval_task = asyncio.create_task(_approval_timeout_loop())
    try:
        yield
    finally:
        health_task.cancel()
        approval_task.cancel()
        await workflow_runner.stop()
        await scheduler.stop()
        await worker.stop()


app = FastAPI(title="Claude Orchestrator", version="0.1.0", lifespan=lifespan)

@app.middleware("http")
async def refresh_session_cookie(request, call_next):
    """current_principal flags request.state when it slid the session; re-send the
    cookie then so the browser's max_age slides with the server-side expiry."""
    response = await call_next(request)
    raw = getattr(request.state, "refresh_session_cookie", None)
    if raw:
        auth_mod.set_session_cookie(request, response, raw)
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Open: /auth/* guards its own routes per-endpoint; webhooks verify an HMAC;
# the WebSocket stream authenticates the cookie itself (ws_principal).
app.include_router(auth.router)
app.include_router(webhooks.router)
app.include_router(stream.router)

# Everything else requires a logged-in user or an API token.
_authed = [Depends(current_principal)]
for _r in (
    tasks.router,
    mcp.router,
    projects.router,
    templates.router,
    schedules.router,
    approvals.router,
    usage.router,
    findings.router,
    agents.router,
    workflows.router,
    skills.router,
    context_docs.router,
    users.router,
):
    app.include_router(_r, dependencies=_authed)


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok", "version": app.version}


@app.get("/system/claude", tags=["meta"], dependencies=_authed)
async def claude_status():
    """Report whether the Claude CLI is present and can authenticate — so a
    headless deployment can be verified before running tasks."""

    async def _run(args: list[str]):
        try:
            proc = await asyncio.create_subprocess_exec(
                settings.claude_bin,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            return proc.returncode, out.decode(errors="replace").strip()
        except FileNotFoundError:
            return None, "not found"
        except (asyncio.TimeoutError, OSError):
            return None, "error"

    vcode, version = await _run(["--version"])
    acode, _ = await _run(["auth", "status"])
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return {
        "claude_bin": settings.claude_bin,
        "found": vcode is not None,
        "version": version if vcode == 0 else None,
        "authenticated": bool(has_key or acode == 0),
        "auth_method": "ANTHROPIC_API_KEY" if has_key else ("login/token" if acode == 0 else None),
    }
