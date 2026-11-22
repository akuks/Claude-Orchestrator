"""Ask about this run (phase 3c): a person's private question, answered from the run's record.
Never on the agent token's allowlist."""

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from ..access import require_task
from ..asks import service
from ..config import settings
from ..database import SessionLocal
from ..models import RunAsk
from ..redaction import clean_text, redact
from ..schemas import AskIn, AskOut

router = APIRouter(prefix="/tasks", tags=["asks"])


@router.post("/{task_id}/asks", response_model=AskOut, status_code=202)
async def create_ask(task_id: str, body: AskIn, request: Request):
    if not settings.ask_enabled:
        raise HTTPException(503, "Asking about runs is turned off")
    p = request.state.principal
    async with SessionLocal() as s:
        await require_task(s, p, task_id)
    q = redact(clean_text(body.question), []).strip()
    if not 1 <= len(q) <= service.QUESTION_MAX:
        raise HTTPException(422, f"The question must be 1 to {service.QUESTION_MAX} characters")
    return await service.start_ask(task_id, p.user.id, q)


@router.get("/{task_id}/asks", response_model=list[AskOut])
async def list_asks(task_id: str, request: Request):
    p = request.state.principal
    async with SessionLocal() as s:
        await require_task(s, p, task_id)
        rows = (await s.execute(
            select(RunAsk).where(RunAsk.task_id == task_id, RunAsk.user_id == p.user.id)
            .order_by(RunAsk.created_at.desc(), RunAsk.id.desc()).limit(20))).scalars().all()
        return rows
