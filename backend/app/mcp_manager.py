"""MCP registry logic: build per-task configs, classify tools, run health probes.

Phase 2 uses stateless per-task config injection (a generated .mcp-config.json
passed to the Claude CLI with --mcp-config) rather than long-running shared
daemons. Health checks validate connectivity on demand and periodically.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from .config import settings
from .crypto import decrypt_json
from .database import SessionLocal
from .mcp_client import probe_stdio
from .models import McpServer, McpToolPolicy

_READ_PREFIXES = ("get", "list", "read", "search", "fetch", "query", "describe", "find")


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
        if srv.scope in ("team", "user"):
            out.append(srv)
        elif srv.scope == "project" and project and srv.project == project:
            out.append(srv)
    return out


def build_config(servers: list[McpServer]) -> dict:
    cfg: dict = {}
    for srv in servers:
        sec = decrypt_json(srv.secrets_encrypted)
        if srv.transport == "stdio":
            entry: dict = {"command": srv.command, "args": srv.args or []}
            if sec.get("env"):
                entry["env"] = sec["env"]
        else:
            entry = {"type": "http", "url": srv.url}
            if sec.get("headers"):
                entry["headers"] = sec["headers"]
        cfg[srv.name] = entry
    return {"mcpServers": cfg}


async def prepare_for_task(project: str | None, workspace: Path) -> dict | None:
    """Write a per-task MCP config and resolve allow/deny tool lists from policy."""
    servers = await servers_for_task(project)
    if not servers:
        return None

    cfg = build_config(servers)
    path = workspace / ".mcp-config.json"
    path.write_text(json.dumps(cfg, indent=2))

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
    for p in policies:
        ref = f"mcp__{name_by_id[p.server_id]}__{p.tool_name}"
        if p.action == "block":
            disallowed.append(ref)
        elif p.action == "auto_approve":
            allowed.append(ref)
    return {
        "config_path": str(path),
        "allowed": allowed,
        "disallowed": disallowed,
        "server_names": [s.name for s in servers],
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
