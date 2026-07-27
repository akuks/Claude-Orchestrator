from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from ..constants import VALID_MODELS
from ..database import SessionLocal
from ..models import Template
from ..schemas import TaskOut, TemplateCreate, TemplateOut
from ..task_service import build_task

router = APIRouter(prefix="/templates", tags=["templates"])

# Pre-built automation templates (Phase 4). These are starting points the user
# can add with one click and then customize; the useful ones assume the relevant
# MCP servers (GitHub, Slack, …) are configured.
PRESETS = [
    {
        "name": "Morning brief",
        "description": "Calendar, unread Slack, open PRs, pending reviews.",
        "prompt": "Give me a concise morning brief: today's calendar events, unread Slack highlights, open pull requests awaiting my review, and anything that looks urgent. Use bullet points.",
        "tags": ["automation", "brief"],
    },
    {
        "name": "PR review digest",
        "description": "Summarize all open PRs across the repo.",
        "prompt": "List all open pull requests. For each, give a one-line summary, its CI status, whether it has merge conflicts, and how long it's been open. Flag any that are ready to merge.",
        "tags": ["automation", "github"],
    },
    {
        "name": "Weekly standup report",
        "description": "Git log + tickets moved + key decisions from the past week.",
        "prompt": "Produce a weekly standup report from the last 7 days of git history and any tracked tickets: what shipped, what's in progress, key decisions, and blockers.",
        "tags": ["automation", "report"],
    },
    {
        "name": "Code quality report",
        "description": "Test coverage, lint issues, TODO/FIXME count.",
        "prompt": "Assess code quality: run the test suite and report coverage if available, summarize lint/type errors, and count TODO/FIXME markers by area. Prioritize the top issues to fix.",
        "tags": ["automation", "quality"],
    },
    {
        "name": "Dependency update check",
        "description": "Outdated packages and security advisories.",
        "prompt": "Check for outdated dependencies and known security advisories in this project. List what's outdated, the severity, and suggest a safe upgrade order.",
        "tags": ["automation", "deps"],
    },
    {
        "name": "Changelog generator",
        "description": "Weekly changelog from git commits.",
        "prompt": "Generate a user-facing changelog for the last week from git commit history, grouped into Features / Fixes / Chores. Keep entries concise.",
        "tags": ["automation", "changelog"],
    },
]


@router.get("/presets")
async def list_presets():
    return PRESETS


@router.post("", response_model=TemplateOut, status_code=201)
async def create_template(payload: TemplateCreate):
    if payload.model and payload.model not in VALID_MODELS:
        raise HTTPException(400, f"Invalid model. Use one of {sorted(VALID_MODELS)}")
    async with SessionLocal() as s:
        tpl = Template(
            name=payload.name,
            description=payload.description,
            prompt=payload.prompt,
            project_id=payload.project_id,
            model=payload.model or "",
            max_turns=payload.max_turns,
            priority=payload.priority,
            tags=payload.tags,
        )
        s.add(tpl)
        await s.commit()
        await s.refresh(tpl)
        return TemplateOut.model_validate(tpl)


@router.get("", response_model=list[TemplateOut])
async def list_templates():
    async with SessionLocal() as s:
        rows = (
            await s.execute(select(Template).order_by(Template.created_at.desc()))
        ).scalars().all()
    return [TemplateOut.model_validate(t) for t in rows]


@router.delete("/{template_id}", status_code=204)
async def delete_template(template_id: str):
    async with SessionLocal() as s:
        tpl = await s.get(Template, template_id)
        if tpl is None:
            raise HTTPException(404, "Template not found")
        await s.delete(tpl)
        await s.commit()


@router.post("/{template_id}/run", response_model=TaskOut, status_code=201)
async def run_template(template_id: str, request: Request):
    async with SessionLocal() as s:
        tpl = await s.get(Template, template_id)
        if tpl is None:
            raise HTTPException(404, "Template not found")
        task = await build_task(
            s,
            prompt=tpl.prompt,
            title=tpl.name,
            project_id=tpl.project_id,
            model=tpl.model or None,
            max_turns=tpl.max_turns,
            priority=tpl.priority,
            tags=tpl.tags,
        )
        await s.commit()
        await s.refresh(task)
        out = TaskOut.model_validate(task)
    await request.app.state.worker.submit(out.id, out.priority, out.created_at)
    return out
