"""Phase 6c: GET /projects/{id}/recommended and the two project settings."""

import uuid

from app.database import SessionLocal
from app.models import McpServer, SkillPack
from conftest import login_as
from test_codemap_api import _project


def _get(c, pid):
    r = c.get(f"/projects/{pid}/recommended")
    assert r.status_code == 200, r.text
    return r.json()


def _keys(body):
    return [i["key"] for i in body["items"]]


async def _add(*rows):
    async with SessionLocal() as s:
        for r in rows:
            s.add(r)
        await s.commit()
        return [r.id for r in rows]


async def _drop(model, ids):
    async with SessionLocal() as s:
        for i in ids:
            row = await s.get(model, i)
            if row is not None:
                await s.delete(row)
        await s.commit()


def test_no_ui_no_items(client, tmp_path):
    p, d = _project(client, tmp_path)
    b = _get(client, p["id"])
    assert b == {"has_ui": False, "react": False, "ui_skills": True, "brand_skill": None,
                 "brand_skill_found": False, "items": []}


def test_no_folder_answers_plainly(client):
    r = client.post("/projects", json={"name": f"rec-{uuid.uuid4().hex[:8]}"})
    assert r.status_code == 201
    b = _get(client, r.json()["id"])
    assert b["has_ui"] is False and b["items"] == []


def test_table_rows(client, tmp_path):
    p, d = _project(client, tmp_path)
    (d / "index.html").write_text("<p>")
    b = _get(client, p["id"])
    assert b["has_ui"] and not b["react"]
    assert _keys(b) == ["browser-playwright", "browser-devtools", "frontend-design"]
    by = {i["key"]: i for i in b["items"]}
    assert by["browser-devtools"]["preset"]["args"] == ["-y", "chrome-devtools-mcp@1.13.0"]
    assert by["frontend-design"]["kind"] == "skill" and by["frontend-design"]["preset"] is None
    assert not any(i["present"] for i in b["items"])
    (d / "package.json").write_text('{"dependencies": {"react": "18"}}')
    b = _get(client, p["id"])
    assert b["react"] and _keys(b) == ["browser-playwright", "browser-devtools", "ui-shadcn", "frontend-design"]
    shadcn = {i["key"]: i for i in b["items"]}["ui-shadcn"]
    assert shadcn["preset"]["args"] == ["-y", "shadcn@4.23.0", "mcp"] and shadcn["kind"] == "tool"


def test_present_rules(client, tmp_path):
    p, d = _project(client, tmp_path)
    (d / "index.html").write_text("<p>")
    mk = lambda name, scope, project, enabled, pkg: McpServer(  # noqa: E731
        name=f"{name}-{uuid.uuid4().hex[:6]}", scope=scope, project=project, command="npx",
        args=["-y", pkg], enabled=enabled)
    ids = client.portal.call(_add, mk("pw", "project", p["name"], True, "@playwright/mcp@0.0.86"),
                             mk("dt-off", "project", p["name"], False, "chrome-devtools-mcp@1.13.0"),
                             mk("sh-other", "project", "someone-else", True, "shadcn@4.23.0")) \
        if hasattr(client, "portal") else None
    if ids is None:
        import asyncio
        ids = asyncio.run(_add(mk("pw", "project", p["name"], True, "@playwright/mcp@0.0.86"),
                               mk("dt-off", "project", p["name"], False, "chrome-devtools-mcp@1.13.0"),
                               mk("sh-other", "project", "someone-else", True, "shadcn@4.23.0")))
    try:
        (d / "package.json").write_text('{"dependencies": {"react": "18"}}')
        by = {i["key"]: i["present"] for i in _get(client, p["id"])["items"]}
        assert by == {"browser-playwright": True, "browser-devtools": False, "ui-shadcn": False,
                      "frontend-design": False}
    finally:
        import asyncio
        asyncio.run(_drop(McpServer, ids))


def test_frontend_design_from_enabled_pack(client, tmp_path):
    import asyncio
    p, d = _project(client, tmp_path)
    (d / "index.html").write_text("<p>")
    pack_dir = tmp_path / "pack"
    (pack_dir / "skills" / "frontend-design").mkdir(parents=True)
    on = SkillPack(name=f"pk-{uuid.uuid4().hex[:6]}", local_path=str(pack_dir), enabled=True, scope="global")
    off = SkillPack(name=f"pk-{uuid.uuid4().hex[:6]}", local_path=str(pack_dir), enabled=False, scope="global")
    other = SkillPack(name=f"pk-{uuid.uuid4().hex[:6]}", local_path=str(pack_dir), enabled=True, scope="project",
                      project_id="not-this-one")

    def present():
        return {i["key"]: i["present"] for i in _get(client, p["id"])["items"]}["frontend-design"]

    ids = asyncio.run(_add(off, other))
    try:
        assert present() is False
        ids += asyncio.run(_add(on))
        assert present() is True
    finally:
        asyncio.run(_drop(SkillPack, ids))


def test_settings_validation_and_roles(client, tmp_path):
    p, _ = _project(client, tmp_path)
    assert p["ui_skills"] is True and p["brand_skill"] is None
    for bad in ("two words", "line\nbreak", 'quo"te', "", "x" * 121):
        assert client.patch(f"/projects/{p['id']}", json={"brand_skill": bad}).status_code == 422, bad
    r = client.patch(f"/projects/{p['id']}", json={"brand_skill": "acme:brand", "ui_skills": False})
    assert r.status_code == 200 and r.json()["brand_skill"] == "acme:brand" and r.json()["ui_skills"] is False
    b = _get(client, p["id"])
    assert b["brand_skill"] == "acme:brand" and b["ui_skills"] is False
    assert client.patch(f"/projects/{p['id']}", json={"brand_skill": None}).json()["brand_skill"] is None

    member = login_as(f"rec-m-{uuid.uuid4().hex[:6]}@test.local")
    maint = login_as(f"rec-x-{uuid.uuid4().hex[:6]}@test.local")
    out = login_as(f"rec-o-{uuid.uuid4().hex[:6]}@test.local")
    r = client.put(f"/projects/{p['id']}/members", json=[
        {"subject_type": "user", "subject_id": member.user_id, "role": "contributor"},
        {"subject_type": "user", "subject_id": maint.user_id, "role": "maintainer"}])
    assert r.status_code == 200, r.text
    assert member.patch(f"/projects/{p['id']}", json={"ui_skills": True}).status_code == 403
    assert member.get(f"/projects/{p['id']}/recommended").status_code == 200
    assert maint.patch(f"/projects/{p['id']}", json={"brand_skill": "b1"}).status_code == 200
    assert out.get(f"/projects/{p['id']}/recommended").status_code in (403, 404)


def test_slow_detect_answers_no_ui(client, tmp_path, monkeypatch):
    import time

    from app.recommend import detect as detect_mod

    p, d = _project(client, tmp_path)
    (d / "index.html").write_text("<p>")
    monkeypatch.setattr(detect_mod, "TIMEOUT", 0.1)
    monkeypatch.setattr(detect_mod, "detect", lambda _d: time.sleep(0.6) or detect_mod.Detected(has_ui=True))
    b = _get(client, p["id"])
    assert b["has_ui"] is False and b["items"] == []
