"""Project decision memory (phase 4a): recorded decisions D-001… that name the files they touch.
Anyone who can see the project reads; project maintainers (and admins) write. No agent-token access."""

from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select, update

from ..access import require_project_role
from ..database import SessionLocal
from ..decisions.health import health as decisions_health
from ..decisions import proposals as proposals_mod
from ..decisions.texts import ALREADY_REVIEWED, QUEUE_FULL
from ..decisions.validate import KINDS, clean_fields, display_id, next_number
from ..models import DecisionProposal, Project, ProjectDecision, Task, User
from ..schemas import (
    DecisionIn, DecisionOut, DecisionPatch, DecisionSupersede, ProposalAccept, ProposalIn, ProposalList,
    ProposalOut,
)

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
        stale_flagged_at=d.stale_flagged_at, stale_reason=d.stale_reason,
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
    """A lifecycle write that re-checks, in the same UPDATE, that the decision is still active; a concurrent
    retire/supersede in between leaves no row to update -> 409 INACTIVE."""
    res = await s.execute(update(ProjectDecision).where(
        ProjectDecision.id == d.id, ProjectDecision.status == "active").values(
        version=ProjectDecision.version + 1, updated_at=_now(), stale_flagged_at=None, stale_reason=None,
        **values))
    if res.rowcount != 1:
        await s.rollback()
        raise HTTPException(409, INACTIVE)
    await s.commit()
    await s.refresh(d)


async def _insert_decision(s, project_id: str, data: dict, user_id: str) -> ProjectDecision:
    """Add one decision from cleaned fields in the caller's transaction (no commit)."""
    now = _now()
    n = await next_number(s, project_id)
    d = ProjectDecision(
        project_id=project_id, number=n, kind=data["kind"], title=data["title"], why=data["why"],
        see=data["see"] or None, touches=data["touches"], source=data["source"],
        decided_by=user_id, decided_at=now, last_verified_at=now, created_at=now, updated_at=now)
    s.add(d)
    await s.flush()
    return d


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
        d = await _insert_decision(s, project_id, data, principal.user.id)
        await s.commit()
        await s.refresh(d)
        return _out(d, await _names(s, [d]))


@router.get("/health")
async def decisions_health_route(project_id: str, request: Request):
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id)
        project = await s.get(Project, project_id)
        rows = (await s.execute(select(ProjectDecision).where(
            ProjectDecision.project_id == project_id, ProjectDecision.status == "active")
            .order_by(ProjectDecision.number))).scalars().all()
        s.expunge_all()  # plain objects from here on: git runs after the session (and its transaction) is closed
    return await decisions_health(project, list(rows))


async def _proposal_out(s, rows) -> list[ProposalOut]:
    users = {x for r in rows for x in (r.proposed_by, r.reviewed_by) if x}
    names = {}
    if users:
        names = {u.id: (u.name or u.email) for u in
                 (await s.execute(select(User).where(User.id.in_(users)))).scalars().all()}
    tids = {r.task_id for r in rows if r.task_id}
    tasks = {}
    if tids:
        tasks = {t.id: t for t in (await s.execute(select(Task).where(Task.id.in_(tids)))).scalars().all()}
    roots = {t.root_id for t in tasks.values() if t.root_id and t.root_id not in tasks}
    if roots:
        tasks.update({t.id: t for t in (await s.execute(select(Task).where(Task.id.in_(roots)))).scalars().all()})
    out = []
    for r in rows:
        t = tasks.get(r.task_id) if r.task_id else None
        root = tasks.get(t.root_id) if t is not None and t.root_id else None
        out.append(ProposalOut(
            id=r.id, source=r.source, kind=r.kind, title=r.title, why=r.why, touches=list(r.touches or []),
            evidence=r.evidence, status=r.status, task_id=r.task_id, task_title=t.title if t else None,
            thread_title=root.title if root else None, proposed_by=r.proposed_by,
            proposed_by_name=names.get(r.proposed_by), reviewed_by=r.reviewed_by, reviewed_at=r.reviewed_at,
            decision_number=r.decision_number, created_at=r.created_at))
    return out


async def _get_proposal(s, project_id: str, proposal_id: str) -> DecisionProposal:
    row = (await s.execute(select(DecisionProposal).where(
        DecisionProposal.id == proposal_id, DecisionProposal.project_id == project_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Suggestion not found")
    return row


@router.get("/proposals", response_model=ProposalList)
async def list_proposals(project_id: str, request: Request, status: Literal["pending", "all"] = "pending"):
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id)
        q = select(DecisionProposal).where(DecisionProposal.project_id == project_id)
        if status == "pending":
            q = q.where(DecisionProposal.status == "pending")
        rows = (await s.execute(q.order_by(DecisionProposal.created_at.desc(), DecisionProposal.id.desc())
                                .limit(proposals_mod.LIST_MAX))).scalars().all()
        pending = await proposals_mod.pending_count(s, project_id)
        return ProposalList(items=await _proposal_out(s, rows), pending=pending)


@router.post("/proposals", response_model=ProposalOut, status_code=201)
async def create_proposal(project_id: str, payload: ProposalIn, request: Request):
    principal = request.state.principal
    async with SessionLocal() as s:
        await require_project_role(s, principal, project_id)
        try:
            fields = proposals_mod.clean_proposal(payload.model_dump(), strict_touches=True)
        except ValueError as e:
            raise HTTPException(422, str(e))
        try:
            row = await proposals_mod.add(s, project_id=project_id, fields=fields, source="person",
                                          proposed_by=principal.user.id, on_full="raise")
        except proposals_mod.QueueFull:
            raise HTTPException(429, QUEUE_FULL)
        await s.commit()
        await s.refresh(row)
        return (await _proposal_out(s, [row]))[0]


@router.post("/proposals/{proposal_id}/accept", response_model=DecisionOut, status_code=201)
async def accept_proposal(project_id: str, proposal_id: str, payload: ProposalAccept, request: Request):
    principal = request.state.principal
    async with SessionLocal() as s:
        await require_project_role(s, principal, project_id, "maintainer")
        prop = await _get_proposal(s, project_id, proposal_id)
        body = payload.model_dump()
        data = _clean({**body, "source": ("suggestion: " + (prop.evidence or ""))[:300]}, partial=False)
        _check_kind_see(data["kind"], data["see"] or None)
        # the claim: exactly one reviewer flips pending -> accepted; the loser changes nothing (no number used)
        now = _now()
        res = await s.execute(update(DecisionProposal).where(
            DecisionProposal.id == proposal_id, DecisionProposal.project_id == project_id,
            DecisionProposal.status == "pending").values(
            status="accepted", reviewed_by=principal.user.id, reviewed_at=now))
        if res.rowcount != 1:
            await s.rollback()
            raise HTTPException(409, ALREADY_REVIEWED)
        d = await _insert_decision(s, project_id, data, principal.user.id)
        await s.execute(update(DecisionProposal).where(DecisionProposal.id == proposal_id).values(
            decision_number=d.number))
        await s.commit()
        await s.refresh(d)
        return _out(d, await _names(s, [d]))


@router.post("/proposals/{proposal_id}/reject", response_model=ProposalOut)
async def reject_proposal(project_id: str, proposal_id: str, request: Request):
    principal = request.state.principal
    async with SessionLocal() as s:
        await require_project_role(s, principal, project_id, "maintainer")
        await _get_proposal(s, project_id, proposal_id)
        res = await s.execute(update(DecisionProposal).where(
            DecisionProposal.id == proposal_id, DecisionProposal.project_id == project_id,
            DecisionProposal.status == "pending").values(
            status="rejected", reviewed_by=principal.user.id, reviewed_at=_now()))
        if res.rowcount != 1:
            await s.rollback()
            raise HTTPException(409, ALREADY_REVIEWED)
        await s.commit()
        row = await _get_proposal(s, project_id, proposal_id)
        await s.refresh(row)
        return (await _proposal_out(s, [row]))[0]


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
            ProjectDecision.id == d.id, ProjectDecision.version == version,
            ProjectDecision.status == "active").values(
            version=ProjectDecision.version + 1, updated_at=_now(), stale_flagged_at=None, stale_reason=None,
            **data))
        if res.rowcount != 1:
            did = d.id
            await s.rollback()
            fresh = (await s.execute(select(ProjectDecision.status).where(
                ProjectDecision.id == did))).scalar_one_or_none()
            raise HTTPException(409, INACTIVE if fresh != "active" else STALE)
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
        # lock both rows (in number order, so two opposite supersedes cannot deadlock) and re-check them: of
        # "A by B" and "B by A" running together, the second sees the first's result and is refused
        locked = (await s.execute(select(ProjectDecision).where(ProjectDecision.id.in_([d.id, by.id]))
                                  .order_by(ProjectDecision.number).with_for_update()
                                  .execution_options(populate_existing=True))).scalars().all()
        status = {x.id: x.status for x in locked}
        if status.get(d.id) != "active":
            raise HTTPException(409, INACTIVE)
        if status.get(by.id) != "active":
            raise HTTPException(422, f"{display_id(payload.by)} is not active")
        await _bump(s, d, status="superseded", superseded_by=by.number)
        return _out(d, await _names(s, [d]))
