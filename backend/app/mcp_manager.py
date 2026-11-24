"""MCP registry logic: build per-task configs, classify tools, run health probes.

Phase 2 uses stateless per-task config injection (a generated .mcp-config.json
passed to the Claude CLI with --mcp-config) rather than long-running shared
daemons. Health checks validate connectivity on demand and periodically.
"""

import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from .config import settings
from .crypto import decrypt_json
from .database import SessionLocal
from .mcp_client import probe_stdio
from .models import McpServer, McpToolPolicy

_READ_PREFIXES = ("get", "list", "read", "search", "fetch", "query", "describe", "find")


def mcp_server_prefix(server_name: str) -> str:
    """The server part of Claude Code's MCP tool names: every character other
    than [a-zA-Z0-9_-] becomes "_" ("github.com" -> "github_com")."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", server_name)


def mcp_tool_ref(server_name: str, tool_name: str) -> str:
    return f"mcp__{mcp_server_prefix(server_name)}__{tool_name}"


def classify_tool(name: str) -> str:
    """Heuristic read/write classification from the tool name."""
    base = name.lower()
    if any(base.startswith(p) or f"_{p}" in base for p in _READ_PREFIXES):
        return "read"
    return "write"


def default_action(classification: str) -> str:
    return {
        "read": "auto_approve",
        "write": "require_approval",
        "dangerous": "block",
    }.get(classification, "require_approval")


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def servers_for_task(project: str | None) -> list[McpServer]:
    async with SessionLocal() as s:
        rows = (
            await s.execute(select(McpServer).where(McpServer.enabled.is_(True)))
        ).scalars().all()
    out = []
    for srv in rows:
        # "team"/"user"/"both" are global — they apply to every task (global
        # takes precedence). "both" additionally carries a project association,
        # but its global reach supersedes project scoping. "project" applies only
        # to its own project's tasks.
        if srv.scope in ("team", "user", "both"):
            out.append(srv)
        elif srv.scope == "project" and project and srv.project == project:
            out.append(srv)
    return out


_LAUNCHER = Path(__file__).resolve().parent / "mcp_launch.py"


def launcher_path() -> str:
    """The launcher that starts a stdio tool from its neutral folder. It lives in
    the server's own code tree (app/), which the guard protects from agents."""
    return str(_LAUNCHER)


def neutral_dir(task_cwd_root: Path, server, taken: set[str] | None = None) -> Path:
    """The private start folder of one tool in one task:
    ``<task_cwd_root>/<server prefix>`` (the same sanitised prefix as its tool
    names). Two tools of one task whose prefixes match, ignoring case (a
    case-insensitive filesystem would merge them), get "-2", "-3", ...
    (``taken``: the casefolded names already used). Always a direct child of
    ``task_cwd_root``."""
    root = Path(task_cwd_root).resolve()
    base = mcp_server_prefix(getattr(server, "name", server) or "") or "_"
    name, n = base, 1
    while True:
        d = (root / name).resolve()
        if d.parent != root:  # the prefix has no separators or dots; check anyway
            raise ValueError(f"unsafe folder name for connected tool {base!r}")
        if not taken or d.name.casefold() not in taken:
            return d
        n += 1
        name = f"{base}-{n}"


def _fresh_private_dir(d: Path) -> None:
    if d.is_symlink() or d.is_file():
        d.unlink()
    elif d.exists():
        shutil.rmtree(d)
    d.mkdir(mode=0o700)
    d.chmod(0o700)  # whatever the umask


# Inert marker files that end the launchers' search for a project above the
# folder: npm/npx take the nearest ancestor holding node_modules or package.json
# as the project (and read its .npmrc, and prefer its node_modules); uv reads the
# nearest uv.toml. Without them the search climbs to workspaces/, which agents
# can write.
STOPPER_FILES = {"package.json": "{}\n", ".npmrc": "", "uv.toml": ""}
STOPPER_DIRS = ("node_modules",)


def place_stoppers(d: Path) -> None:
    """Write the marker files into ``d`` (fresh each time: exact content, 0600;
    ``node_modules/`` empty, 0700). Never follows a link already there."""
    d = Path(d)
    for name in (*STOPPER_FILES, *STOPPER_DIRS):
        p = d / name
        if p.is_symlink() or (p.exists() and not p.is_dir()):
            p.unlink()
        elif p.is_dir():
            shutil.rmtree(p)
    for name, text in STOPPER_FILES.items():
        fd = os.open(d / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(d / name, 0o600)
    for name in STOPPER_DIRS:
        (d / name).mkdir(mode=0o700)
        os.chmod(d / name, 0o700)


def build_config(servers: list[McpServer], *, task_cwd_root: Path | None = None,
                 project_dir: str | None = None) -> dict:
    """The --mcp-config for these servers. With ``task_cwd_root`` (a task's
    run), each stdio server whose "Start in the project folder" switch is off
    starts through the launcher from its own new, private folder under it
    (holding only the inert ``place_stoppers`` markers), and
    gets ``CO_PROJECT_DIR`` when ``project_dir`` is given. Without it (or with
    the switch on) the entry is exactly ``{"command", "args"[, "env"]}``."""
    cfg: dict = {}
    taken: set[str] = set()
    for srv in servers:
        sec = decrypt_json(srv.secrets_encrypted)
        if srv.transport == "stdio":
            if task_cwd_root is not None and not getattr(srv, "start_in_project", False):
                d = neutral_dir(task_cwd_root, srv, taken)
                taken.add(d.name.casefold())
                _fresh_private_dir(d)
                place_stoppers(d)
                env = dict(sec.get("env") or {})
                if project_dir:
                    env["CO_PROJECT_DIR"] = str(project_dir)
                entry = {"command": sys.executable,
                         "args": ["-I", launcher_path(), str(d), "--", srv.command, *(srv.args or [])]}
                if env:
                    entry["env"] = env
            else:
                entry = {"command": srv.command, "args": srv.args or []}
                if sec.get("env"):
                    entry["env"] = sec["env"]
        else:
            entry = {"type": "http", "url": srv.url}
            if sec.get("headers"):
                entry["headers"] = sec["headers"]
        cfg[srv.name] = entry
    return {"mcpServers": cfg}


def task_config_dir(task_id: str) -> Path:
    """A task's MCP config folder (its config file and its tools' start folders)."""
    return settings.workspaces_dir / "_mcp_configs" / task_id


def cleanup_task(task_id: str) -> None:
    """Remove a task's MCP config folder: the config (vault secrets) and every
    tool's start folder under ``cwd/``."""
    shutil.rmtree(task_config_dir(task_id), ignore_errors=True)


async def sweep_stale_configs() -> int:
    """At boot: remove every ``_mcp_configs/<id>`` whose task is not queued or
    running (a crash skipped its cleanup). Returns how many went; never raises."""
    from .constants import Status
    from .models import Task

    try:
        root = settings.workspaces_dir / "_mcp_configs"
        if not root.is_dir():
            return 0
        entries = list(root.iterdir())
        if not entries:
            return 0
        async with SessionLocal() as s:
            live = set((await s.execute(
                select(Task.id).where(Task.id.in_([e.name for e in entries]),
                                      Task.status.in_([Status.QUEUED, Status.RUNNING]))
            )).scalars().all())
        gone = 0
        for e in entries:
            if e.name in live:
                continue
            try:
                if e.is_symlink() or not e.is_dir():
                    e.unlink()
                else:
                    shutil.rmtree(e)
                gone += 1
            except OSError:
                continue
        return gone
    except Exception:  # noqa: BLE001 - a boot sweep never stops the boot
        import logging

        logging.getLogger(__name__).warning("mcp config sweep failed", exc_info=True)
        return 0


async def prepare_for_task(project: str | None, config_dir: Path,
                           project_dir: str | None = None) -> dict | None:
    """Write a per-task MCP config and resolve allow/deny tool lists from policy.

    Also returns co-guard's policy shapes: ``servers`` ({lower-cased server
    prefix: {name, environment}}, policy ``mcp_servers``) and
    ``tool_defaults`` ({"mcp__<prefix>__*": default_action}, policy
    ``mcp_defaults``). Two servers with the same prefix keep the more cautious
    environment (prod > unknown > dev) and the more restrictive default.

    The config holds vault secrets, so it is written to config_dir — a per-task
    location OUTSIDE the agent's working directory (never the project repo) — so
    an agent can't read or accidentally commit it. --mcp-config takes the absolute
    path, so the file does not need to live in the cwd.

    Each stdio tool (switch off) starts from ``config_dir/cwd/<prefix>/``, a
    new 0700 folder made here that holds only the inert marker files; ``project_dir`` reaches it as
    ``CO_PROJECT_DIR``. All of it goes with ``cleanup_task``.
    """
    servers = await servers_for_task(project)
    if not servers:
        return None

    config_dir.parent.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(mode=0o700, exist_ok=True)
    config_dir.chmod(0o700)
    cwd_root = config_dir / "cwd"
    _fresh_private_dir(cwd_root)
    cfg = build_config(servers, task_cwd_root=cwd_root, project_dir=project_dir)
    path = (config_dir / ".mcp-config.json").resolve()
    path.write_text(json.dumps(cfg, indent=2))
    try:
        path.chmod(0o600)  # owner-only
    except OSError:
        pass

    name_by_id = {s.id: s.name for s in servers}
    async with SessionLocal() as s:
        policies = (
            await s.execute(
                select(McpToolPolicy).where(
                    McpToolPolicy.server_id.in_(list(name_by_id.keys()))
                )
            )
        ).scalars().all()

    allowed, disallowed = [], []
    # co-guard: every policy of this task's servers, keyed by the tool's wire
    # name (a "*" tool name becomes the server-level pattern mcp__<srv>__*).
    tool_policies: dict[str, str] = {}
    for p in policies:
        ref = mcp_tool_ref(name_by_id[p.server_id], p.tool_name)
        tool_policies[ref] = p.action
        if p.action == "block":
            disallowed.append(ref)
        elif p.action == "auto_approve":
            allowed.append(ref)
    env_rank = {"dev": 0, "unknown": 1, "prod": 2}
    act_rank = {"auto_approve": 0, "require_approval": 1, "block": 2}
    srv_info: dict[str, dict] = {}
    tool_defaults: dict[str, str] = {}
    for srv in servers:
        prefix = mcp_server_prefix(srv.name).lower()
        env = getattr(srv, "environment", None)
        env = env if env in env_rank else "unknown"
        prev = srv_info.get(prefix)
        if prev is None or env_rank[env] > env_rank[prev["environment"]]:
            srv_info[prefix] = {"name": srv.name, "environment": env}
        act = getattr(srv, "default_action", None)
        act = act if act in act_rank else "require_approval"
        key = f"mcp__{prefix}__*"
        if key not in tool_defaults or act_rank[act] > act_rank[tool_defaults[key]]:
            tool_defaults[key] = act
    return {
        "config_path": str(path),
        "allowed": allowed,
        "disallowed": disallowed,
        "server_names": [s.name for s in servers],
        "tool_policies": tool_policies,
        "servers": srv_info,
        "tool_defaults": tool_defaults,
    }


async def probe_and_store(server_id: str, seed_policies: bool = True) -> dict:
    """Probe a server, persist status + discovered tools, optionally seed policies."""
    async with SessionLocal() as s:
        srv = await s.get(McpServer, server_id)
        if srv is None:
            return {"ok": False, "error": "server not found"}

        if srv.transport != "stdio":
            srv.status = "unknown"
            srv.status_detail = "HTTP transport health check not supported in Phase 2"
            srv.last_checked_at = _now()
            await s.commit()
            return {"ok": None, "tools": srv.tools, "error": srv.status_detail}

        sec = decrypt_json(srv.secrets_encrypted)
        command, args, env = srv.command, list(srv.args or []), sec.get("env") or {}

    result = await probe_stdio(
        command, args, env, timeout=settings.mcp_probe_timeout_seconds
    )

    async with SessionLocal() as s:
        srv = await s.get(McpServer, server_id)
        if srv is None:
            return result
        srv.last_checked_at = _now()
        if result["ok"]:
            srv.status = "healthy"
            srv.status_detail = f"{len(result['tools'])} tools"
            srv.tools = result["tools"]
        else:
            srv.status = "disconnected"
            srv.status_detail = (result.get("error") or "probe failed")[:500]
        await s.commit()

        if seed_policies and result["ok"]:
            existing = {
                p.tool_name
                for p in (
                    await s.execute(
                        select(McpToolPolicy).where(McpToolPolicy.server_id == server_id)
                    )
                ).scalars().all()
            }
            for t in result["tools"]:
                tname = t["name"]
                if not tname or tname in existing:
                    continue
                cls = classify_tool(tname)
                s.add(
                    McpToolPolicy(
                        server_id=server_id,
                        tool_name=tname,
                        classification=cls,
                        action=default_action(cls),
                    )
                )
            await s.commit()

    return result


async def health_check_all() -> None:
    async with SessionLocal() as s:
        ids = (
            await s.execute(
                select(McpServer.id).where(
                    McpServer.enabled.is_(True), McpServer.transport == "stdio"
                )
            )
        ).scalars().all()
    for sid in ids:
        try:
            await probe_and_store(sid, seed_policies=False)
        except Exception:  # noqa: BLE001 - health loop must never die
            continue
