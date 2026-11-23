import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from . import mcp_manager
from . import auth as auth_mod
from .asks.service import fail_interrupted_asks
from .auth import current_principal
from .config import settings
from .credentials import credential_status
from .constants import Status
from .database import SessionLocal, init_db
from .guard import grants as guard_grants
from .guard import runtime as guard_runtime
from .guard.plugin import ensure_plugin
from .models import Task
from .routers import (
    agents,
    approvals,
    auth,
    context_docs,
    credentials,
    decisions,
    guard,
    findings,
    handoffs,
    mcp,
    projects,
    questions,
    asks,
    schedules,
    skills,
    stream,
    tasks,
    teams,
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


async def auto_reject_stale(submit=None) -> list[str]:
    """Cancel tasks left awaiting approval longer than the configured timeout;
    co-guard (Ruling P24): each one drops its thread's deferred follow-ups
    (``grants.after_task_finished``). Returns the cancelled task ids."""
    if settings.approval_timeout_seconds <= 0:
        return []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=settings.approval_timeout_seconds)
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
            t.decided_at = now
            t.completed_at = now
            t.decision_reason = "Auto-rejected (approval timed out)"
        await s.commit()
        ended = [t.id for t in stale]
    for tid in ended:
        try:
            await guard_grants.after_task_finished(tid, submit=submit)
        except Exception:  # noqa: BLE001
            pass
    return ended


async def _approval_timeout_loop():
    """Auto-reject approvals left pending longer than the configured timeout."""
    while True:
        await asyncio.sleep(settings.approval_check_interval_seconds)
        try:
            await auto_reject_stale(app.state.worker.submit)
        except Exception:  # noqa: BLE001
            continue


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # co-guard plugin (read-only, content-hashed): every task loads it first.
    ensure_plugin(guard_runtime.plugin_root())
    guard_runtime.log_approval_address()
    await auth.bootstrap_admin()
    await auth.assign_orphans_to_first_admin()
    await fail_interrupted_asks()
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
# co-guard identity check (no auth by design, see the route).
app.include_router(guard.router)

# Everything else requires a logged-in user or an API token.
_authed = [Depends(current_principal)]
for _r in (
    tasks.router,
    questions.router,
    asks.router,
    handoffs.router,
    mcp.router,
    projects.router,
    decisions.router,
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
    users.directory_router,
    teams.router,
    credentials.router,
    credentials.admin_router,
    guard.api,
):
    app.include_router(_r, dependencies=_authed)


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok", "version": app.version}


@app.get("/system/claude", tags=["meta"], dependencies=_authed)
async def claude_status(request: Request):
    """Whether the Claude CLI is present on the server (binary + version, a
    server check), and whether the *calling user* can run tasks: their own
    Claude credential, the org fallback, or none. The server's own login or
    ANTHROPIC_API_KEY is never reported - tasks never use it. No secrets."""

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
    async with SessionLocal() as s:
        you = await credential_status(s, request.state.principal.user.id)
    return {
        "claude_bin": settings.claude_bin,
        "found": vcode is not None,
        "version": version if vcode == 0 else None,
        "authenticated": you["claude"] != "none",
        "auth_method": you["claude"] if you["claude"] != "none" else None,
        "you": you,
    }
