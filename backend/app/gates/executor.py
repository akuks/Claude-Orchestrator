"""Server-side gate command executor: fixed shell, clean env, guard check, log file, timeout and kill."""

from __future__ import annotations

import asyncio
import os
import re
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from .. import agent_env, credentials
from ..config import settings
from ..guard import policy_inputs, rules
from ..guard import runtime as guard_runtime
from .test_results import TestResult, parse

LOG_CAP = 20 * 1024 * 1024
PARSE_TAIL = 20 * 1024 * 1024
KILL_GRACE_S = 5.0
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def shell_path() -> str:
    return "/bin/bash" if os.path.exists("/bin/bash") else "/bin/sh"


def gate_env(project) -> dict[str, str]:
    env = credentials.base_env()
    agent_env._apply_project_env(env, dict(getattr(project, "env", None) or {}))
    for k in list(env):
        if (k.startswith("CO_") or k in agent_env.PROJECT_ENV_BLOCKED or k == "SSH_AUTH_SOCK"
                or k.endswith("ASKPASS")):
            del env[k]
    home = _gates_root() / "_home"
    _mkdir_private(_gates_root())
    _mkdir_private(home)
    env.update({
        "CI": "1",
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
    })
    return env


async def guard_verdict(s, project, cmd: str):
    """The guard's verdict on one command. Runs even when the project's guard is off."""
    cwd = Path(project.directory)
    task = SimpleNamespace(id="gate", root_id="gate")
    inputs = await policy_inputs.gather(s, task=task, project=project, owner_id=None, mcp=None)
    policy = guard_runtime.build_policy(task_id="gate", project=project, workspace=cwd, cwd=cwd,
                                        claude_config_dir=None, mcp_tools=None, extra=inputs.policy)
    policy["guard_enabled"] = True
    return rules.evaluate("Bash", {"command": cmd}, policy, str(cwd))


def _gates_root() -> Path:
    return settings.workspaces_dir / "_gates"


def _run_dir(run_id: str) -> Path:
    if not _SAFE_ID.match(str(run_id)) or str(run_id) in (".", ".."):
        raise ValueError("bad run id")
    return _gates_root() / str(run_id)


def _mkdir_private(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(d, 0o700)


def log_file(run_id: str, stage_index: int, n: int) -> Path:
    d = _run_dir(run_id)
    _mkdir_private(_gates_root())
    _mkdir_private(d)
    return d / f"stage{int(stage_index)}-{int(n)}.log"


def prune_logs(run_id: str, stage_index: int, keep: int = 5) -> None:
    d = _run_dir(run_id)
    pre = f"stage{int(stage_index)}-"
    items = []
    for f in d.glob(f"{pre}*.log") if d.is_dir() else []:
        num = f.name[len(pre):-4]
        if num.isdigit():
            items.append((int(num), f))
    items.sort()
    for _, f in items[: max(0, len(items) - keep)]:
        f.unlink(missing_ok=True)


@dataclass
class Outcome:
    outcome: str  # passed | failed | timed_out | cancelled
    exit_code: int | None
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    result: TestResult


async def kill_group(proc) -> None:
    def _sig(sig):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    _sig(signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), KILL_GRACE_S)
    except asyncio.TimeoutError:
        pass
    # SIGKILL the group regardless: the leader may be gone while children linger.
    _sig(signal.SIGKILL)
    try:
        await asyncio.wait_for(proc.wait(), KILL_GRACE_S)
    except asyncio.TimeoutError:
        pass


def _read_tail(path: Path, n: int) -> str:
    with open(path, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        if size > n:
            f.seek(size - n)
        return f.read().decode("utf-8", "replace")


def _write_all(fd: int, data: bytes) -> int:
    done = 0
    while done < len(data):
        n = os.write(fd, data[done:])
        if n <= 0:
            raise OSError("short write")
        done += n
    return done


async def _pump(proc, fd: int, state: dict) -> None:
    """Copy the pipe to the log up to LOG_CAP; always keep draining so the child never blocks."""
    while True:
        chunk = await proc.stdout.read(65536)
        if not chunk:
            return
        room = LOG_CAP - state["written"]
        if room > 0 and not state.get("error"):
            part = chunk[:room]
            try:
                state["written"] += _write_all(fd, part)
            except OSError as e:
                state["error"] = str(e)


async def run_command(cmd, *, cwd, env, log_path, timeout_s, on_spawn=None) -> Outcome:
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    proc = None
    outcome = "failed"
    try:
        os.fchmod(fd, 0o600)
        proc = await asyncio.create_subprocess_exec(
            shell_path(), "-c", cmd, cwd=str(cwd), env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
        if on_spawn:
            on_spawn(proc)
        state: dict = {"written": 0, "error": None}
        pump = asyncio.ensure_future(_pump(proc, fd, state))
        try:
            await asyncio.wait_for(proc.wait(), timeout_s)
            outcome = "passed" if proc.returncode == 0 else "failed"
        except asyncio.TimeoutError:
            outcome = "timed_out"
            await kill_group(proc)
        except asyncio.CancelledError:
            await asyncio.shield(kill_group(proc))
            pump.cancel()
            raise
        # Drain what is left (children holding the pipe are killed on timeout).
        try:
            await asyncio.wait_for(pump, 5.0 if outcome != "timed_out" else 2.0)
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            pump.cancel()
            await asyncio.shield(kill_group(proc))
            raise
        if outcome != "timed_out":
            # leftover background children in the group must not outlive the command
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    finally:
        os.close(fd)
    finished = datetime.now(timezone.utc)
    duration = int((time.monotonic() - t0) * 1000)
    try:
        text = await asyncio.to_thread(_read_tail, Path(log_path), PARSE_TAIL)
        result = await asyncio.to_thread(parse, text)
    except Exception:  # noqa: BLE001 - an unparsable log is "no result"
        result = TestResult(None, None, None, None, None, ())
    code = None if outcome == "timed_out" else proc.returncode
    return Outcome(outcome, code, started, finished, duration, result)
