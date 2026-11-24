"""GET /admin/health: the admin "System health" page's data (admin only)."""

from fastapi import APIRouter, Depends

from .. import auth
from ..health import runner

router = APIRouter(prefix="/admin/health", tags=["health"], dependencies=[Depends(auth.require_admin)])


@router.get("")
async def system_health(refresh: bool = False):
    """Runs the checks now (no background job). Cached 30 s per process;
    ``?refresh=true`` ("Check again") skips the cache."""
    return await runner.run_all(force=refresh)
