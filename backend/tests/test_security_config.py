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
