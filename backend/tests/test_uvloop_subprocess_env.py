"""uvicorn runs on uvloop when it's installed (it is, in production), and uvloop's
subprocess transport only accepts a plain dict as `env` — not a dict subclass
like AgentEnv. The test client runs on the stdlib loop, so the suite never saw
this. Every claude spawn site must hand the subprocess a plain dict."""

import asyncio
import inspect

import pytest

uvloop = pytest.importorskip("uvloop")

from app import credentials, memory, security, worker  # noqa: E402
from app.agent_env import AgentEnv  # noqa: E402


def test_uvloop_rejects_dict_subclass_env():
    # Documents the root cause: if this ever starts passing, uvloop relaxed it.
    async def spawn():
        proc = await asyncio.create_subprocess_exec("true", env=AgentEnv(PATH="/usr/bin:/bin"))
        await proc.wait()

    loop = uvloop.new_event_loop()
    try:
        with pytest.raises(TypeError):
            loop.run_until_complete(spawn())
    finally:
        loop.close()


def test_plain_dict_env_works_on_uvloop():
    async def spawn():
        proc = await asyncio.create_subprocess_exec("true", env=dict(AgentEnv(PATH="/usr/bin:/bin")))
        return await proc.wait()

    loop = uvloop.new_event_loop()
    try:
        assert loop.run_until_complete(spawn()) == 0
    finally:
        loop.close()


@pytest.mark.parametrize("module", [worker, memory, security, credentials])
def test_spawn_sites_pass_plain_dict(module):
    src = inspect.getsource(module)
    # Every env= handed to a subprocess must be wrapped in dict(...).
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("env=") and "create_subprocess" not in s:
            assert s.startswith("env=dict("), f"{module.__name__}: {s}"
