"""Phase 7f: UI workflow Screens gate, the Set up UI checks task, the Brand guide template."""

import asyncio
import types
import uuid

from sqlalchemy import select

from app import ui_checks
from app.database import SessionLocal
from app.gates import executor as ex
from app.gates import spec
from app.models import ContextDoc, Task
from app.workflow_templates import TEMPLATES, templates_out
from conftest import login_as
from test_codemap_api import _project


def test_ui_template_stages():
    st = TEMPLATES["ui"]["stages"]
    assert [s["name"] for s in st] == ["Direction", "Approve look", "Build", "Test", "Screens", "Design review", "Approve"]
    assert st[1]["kind"] == "approval" and st[1]["ahead"] is True
    assert "ahead" not in st[6]
    assert st[3]["run"] == ["npm test"]
    assert st[4]["kind"] == "gate" and st[4]["timeout_min"] == 60
    assert st[4]["run"] == ["npm run test:ui", "npm run test:a11y", "npm run test:lighthouse"]
    assert "Set up UI checks" in [t for t in templates_out() if t["key"] == "ui"][0]["hint"]
    assert "Node 20" in [t for t in templates_out() if t["key"] == "ui"][0]["hint"]


def test_screens_commands_pass_check_command_and_guard(client, tmp_path):
    for c in ui_checks.GATE_COMMANDS:
        assert spec.check_command(c) == c
    proj = types.SimpleNamespace(directory=str(tmp_path), trust="own", quarantine=False, on_no_answer="deny",
                                 guard_enabled=False, approval_wait_seconds=300, id="p1")

    async def go(cmd):
        async with SessionLocal() as s:
            return await ex.guard_verdict(s, proj, cmd)

    for c in ui_checks.GATE_COMMANDS:
        assert asyncio.run(go(c)).decision == "allow", c


def test_setup_prompt_content():
    p = ui_checks.setup_prompt()
    for part in (ui_checks.VISUAL_SPEC, ui_checks.A11Y_SPEC, ui_checks.LIGHTHOUSERC):
        assert part.rstrip() in p
    assert "maxDiffPixelRatio: 0.01" in p and "390" in p and "1280" in p
    assert "wcag21aa" in p and '"numberOfRuns": 3' in p
    assert "npm i -D @playwright/test @axe-core/playwright @lhci/cli" in p
    for s in ("test:ui", "test:a11y", "test:lighthouse", "test:ui:update"):
        assert f"`{s}`" in p
    low = p.lower()
    assert "never overwrite" in low and "do not push" in low and "only if" in low
    assert "commit your changes on the current branch" in low
    assert "git push" not in low and "--force" not in low


def _count(pid):
    async def go():
        async with SessionLocal() as s:
            return (await s.execute(select(Task).where(Task.project_id == pid, Task.title == "Set up UI checks"))).scalars().all()
    return asyncio.run(go())


def test_setup_creates_task_for_maintainer(client, tmp_path):
    p, _ = _project(client, tmp_path)
    r = client.post(f"/projects/{p['id']}/ui-checks/setup")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == "Set up UI checks" and body["project_id"] == p["id"]
    assert "Never overwrite" in body["prompt"]
    assert len(_count(p["id"])) == 1


def test_setup_needs_maintainer(client, tmp_path):
    p, _ = _project(client, tmp_path)
    other = login_as(f"uic-{uuid.uuid4().hex[:6]}@example.com")
    assert other.post(f"/projects/{p['id']}/ui-checks/setup").status_code in (403, 404)
    assert _count(p["id"]) == []
    assert client.post("/projects/nope/ui-checks/setup").status_code in (403, 404)


def test_brand_template_and_409(client, tmp_path):
    p, _ = _project(client, tmp_path)
    r = client.post("/context-docs/templates/brand", json={"project_id": p["id"]})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["path"] == "brand/brand.md" and d["title"] == "Brand guide" and d["scope"] == "project"
    assert d["abstract"].startswith("Brand rules: tokens, colours")
    assert "<product name>" in d["body"] and "npm run test:lighthouse" in d["body"]
    again = client.post("/context-docs/templates/brand", json={"project_id": p["id"]})
    assert again.status_code == 409

    async def n():
        async with SessionLocal() as s:
            return len((await s.execute(select(ContextDoc).where(ContextDoc.project_id == p["id"]))).scalars().all())
    assert asyncio.run(n()) == 1


def test_brand_template_needs_maintainer(client, tmp_path):
    p, _ = _project(client, tmp_path)
    other = login_as(f"uib-{uuid.uuid4().hex[:6]}@example.com")
    assert other.post("/context-docs/templates/brand", json={"project_id": p["id"]}).status_code in (403, 404)
    assert client.get(f"/context-docs?project_id={p['id']}").json() == []
