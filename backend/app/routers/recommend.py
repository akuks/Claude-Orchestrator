"""Recommended tools for projects with a user interface (phase 6c)."""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from ..access import require_project_role
from ..database import SessionLocal
from ..models import Project
from ..recommend import detect as detect_mod
from ..recommend import panel

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
