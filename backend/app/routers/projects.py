import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from .. import memory
from ..config import settings
from ..constants import VALID_MODELS, Status
from ..database import SessionLocal
from ..models import Project, Task, TaskSummary
from ..schemas import (
    MemoryOut,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    TaskSummaryOut,
)

router = APIRouter(prefix="/projects", tags=["projects"])


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "project"


async def _counts(s, project_ids: list[str]) -> dict[str, tuple[int, float]]:
    if not project_ids:
        return {}
    rows = (
        await s.execute(
            select(
                Task.project_id,
                func.count(),
                func.coalesce(func.sum(Task.total_cost_usd), 0.0),
            )
            .where(Task.project_id.in_(project_ids))
            .group_by(Task.project_id)
        )
    ).all()
    return {pid: (n, float(cost or 0)) for pid, n, cost in rows}


def _to_out(project: Project, count: int = 0, cost: float = 0.0) -> ProjectOut:
    out = ProjectOut.model_validate(project)
    out.task_count = count
    out.total_cost_usd = round(cost, 4)
    return out


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(payload: ProjectCreate):
    if payload.default_model not in VALID_MODELS:
        raise HTTPException(400, f"Invalid model. Use one of {sorted(VALID_MODELS)}")
    slug = _slugify(payload.name)
    directory = (
        Path(payload.directory).expanduser()
        if payload.directory
        else settings.projects_dir / slug
    )
    async with SessionLocal() as s:
        dup = (
            await s.execute(select(Project).where(Project.slug == slug))
        ).scalar_one_or_none()
        if dup:
            raise HTTPException(409, f"A project with slug '{slug}' already exists")
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(400, f"Could not create/access directory: {exc}")
        project = Project(
            name=payload.name,
            slug=slug,
            directory=str(directory.resolve()),
            instructions=payload.instructions,
            default_model=payload.default_model,
            memory_enabled=payload.memory_enabled,
        )
        s.add(project)
        await s.commit()
        await s.refresh(project)
        return _to_out(project)


@router.get("", response_model=list[ProjectOut])
async def list_projects(include_archived: bool = False):
    stmt = select(Project).order_by(Project.created_at.desc())
    if not include_archived:
        stmt = stmt.where(Project.archived.is_(False))
    async with SessionLocal() as s:
        rows = (await s.execute(stmt)).scalars().all()
        counts = await _counts(s, [p.id for p in rows])
    return [_to_out(p, *counts.get(p.id, (0, 0.0))) for p in rows]


async def _get_or_404(s, project_id: str) -> Project:
    project = await s.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    return project


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str):
    async with SessionLocal() as s:
        project = await _get_or_404(s, project_id)
        counts = await _counts(s, [project_id])
    return _to_out(project, *counts.get(project_id, (0, 0.0)))


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(project_id: str, payload: ProjectUpdate):
    data = payload.model_dump(exclude_unset=True)
    if "default_model" in data and data["default_model"] not in VALID_MODELS:
        raise HTTPException(400, "Invalid model")
    async with SessionLocal() as s:
        project = await _get_or_404(s, project_id)
        if "directory" in data and data["directory"]:
            d = Path(data.pop("directory")).expanduser()
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise HTTPException(400, f"Could not access directory: {exc}")
            project.directory = str(d.resolve())
        for k, v in data.items():
            setattr(project, k, v)
        await s.commit()
        await s.refresh(project)
        counts = await _counts(s, [project_id])
    return _to_out(project, *counts.get(project_id, (0, 0.0)))


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str):
    async with SessionLocal() as s:
        project = await _get_or_404(s, project_id)
        await s.delete(project)
        await s.commit()


@router.get("/{project_id}/memory", response_model=MemoryOut)
async def get_memory(project_id: str):
    async with SessionLocal() as s:
        project = await _get_or_404(s, project_id)
        return MemoryOut(
            memory=project.memory,
            memory_prev=project.memory_prev,
            memory_updated_at=project.memory_updated_at,
        )


@router.post("/{project_id}/memory/regenerate", response_model=MemoryOut)
async def regenerate_memory(project_id: str):
    async with SessionLocal() as s:
        await _get_or_404(s, project_id)
    await memory.rebuild_memory(project_id)
    async with SessionLocal() as s:
        project = await _get_or_404(s, project_id)
        return MemoryOut(
            memory=project.memory,
            memory_prev=project.memory_prev,
            memory_updated_at=project.memory_updated_at,
        )


@router.get("/{project_id}/summaries", response_model=list[TaskSummaryOut])
async def list_summaries(project_id: str, limit: int = 50):
    async with SessionLocal() as s:
        await _get_or_404(s, project_id)
        rows = (
            await s.execute(
                select(TaskSummary)
                .where(TaskSummary.project_id == project_id)
                .order_by(TaskSummary.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
    return [TaskSummaryOut.model_validate(r) for r in rows]


@router.get("/{project_id}/stats")
async def project_stats(project_id: str):
    async with SessionLocal() as s:
        await _get_or_404(s, project_id)
        by_status_rows = (
            await s.execute(
                select(Task.status, func.count())
                .where(Task.project_id == project_id)
                .group_by(Task.status)
            )
        ).all()
        total_cost = (
            await s.execute(
                select(func.coalesce(func.sum(Task.total_cost_usd), 0.0)).where(
                    Task.project_id == project_id
                )
            )
        ).scalar_one()
    by_status = {k: v for k, v in by_status_rows}
    return {
        "project_id": project_id,
        "task_count": sum(by_status.values()),
        "by_status": by_status,
        "total_cost_usd": round(float(total_cost or 0), 4),
        "completed": by_status.get(Status.COMPLETED, 0),
    }
