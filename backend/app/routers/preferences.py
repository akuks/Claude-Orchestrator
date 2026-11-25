"""The caller's own personal preferences (phase 8b): ``GET/PUT /me/preferences``.

Only the person reads or writes their list: no user id anywhere, so an admin reaches only their own. Agent tokens
get 403 (outside their allowlist in ``auth.current_principal``, and again here). Bodies are validated by
``preferences.validate`` rather than by FastAPI, whose default 422 echoes the request body back."""

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from .. import preferences
from ..database import SessionLocal
from ..models import UserPreferences

router = APIRouter(prefix="/me/preferences", tags=["preferences"])


def _uid(request: Request) -> str:
    p = request.state.principal
    if p.is_agent:
        raise HTTPException(403, "Token not allowed for this route")
    return p.user.id


def _out(row) -> dict:
    out = preferences.shape(row)
    at = out["updated_at"]
    if isinstance(at, datetime):
        out["updated_at"] = (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).isoformat()
    return out


@router.get("")
async def get_my_preferences(request: Request):
    uid = _uid(request)
    async with SessionLocal() as s:
        return _out(await s.get(UserPreferences, uid))


@router.put("")
async def put_my_preferences(request: Request, data: Any = Body(...)):
    uid = _uid(request)
    try:
        enabled, items = preferences.validate(data)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None  # the message names the rule, never the text
    for attempt in range(2):
        async with SessionLocal() as s:
            row = await s.get(UserPreferences, uid)
            if row is None:
                row = UserPreferences(user_id=uid)
                s.add(row)
            row.enabled, row.items = enabled, items
            row.updated_at = datetime.now(timezone.utc)
            try:
                await s.commit()
            except IntegrityError:
                # a concurrent first PUT created the row: retry as an update
                await s.rollback()
                if attempt:
                    raise
                continue
            return _out(row)
