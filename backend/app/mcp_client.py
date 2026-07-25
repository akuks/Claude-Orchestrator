"""Minimal MCP stdio client used for health checks and tool discovery.

Speaks just enough of the JSON-RPC handshake (newline-delimited over stdio) to
initialize a server and list its tools, then tears the process down. Not a full
MCP client — the Claude Code CLI is the real client at task time.
"""

import asyncio
import json
import os


async def _send(proc, msg: dict) -> None:
    proc.stdin.write((json.dumps(msg) + "\n").encode())
    await proc.stdin.drain()


async def _read_until(proc, want_id: int):
    while True:
        raw = await proc.stdout.readline()
        if not raw:
            return None
        line = raw.decode(errors="replace").strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue  # ignore non-JSON banner lines
        if obj.get("id") == want_id:
            return obj


async def _handshake(proc) -> dict:
    await _send(
        proc,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "claude-orchestrator", "version": "0.1.0"},
            },
        },
    )
    init = await _read_until(proc, 1)
    if init is None:
        return {"ok": False, "tools": [], "error": "no response to initialize"}
    if "error" in init:
        return {"ok": False, "tools": [], "error": str(init["error"])}

    await _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    await _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    resp = await _read_until(proc, 2)
    tools = []
    if resp and "result" in resp:
        for t in resp["result"].get("tools", []) or []:
            tools.append(
                {"name": t.get("name"), "description": (t.get("description") or "")[:200]}
            )
    return {"ok": True, "tools": tools, "error": None}


async def probe_stdio(
    command: str, args: list[str] | None, env: dict | None, timeout: int = 15
) -> dict:
    """Return {ok, tools, error} for a stdio MCP server config."""
    full_env = {**os.environ, **(env or {})}
    try:
        proc = await asyncio.create_subprocess_exec(
            command,
            *(args or []),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=full_env,
        )
    except FileNotFoundError:
        return {"ok": False, "tools": [], "error": f"command not found: {command}"}
    except OSError as exc:
        return {"ok": False, "tools": [], "error": str(exc)}

    try:
        return await asyncio.wait_for(_handshake(proc), timeout=timeout)
    except asyncio.TimeoutError:
        return {"ok": False, "tools": [], "error": "timed out during MCP handshake"}
    finally:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=3)
        except (asyncio.TimeoutError, ProcessLookupError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass
