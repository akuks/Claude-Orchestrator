import asyncio
import json
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from ..config import settings
from ..database import SessionLocal
from ..models import SkillPack
from ..schemas import SkillPackCreate, SkillPackOut, SkillPackUpdate

router = APIRouter(prefix="/skills", tags=["skills"])


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


async def _git_clone(url: str, dest: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        "git", "clone", "--depth", "1", url, str(dest),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise HTTPException(400, f"git clone failed: {err.decode(errors='replace')[:300]}")


@router.get("", response_model=list[SkillPackOut])
async def list_skillpacks():
    async with SessionLocal() as s:
        rows = (await s.execute(select(SkillPack).order_by(SkillPack.created_at.desc()))).scalars().all()
    return [SkillPackOut.model_validate(r) for r in rows]


@router.post("", response_model=SkillPackOut, status_code=201)
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


@router.patch("/{pack_id}", response_model=SkillPackOut)
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


@router.delete("/{pack_id}", status_code=204)
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
