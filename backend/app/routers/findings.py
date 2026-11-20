from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select

from ..access import ALL, require_project_role, visible_project_ids
from ..database import SessionLocal
from ..models import Finding, Project
from ..schemas import FindingOut, FindingUpdate

router = APIRouter(prefix="/findings", tags=["findings"])

_STATUSES = {"open", "fixed", "accepted", "false_positive"}
_SEVERITIES = {"critical", "high", "medium", "low", "info"}
_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _scoped(stmt, visible):
    return stmt if visible is ALL else stmt.where(Finding.project_id.in_(visible))


@router.get("", response_model=list[FindingOut])
async def list_findings(
    request: Request,
    project_id: str | None = None,
    status: str | None = None,
    severity: str | None = None,
):
    stmt = select(Finding)
    if project_id:
        stmt = stmt.where(Finding.project_id == project_id)
    if status:
        stmt = stmt.where(Finding.status == status)
    if severity:
        stmt = stmt.where(Finding.severity == severity)
    async with SessionLocal() as s:
        visible = await visible_project_ids(s, request.state.principal)
        rows = (await s.execute(_scoped(stmt, visible))).scalars().all()
    # Sort by severity then most-recently-seen.
    rows.sort(key=lambda f: (_SEV_ORDER.get(f.severity, 9), -f.last_seen.timestamp()))
    return [FindingOut.model_validate(f) for f in rows]


@router.get("/summary")
async def summary(request: Request, project_id: str | None = None):
    async with SessionLocal() as s:
        visible = await visible_project_ids(s, request.state.principal)
        stmt = _scoped(select(Finding.status, Finding.severity, func.count()), visible)
        if project_id:
            stmt = stmt.where(Finding.project_id == project_id)
        rows = (await s.execute(stmt.group_by(Finding.status, Finding.severity))).all()
        pq = select(Project) if visible is ALL else select(Project).where(Project.id.in_(visible))
        projects = {p.id: p.name for p in (await s.execute(pq)).scalars().all()}
    by_status: dict[str, int] = {}
    open_by_severity: dict[str, int] = {}
    total = 0
    for st, sev, n in rows:
        total += n
        by_status[st] = by_status.get(st, 0) + n
        if st == "open":
            open_by_severity[sev] = open_by_severity.get(sev, 0) + n
    return {
        "total": total,
        "by_status": by_status,
        "open_by_severity": open_by_severity,
        "open_critical": open_by_severity.get("critical", 0),
        "open_high": open_by_severity.get("high", 0),
        "projects": projects,
    }


@router.patch("/{finding_id}", response_model=FindingOut)
async def update_finding(finding_id: int, payload: FindingUpdate, request: Request):
    data = payload.model_dump(exclude_unset=True)
    if "status" in data and data["status"] not in _STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(_STATUSES)}")
    if "severity" in data and data["severity"] not in _SEVERITIES:
        raise HTTPException(400, f"severity must be one of {sorted(_SEVERITIES)}")
    async with SessionLocal() as s:
        f = await s.get(Finding, finding_id)
        if f is None:
            raise HTTPException(404, "Finding not found")
        await require_project_role(s, request.state.principal, f.project_id)
        for k, v in data.items():
            setattr(f, k, v)
        await s.commit()
        await s.refresh(f)
        return FindingOut.model_validate(f)
