"""Code map routes (phase 6b): status, refresh (maintainers) and the log tail."""

import os
import re

from fastapi import APIRouter, HTTPException, Request

from ..access import require_project_role
from ..codemap import indexer, status
from ..database import SessionLocal
from ..models import Project
from ..schemas import CodeMapLogOut, CodeMapOut

router = APIRouter(prefix="/projects/{project_id}/code-map", tags=["code-map"])


async def _project(s, project_id: str) -> Project:
    project = await s.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Not found")
    return project


@router.get("", response_model=CodeMapOut)
async def get_code_map(project_id: str, request: Request):
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id)
        return await status.for_project(s, await _project(s, project_id))


@router.post("/refresh", response_model=CodeMapOut)
async def refresh_code_map(project_id: str, request: Request):
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id, "maintainer")
        project = await _project(s, project_id)
        if not project.code_map:
            raise HTTPException(409, "Turn the code map on for this project first")
        if not project.directory:
            raise HTTPException(422, "The code map needs a project folder")
        if not await status.server_registered(s, project):
            raise HTTPException(409, "Add the Code map tool first")
        indexer.start(project_id)  # a manual refresh ignores back-off
        return await status.for_project(s, project)


def _hide_folder(lines: list[str], directory: str | None) -> list[str]:
    """Replace the project's real folder (and the way it was typed) with `<project folder>`."""
    spellings = set()
    if directory:
        spellings.add(directory.rstrip("/") or directory)
        try:
            spellings.add(os.path.realpath(directory))
        except OSError:
            pass
    spellings = sorted((x for x in spellings if len(x) > 1), key=len, reverse=True)
    # only a whole path: not part of a longer one (/app2/x, /apple) and not inside another path (/x/app)
    pats = [re.compile(r"(?<![\w./-])" + re.escape(sp) + r"(?=/|\s|['\"`:]|$)") for sp in spellings]
    out = []
    for ln in lines:
        for pat in pats:
            ln = pat.sub("<project folder>", ln)
        out.append(ln)
    return out


@router.get("/log", response_model=CodeMapLogOut)
async def get_log(project_id: str, request: Request):
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id)
        project = await _project(s, project_id)
    return {"lines": _hide_folder(await status.log_lines(project), project.directory)}
