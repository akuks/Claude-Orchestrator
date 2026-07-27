import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import mcp_manager
from .config import settings
from .database import init_db
from .routers import mcp, projects, schedules, stream, tasks, templates
from .scheduler import Scheduler
from .worker import WorkerManager


async def _mcp_health_loop():
    while True:
        await asyncio.sleep(settings.mcp_health_interval_seconds)
        try:
            await mcp_manager.health_check_all()
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
    try:
        yield
    finally:
        health_task.cancel()
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


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok", "version": app.version}
