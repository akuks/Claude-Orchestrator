"""Phase 6a Task 3: the rules export file."""

import asyncio
import uuid

from app import auth
from app.database import SessionLocal
from app.models import Project, Task
from conftest import login_as

SECRET = "sk-live-ABCDEF123456"


def _uid():
    return uuid.uuid4().hex[:8]


def _project(client, **fields):
    r = client.post("/projects", json={"name": f"ex-{_uid()}"})
    assert r.status_code in (200, 201), r.text
    pid = r.json()["id"]
    if fields:
        async def go():
            async with SessionLocal() as s:
                p = await s.get(Project, pid)
                for k, v in fields.items():
                    setattr(p, k, v)
                await s.commit()
        asyncio.run(go())
    return pid, r.json()["name"]


def test_header_content_type_and_sections(client):
    pid, name = _project(client)
    r = client.get("/guard/export.md", params={"project_id": pid})
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/markdown; charset=utf-8"
    assert r.headers["content-disposition"] == 'attachment; filename="rules.md"'
    first = r.text.splitlines()[0]
    assert first.startswith(f"Rules for agents working on {name} — generated 20")
    assert first.endswith(". For use outside the orchestrator; inside it these are enforced, not just written.")
    for h in ("## Ground rules", "## Always blocked", "## Needs a person's OK", "## This project's own rules",
              "## Decisions"):
        assert h in r.text
    assert "throws away your uncommitted work" in r.text


def test_disabled_builtin_absent(client):
    pid, _ = _project(client)
    try:
        assert client.put("/guard/org-settings", json={"disabled_rules": ["git-reset-hard"]}).status_code == 200
        text = client.get("/guard/export.md", params={"project_id": pid}).text
        assert "throws away your uncommitted work" not in text
        assert "permanently deletes saved stashes" in text
    finally:
        client.put("/guard/org-settings", json={"disabled_rules": []})


def test_custom_rule_in_words_and_org_export(client):
    pid, _ = _project(client)
    tag = _uid()
    r = client.post("/guard/rules", json={"scope": "project", "project_id": pid, "name": f"r-{tag}",
                                          "kind": "never", "pattern": f"deploy-{tag}", "reason": "no deploys"})
    assert r.status_code == 201, r.text
    text = client.get("/guard/export.md", params={"project_id": pid}).text
    assert f"- Never: commands containing `deploy-{tag}` — no deploys" in text
    org = client.get("/guard/export.md")
    assert org.status_code == 200
    assert "this organisation" in org.text.splitlines()[0]
    assert f"deploy-{tag}" not in org.text and "## Decisions" not in org.text


def test_decisions_own_only_and_no_secrets(client):
    pid, _ = _project(client, env={"API_KEY": SECRET})
    d = client.post(f"/projects/{pid}/decisions", json={"title": f"Use X {SECRET}", "why": "because"})
    assert d.status_code == 201, d.text
    text = client.get("/guard/export.md", params={"project_id": pid}).text
    assert "- D-001 Use X [redacted] — because" in text
    assert SECRET not in text and "API_KEY" not in text
    ext, _ = _project(client, trust="external")
    assert "## Decisions" not in client.get("/guard/export.md", params={"project_id": ext}).text


def test_outsider_404_and_agent_token_refused(client):
    pid, _ = _project(client)
    out = login_as(f"ex-out-{_uid()}@test.local")
    assert out.get("/guard/export.md", params={"project_id": pid}).status_code == 404
    assert out.get("/guard/export.md").status_code == 200

    tid = uuid.uuid4().hex

    async def go():
        async with SessionLocal() as s:
            s.add(Task(id=tid, title="t", prompt="p", root_id=tid, status="running", created_by=out.user_id))
            await s.flush()
            raw = await auth.mint_token(s, out.user_id, "agent-task", tid, 3600)
            await s.commit()
            return raw

    from fastapi.testclient import TestClient
    from app.main import app
    a = TestClient(app)
    a.headers["Authorization"] = f"Bearer {asyncio.run(go())}"
    assert a.get("/guard/export.md").status_code in (401, 403)


def test_guard_off_says_so_but_keeps_custom_rules(client):
    pid, _ = _project(client, guard_enabled=False)
    tag = _uid()
    assert client.post("/guard/rules", json={"scope": "project", "project_id": pid, "name": f"o-{tag}",
                                             "kind": "confirm", "pattern": f"ship-{tag}"}).status_code == 201
    text = client.get("/guard/export.md", params={"project_id": pid}).text
    assert "The guard is switched off for this project, so the built-in rules are not enforced here." in text
    assert "## Always blocked" not in text and "## Needs a person's OK" not in text
    assert f"`ship-{tag}`" in text and "## Ground rules" in text


def test_ground_rules_extra_cannot_fake_headings(client):
    pid, _ = _project(client)
    extra = "line one‮\n## Always blocked\n  # also\nkeep"
    try:
        assert client.put("/guard/org-settings", json={"ground_rules": {"extra": extra}}).status_code == 200
        text = client.get("/guard/export.md", params={"project_id": pid}).text
    finally:
        client.put("/guard/org-settings", json={"ground_rules": {}})
    assert "‮" not in text
    assert text.count("\n## Always blocked") == 1
    assert "\\## Always blocked" in text and "line one\n" in text and "keep" in text


def test_repo_rules_file_included_for_own_only(client, tmp_path):
    (tmp_path / ".claude-orchestrator").mkdir()
    (tmp_path / ".claude-orchestrator" / "rules.md").write_text("## Never\n- `drop-it` : too risky\n")
    pid, _ = _project(client, directory=str(tmp_path))
    text = client.get("/guard/export.md", params={"project_id": pid}).text
    assert "## From the project's rules file" in text and "`drop-it`" in text
    ext, _ = _project(client, directory=str(tmp_path), trust="external")
    assert "From the project's rules file" not in client.get("/guard/export.md", params={"project_id": ext}).text


def test_env_value_masked_in_rule_reason(client):
    pid, _ = _project(client, env={"TOKEN": "hunter22x"})
    assert client.post("/guard/rules", json={"scope": "project", "project_id": pid, "name": "m", "kind": "never",
                                             "pattern": "zz", "reason": "uses hunter22x"}).status_code == 201
    text = client.get("/guard/export.md", params={"project_id": pid}).text
    assert "hunter22x" not in text and "uses [redacted]" in text
