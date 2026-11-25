"""Recommended tools for projects with a user interface (phase 6c)."""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from .. import ui_checks
from ..access import require_project_role
from ..database import SessionLocal
from ..constants import Status
from ..models import Project, Task
from ..recommend import detect as detect_mod
from ..recommend import panel
from ..schemas import TaskCreate, TaskOut
from . import tasks as tasks_router

router = APIRouter(prefix="/projects/{project_id}/recommended", tags=["recommended"])


@router.get("")
async def get_recommended(project_id: str, request: Request):
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id)
        project = await s.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Not found")
        try:
            found = await asyncio.wait_for(asyncio.to_thread(detect_mod.detect, project.directory),
                                           detect_mod.TIMEOUT)
        except Exception:  # too slow (or any failure): answer as if no user interface
            found = detect_mod.Detected()
        return await panel.build(s, project, found)


checks_router = APIRouter(prefix="/projects/{project_id}/ui-checks", tags=["recommended"])


@checks_router.post("/setup", response_model=TaskOut, status_code=201)
async def setup_ui_checks(project_id: str, request: Request):
    """Start an ordinary agent task that adds the UI checks to the project (phase 7f). It is created exactly like
    any task by this person (their credential and budget defaults, the guard, approvals)."""
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id, "maintainer")
        if await s.get(Project, project_id) is None:
            raise HTTPException(404, "Not found")
        busy = (await s.execute(select(Task.id).where(
            Task.project_id == project_id, Task.title == ui_checks.SETUP_TITLE,
            Task.status.in_(Status.ACTIVE)).limit(1))).first()
        if busy is not None:
            raise HTTPException(409, "UI checks are already being set up for this project.")
    payload = TaskCreate(prompt=ui_checks.setup_prompt(), title=ui_checks.SETUP_TITLE, project_id=project_id,
                         tags=["ui-checks"])
    return await tasks_router.create_task(payload, request)
