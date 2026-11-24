"""The system health checks (admin "System health" page).

A check is ``Check(id, title, fn)`` where ``fn(s)`` is an async function that
returns a ``Result``; ``s`` is a session factory (``SessionLocal``): every DB
check opens its own session, because the checks run concurrently. A check never
runs ``claude`` with a prompt (only ``--version``), never uses a shell
(argv only) and never writes anything but a temp file it removes again. The
runner (``runner.py``) scrubs every string and turns an exception or a
timeout into "could not check", so a check can simply raise.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy import select

from ..config import settings
from ..guard import plugin as guard_plugin
from ..guard import runtime as guard_runtime

OK, ATTENTION, PROBLEM = "ok", "attention", "problem"

GB = 1024 ** 3
DISK_ATTENTION_BYTES = 5 * GB
DISK_PROBLEM_BYTES = 1 * GB
SELFTEST_SLOW_MS = 2000
SELFTEST_CALL_TIMEOUT = 4  # seconds per hook call (two calls fit the 10 s check budget)
VERSION_TIMEOUT = 8  # seconds for a `--version` style probe (the check budget is 10)
GUARD_ERROR_DAYS = 7


@dataclass
class Result:
    status: str
    summary: str
    detail: dict | None = None
    fix: str | None = None


@dataclass
class Check:
    id: str
    title: str
    fn: Callable[..., Awaitable[Result]] = field(repr=False)


# -- probes (the seams the tests replace) -------------------------------------
async def run_argv(argv: list[str], timeout: float = VERSION_TIMEOUT) -> tuple[int | None, str]:
    """Run ``argv`` (never a shell) with stdin /dev/null; (return code, first
    line of stdout+stderr). (None, "not found") when the program is missing,
    (None, "timed out") when it hangs (it is killed)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT)
    except (FileNotFoundError, PermissionError):
        return None, "not found"
    except OSError as e:
        return None, f"could not start ({type(e).__name__})"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        return None, "timed out"
    finally:
        if proc.returncode is None:  # a timeout, or the runner cancelled this check
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
    text = out.decode(errors="replace").strip()
    return proc.returncode, (text.splitlines()[0] if text else "")[:200]


def disk_free_bytes(path: Path) -> int:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free


def _can_write(directory: Path) -> bool:
    """Create and delete a temp file in ``directory`` (or, when it does not exist
    yet, in its nearest existing parent: the app creates it when it needs it)."""
    d = Path(directory)
    while not d.exists() and d != d.parent:
        d = d.parent
    try:
        fd, name = tempfile.mkstemp(prefix=".co-health-", dir=d)
        os.close(fd)
        os.unlink(name)
        return True
    except OSError:
        return False


def _first_version(line: str) -> str:
    return line.strip()[:120]


# -- tool checks --------------------------------------------------------------
async def _tool(argv: list[str], *, name: str, missing_status: str, fix: str) -> Result:
    code, line = await run_argv(argv)
    if code is None:
        why = "is not installed" if line == "not found" else "did not answer" if line == "timed out" else "could not run"
        return Result(missing_status, f"{name} {why}", {"answer": line}, fix)
    if code != 0:
        return Result(missing_status, f"{name} is installed but did not answer properly",
                      {"exit_code": code, "answer": _first_version(line)}, fix)
    return Result(OK, f"{name} is ready", {"version": _first_version(line)})


async def check_claude(s) -> Result:
    # ONLY `--version`: never a prompt
    return await _tool([settings.claude_bin, "--version"], name="Claude", missing_status=PROBLEM,
                       fix="Install Claude Code on this machine, or point the app at it with CO_CLAUDE_BIN.")


async def check_python(s) -> Result:
    return Result(OK, "Python is ready", {"version": platform.python_version(), "implementation": platform.python_implementation()})


async def check_git(s) -> Result:
    return await _tool(["git", "--version"], name="Git", missing_status=PROBLEM,
                       fix="Install git on this machine; tasks and projects need it.")


async def check_node(s) -> Result:
    return await _tool(["node", "--version"], name="Node", missing_status=ATTENTION,
                       fix="Install Node if your projects or tools need it.")


async def check_docker(s) -> Result:
    code, line = await run_argv(["docker", "version", "--format", "{{.Server.Version}}"])
    if code is None and line == "not found":
        return Result(ATTENTION, "Docker is not installed (only needed for container checks)", {"answer": line},
                      "Install Docker if a workflow waits for a container to be ready.")
    if code != 0:
        return Result(ATTENTION, "Docker is installed but not running", {"exit_code": code, "answer": _first_version(line)},
                      "Start Docker if a workflow waits for a container to be ready.")
    return Result(OK, "Docker is ready", {"server_version": _first_version(line)})


# -- the guard ----------------------------------------------------------------
def _guard_intact() -> tuple[bool, str]:
    root = guard_runtime.plugin_root()
    files = guard_plugin.plugin_files()
    want = f".{root.name}-{guard_plugin.content_hash(files)[:20]}"
    if not root.is_symlink():
        return False, "the guard is not installed"
    if os.readlink(root) != want:
        return False, "the guard does not match this version of the app"
    if not guard_plugin._matches(root.resolve(), files):  # noqa: SLF001 - verify without rewriting
        return False, "a guard file was changed"
    return True, want


async def check_guard_files(s) -> Result:
    ok, why = await asyncio.to_thread(_guard_intact)
    if ok:
        return Result(OK, "The guard files are intact", {"content": why.rsplit("-", 1)[-1]})
    return Result(PROBLEM, f"The guard is not intact: {why}", None, "Restart the app to restore it.")


_HARMLESS = {"tool_name": "Bash", "tool_input": {"command": "echo hello"}}
_BLOCKED = {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}


def _hook_call(argv, env, payload) -> tuple[str, int]:
    """One hook call: ("allow" | "deny" | "unexpected", milliseconds)."""
    started = time.monotonic()
    try:
        p = subprocess.run(argv, input=json.dumps(payload), capture_output=True, text=True, env=env,
                           timeout=SELFTEST_CALL_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "unexpected", int((time.monotonic() - started) * 1000)
    ms = int((time.monotonic() - started) * 1000)
    if p.returncode != 0:
        return "unexpected", ms
    out = p.stdout.strip()
    if not out:
        return "allow", ms
    try:
        decision = (json.loads(out).get("hookSpecificOutput") or {}).get("permissionDecision")
    except (ValueError, AttributeError):
        return "unexpected", ms
    return ("deny" if decision == "deny" else "allow" if decision in (None, "allow") else "unexpected"), ms


def _selftest() -> Result:
    root = guard_runtime.plugin_root()
    hook = root.resolve() / "hooks" / "co_hook.py"
    if not hook.is_file():
        return Result(PROBLEM, "The guard is not installed, so it could not be tested", None,
                      "Restart the app to restore it.")
    task_id = f"health-{uuid.uuid4().hex[:12]}"
    tmp = Path(tempfile.mkdtemp(prefix="co-health-")).resolve()
    try:
        try:
            tdir = guard_runtime.make_task_dir(task_id)
            policy = guard_runtime.build_policy(task_id=task_id, project=None, workspace=tmp, cwd=tmp,
                                                claude_config_dir=None, mcp_tools=None)
        except guard_runtime.GuardConfigError as e:
            return Result(PROBLEM, "The guard cannot be set up: its approval address is not usable",
                          {"error": str(e)}, "Set CO_BIND_HOST to a literal IP address and restart the app.")
        policy["api_base"] = "http://127.0.0.1:9"  # a test must never report to (or wait on) the real server
        policy_file = guard_runtime.write_policy(task_id, policy)
        env = {"PATH": os.environ.get("PATH", ""), "HOME": guard_runtime.real_home(),
               "CO_POLICY_FILE": str(policy_file), "CO_TASK_DIR": str(tdir), "CLAUDE_PLUGIN_ROOT": str(root.resolve())}
        argv = [sys.executable, "-I", "-S", str(hook), "PreToolUse"]  # as hooks.json runs it
        base = {"hook_event_name": "PreToolUse", "cwd": str(tmp)}
        allow, allow_ms = _hook_call(argv, env, {**base, **_HARMLESS, "tool_use_id": "health-1"})
        deny, deny_ms = _hook_call(argv, env, {**base, **_BLOCKED, "tool_use_id": "health-2"})
    finally:
        guard_runtime.remove_task_dir(task_id)
        shutil.rmtree(tmp, ignore_errors=True)
    detail = {"harmless_call": allow, "harmless_ms": allow_ms, "dangerous_call": deny, "dangerous_ms": deny_ms}
    if allow != "allow" or deny != "deny":
        what = ("a dangerous command was not blocked" if deny != "deny" and allow == "allow"
                else "a harmless command was refused" if allow != "allow" and deny == "deny"
                else "the guard gave wrong answers")
        return Result(PROBLEM, f"The guard is not working: {what}", detail,
                      "Restart the app to restore the guard; if this stays, do not run tasks.")
    if max(allow_ms, deny_ms) > SELFTEST_SLOW_MS:
        return Result(ATTENTION, "The guard works but is slow to answer", detail,
                      "The machine may be overloaded; slow checks make tasks slower.")
    return Result(OK, "The guard allowed a harmless command and blocked a dangerous one", detail)


async def check_guard_selftest(s) -> Result:
    return await asyncio.to_thread(_selftest)


async def check_guard_errors(s) -> Result:
    from ..models import TaskEvent

    since = datetime.now(timezone.utc) - timedelta(days=GUARD_ERROR_DAYS)
    async with s() as sess:
        rows = (await sess.execute(
            select(TaskEvent.payload, TaskEvent.created_at)
            .where(TaskEvent.type == "guard", TaskEvent.created_at >= since)
            .order_by(TaskEvent.id.desc()).limit(2000))).all()
    errors = [p for p, _ in rows if isinstance(p, dict) and p.get("event") == "guard_error"]
    if not errors:
        return Result(OK, "No guard errors in the last 7 days", {"count": 0})
    latest = errors[0]
    msg = f"{latest.get('hook_event') or '?'}: {latest.get('error') or 'unknown error'}"
    n = len(errors)
    return Result(ATTENTION, f"{n} guard error{'s' if n != 1 else ''} in the last 7 days",
                  {"count": n, "latest": msg}, "Open the failed tasks' run logs and look at the guard lines.")


# -- the app itself -----------------------------------------------------------
def _folders() -> list[tuple[str, Path]]:
    ws = Path(settings.workspaces_dir)
    return [("workspaces", ws), ("projects", Path(settings.projects_dir)),
            ("gate logs", ws / "_gates"), ("user folders", ws / "_users")]


async def check_folders(s) -> Result:
    state = {name: await asyncio.to_thread(_can_write, d) for name, d in _folders()}
    bad = [n for n, ok in state.items() if not ok]
    if bad:
        return Result(PROBLEM, f"The app cannot write to: {', '.join(bad)}", {"writable": state},
                      "Give the user the app runs as write access to those folders.")
    return Result(OK, "The app can write to all of its folders", {"writable": state})


def _head_revision() -> str | None:
    from alembic.script import ScriptDirectory

    from ..database import _alembic_cfg

    return ScriptDirectory.from_config(_alembic_cfg(settings.database_url)).get_current_head()


async def check_database(s) -> Result:
    from sqlalchemy import text

    async with s() as sess:
        current = (await sess.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    head = await asyncio.to_thread(_head_revision)
    detail = {"current": current, "latest": head}
    if current == head:
        return Result(OK, "The database is up to date", detail)
    return Result(PROBLEM, "The database needs updating", detail, "Restart the app; it updates the database on start.")


async def check_mcp(s) -> Result:
    from ..models import McpServer

    async with s() as sess:
        servers = (await sess.execute(select(McpServer).where(McpServer.enabled.is_(True)).order_by(McpServer.name))).scalars().all()
    if not servers:
        return Result(OK, "No tools are connected", {"servers": [], "start_in_project": 0})
    listing = [{"name": m.name, "status": m.status} for m in servers]
    # an admin's choice, so never attention by itself; the summary names it
    in_project = sum(1 for m in servers if m.transport == "stdio" and getattr(m, "start_in_project", False))
    detail = {"servers": listing, "start_in_project": in_project}
    note = f"; {in_project} start(s) in the project folder" if in_project else ""
    bad = [m.name for m in servers if m.status not in ("healthy", "unknown")]
    if bad:
        return Result(ATTENTION, f"{len(bad)} of {len(servers)} connected tool{'s' if len(servers) != 1 else ''} not responding: {', '.join(bad)}{note}",
                      detail, "Open MCP Servers and test the connection.")
    return Result(OK, f"{len(servers)} connected tool{'s' if len(servers) != 1 else ''} working{note}", detail)


async def check_skill_packs(s) -> Result:
    from ..models import SkillPack

    async with s() as sess:
        packs = (await sess.execute(select(SkillPack).order_by(SkillPack.name))).scalars().all()
    if not packs:
        return Result(OK, "No skill packs installed", {"packs": []})
    from ..skillpacks import update as pack_update

    listing, updates = [], 0
    for p in packs:
        latest = getattr(p, "latest_sha", None)  # filled by the update check (migration 0017)
        current = await asyncio.to_thread(pack_update.current_sha_fast, p.local_path)
        has_update = bool(current and latest and current != latest)
        updates += has_update
        listing.append({"name": p.name, "pinned": current[:7] if current else None, "version": p.version,
                        "update_known": has_update})
    summary = f"{len(packs)} skill pack{'s' if len(packs) != 1 else ''}" + (f", {updates} with an update" if updates else "")
    return Result(OK, summary, {"packs": listing})


async def check_code_map(s) -> Result:
    """Which projects keep a code map, and whether any refresh is failing. Reads the database only."""
    from ..codemap.status import code_map_server
    from ..models import McpServer, Project, ProjectIndex

    async with s() as sess:
        servers = (await sess.execute(select(McpServer).where(McpServer.enabled.is_(True)))).scalars().all()
        rows = (await sess.execute(
            select(Project.name, ProjectIndex.status)
            .select_from(Project).outerjoin(ProjectIndex, ProjectIndex.project_id == Project.id)
            .where(Project.code_map.is_(True)).order_by(Project.name))).all()
    have_tool = code_map_server(servers) is not None
    failing = [n for n, st in rows if st == "failed"]
    listing = [{"project": n, "status": st or "never"} for n, st in rows]
    detail = {"projects": listing, "tool_added": have_tool}
    n = len(rows)
    if failing:
        k = len(failing)
        return Result(ATTENTION, f"{k} project{chr(39)}s code map is failing" if k == 1 else f"{k} projects{chr(39)} code maps are failing",
                      detail, "Open the project, then Code map, and read why the last refresh failed.")
    if not have_tool:
        if n:
            return Result(ATTENTION, "The Code map tool is not added yet", detail,
                          "Open Connected tools and add the Code map preset.")
        return Result(OK, "The Code map tool is not added yet", detail)
    if not n:
        return Result(OK, "No project keeps a code map", detail)
    building = sum(1 for _, st in rows if st == "running")
    waiting = sum(1 for _, st in rows if st not in ("ok", "running"))
    head = f"{n} project{'s' if n != 1 else ''} keep{'s' if n == 1 else ''} a code map"
    parts = ([f"{waiting} not built yet"] if waiting else []) + ([f"{building} building"] if building else [])
    return Result(OK, head + ("; " + ", ".join(parts) if parts else "; all up to date"), detail)


async def check_disk(s) -> Result:
    free = await asyncio.to_thread(disk_free_bytes, Path(settings.workspaces_dir))
    gb = free / GB
    detail = {"free_gb": round(gb, 1)}
    if free < DISK_PROBLEM_BYTES:
        return Result(PROBLEM, f"Almost out of disk space ({gb:.1f} GB free)", detail, "Free up disk space; tasks will fail without it.")
    if free < DISK_ATTENTION_BYTES:
        return Result(ATTENTION, f"Disk space is getting low ({gb:.1f} GB free)", detail, "Free up some disk space soon.")
    return Result(OK, f"{gb:.0f} GB free", detail)


CHECKS: list[Check] = [
    Check("claude", "Claude is installed", check_claude),
    Check("python", "Python", check_python),
    Check("git", "Git", check_git),
    Check("node", "Node", check_node),
    Check("guard_files", "The guard is intact", check_guard_files),
    Check("guard_selftest", "The guard blocks what it should", check_guard_selftest),
    Check("guard_errors", "Guard errors in the last 7 days", check_guard_errors),
    Check("folders", "The app can write its folders", check_folders),
    Check("database", "Database is up to date", check_database),
    Check("mcp", "Connected tools", check_mcp),
    Check("skill_packs", "Skill packs", check_skill_packs),
    Check("code_map", "Code map", check_code_map),
    Check("docker", "Docker (for 'wait until ready' checks)", check_docker),
    Check("disk", "Free disk space", check_disk),
]
