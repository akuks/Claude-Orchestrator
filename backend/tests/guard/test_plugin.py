"""ensure_plugin: layout, hooks.json in the spike-proven format, read-only
perms, content-hashed idempotence and atomic swap."""

import json
import os
import shlex
import stat
import sys

from app.guard import co_hook, plugin as plugin_mod
from app.guard.plugin import ensure_plugin

FILES = (".claude-plugin/plugin.json", "hooks/hooks.json", "hooks/co_hook.py", "lib/rules.py", "lib/shell.py")


def _mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


def _tree(root):
    out = {}
    for d, dirs, files in os.walk(root):
        for n in dirs + files:
            p = os.path.join(d, n)
            out[os.path.relpath(p, root)] = _mode(p)
    return out


def test_layout_and_hooks_json(tmp_path):
    root = ensure_plugin(tmp_path / "_system" / "co-guard")
    assert root == tmp_path / "_system" / "co-guard"
    for f in FILES:
        assert (root / f).is_file(), f
    assert json.loads((root / ".claude-plugin/plugin.json").read_text())["name"] == "co-guard"
    hooks = json.loads((root / "hooks/hooks.json").read_text())["hooks"]
    assert set(hooks) == {"PreToolUse", "PostToolUse", "SessionStart", "Stop", "SessionEnd", "SubagentStart",
                          "SubagentStop"}
    pre = hooks["PreToolUse"][0]
    assert pre["matcher"] == "Bash|Edit|Write|NotebookEdit|MultiEdit|mcp__.*"
    h = pre["hooks"][0]
    assert h["type"] == "command" and h["timeout"] == 900 + 30
    # spike format: a string command, "${CLAUDE_PLUGIN_ROOT}" expanded by Claude Code
    assert h["command"] == f'{shlex.quote(sys.executable)} -I "${{CLAUDE_PLUGIN_ROOT}}/hooks/co_hook.py" PreToolUse'
    for ev, entries in hooks.items():
        cmd = entries[0]["hooks"][0]["command"]
        assert cmd.endswith(f" {ev}") and " -I " in cmd
        if ev != "PreToolUse":
            assert entries[0]["hooks"][0]["timeout"] < co_hook.HOOK_TIMEOUT
    assert "matcher" not in hooks["SessionStart"][0]
    assert (root / "lib/rules.py").read_bytes() == (plugin_mod._SRC / "rules.py").read_bytes()


def test_read_only_perms(tmp_path):
    root = ensure_plugin(tmp_path / "co-guard")
    real = root.resolve()
    assert _mode(real) == 0o555
    for rel, mode in _tree(real).items():
        want = 0o555 if (real / rel).is_dir() else 0o444
        assert mode == want, rel


def test_idempotent(tmp_path):
    root = ensure_plugin(tmp_path / "co-guard")
    real = root.resolve()
    before = {p: os.stat(real / p).st_mtime_ns for p in FILES}
    link_ino = os.lstat(root).st_ino
    assert ensure_plugin(tmp_path / "co-guard") == root
    assert root.resolve() == real
    assert {p: os.stat(real / p).st_mtime_ns for p in FILES} == before
    assert os.lstat(root).st_ino == link_ino  # the symlink was not even replaced
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(["co-guard", real.name])


def test_content_change_swaps_atomically(tmp_path, monkeypatch):
    root = ensure_plugin(tmp_path / "co-guard")
    old = root.resolve()
    orig = plugin_mod.plugin_files

    def changed():
        files = orig()
        files["hooks/co_hook.py"] += b"\n# v2\n"
        return files

    swaps = []
    real_replace = os.replace
    monkeypatch.setattr(plugin_mod, "plugin_files", changed)
    monkeypatch.setattr(plugin_mod.os, "replace", lambda a, b: (swaps.append((a, b)), real_replace(a, b)))
    ensure_plugin(root)
    new = root.resolve()
    assert new != old and new.parent == old.parent
    assert (root / "hooks/co_hook.py").read_bytes().endswith(b"# v2\n")
    assert root.is_symlink() and len(swaps) == 1 and swaps[0][1] == root  # one atomic rename of the link
    assert not old.exists()  # the old version is cleaned up
    assert _mode(new) == 0o555 and _mode(new / "hooks/co_hook.py") == 0o444


def test_tampered_version_is_rewritten(tmp_path):
    root = ensure_plugin(tmp_path / "co-guard")
    f = root.resolve() / "hooks" / "co_hook.py"
    os.chmod(f.parent, 0o755)
    os.chmod(f, 0o644)
    f.write_text("print('evil')")
    ensure_plugin(root)
    assert b"evil" not in (root / "hooks/co_hook.py").read_bytes()
    assert _mode(root / "hooks/co_hook.py") == 0o444


def test_old_real_directory_layout_is_replaced(tmp_path):
    legacy = tmp_path / "co-guard"
    (legacy / "hooks").mkdir(parents=True)
    (legacy / "hooks" / "x").write_text("old")
    os.chmod(legacy / "hooks", 0o555)
    root = ensure_plugin(legacy)
    assert root.is_symlink() and (root / "hooks/co_hook.py").is_file()
    assert not (root / "hooks" / "x").exists()


def test_leftover_temp_dirs_are_removed(tmp_path):
    ensure_plugin(tmp_path / "co-guard")
    junk = tmp_path / ".co-guard-tmp-deadbeef"
    junk.mkdir()
    os.chmod(junk, 0o555)
    ensure_plugin(tmp_path / "co-guard")
    assert not junk.exists()


def test_plugin_lib_runs_standalone_isolated(tmp_path):
    """The copied guard library imports nothing from the app (Ruling R1)."""
    import subprocess

    root = ensure_plugin(tmp_path / "co-guard")
    req = {"tool_name": "Bash", "tool_input": {"command": "rm -rf ~"}, "policy": {"home": str(tmp_path)},
           "cwd": str(tmp_path)}
    p = subprocess.run([sys.executable, "-I", str(root / "lib" / "rules.py")], input=json.dumps(req),
                       capture_output=True, text=True, cwd=str(tmp_path), timeout=30)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["decision"] == "deny"
