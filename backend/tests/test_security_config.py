"""Phase 7e task 3 (spec §7): scanner detection in the review prompt and the unsafe Claude-settings scan."""

import json
import os

from app import security
from app.security_config import claude_config_findings

TOKEN = "ghp_" + "a1B2c3D4e5" * 4


def _write(root, rel, data):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data if isinstance(data, str) else json.dumps(data))


def test_findings_for_hooks_wide_allow_mcp_and_token(tmp_path):
    _write(tmp_path, ".claude/settings.json", {
        "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "x"}]}]},
        "permissions": {"allow": ["Bash(*)", "Read"]},
        "env": {"GH": TOKEN},
    })
    _write(tmp_path, ".mcp.json", {"mcpServers": {"fs": {"command": "x"}}})
    got = claude_config_findings(str(tmp_path))
    titles = [f["title"] for f in got]
    assert all(t.startswith("Claude settings: ") for t in titles)
    assert len(got) == 4
    blob = json.dumps(got)
    assert TOKEN not in blob and "env.GH" in blob


def test_clean_missing_big_bad_and_linked_files_give_nothing(tmp_path):
    assert claude_config_findings(str(tmp_path)) == []
    assert claude_config_findings(None) == [] and claude_config_findings("/nonexistent-x") == []
    _write(tmp_path, ".claude/settings.json", "{not json")
    _write(tmp_path, ".claude/settings.local.json", '{"hooks": {"A": []}, "x": "' + "a" * 300000 + '"}')
    real = tmp_path / "real.json"
    real.write_text(json.dumps({"permissions": {"allow": ["*"]}}))
    os.symlink(real, tmp_path / ".mcp.json")
    assert claude_config_findings(str(tmp_path)) == []


def test_linked_claude_folder_is_not_followed(tmp_path):
    out = tmp_path / "out"
    _write(out, "settings.json", {"permissions": {"allow": ["*"]}})
    proj = tmp_path / "proj"
    proj.mkdir()
    os.symlink(out, proj / ".claude")
    assert claude_config_findings(str(proj)) == []


def test_prompt_lists_scanners_and_rule(tmp_path, monkeypatch):
    monkeypatch.setattr(security.shutil, "which", lambda n: "/bin/" + n if n in ("semgrep", "npm") else None)
    _write(tmp_path, "package-lock.json", "{}")
    _write(tmp_path, ".claude/settings.json", {"permissions": {"allow": ["Bash"]}})
    p = security.build_security_prompt("b", "main", "changed", str(tmp_path))
    assert "semgrep, npm audit" in p
    assert "If none could run, say 'Not scanned' — never call the code clean." in p
    assert "If none, say so" not in p
    assert "Claude settings: wide permission" in p


def test_prompt_with_no_scanners_still_has_the_rule(monkeypatch):
    monkeypatch.setattr(security.shutil, "which", lambda n: None)
    p = security.build_security_prompt("b")
    assert "found on this machine: none" in p and "'Not scanned'" in p


def test_names_are_masked_and_cut(tmp_path):
    long_name = "x" * 200
    _write(tmp_path, ".claude/settings.json", {"hooks": {TOKEN: [], long_name: []}, "permissions": {"allow": ["*"]}})
    _write(tmp_path, ".mcp.json", {"mcpServers": {TOKEN: {}}})
    got = claude_config_findings(str(tmp_path))
    blob = json.dumps(got)
    assert TOKEN not in blob and "x" * 61 not in blob and got


def _run_extract(tmp_path, monkeypatch, env):
    import asyncio
    import uuid

    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import Finding, Project, Task

    _write(tmp_path, ".claude/settings.json", {"hooks": {"Stop": []}})
    pid, tid = uuid.uuid4().hex, uuid.uuid4().hex

    async def go():
        async with SessionLocal() as s:
            s.add(Project(id=pid, name="sec-" + pid[:6], slug="sec-" + pid[:6], directory=str(tmp_path)))
            s.add(Task(id=tid, title="Security review (full)", prompt="p", project_id=pid, root_id=tid,
                       status="completed", tags=["vapt"], result_text="## Summary\nNo issues."))
            s.add(Finding(project_id=pid, fingerprint="old-" + pid, severity="high", title="old one", status="open"))
            await s.commit()

        async def fake_env(*a, **k):
            return env

        async def fake_json(*a, **k):
            return {"findings": []}

        monkeypatch.setattr(security, "agent_env_or_none", fake_env)
        monkeypatch.setattr(security, "_extract_json", fake_json)
        await security.extract_findings(tid, None)
        async with SessionLocal() as s:
            rows = (await s.execute(select(Finding).where(Finding.project_id == pid))).scalars().all()
            return {f.title: f.status for f in rows}

    return asyncio.run(go())


def test_full_scan_without_a_credential_fixes_nothing(client, tmp_path, monkeypatch):
    got = _run_extract(tmp_path, monkeypatch, None)
    assert got["old one"] == "open"
    assert got["Claude settings: hooks in .claude/settings.json"] == "open"


def test_full_scan_with_a_credential_still_fixes_what_vanished(client, tmp_path, monkeypatch):
    assert _run_extract(tmp_path, monkeypatch, {"PATH": "/bin"})["old one"] == "fixed"
