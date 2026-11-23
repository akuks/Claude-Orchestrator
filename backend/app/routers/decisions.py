"""Project decision memory (phase 4a): recorded decisions D-001… that name the files they touch.
Anyone who can see the project reads; project maintainers (and admins) write. No agent-token access."""

from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select, update

from ..access import require_project_role
from ..database import SessionLocal
from ..decisions.validate import KINDS, clean_fields, display_id, next_number
from ..models import ProjectDecision, User
from ..schemas import DecisionIn, DecisionOut, DecisionPatch, DecisionSupersede

router = APIRouter(prefix="/projects/{project_id}/decisions", tags=["decisions"])

STALE = "changed by someone else — reload"
INACTIVE = "this decision is no longer active"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _out(d: ProjectDecision, names: dict) -> DecisionOut:
    return DecisionOut(
        id=d.id, number=d.number, display_id=display_id(d.number), kind=d.kind, title=d.title, why=d.why,
        see=d.see or None, touches=list(d.touches or []), decided_by=d.decided_by,
        decided_by_name=names.get(d.decided_by), decided_at=d.decided_at, last_verified_at=d.last_verified_at,
        source=d.source, status=d.status, superseded_by=d.superseded_by, version=d.version,
        created_at=d.created_at, updated_at=d.updated_at,
    )


async def _names(s, rows) -> dict:
    ids = {d.decided_by for d in rows if d.decided_by}
    if not ids:
        return {}
    users = (await s.execute(select(User).where(User.id.in_(ids)))).scalars().all()
    return {u.id: (u.name or u.email) for u in users}


async def _get(s, project_id: str, number: int) -> ProjectDecision:
    d = (await s.execute(select(ProjectDecision).where(
        ProjectDecision.project_id == project_id, ProjectDecision.number == number))).scalar_one_or_none()
    if d is None:
        raise HTTPException(404, "Decision not found")
    return d


def _require_active(d: ProjectDecision) -> None:
    if d.status != "active":
        raise HTTPException(409, INACTIVE)


def _check_kind_see(kind: str, see: Optional[str]) -> None:
    if kind == "reference" and not see:
        raise HTTPException(422, "a reference needs see (a path in the project)")
    if kind != "reference" and see:
        raise HTTPException(422, "see is only for a reference")


def _clean(data: dict, *, partial: bool) -> dict:
    try:
        return clean_fields(data, partial=partial)
    except ValueError as e:
        raise HTTPException(422, str(e))


async def _bump(s, d: ProjectDecision, **values) -> None:
    await s.execute(update(ProjectDecision).where(ProjectDecision.id == d.id).values(
        version=ProjectDecision.version + 1, updated_at=_now(), **values))
    await s.commit()
    await s.refresh(d)


@router.get("", response_model=list[DecisionOut])
async def list_decisions(project_id: str, request: Request, status: Literal["active", "all"] = "active",
                         kind: Optional[str] = None):
    if kind is not None and kind not in KINDS:
        raise HTTPException(422, f"kind must be one of: {', '.join(KINDS)}")
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id)
        q = select(ProjectDecision).where(ProjectDecision.project_id == project_id)
        if status == "active":
            q = q.where(ProjectDecision.status == "active")
        if kind:
            q = q.where(ProjectDecision.kind == kind)
        rows = (await s.execute(q.order_by(ProjectDecision.number.desc()))).scalars().all()
        names = await _names(s, rows)
        return [_out(d, names) for d in rows]


@router.post("", response_model=DecisionOut, status_code=201)
async def create_decision(project_id: str, payload: DecisionIn, request: Request):
    principal = request.state.principal
    async with SessionLocal() as s:
        await require_project_role(s, principal, project_id, "maintainer")
        data = _clean(payload.model_dump(), partial=False)
        now = _now()
        n = await next_number(s, project_id)
        d = ProjectDecision(
            project_id=project_id, number=n, kind=data["kind"], title=data["title"], why=data["why"],
            see=data["see"] or None, touches=data["touches"], source=data["source"],
            decided_by=principal.user.id, decided_at=now, last_verified_at=now, created_at=now, updated_at=now)
        s.add(d)
        await s.commit()
        await s.refresh(d)
        return _out(d, await _names(s, [d]))


@router.get("/{number}", response_model=DecisionOut)
async def get_decision(project_id: str, number: int, request: Request):
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id)
        d = await _get(s, project_id, number)
        return _out(d, await _names(s, [d]))


@router.patch("/{number}", response_model=DecisionOut)
async def patch_decision(project_id: str, number: int, payload: DecisionPatch, request: Request):
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id, "maintainer")
        d = await _get(s, project_id, number)
        _require_active(d)
        body = payload.model_dump(exclude_unset=True)
        version = body.pop("version")
        body = {k: v for k, v in body.items() if v is not None or k == "see"}
        data = _clean(body, partial=True)
        if "see" in data:
            data["see"] = data["see"] or None
        _check_kind_see(data.get("kind", d.kind), data["see"] if "see" in data else d.see)
        if version != d.version:
            raise HTTPException(409, STALE)
        res = await s.execute(update(ProjectDecision).where(
            ProjectDecision.id == d.id, ProjectDecision.version == version).values(
            version=ProjectDecision.version + 1, updated_at=_now(), **data))
        if res.rowcount != 1:
            await s.rollback()
            raise HTTPException(409, STALE)
        await s.commit()
        await s.refresh(d)
        return _out(d, await _names(s, [d]))


@router.post("/{number}/verify", response_model=DecisionOut)
async def verify_decision(project_id: str, number: int, request: Request):
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id, "maintainer")
        d = await _get(s, project_id, number)
        _require_active(d)
        await _bump(s, d, last_verified_at=_now())
        return _out(d, await _names(s, [d]))


@router.post("/{number}/retire", response_model=DecisionOut)
async def retire_decision(project_id: str, number: int, request: Request):
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id, "maintainer")
        d = await _get(s, project_id, number)
        _require_active(d)
        await _bump(s, d, status="retired", superseded_by=None)
        return _out(d, await _names(s, [d]))


@router.post("/{number}/supersede", response_model=DecisionOut)
async def supersede_decision(project_id: str, number: int, payload: DecisionSupersede, request: Request):
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id, "maintainer")
        d = await _get(s, project_id, number)
        _require_active(d)
        if payload.by == d.number:
            raise HTTPException(422, "a decision cannot supersede itself")
        by = (await s.execute(select(ProjectDecision).where(
            ProjectDecision.project_id == project_id, ProjectDecision.number == payload.by))).scalar_one_or_none()
        if by is None:
            raise HTTPException(422, f"{display_id(payload.by)} does not exist in this project")
        if by.status != "active":
            raise HTTPException(422, f"{display_id(payload.by)} is not active")
        await _bump(s, d, status="superseded", superseded_by=by.number)
        return _out(d, await _names(s, [d]))
