import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import delete, func, select, update

from .. import licence as licence_mod
from .. import memory
from ..agent_env import MissingCredential
from ..config import settings
from ..constants import VALID_MODELS, Status
from ..database import SessionLocal
from ..access import (
    ALL,
    project_role,
    require_project_role,
    user_is_maintainer,
    visible_project_ids,
)
from ..guard import custom as guard_custom
from ..guard import policy_store
from ..guard import trust as guard_trust
from ..resume import gitfacts
from ..models import ActionRule, McpServer, Project, ProjectDecision, ProjectMember, Task, TaskSummary, Team, User, UserCredential
from ..schemas import (
    MemberIn,
    MemberOut,
    MemoryOut,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    ServerEntry,
    TaskSummaryOut,
)

import yaml

router = APIRouter(prefix="/projects", tags=["projects"])

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

MAINTAINER_FIELDS = {
    "code_map",
    "name",
    "instructions",
    "default_model",
    "memory_enabled",
    "memory",
    "budget_usd",
    "github_repo",
    "auto_review_prs",
    "automation_owner_id",
    "guard_enabled",
    "approval_wait_seconds",
    "trust",
    "quarantine",
    "on_no_answer",
    "readonly_paths",
    "reference_project_ids",
    "fresh_session_tokens",
}
ADMIN_FIELDS = {"directory", "env", "archived"}
MASK = "••••"
# co-guard policy fields (Phase 2)
TRUST_VALUES = ("own", "external")
ON_NO_ANSWER_VALUES = ("deny", "deny_and_resume")
READONLY_MAX = 50
REFERENCES_MAX = 20
IMPORT_TRUST_BUDGET = 30  # seconds of trust checks per import; later folders get R_TIME_LIMIT
_NULL_MEANS_LEAVE = ("name", "guard_enabled", "approval_wait_seconds", "trust", "quarantine", "on_no_answer",
                     "readonly_paths", "reference_project_ids", "code_map")


def _require_admin(request: Request) -> None:
    if not request.state.principal.is_admin:
        raise HTTPException(403, "Admin only")


async def _require_unique_name(s, name: str, exclude_id: str | None = None) -> None:
    """Project names are unique (incl. archived): project-scoped MCP servers
    and budgets are matched by name, so a shared name would share them."""
    stmt = select(Project.id).where(Project.name == name)
    if exclude_id is not None:
        stmt = stmt.where(Project.id != exclude_id)
    if (await s.execute(stmt.limit(1))).first() is not None:
        raise HTTPException(409, f"A project named '{name}' already exists")


async def _member_emails(s) -> list[str]:
    """Every active org member's git email (the trust heuristic's "us")."""
    rows = await s.execute(
        select(UserCredential.git_email)
        .join(User, User.id == UserCredential.user_id)
        .where(User.active.is_(True), UserCredential.git_email.is_not(None))
    )
    return [e for e in rows.scalars() if e]


async def _suggest_trust(directory: str, emails: list[str]) -> tuple[str, str]:
    return await asyncio.to_thread(guard_trust.suggest_trust, directory, emails)


def _covers(folder: str, path: str) -> bool:
    """`folder` is `path` or one of its ancestors (case-folded, R9)."""
    f, x = folder.casefold(), path.casefold()
    return f == "/" or x == f or x.startswith(f.rstrip("/") + "/")


ORCHESTRATOR_FOLDER = "That folder is part of the orchestrator itself"
# workspaces/ internals that are never a project's (others starting with "_" count too)
_WORKSPACE_INTERNALS = ("_tasks", "_users", "_mcp_configs", "_gates", "_codemap", "_system", "_context")


def _orchestrator_folders() -> set[str]:
    """The server's own code, migrations and venv, and every workspaces/_* folder
    (normalised and real forms). The guard stops protecting a core folder that
    holds the task's project, so a project there could change the server."""
    from ..guard.runtime import BACKEND_DIR

    paths = [BACKEND_DIR / "app", BACKEND_DIR / "migrations", BACKEND_DIR / ".venv", Path(sys.prefix)]
    ws = Path(settings.workspaces_dir)
    names = set(_WORKSPACE_INTERNALS)
    try:
        names |= {c.name for c in ws.iterdir() if c.name.startswith("_")}
    except OSError:
        pass
    paths += [ws / n for n in names]
    out: set[str] = set()
    for p in paths:
        out |= {os.path.normpath(os.path.abspath(str(p))), os.path.realpath(str(p))}
    return out


def _refuse_orchestrator_folder(directory) -> None:
    """422 when `directory` is, holds or sits inside one of the orchestrator's own folders."""
    d = str(directory)
    forms = {os.path.normpath(os.path.abspath(d)), os.path.realpath(d)}
    for o in _orchestrator_folders():
        if any(_covers(f, o) or _covers(o, f) for f in forms):
            raise HTTPException(422, ORCHESTRATOR_FOLDER)


_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f]")
_PROJECT_ID = re.compile(r"[0-9a-f]{32}")


def _root_problem(full: str, directory: str) -> str | None:
    """Why `full` (absolute) cannot be a read-only root of the project in
    `directory`, checked in normalised and real form: /, home or above it, or
    the project folder or above it (every change would be blocked)."""
    home = os.path.normpath(str(Path.home()))
    homes = {home, os.path.realpath(home)}
    projects = {os.path.normpath(directory), os.path.realpath(directory)}
    for form in {os.path.normpath(full), os.path.realpath(full)}:
        if form in ("/", "//") or any(_covers(form, h) for h in homes):
            return "is / or your home folder or above it"
        if any(_covers(form, d) for d in projects):
            return "is the project folder or above it; every change would be blocked"
    return None


def _readonly_paths(entries: list, directory: str) -> list[dict]:
    """Validated readonly_paths, stored as given ({path, reason}). A relative
    path resolves against the project folder (as the hook does). Entries are
    ReadonlyPathIn objects or stored dicts (re-checked when the folder moves)."""
    if len(entries) > READONLY_MAX:
        raise HTTPException(400, f"at most {READONLY_MAX} read-only paths")
    out, seen = [], set()
    for e in entries:
        path = e.get("path") if isinstance(e, dict) else e.path
        reason = e.get("reason") if isinstance(e, dict) else e.reason
        raw = (path if isinstance(path, str) else "").strip()
        if not raw:
            raise HTTPException(400, "a read-only path is empty")
        if _CONTROL.search(raw):
            raise HTTPException(400, "a read-only path contains a control character")
        if reason is not None and _CONTROL.search(str(reason)):
            raise HTTPException(400, f"the reason for read-only path {raw!r} contains a control character")
        if raw.startswith("~"):
            raise HTTPException(400, f"write the read-only path {raw!r} in full (no ~)")
        full = raw if os.path.isabs(raw) else os.path.join(directory, raw)
        why = _root_problem(full, directory)
        if why:
            raise HTTPException(400, f"read-only path {raw!r} {why}")
        if raw in seen:
            continue
        seen.add(raw)
        out.append({"path": raw, "reason": guard_custom.clean_reason(reason)})
    return out


async def _reference_ids(s, principal, project_id: str, ids: list[str], directory: str,
                         *, check_access: bool = True) -> list[str]:
    """Other projects the editor can see (an unknown and an invisible id get
    the same answer), whose folders pass the read-only root checks against
    this project's `directory`. check_access=False (the folder moved; the ids
    are stored ones): only the folder checks, and ids gone since are skipped."""
    out = list(dict.fromkeys(ids))
    if len(out) > REFERENCES_MAX:
        raise HTTPException(400, f"at most {REFERENCES_MAX} reference projects")
    for rid in out:
        if not isinstance(rid, str) or not _PROJECT_ID.fullmatch(rid):
            raise HTTPException(400, f"{str(rid)[:80]!r} is not a project id")
        if rid == project_id:
            raise HTTPException(400, "a project cannot reference itself")
        ref = await s.get(Project, rid)
        if check_access and (ref is None or await project_role(s, principal, rid) is None):
            raise HTTPException(400, f"unknown project: {rid}")
        if ref is None:
            continue
        why = _root_problem(ref.directory, directory)
        if why:
            raise HTTPException(400, f"reference project {ref.name!r}: its folder {why}")
    return out


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


def _to_out(
    project: Project,
    count: int = 0,
    cost: float = 0.0,
    *,
    role: str | None = None,
    admin: bool,
) -> ProjectOut:
    out = ProjectOut.model_validate(project)
    out.my_role = role
    out.licence_badge = licence_mod.badge(project)
    if not admin:
        out.env = {k: MASK for k in out.env}
    out.task_count = count
    out.total_cost_usd = round(cost, 4)
    return out


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(payload: ProjectCreate, request: Request):
    _require_admin(request)
    if payload.default_model not in VALID_MODELS:
        raise HTTPException(400, f"Invalid model. Use one of {sorted(VALID_MODELS)}")
    slug = _slugify(payload.name)
    directory = (
        Path(payload.directory).expanduser()
        if payload.directory
        else settings.projects_dir / slug
    )
    async with SessionLocal() as s:
        await _require_unique_name(s, payload.name)
        dup = (
            await s.execute(select(Project).where(Project.slug == slug))
        ).scalar_one_or_none()
        if dup:
            raise HTTPException(409, f"A project with slug '{slug}' already exists")
        _refuse_orchestrator_folder(directory)  # before anything is created there
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(400, f"Could not create/access directory: {exc}")
        trust, trust_reason = await _suggest_trust(str(directory.resolve()), await _member_emails(s))
        project = Project(
            name=payload.name,
            slug=slug,
            directory=str(directory.resolve()),
            instructions=payload.instructions,
            default_model=payload.default_model,
            memory_enabled=payload.memory_enabled,
            budget_usd=payload.budget_usd,
            github_repo=payload.github_repo,
            auto_review_prs=payload.auto_review_prs,
            env=payload.env,
            created_by=request.state.principal.user.id,
            trust=trust,
            quarantine=trust == "external",
            licence=await licence_mod.for_directory(str(directory.resolve())),
        )
        s.add(project)
        await s.commit()
        await s.refresh(project)
        out = _to_out(project, role="maintainer", admin=True)
        out.trust_reason = trust_reason
        return out


@router.get("", response_model=list[ProjectOut])
async def list_projects(request: Request, include_archived: bool = False):
    principal = request.state.principal
    stmt = select(Project).order_by(Project.created_at.desc())
    if not include_archived:
        stmt = stmt.where(Project.archived.is_(False))
    async with SessionLocal() as s:
        visible = await visible_project_ids(s, principal)
        if visible is not ALL:
            stmt = stmt.where(Project.id.in_(visible))
        rows = (await s.execute(stmt)).scalars().all()
        counts = await _counts(s, [p.id for p in rows])
        roles = {}
        if not principal.is_admin:
            for p in rows:
                roles[p.id] = await project_role(s, principal, p.id)
    admin = principal.is_admin
    return [
        _to_out(
            p,
            *counts.get(p.id, (0, 0.0)),
            role="maintainer" if admin else roles.get(p.id),
            admin=admin,
        )
        for p in rows
    ]


def _read_cwd(jsonl_path: Path) -> str | None:
    """The real working directory is stored on transcript lines as `cwd`."""
    try:
        with jsonl_path.open("r", errors="replace") as f:
            for _ in range(20):
                line = f.readline()
                if not line:
                    break
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cwd = obj.get("cwd")
                if cwd:
                    return cwd
    except OSError:
        return None
    return None


def _noise_roots() -> list[str]:
    """Paths that shouldn't be offered as projects: our own managed dirs and temp."""
    import tempfile

    roots = [
        str(settings.workspaces_dir.resolve()),
        str(settings.projects_dir.resolve()),
        os.path.normpath(tempfile.gettempdir()),
        "/tmp",
        "/private/tmp",
        "/private/var/folders",
        "/var/folders",
    ]
    return [os.path.normpath(r) for r in roots]


def _is_noise(path: str, roots: list[str]) -> bool:
    return any(path == r or path.startswith(r + os.sep) for r in roots)


@router.get("/discover")
async def discover_projects(request: Request):
    """Find directories where Claude Code has been run (from ~/.claude/projects).

    Filters out non-existent paths, temp dirs, and the orchestrator's own
    sandbox/project working directories.
    """
    _require_admin(request)
    roots = _noise_roots()
    found: dict[str, dict] = {}
    if CLAUDE_PROJECTS_DIR.exists():
        for d in CLAUDE_PROJECTS_DIR.iterdir():
            if not d.is_dir():
                continue
            jsonls = sorted(
                d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
            )
            if not jsonls:
                continue
            cwd = next((c for j in jsonls[:3] if (c := _read_cwd(j))), None)
            if not cwd:
                continue
            key = os.path.normpath(cwd)
            if _is_noise(key, roots) or not Path(key).is_dir():
                continue
            last = max(p.stat().st_mtime for p in jsonls)
            info = found.setdefault(key, {"sessions": 0, "last": 0.0})
            info["sessions"] += len(jsonls)
            info["last"] = max(info["last"], last)

    async with SessionLocal() as s:
        existing = {
            os.path.normpath(p.directory)
            for p in (await s.execute(select(Project))).scalars().all()
        }

    out = []
    for key, info in found.items():
        out.append(
            {
                "directory": key,
                "name": Path(key).name or key,
                "sessions": info["sessions"],
                "last_active": datetime.fromtimestamp(
                    info["last"], tz=timezone.utc
                ).isoformat(),
                "exists": Path(key).exists(),
                "already_added": key in existing,
            }
        )
    out.sort(key=lambda x: x["last_active"], reverse=True)
    return out


class ImportRequest(BaseModel):
    directories: list[str]


@router.post("/import", response_model=list[ProjectOut])
async def import_projects(payload: ImportRequest, request: Request):
    """Bulk-create projects from a list of directories (skips already-added)."""
    _require_admin(request)
    created: list[Project] = []
    reasons: dict[str, str] = {}
    async with SessionLocal() as s:
        emails = await _member_emails(s)
        trust_started = time.monotonic()
        rows = (await s.execute(select(Project))).scalars().all()
        existing_dirs = {os.path.normpath(p.directory) for p in rows}
        existing_slugs = {p.slug for p in rows}
        existing_names = {p.name for p in rows}
        for raw in payload.directories:
            d = os.path.normpath(str(Path(raw).expanduser()))
            if d in existing_dirs:
                continue
            _refuse_orchestrator_folder(d)
            name = Path(d).name or d
            if name in existing_names:
                raise HTTPException(409, f"A project named '{name}' already exists")
            slug = base = _slugify(name)
            i = 2
            while slug in existing_slugs:
                slug = f"{base}-{i}"
                i += 1
            if time.monotonic() - trust_started >= IMPORT_TRUST_BUDGET:
                trust, reasons[d] = "external", guard_trust.R_TIME_LIMIT
            else:  # each check is bounded by trust.GIT_TIMEOUT (5 s)
                trust, reasons[d] = await _suggest_trust(d, emails)
            project = Project(
                name=name,
                slug=slug,
                directory=d,
                default_model=settings.default_model,
                memory_enabled=settings.memory_enabled_default,
                created_by=request.state.principal.user.id,
                trust=trust,
                quarantine=trust == "external",
                licence=await licence_mod.for_directory(d),
            )
            s.add(project)
            existing_dirs.add(d)
            existing_slugs.add(slug)
            existing_names.add(name)
            created.append(project)
        await s.commit()
        for p in created:
            await s.refresh(p)
    outs = []
    for p in created:
        out = _to_out(p, role="maintainer", admin=True)
        out.trust_reason = reasons.get(p.directory)
        outs.append(out)
    return outs


async def _get_or_404(s, project_id: str) -> Project:
    project = await s.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    return project


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str, request: Request):
    principal = request.state.principal
    async with SessionLocal() as s:
        role = await require_project_role(s, principal, project_id)
        project = await _get_or_404(s, project_id)
        counts = await _counts(s, [project_id])
    return _to_out(project, *counts.get(project_id, (0, 0.0)), role=role, admin=principal.is_admin)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(project_id: str, payload: ProjectUpdate, request: Request):
    principal = request.state.principal
    data = payload.model_dump(exclude_unset=True)
    for k in _NULL_MEANS_LEAVE:
        if k in data and data[k] is None:
            del data[k]  # required columns; null means "leave it"
    async with SessionLocal() as s:
        # Visibility (404) and role (403) are checked before any validation or write.
        role = await require_project_role(s, principal, project_id, "maintainer")
        # Allowlist: a non-admin may send maintainer fields only.
        if not principal.is_admin and data.keys() - MAINTAINER_FIELDS:
            raise HTTPException(403, "Admin only")
        if "default_model" in data and data["default_model"] not in VALID_MODELS:
            raise HTTPException(400, "Invalid model")
        if "trust" in data and data["trust"] not in TRUST_VALUES:
            raise HTTPException(400, f"trust must be one of: {', '.join(TRUST_VALUES)}")
        if "on_no_answer" in data and data["on_no_answer"] not in ON_NO_ANSWER_VALUES:
            raise HTTPException(400, f"on_no_answer must be one of: {', '.join(ON_NO_ANSWER_VALUES)}")
        project = await _get_or_404(s, project_id)
        if data.get("automation_owner_id") is not None and not await user_is_maintainer(
            s, data["automation_owner_id"], project_id
        ):
            raise HTTPException(400, "Automation owner must be a maintainer of this project")
        if data.get("name") and data["name"] != project.name:
            await _require_unique_name(s, data["name"], exclude_id=project_id)
        if "directory" in data and data["directory"]:
            d = Path(data.pop("directory")).expanduser()
            _refuse_orchestrator_folder(d)  # before anything is created there
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise HTTPException(400, f"Could not access directory: {exc}")
            dir_changed = str(d.resolve()) != project.directory
            project.directory = str(d.resolve())
            if dir_changed:
                project.licence = await licence_mod.for_directory(project.directory)
        else:
            dir_changed = False
        # A moved folder re-checks the stored paths and references (M3).
        if "readonly_paths" in data:
            data["readonly_paths"] = _readonly_paths(payload.readonly_paths, project.directory)
        elif dir_changed:
            _readonly_paths(list(project.readonly_paths or []), project.directory)
        if "reference_project_ids" in data:
            data["reference_project_ids"] = await _reference_ids(
                s, principal, project_id, data["reference_project_ids"], project.directory)
        elif dir_changed:
            await _reference_ids(s, principal, project_id, list(project.reference_project_ids or []),
                                 project.directory, check_access=False)
        if data.get("name") and data["name"] != project.name:
            # Project-scoped MCP servers reference the project by name; carry
            # them along so runtime matching and visibility follow the rename.
            # Names are unique, so every server on the old name is this project's.
            await s.execute(
                update(McpServer)
                .where(McpServer.scope == "project", McpServer.project == project.name)
                .values(project=data["name"])
            )
        if data.get("code_map") and data.get("trust", project.trust) == "external" and not principal.is_admin:
            raise HTTPException(403, "Only an admin can turn the code map on for outside code")
        if data.get("code_map") and not (project.directory and Path(project.directory).is_dir()):
            raise HTTPException(422, "The code map needs a project folder that exists")
        for k, v in data.items():
            setattr(project, k, v)
        await s.commit()
        await s.refresh(project)
        counts = await _counts(s, [project_id])
    return _to_out(project, *counts.get(project_id, (0, 0.0)), role=role, admin=principal.is_admin)


@router.post("/{project_id}/licence/refresh", response_model=ProjectOut)
async def refresh_licence(project_id: str, request: Request):
    """Re-read the project's licence file (maintainers). Never run on a page load."""
    principal = request.state.principal
    async with SessionLocal() as s:
        role = await require_project_role(s, principal, project_id, "maintainer")
        project = await _get_or_404(s, project_id)
        project.licence = await licence_mod.for_directory(project.directory)
        await s.commit()
        await s.refresh(project)
        counts = await _counts(s, [project_id])
    return _to_out(project, *counts.get(project_id, (0, 0.0)), role=role, admin=principal.is_admin)


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str, request: Request):
    _require_admin(request)
    async with SessionLocal() as s:
        project = await _get_or_404(s, project_id)
        await s.execute(delete(ProjectMember).where(ProjectMember.project_id == project_id))
        await s.execute(delete(ActionRule).where(ActionRule.project_id == project_id))
        await s.execute(delete(ProjectDecision).where(ProjectDecision.project_id == project_id))
        await s.delete(project)
        await s.commit()


@router.get("/{project_id}/memory", response_model=MemoryOut)
async def get_memory(project_id: str, request: Request):
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id)
        project = await _get_or_404(s, project_id)
        return MemoryOut(
            memory=project.memory,
            memory_prev=project.memory_prev,
            memory_updated_at=project.memory_updated_at,
        )


@router.post("/{project_id}/memory/regenerate", response_model=MemoryOut)
async def regenerate_memory(project_id: str, request: Request):
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id, "maintainer")
        await _get_or_404(s, project_id)
    # Runs Claude as the clicking user (their credential or the org fallback).
    try:
        await memory.rebuild_memory(project_id, request.state.principal.user.id)
    except MissingCredential as e:
        raise HTTPException(409, str(e))
    async with SessionLocal() as s:
        project = await _get_or_404(s, project_id)
        return MemoryOut(
            memory=project.memory,
            memory_prev=project.memory_prev,
            memory_updated_at=project.memory_updated_at,
        )


@router.get("/{project_id}/summaries", response_model=list[TaskSummaryOut])
async def list_summaries(project_id: str, request: Request, limit: int = 50):
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id)
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
async def project_stats(project_id: str, request: Request):
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id)
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


# ---- Server inventory (Remote Ops) ----------------------------------------
# The inventory is a plain `servers.yaml` in the project's directory so the
# Remote Ops agents can `cat` it at run time (their cwd IS the project dir).
# These endpoints keep that file as the source of truth, editable from the UI.

def _servers_path(project: Project) -> Path:
    return Path(project.directory).expanduser() / "servers.yaml"


def _read_servers(project: Project) -> list[dict]:
    path = _servers_path(project)
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise HTTPException(400, f"servers.yaml is not valid YAML: {e}")
    if not isinstance(data, dict):
        raise HTTPException(400, "servers.yaml must be a mapping of name -> details")
    out = []
    for name, d in data.items():
        d = d or {}
        out.append(
            {
                "name": str(name),
                "host": d.get("host", ""),
                "user": d.get("user", ""),
                "pem": d.get("pem", ""),
                "notes": d.get("notes"),
            }
        )
    return out


@router.get("/{project_id}/guard/rules-file")
async def get_rules_file(project_id: str, request: Request):
    """The repo's add-only co-guard rules file as the worker reads it; an
    external project's file is never read (``read`` false)."""
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id)
        project = await _get_or_404(s, project_id)
    if project.trust != "own":
        path = Path(project.directory) / policy_store.RULES_FILE
        return {"path": str(path), "present": os.path.lexists(path), "read": False,
                "confirm": [], "never": [], "errors": []}
    out = await asyncio.to_thread(policy_store.read_repo_rules, project.directory)
    return {**out, "read": True}


@router.get("/{project_id}/git-state")
async def get_git_state(project_id: str, request: Request):
    """Read-only git facts for the project's directory (never raises)."""
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id)
        project = await _get_or_404(s, project_id)
    f = await asyncio.to_thread(gitfacts.facts, project.directory, gitfacts.Budget())
    return {"is_repo": f.is_repo, "no_commits": f.no_commits, "branch": f.branch, "head": f.head,
            "dirty": f.dirty, "dirty_capped": f.dirty_capped, "error": f.error}


@router.get("/{project_id}/servers", response_model=list[ServerEntry])
async def list_servers(project_id: str, request: Request):
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id)
        project = await _get_or_404(s, project_id)
    return _read_servers(project)


@router.put("/{project_id}/servers", response_model=list[ServerEntry])
async def replace_servers(project_id: str, servers: list[ServerEntry], request: Request):
    """Replace the whole inventory. Names must be unique."""
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id)
    _require_admin(request)
    names = [x.name for x in servers]
    if len(names) != len(set(names)):
        raise HTTPException(400, "Server names must be unique")
    async with SessionLocal() as s:
        project = await _get_or_404(s, project_id)
    mapping = {
        x.name: {
            "host": x.host,
            "user": x.user,
            "pem": x.pem,
            **({"notes": x.notes} if x.notes else {}),
        }
        for x in servers
    }
    path = _servers_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Server inventory for the Remote Ops agents (managed from the UI).\n"
        "# PEM keys live on this host (chmod 600); only their PATH is stored here.\n\n"
    )
    body = yaml.safe_dump(mapping, sort_keys=True, default_flow_style=False) if mapping else ""
    path.write_text(header + body)
    return _read_servers(project)


MEMBER_ROLES = ("maintainer", "contributor")


async def _members_out(s, project_id: str) -> list[MemberOut]:
    rows = (
        await s.execute(select(ProjectMember).where(ProjectMember.project_id == project_id))
    ).scalars().all()
    uids = [r.subject_id for r in rows if r.subject_type == "user"]
    tids = [r.subject_id for r in rows if r.subject_type == "team"]
    users = {}
    teams = {}
    if uids:
        users = {u.id: u for u in (await s.execute(select(User).where(User.id.in_(uids)))).scalars()}
    if tids:
        teams = {t.id: t for t in (await s.execute(select(Team).where(Team.id.in_(tids)))).scalars()}
    out = []
    for r in rows:
        if r.subject_type == "user":
            u = users.get(r.subject_id)
            label = (u.name or u.email) if u else r.subject_id
        else:
            t = teams.get(r.subject_id)
            label = t.name if t else r.subject_id
        out.append(
            MemberOut(subject_type=r.subject_type, subject_id=r.subject_id, role=r.role, label=label)
        )
    return sorted(out, key=lambda m: (m.subject_type, m.label.lower()))


@router.get("/{project_id}/members", response_model=list[MemberOut])
async def list_members(project_id: str, request: Request):
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id)
        return await _members_out(s, project_id)


@router.put("/{project_id}/members", response_model=list[MemberOut])
async def set_members(project_id: str, body: list[MemberIn], request: Request):
    async with SessionLocal() as s:
        await require_project_role(s, request.state.principal, project_id, "maintainer")
        if not await s.get(Project, project_id):
            raise HTTPException(404, "Not found")
        seen = set()
        for m in body:
            if m.role not in MEMBER_ROLES:
                raise HTTPException(400, f"role must be one of: {', '.join(MEMBER_ROLES)}")
            if m.subject_type not in ("user", "team"):
                raise HTTPException(400, "subject_type must be 'user' or 'team'")
            key = (m.subject_type, m.subject_id)
            if key in seen:
                raise HTTPException(400, "Duplicate member")
            seen.add(key)
            model = User if m.subject_type == "user" else Team
            if not await s.get(model, m.subject_id):
                raise HTTPException(400, f"Unknown {m.subject_type}: {m.subject_id}")
        await s.execute(delete(ProjectMember).where(ProjectMember.project_id == project_id))
        for m in body:
            s.add(
                ProjectMember(
                    project_id=project_id,
                    subject_type=m.subject_type,
                    subject_id=m.subject_id,
                    role=m.role,
                )
            )
        await s.commit()
        return await _members_out(s, project_id)
