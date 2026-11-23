import asyncio
import os
import stat
import sys
import time
import types

import pytest

from app.config import settings
from app.database import SessionLocal
from app.gates import executor as ex
from app.guard import runtime as guard_runtime


def _run(cmd, tmp_path, timeout_s=20, **kw):
    log = tmp_path / "out.log"
    out = asyncio.run(ex.run_command(cmd, cwd=tmp_path, env=ex.gate_env(types.SimpleNamespace(env={})),
                                     log_path=log, timeout_s=timeout_s, **kw))
    return out, log


def test_pass_and_fail(tmp_path):
    assert _run("true", tmp_path)[0].outcome == "passed"
    o, _ = _run("exit 3", tmp_path)
    assert o.outcome == "failed" and o.exit_code == 3
    assert o.finished_at >= o.started_at and o.started_at.tzinfo is not None


def test_log_mode_and_content(tmp_path):
    o, log = _run("echo hello", tmp_path)
    assert "hello" in log.read_text()
    assert stat.S_IMODE(log.stat().st_mode) == 0o600


def test_log_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "LOG_CAP", 1024)
    o, log = _run(f"{sys.executable} -c \"for i in range(5000): print(i)\"", tmp_path)
    assert o.outcome == "passed" and log.stat().st_size == 1024


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_gone(pid, secs=10):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return not _alive(pid)


def test_timeout_kills_child(tmp_path):
    pidf = tmp_path / "pid"
    o, _ = _run(f"sleep 30 & echo $! > {pidf}; wait", tmp_path, timeout_s=1.5)
    assert o.outcome == "timed_out" and o.exit_code is None
    assert _wait_gone(int(pidf.read_text()))


def test_cancel_kills_group(tmp_path):
    pidf = tmp_path / "pid"

    async def go():
        t = asyncio.ensure_future(ex.run_command(
            f"sleep 30 & echo $! > {pidf}; wait", cwd=tmp_path, env=ex.gate_env(types.SimpleNamespace(env={})),
            log_path=tmp_path / "c.log", timeout_s=60))
        for _ in range(100):
            if pidf.exists() and pidf.read_text().strip():
                break
            await asyncio.sleep(0.1)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t

    asyncio.run(go())
    assert _wait_gone(int(pidf.read_text()))


def test_on_spawn(tmp_path):
    seen = []
    _run("true", tmp_path, on_spawn=seen.append)
    assert len(seen) == 1 and seen[0].pid > 0


def test_gate_env(monkeypatch):
    monkeypatch.setenv("CO_SECRET", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "server")
    proj = types.SimpleNamespace(env={"ANTHROPIC_API_KEY": "k", "CO_X": "1", "FOO": "bar", "PATH_PREPEND": "/opt/x"})
    env = ex.gate_env(proj)
    assert "ANTHROPIC_API_KEY" not in env and not [k for k in env if k.startswith("CO_")]
    assert env["FOO"] == "bar" and env["CI"] == "1" and env["PATH"].startswith("/opt/x")


def test_shell_path_ignores_SHELL(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/true")
    assert ex.shell_path() in ("/bin/bash", "/bin/sh")


def _proj(tmp_path):
    return types.SimpleNamespace(directory=str(tmp_path), trust="own", quarantine=False, on_no_answer="deny",
                                 guard_enabled=False, approval_wait_seconds=300, id="p1")


def test_guard_verdict(client, tmp_path):
    async def go(cmd):
        async with SessionLocal() as s:
            return await ex.guard_verdict(s, _proj(tmp_path), cmd)

    assert asyncio.run(go("rm -rf /")).decision == "deny"
    assert asyncio.run(go("pytest -q")).decision == "allow"


def test_build_policy_protects_gates(tmp_path):
    p = guard_runtime.build_policy(task_id="T", project=None, workspace=tmp_path, cwd=tmp_path,
                                   claude_config_dir=None, mcp_tools=None)
    assert str((settings.workspaces_dir / "_gates").resolve()) in p["protected_paths"]


def test_log_file_and_prune(tmp_path):
    for n in range(8):
        ex.log_file("r-test", 0, n).write_text("x")
    d = ex.log_file("r-test", 0, 99).parent
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    ex.prune_logs("r-test", 0)
    assert sorted(p.name for p in d.glob("stage0-*.log")) == [f"stage0-{n}.log" for n in range(3, 8)]
    with pytest.raises(ValueError):
        ex.log_file("../x", 0, 0)


def test_gate_env_isolates_home(monkeypatch):
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/x")
    proj = types.SimpleNamespace(env={"HOME": "/root", "GIT_CONFIG_GLOBAL": "/x", "GIT_ASKPASS": "a",
                                      "SSH_ASKPASS": "b", "XDG_CONFIG_HOME": "/y"})
    env = ex.gate_env(proj)
    home = (settings.workspaces_dir / "_gates" / "_home")
    assert env["HOME"] == str(home) and stat.S_IMODE(home.stat().st_mode) == 0o700
    assert env["GIT_CONFIG_NOSYSTEM"] == "1" and env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["XDG_CONFIG_HOME"].startswith(str(home)) and env["XDG_CACHE_HOME"].startswith(str(home))
    assert "SSH_AUTH_SOCK" not in env and not [k for k in env if k.endswith("ASKPASS")]


def test_cancel_during_drain(tmp_path, monkeypatch):
    # a background child keeps the pipe open after the shell exits, so the drain window is long
    pidf = tmp_path / "pid"

    async def go():
        t = asyncio.ensure_future(ex.run_command(
            f"(sleep 30 & echo $! > {pidf}); true", cwd=tmp_path,
            env=ex.gate_env(types.SimpleNamespace(env={})), log_path=tmp_path / "d.log", timeout_s=60))
        for _ in range(100):
            if pidf.exists() and pidf.read_text().strip():
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.5)  # shell has exited; pump is draining
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t

    asyncio.run(go())
    assert _wait_gone(int(pidf.read_text()))


def test_write_error_keeps_draining(tmp_path, monkeypatch):
    def boom(fd, data):
        raise OSError(28, "no space")

    monkeypatch.setattr(ex.os, "write", boom)
    o, _ = _run(f"{sys.executable} -c \"for i in range(20000): print('x'*50)\"", tmp_path)
    assert o.outcome == "passed"
