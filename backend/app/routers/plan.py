"""Claude plan limits (phase 7c): the signed-in person's latest plan status, every owner's (admins), and
"Try now" (admins: forget an owner's status, so its waiting tasks are picked again at once)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from .. import auth, planlimits
from ..constants import Status
from ..database import SessionLocal
from ..models import PlanLimitStatus, Task, User

router = APIRouter(prefix="/plan", tags=["plan"])


@router.get("/status")
async def my_status(request: Request):
    """The caller's plan: whose credential their tasks run on (``owner``: their user id, or "org" for the
    org fallback key, or null without a credential), whether it is an API key (``plan_applies`` false:
    plan limits do not apply), and the owner's latest stored status (null when nothing was seen)."""
    uid = request.state.principal.user.id
    async with SessionLocal() as s:
        who = await planlimits.credential_owner(s, uid)
        row = await planlimits.get_row(s, who["owner"]) if who else None
        if row is not None and who and row.credential_ref and row.credential_ref != who["ref"]:
            row = None  # recorded for a credential this person no longer uses
        return {
            "owner": who["owner"] if who else None,
            "api_key": bool(who and who["api_key"]),
            "plan_applies": bool(who and not who["api_key"]),
            "status": planlimits.row_out(row),
        }


@router.get("/status/all", dependencies=[Depends(auth.require_admin)])
async def all_status():
    """Every owner's latest status, newest first, with the person's name (``user_id`` null for "org")."""
    async with SessionLocal() as s:
        rows = (await s.execute(select(PlanLimitStatus).order_by(PlanLimitStatus.seen_at.desc()))).scalars().all()
        ids = [r.credential_owner for r in rows if r.credential_owner != planlimits.ORG]
        users = {u.id: u for u in (await s.execute(select(User).where(User.id.in_(ids)))).scalars().all()} if ids else {}
        out = []
        for r in rows:
            u = users.get(r.credential_owner)
            item = planlimits.row_out(r)
            item["user_id"] = None if r.credential_owner == planlimits.ORG else r.credential_owner
            item["name"] = "Org fallback key" if r.credential_owner == planlimits.ORG else (
                (u.name or u.email) if u is not None else None)
            out.append(item)
        return out


@router.delete("/status/{owner}", dependencies=[Depends(auth.require_admin)])
async def try_now(owner: str, request: Request):
    """"Try now": the owner's stored status is removed and its queued tasks waiting for the plan's reset
    lose that wait and are picked again now (if the limit is still reached, the next run says so again)."""
    if not owner or len(owner) > 32:
        raise HTTPException(404, "No plan status for that owner")
    async with SessionLocal() as s:
        row = await planlimits.get_row(s, owner)
        if row is not None:
            await s.delete(row)
        waiting = (await s.execute(select(Task).where(
            Task.status == Status.QUEUED, Task.not_before.isnot(None), Task.wait_reason.isnot(None)))).scalars().all()
        cleared = []
        for t in waiting:
            if not t.created_by:
                continue
            if owner == planlimits.ORG:
                mine = await planlimits.has_fallback_owner(s, t.created_by)
            else:
                mine = t.created_by == owner and not await planlimits.has_fallback_owner(s, t.created_by)
            if mine:
                t.not_before = t.wait_reason = None
                cleared.append(t.id)
        await s.commit()
    woken = await request.app.state.worker.wake(cleared) if cleared else []
    return {"removed": row is not None, "tasks": cleared, "woken": woken}
