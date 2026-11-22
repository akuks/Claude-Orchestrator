"""Ask about this run (phase 3c): service functions."""

from datetime import datetime, timezone

from sqlalchemy import update

from ..database import SessionLocal
from ..models import RunAsk


async def fail_interrupted_asks() -> int:
    """Startup sweep: an ask still pending belonged to a process that is gone; mark it failed."""
    async with SessionLocal() as s:
        res = await s.execute(
            update(RunAsk).where(RunAsk.status == "pending")
            .values(status="failed", error="interrupted", answered_at=datetime.now(timezone.utc)))
        await s.commit()
        return res.rowcount or 0
