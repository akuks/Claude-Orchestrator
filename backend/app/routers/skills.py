import asyncio
import os
import json
import re
import shutil
import stat
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import or_, select

from ..access import ALL, visible_project_ids
from ..auth import require_admin
from ..config import settings
from ..database import SessionLocal
from .. import skills_lint
from ..models import SkillPack
from ..schemas import SkillPackCreate, SkillPackOut, SkillPackUpdate

router = APIRouter(prefix="/skills", tags=["skills"])
_admin = [Depends(require_admin)]  # writes are admin-only; reads are open


# Curated skill packs (Claude Code plugins). One-click install from the UI.
PRESETS = [
    {
        "name": "cybersecurity-skills",
        "source_url": "https://github.com/mukul975/Anthropic-Cybersecurity-Skills",
        "description": "818 security skills (MITRE ATT&CK, SOC, forensics, red team, IR) — pairs with the Security Auditor.",
    },
    {
        "name": "diagram-design",
        "source_url": "https://github.com/cathrynlavery/diagram-design",
        "description": "Editorial architecture/flow/sequence diagrams as self-contained HTML+SVG from a description.",
    },
    {
        "name": "scientific-agent-skills",
        "source_url": "https://github.com/K-Dense-AI/scientific-agent-skills",
        "description": "177 scientific skills (bio/chem/physics, 100+ databases). Niche — install only if you need it.",
    },
]


@router.get("/presets")
async def list_presets():
    return PRESETS


class LintIn(BaseModel):
    text: str


MAX_PACK_FILES = 200


@router.post("/lint")
async def lint_text(payload: LintIn):
    if len(payload.text.encode("utf-8")) > skills_lint.MAX_BYTES:
        raise HTTPException(422, "text is over 256 KB")
    problems = skills_lint.lint(payload.text)
    return {"ok": not problems, "problems": problems}


@router.get("/template")
async def skill_template():
    return {"text": skills_lint.TEMPLATE}


_TOO_BIG = object()


def _read_capped(path: str):
    """Read a regular, non-symlink file; None if it is over the cap or not a file."""
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb", closefd=False) as f:
            data = f.read(skills_lint.MAX_BYTES + 1)
    finally:
        os.close(fd)
    if len(data) > skills_lint.MAX_BYTES:
        return _TOO_BIG
    return data.decode("utf-8", errors="replace")


def _lint_pack_dir(root: str) -> list[dict]:
    out: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d != ".git" and not os.path.islink(os.path.join(dirpath, d)))
        for fn in sorted(filenames):
            if fn != "SKILL.md":
                continue
            full = os.path.join(dirpath, fn)
            try:
                if not stat.S_ISREG(os.lstat(full).st_mode):
                    continue
            except OSError:
                continue
            if len(out) >= MAX_PACK_FILES:
                return out
            rel = os.path.relpath(full, root)
            text = _read_capped(full)
            if text is None:
                continue
            problems = (
                [{"line": 1, "message": "file too large"}] if text is _TOO_BIG else skills_lint.lint(text)
            )
            out.append({"file": rel, "problems": problems})
    return out


@router.get("/{pack_id}/lint")
async def lint_pack(pack_id: str, request: Request):
    async with SessionLocal() as s:
        pack = await s.get(SkillPack, pack_id)
        if pack is not None and pack.scope == "project":
            visible = await visible_project_ids(s, request.state.principal)
            if visible is not ALL and pack.project_id not in visible:
                pack = None  # never reveal existence
    if pack is None:
        raise HTTPException(404, "Skill pack not found")
    if not pack.local_path or not os.path.isdir(pack.local_path):
        raise HTTPException(404, "Skill pack directory not found")
    return await asyncio.to_thread(_lint_pack_dir, pack.local_path)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", name.lower()).strip("-") or "skillpack"


def _plugin_meta(path: Path) -> tuple[str | None, str | None]:
    """Read name/version from a cloned plugin's .claude-plugin/plugin.json."""
    mf = path / ".claude-plugin" / "plugin.json"
    if mf.exists():
        try:
            d = json.loads(mf.read_text())
            return d.get("name"), d.get("version")
        except Exception:
            pass
    return None, None


def _clone_env() -> dict[str, str]:
    """git clone needs the network, so not gitsafe's no-transport env: https
    and ssh only (no file://, ext::, git://), no prompts, and no inherited
    GIT_* variable (GIT_DIR, GIT_CONFIG_PARAMETERS ...) steering it."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_ALLOW_PROTOCOL": "https:ssh", "GIT_TERMINAL_PROMPT": "0"})
    return env


async def _git_clone(url: str, dest: Path) -> None:
    # "--": a URL starting with "-" is never read as an option (--upload-pack=...)
    proc = await asyncio.create_subprocess_exec(
        "git", "-c", "core.hooksPath=/dev/null", "clone", "--depth", "1", "--", url, str(dest),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL,
        env=_clone_env(),
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise HTTPException(400, f"git clone failed: {err.decode(errors='replace')[:300]}")


@router.get("", response_model=list[SkillPackOut])
async def list_skillpacks(request: Request):
    stmt = select(SkillPack).order_by(SkillPack.created_at.desc())
    async with SessionLocal() as s:
        visible = await visible_project_ids(s, request.state.principal)
        if visible is not ALL:  # project packs only for that project's members
            stmt = stmt.where(
                or_(SkillPack.scope != "project", SkillPack.project_id.in_(visible))
            )
        rows = (await s.execute(stmt)).scalars().all()
    return [SkillPackOut.model_validate(r) for r in rows]


@router.post("", response_model=SkillPackOut, status_code=201, dependencies=_admin)
async def install_skillpack(payload: SkillPackCreate):
    if not payload.source_url and not payload.local_path:
        raise HTTPException(400, "Provide a source_url (git repo) or a local_path")
    if payload.scope not in ("global", "project"):
        raise HTTPException(400, "scope must be 'global' or 'project'")

    if payload.local_path:
        path = Path(payload.local_path).expanduser().resolve()
        if not path.exists():
            raise HTTPException(400, f"local_path does not exist: {path}")
        name = payload.name or path.name
    else:
        name = _slug(payload.name or payload.source_url.rstrip("/").split("/")[-1].removesuffix(".git"))
        path = settings.skillpacks_dir / name
        if path.exists():
            shutil.rmtree(path)  # re-install fresh
        await _git_clone(payload.source_url, path)

    pname, version = _plugin_meta(path)
    name = payload.name or pname or name

    async with SessionLocal() as s:
        dup = (await s.execute(select(SkillPack).where(SkillPack.name == name))).scalar_one_or_none()
        if dup:
            raise HTTPException(409, f"A skill pack named '{name}' already exists")
        pack = SkillPack(
            name=name,
            description=payload.description,
            source_url=payload.source_url,
            local_path=str(path),
            version=version,
            scope=payload.scope,
            project_id=payload.project_id,
        )
        s.add(pack)
        await s.commit()
        await s.refresh(pack)
        return SkillPackOut.model_validate(pack)


@router.patch("/{pack_id}", response_model=SkillPackOut, dependencies=_admin)
async def update_skillpack(pack_id: str, payload: SkillPackUpdate):
    data = payload.model_dump(exclude_unset=True)
    if "scope" in data and data["scope"] not in ("global", "project"):
        raise HTTPException(400, "scope must be 'global' or 'project'")
    async with SessionLocal() as s:
        pack = await s.get(SkillPack, pack_id)
        if pack is None:
            raise HTTPException(404, "Skill pack not found")
        for k, v in data.items():
            setattr(pack, k, v)
        await s.commit()
        await s.refresh(pack)
        return SkillPackOut.model_validate(pack)


@router.delete("/{pack_id}", status_code=204, dependencies=_admin)
async def delete_skillpack(pack_id: str):
    async with SessionLocal() as s:
        pack = await s.get(SkillPack, pack_id)
        if pack is None:
            raise HTTPException(404, "Skill pack not found")
        local = pack.local_path
        source = pack.source_url
        await s.delete(pack)
        await s.commit()
    # Only remove a dir we cloned (under skillpacks_dir), never a user's local path.
    if source and local and Path(local).resolve().is_relative_to(settings.skillpacks_dir):
        shutil.rmtree(local, ignore_errors=True)
