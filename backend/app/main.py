from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import init_db
from .routers import stream, tasks
from .worker import WorkerManager


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    worker = WorkerManager()
    app.state.worker = worker
    await worker.start()
    try:
        yield
    finally:
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


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok", "version": app.version}
