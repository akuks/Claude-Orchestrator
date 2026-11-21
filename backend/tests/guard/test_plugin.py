"""ensure_plugin: layout, hooks.json in the spike-proven format, read-only
perms, content-hashed idempotence and atomic swap."""

import json
import os
import shlex
import stat
import sys

from app.guard import co_hook, plugin as plugin_mod
from app.guard.plugin import ensure_plugin

FILES = (".claude-plugin/plugin.json", "hooks/hooks.json", "hooks/co_hook.py", "lib/rules.py", "lib/shell.py",
         "lib/dbrules.py", "lib/custom.py")


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
                          "SubagentStop", "ConfigChange"}
    pre = hooks["PreToolUse"][0]
    assert pre["matcher"] == ".*"  # Ruling R17: every tool
    assert hooks["ConfigChange"][0]["matcher"] == "user_settings|project_settings|local_settings"
    h = pre["hooks"][0]
    assert h["type"] == "command" and h["timeout"] == 900 + 30
    # spike format: a string command, "${CLAUDE_PLUGIN_ROOT}" expanded by Claude Code
    assert h["command"] == (f'{shlex.quote(sys.executable)} -I -S "${{CLAUDE_PLUGIN_ROOT}}/hooks/co_hook.py" '
                            "PreToolUse")
    for ev, entries in hooks.items():
        cmd = entries[0]["hooks"][0]["command"]
        assert cmd.endswith(f" {ev}") and " -I -S " in cmd
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
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(["co-guard", real.name, ".co-guard.lock"])


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



def test_extra_files_and_wrong_dir_modes_are_repaired(tmp_path):
    root = ensure_plugin(tmp_path / "co-guard")
    real = root.resolve()
    os.chmod(real, 0o755)
    os.chmod(real / "hooks", 0o755)
    (real / "hooks" / "extra.py").write_text("print('planted')")
    os.chmod(real / "hooks", 0o555)
    os.chmod(real, 0o555)
    ensure_plugin(root)
    assert not (root / "hooks" / "extra.py").exists()
    os.chmod(root.resolve() / "lib", 0o755)  # a writable subdir alone is enough to rebuild
    ensure_plugin(root)
    assert _mode(root.resolve() / "lib") == 0o555


def test_planted_symlink_is_repaired(tmp_path):
    root = ensure_plugin(tmp_path / "co-guard")
    real = root.resolve()
    os.chmod(real / "lib", 0o755)
    (real / "lib" / "rules.py").chmod(0o644)
    (real / "lib" / "rules.py").unlink()
    (real / "lib" / "rules.py").symlink_to(tmp_path / "evil.py")
    (tmp_path / "evil.py").write_text("x")
    os.chmod(real / "lib", 0o555)
    ensure_plugin(root)
    assert not (root.resolve() / "lib" / "rules.py").is_symlink()


def test_interpreter_must_be_an_absolute_file(monkeypatch, tmp_path):
    import pytest

    monkeypatch.setattr(plugin_mod.sys, "executable", "python3")
    with pytest.raises(RuntimeError):
        ensure_plugin(tmp_path / "co-guard")
    monkeypatch.setattr(plugin_mod.sys, "executable", str(tmp_path / "missing"))
    with pytest.raises(RuntimeError):
        ensure_plugin(tmp_path / "co-guard")


def test_concurrent_calls_are_safe(tmp_path):
    import threading

    errs = []

    def go():
        try:
            ensure_plugin(tmp_path / "co-guard")
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and (tmp_path / "co-guard" / "hooks" / "co_hook.py").is_file()


def test_per_task_calls_keep_older_versions(tmp_path, monkeypatch):
    """Minor 1 (round 2): only the startup call prunes; a running task may use an older version."""
    root = ensure_plugin(tmp_path / "co-guard")
    old = root.resolve()
    orig = plugin_mod.plugin_files

    def changed():
        files = orig()
        files["hooks/co_hook.py"] += b"\n# v3\n"
        return files

    monkeypatch.setattr(plugin_mod, "plugin_files", changed)
    ensure_plugin(root, prune=False)
    assert root.resolve() != old and old.exists()
    ensure_plugin(root)  # startup
    assert not old.exists()


# ---- final fix wave 6: precompiled lib, no network imports off the approval path ----------


def test_lib_is_precompiled_for_the_server_interpreter(tmp_path):
    import importlib.util

    root = ensure_plugin(tmp_path / "co-guard")
    real = root.resolve()
    for name in ("rules", "shell", "dbrules", "custom"):
        pyc = real / "lib" / "__pycache__" / f"{name}.{sys.implementation.cache_tag}.pyc"
        assert pyc.is_file() and _mode(pyc) == 0o444
        assert importlib.util.cache_from_source(str(real / "lib" / f"{name}.py")) == str(pyc)
        data = pyc.read_bytes()
        assert data[:4] == importlib.util.MAGIC_NUMBER
        assert int.from_bytes(data[4:8], "little") == 0b11  # hash-based, checked against the source
        assert data[8:16] == importlib.util.source_hash((real / "lib" / f"{name}.py").read_bytes())
    files = plugin_mod.plugin_files()
    assert f"lib/__pycache__/rules.{sys.implementation.cache_tag}.pyc" in files  # part of the content hash
    assert plugin_mod.plugin_files() == files  # deterministic


def test_hook_uses_the_cache_and_skips_network_imports(tmp_path):
    """The hook loads lib/*.py from the shipped .pyc (nothing compiled at run
    time) and imports urllib only when it must talk to the approval API."""
    import subprocess

    root = ensure_plugin(tmp_path / "co-guard")
    tdir = tmp_path / "_tasks" / "T1"
    tdir.mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    (tdir / "policy.json").write_text(json.dumps({
        "task_id": "T1", "api_base": "http://127.0.0.1:9", "project_dir": str(proj), "cwd": str(proj),
        "home": str(tmp_path / "home"), "protected_paths": [str(root), str(tdir)]}))
    probe = (
        "import runpy, sys, json\n"
        "sys.argv = ['co_hook.py', sys.argv[1]]\n"
        "try:\n"
        "    runpy.run_path(%r, run_name='__main__')\n"
        "except SystemExit:\n"
        "    pass\n"
        "sys.stderr.write(json.dumps({'urllib': 'urllib.request' in sys.modules}) + '\\n')\n"
    ) % str(root / "hooks" / "co_hook.py")
    env = {"PATH": os.environ.get("PATH", ""), "CO_POLICY_FILE": str(tdir / "policy.json"),
           "CO_TASK_DIR": str(tdir), "CO_TASK_ID": "T1"}
    for event, payload in (
        ("PreToolUse", {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"},
                        "tool_use_id": "tu1"}),
        ("PostToolUse", {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_use_id": "tu1"}),
    ):
        p = subprocess.run([sys.executable, "-I", "-S", "-c", probe, event],
                           input=json.dumps(payload), capture_output=True, text=True, timeout=30, env=env,
                           cwd=str(proj))
        info = json.loads(p.stderr.strip().splitlines()[-1])
        assert info["urllib"] is False, (event, p.stderr[-2000:])
    # no write of a fresh .pyc was even attempted: the shipped one was valid
    assert sorted(os.listdir(root.resolve() / "lib" / "__pycache__")) == sorted(
        f"{n}.{sys.implementation.cache_tag}.pyc" for n in ("rules", "shell", "dbrules", "custom"))
