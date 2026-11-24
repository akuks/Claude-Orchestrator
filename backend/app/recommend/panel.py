"""The Recommended panel's items and the design-skills paragraph (phase 6c)."""

from __future__ import annotations

import os
import re

from sqlalchemy import select

from ..codemap.presets import PRESETS
from ..models import McpServer, SkillPack
from .detect import Detected, valid_brand

UI_PARAGRAPH = ("This project has a user interface. Before changing anything a user sees, load the frontend-design "
                "skill (frontend-design:frontend-design) if you have it. {brand}")
BRAND_SET = "Also load the brand skill `{name}`."
BRAND_FOUND = "Also load the project's brand skill."
BRAND_UNKNOWN = "If your skills list has a brand or style skill for this project, load it too."

# (key, kind, shown when, why, package name the registered tool must mention)
ITEMS = (
    ("browser-playwright", "tool", "ui", "Lets an agent open your pages in a real browser and look at them.",
     "@playwright/mcp"),
    ("browser-devtools", "tool", "ui", "Lets an agent inspect a running page: its console, network and speed.",
     "chrome-devtools-mcp"),
    ("ui-shadcn", "tool", "react", "Lets an agent browse and add ready-made interface parts (shadcn).", "shadcn"),
    ("frontend-design", "skill", "ui", "A skill that makes interface work look designed. Add it under Skills.",
     None),
)
_NAMES = {"browser-playwright": "Browser (Playwright)", "browser-devtools": "Browser inspector (DevTools)",
          "ui-shadcn": "Interface parts (shadcn)", "frontend-design": "Frontend design skill"}
SKILL = "frontend-design"


def ui_skills_text(project, detected: Detected) -> str | None:
    """The paragraph for the main session and helpers, or None (switch off, no user interface, or no project)."""
    if project is None or getattr(project, "ui_skills", True) is not True or not detected.has_ui:
        return None
    brand = getattr(project, "brand_skill", None)
    if valid_brand(brand):  # an invalid stored value counts as not set
        tail = BRAND_SET.format(name=brand)
    elif detected.brand_skill_found:
        tail = BRAND_FOUND
    else:
        tail = BRAND_UNKNOWN
    return UI_PARAGRAPH.format(brand=tail)


def _pkg_of(arg: str) -> str:
    m = re.match(r"^(@[^/@]+/[^@]+|[^@]+)", arg)
    return m.group(1) if m else arg


def tool_present(servers, project, package: str) -> bool:
    """An enabled tool in this project's scope whose arguments name `package` (version ignored)."""
    for m in servers:
        if not getattr(m, "enabled", True) or (m.scope == "project" and m.project != project.name):
            continue
        if any(_pkg_of(str(a)) == package for a in (m.args or [])):
            return True
    return False


def _pack_has_skill(pack) -> bool:
    if SKILL in (pack.name or "").lower() or SKILL in (pack.source_url or "").lower():
        return True
    base = pack.local_path or ""
    if not base:
        return False
    for rel in (("skills", SKILL), ("plugins", SKILL, "skills", SKILL), (".claude", "skills", SKILL)):
        try:
            if os.path.isdir(os.path.join(base, *rel)) and not os.path.islink(os.path.join(base, *rel)):
                return True
        except OSError:
            pass
    return False


def packs_have_skill(packs, project) -> bool:
    return any(p.enabled and (p.scope == "global" or p.project_id == project.id) and _pack_has_skill(p)
               for p in packs)


async def build(s, project, detected: Detected) -> dict:
    servers = list((await s.execute(select(McpServer).where(McpServer.enabled.is_(True)))).scalars().all())
    packs = list((await s.execute(select(SkillPack).where(SkillPack.enabled.is_(True)))).scalars().all())
    presets = {p["key"]: p for p in PRESETS}
    items = []
    for key, kind, when, why, package in ITEMS:
        if not detected.has_ui or (when == "react" and not detected.react):
            continue
        if kind == "tool":
            present = tool_present(servers, project, package)
            preset = dict(presets[key], args=list(presets[key]["args"]))
        else:
            present = packs_have_skill(packs, project)
            preset = None
        items.append({"key": key, "name": _NAMES[key], "why": why, "kind": kind, "present": present,
                      "preset": preset})
    brand = project.brand_skill if valid_brand(project.brand_skill) else None
    return {"has_ui": detected.has_ui, "react": detected.react, "ui_skills": bool(project.ui_skills),
            "brand_skill": brand, "brand_skill_found": detected.brand_skill_found, "items": items}
