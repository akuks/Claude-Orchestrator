"""Forge policy Phase 2 final fix wave: the skill-pack clone is restricted to
https/ssh (item 13). Other items live next to the code they test
(test_guard_grants, test_redaction, guard/test_co_hook, guard/test_rules,
test_guard_policy_inputs)."""

import asyncio
import shutil
import subprocess

import pytest
from fastapi import HTTPException

from app.routers import skills


def test_git_clone_ends_options_and_allows_only_https_and_ssh(monkeypatch, tmp_path):
    seen = {}

    class Proc:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_exec(*args, **kw):
        seen["args"], seen["env"] = args, kw.get("env")
        return Proc()

    monkeypatch.setenv("GIT_DIR", "/elsewhere/.git")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.sshCommand=evil'")
    monkeypatch.setattr(skills.asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(skills._git_clone("--upload-pack=touch /tmp/x", tmp_path / "d"))
    args, env = seen["args"], seen["env"]
    assert args[0] == "git" and "clone" in args
    i = args.index("--")
    assert args[i + 1] == "--upload-pack=touch /tmp/x" and args[i + 2] == str(tmp_path / "d")
    assert env["GIT_ALLOW_PROTOCOL"] == "https:ssh" and env["GIT_TERMINAL_PROMPT"] == "0"
    assert "GIT_DIR" not in env and "GIT_CONFIG_PARAMETERS" not in env


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
@pytest.mark.parametrize("scheme", ["file", "ext"])
def test_git_clone_refuses_other_protocols(tmp_path, scheme):
    src = tmp_path / "src"
    subprocess.run(["git", "init", "-q", str(src)], check=True)
    url = f"file://{src}" if scheme == "file" else f"ext::sh -c touch% {tmp_path}/pwned"
    with pytest.raises(HTTPException) as e:
        asyncio.run(skills._git_clone(url, tmp_path / "dest"))
    assert e.value.status_code == 400
    assert not (tmp_path / "pwned").exists()
