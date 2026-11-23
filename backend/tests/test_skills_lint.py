import asyncio
import os
import uuid

from app.database import SessionLocal
from app.models import SkillPack
from app.skills_lint import MAX_BYTES, TEMPLATE, lint
from conftest import login_as

GOOD = "\n".join([
    "---", "name: demo", "description: Use when asked to demo.", "---",
    "1. Input: a path. Action: read it. Output: notes.",
    "2. Verify: the file exists.", "   Evidence: the path.", ""])


def msgs(p):
    return " | ".join(x["message"] for x in p)


def test_template_lints_clean():
    assert lint(TEMPLATE) == []
    assert "jspark" not in TEMPLATE.lower() and "forge" not in TEMPLATE.lower()


def test_good_and_missing_name_line():
    assert lint(GOOD) == []
    e = lint(GOOD.replace("name: demo\n", ""))
    assert len(e) == 1 and e[0]["line"] == 1 and "name" in e[0]["message"]
    e = lint(GOOD.replace("name: demo", "name:"))
    assert e[0]["line"] == 2


def test_description_rules():
    e = lint(GOOD.replace("Use when asked", "Helps when asked"))
    assert e == [{"line": 3, "message": 'description must start with "Use when" (it states the trigger)'}]
    assert "description" in msgs(lint(GOOD.replace("description: Use when asked to demo.\n", "")))


def test_frontmatter():
    assert "frontmatter" in msgs(lint("1. Input: a. Action: b. Output: c.\n"))
    assert "not closed" in msgs(lint("---\nname: x\n"))
    assert lint("") != []


def test_plain_skill_needs_only_frontmatter():
    assert lint("---\nname: x\ndescription: Use when x.\n---\n1. Run the thing.\n2. Say done.\n") == []


def test_part_missing_output_at_its_line():
    bad = GOOD.replace("2. Verify", "2. Input: a. Action: b.\n3. Verify")
    e = lint(bad)
    assert e == [{"line": 6, "message": "part is missing Output:"}]


def test_verify_required_and_evidence():
    no_verify = "\n".join(GOOD.split("\n")[:5]) + "\n"
    e = lint(no_verify)
    assert e and e[0]["line"] == 5 and "Verify:" in e[0]["message"]
    e = lint(GOOD.replace("   Evidence: the path.\n", ""))
    assert e[0]["line"] == 6 and "Evidence:" in e[0]["message"]


def test_comment_ignored_and_lines_kept():
    t = GOOD.replace("1. Input", "<!-- 9. Input: Action: Output:\n still comment -->\n1. Input")
    assert lint(t) == []
    t2 = t.replace("2. Verify", "2. Input: a. Action: b.\n3. Verify")
    assert lint(t2) == [{"line": 8, "message": "part is missing Output:"}]


def test_lint_endpoint(client):
    r = client.post("/skills/lint", json={"text": GOOD})
    assert r.status_code == 200 and r.json() == {"ok": True, "problems": []}
    r = client.post("/skills/lint", json={"text": "x"})
    assert r.json()["ok"] is False and r.json()["problems"][0]["line"] == 1
    assert client.post("/skills/lint", json={"text": "a" * (MAX_BYTES + 1)}).status_code == 422
    assert client.post("/skills/lint", json={"text": "a" * (300 * 1024)}).status_code == 422


def test_template_endpoint(client):
    r = client.get("/skills/template")
    assert r.status_code == 200 and r.json()["text"] == TEMPLATE


def _pack(path, scope="global", project_id=None):
    p = SkillPack(name=f"sp-{uuid.uuid4().hex[:8]}", local_path=str(path), scope=scope, project_id=project_id)

    async def go():
        async with SessionLocal() as s:
            s.add(p)
            await s.commit()

    asyncio.run(go())
    return p.id


def test_pack_lint_walks_and_skips_symlinks(client, tmp_path):
    pack = tmp_path / "pack"
    (pack / "a").mkdir(parents=True)
    (pack / "a" / "SKILL.md").write_text(GOOD)
    (pack / "b").mkdir()
    (pack / "b" / "SKILL.md").write_text("nope")
    (pack / "big").mkdir()
    (pack / "big" / "SKILL.md").write_text("a" * (MAX_BYTES + 1))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("secret")
    os.symlink(outside, pack / "linkdir")
    (pack / "c").mkdir()
    os.symlink(outside / "SKILL.md", pack / "c" / "SKILL.md")
    pid = _pack(pack)
    r = client.get(f"/skills/{pid}/lint")
    assert r.status_code == 200, r.text
    by = {x["file"]: x["problems"] for x in r.json()}
    assert set(by) == {"a/SKILL.md", "b/SKILL.md", "big/SKILL.md"}
    assert by["a/SKILL.md"] == []
    assert by["b/SKILL.md"][0]["line"] == 1
    assert by["big/SKILL.md"] == [{"line": 1, "message": "file too large"}]


def test_pack_lint_file_cap(client, tmp_path):
    for i in range(205):
        d = tmp_path / f"d{i:03d}"
        d.mkdir()
        (d / "SKILL.md").write_text(GOOD)
    r = client.get(f"/skills/{_pack(tmp_path)}/lint")
    assert len(r.json()) == 200


def test_pack_lint_visibility_and_missing(client, tmp_path):
    (tmp_path / "SKILL.md").write_text(GOOD)
    pid = client.post("/projects", json={"name": f"sk-{uuid.uuid4().hex[:8]}"}).json()["id"]
    pack = _pack(tmp_path, "project", pid)
    other = login_as(f"sk-{uuid.uuid4().hex[:8]}@test.local")
    assert other.get(f"/skills/{pack}/lint").status_code == 404
    assert client.get(f"/skills/{pack}/lint").status_code == 200
    assert client.get("/skills/nope/lint").status_code == 404
