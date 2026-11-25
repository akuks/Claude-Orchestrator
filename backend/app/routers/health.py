"""GET /admin/health: the admin "System health" page's data (admin only); and (phase 7d) the switch for
tidying old run history: GET/PATCH /admin/health/retention, POST /admin/health/retention/run."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import auth, retention
from ..health import runner

router = APIRouter(prefix="/admin/health", tags=["health"], dependencies=[Depends(auth.require_admin)])

_JOBS: set = set()  # "Run now" passes in flight (a reference so they are not collected)


@router.get("")
async def system_health(refresh: bool = False):
    """Runs the checks now (no background job). Cached 30 s per process;
    ``?refresh=true`` ("Check again") skips the cache."""
    return await runner.run_all(force=refresh)


class RetentionPatch(BaseModel):
    enabled: bool | None = None
    days: int | None = None


async def _retention_out() -> dict:
    cfg = await retention.load_settings()
    pre = await retention.preview(cfg["days"])
    return {"enabled": cfg["enabled"], "days": cfg["days"], "choices": list(retention.CHOICES),
            "tasks": pre["tasks"], "bytes": pre["bytes"], "last_run": cfg["last_run"],
            "last_count": cfg["last_count"], "running": retention.is_running()}


@router.get("/retention")
async def get_retention():
    """The tidying switch, what it would tidy now with its days, and the last run."""
    return await _retention_out()


@router.patch("/retention")
async def patch_retention(payload: RetentionPatch, request: Request):
    try:
        await retention.save_settings(enabled=payload.enabled, days=payload.days,
                                      by=request.state.principal.user.id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return await _retention_out()


@router.post("/retention/run", status_code=202)
async def run_retention():
    """Tidy now in the background (the switch must be on); GET shows ``running`` and then the last run."""
    if not (await retention.load_settings())["enabled"]:
        raise HTTPException(409, "Tidying is switched off")
    if retention.is_running():
        raise HTTPException(409, "Tidying is already running")
    job = asyncio.create_task(retention.run_if_enabled())
    _JOBS.add(job)
    job.add_done_callback(_JOBS.discard)
    return JSONResponse({"running": True}, status_code=202)
