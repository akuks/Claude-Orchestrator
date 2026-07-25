from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy import Integer, cast, delete, func, select

from .. import mcp_manager
from ..constants import VALID_MODELS  # noqa: F401 (kept for symmetry)
from ..crypto import decrypt_json, encrypt_json
from ..database import SessionLocal
from ..models import McpCall, McpServer, McpToolPolicy
from ..schemas import (
    McpObservabilityOut,
    McpProbeOut,
    McpServerCreate,
    McpServerOut,
    McpServerUpdate,
    PolicyIn,
    PolicyOut,
)

router = APIRouter(prefix="/mcp", tags=["mcp"])

_VALID_TRANSPORTS = {"stdio", "http"}
_VALID_SCOPES = {"team", "user", "project"}
_VALID_ACTIONS = {"auto_approve", "require_approval", "block"}


def _to_out(srv: McpServer) -> McpServerOut:
    sec = decrypt_json(srv.secrets_encrypted)
    out = McpServerOut.model_validate(srv)
    out.has_env = bool(sec.get("env"))
    out.has_headers = bool(sec.get("headers"))
    return out


def _pack_secrets(env: dict | None, headers: dict | None) -> str | None:
    payload = {}
    if env:
        payload["env"] = env
    if headers:
        payload["headers"] = headers
    return encrypt_json(payload) if payload else None


@router.post("/servers", response_model=McpServerOut, status_code=201)
async def create_server(payload: McpServerCreate):
    if payload.transport not in _VALID_TRANSPORTS:
        raise HTTPException(400, f"transport must be one of {sorted(_VALID_TRANSPORTS)}")
    if payload.scope not in _VALID_SCOPES:
        raise HTTPException(400, f"scope must be one of {sorted(_VALID_SCOPES)}")
    if payload.transport == "stdio" and not payload.command:
        raise HTTPException(400, "stdio transport requires a command")
    if payload.transport == "http" and not payload.url:
        raise HTTPException(400, "http transport requires a url")
    if payload.scope == "project" and not payload.project:
        raise HTTPException(400, "project scope requires a project name")

    async with SessionLocal() as s:
        dup = (
            await s.execute(select(McpServer).where(McpServer.name == payload.name))
        ).scalar_one_or_none()
        if dup:
            raise HTTPException(409, f"MCP server '{payload.name}' already exists")
        srv = McpServer(
            name=payload.name,
            transport=payload.transport,
            scope=payload.scope,
            project=payload.project,
            command=payload.command,
            args=payload.args,
            url=payload.url,
            secrets_encrypted=_pack_secrets(payload.env, payload.headers),
            enabled=payload.enabled,
        )
        s.add(srv)
        await s.commit()
        await s.refresh(srv)
        return _to_out(srv)


@router.get("/servers", response_model=list[McpServerOut])
async def list_servers(scope: str | None = None, project: str | None = None):
    stmt = select(McpServer).order_by(McpServer.created_at.desc())
    if scope:
        stmt = stmt.where(McpServer.scope == scope)
    if project:
        stmt = stmt.where(McpServer.project == project)
    async with SessionLocal() as s:
        rows = (await s.execute(stmt)).scalars().all()
    return [_to_out(r) for r in rows]


async def _get_or_404(s, server_id: str) -> McpServer:
    srv = await s.get(McpServer, server_id)
    if srv is None:
        raise HTTPException(404, "MCP server not found")
    return srv


@router.get("/servers/{server_id}", response_model=McpServerOut)
async def get_server(server_id: str):
    async with SessionLocal() as s:
        return _to_out(await _get_or_404(s, server_id))


@router.patch("/servers/{server_id}", response_model=McpServerOut)
async def update_server(server_id: str, payload: McpServerUpdate):
    async with SessionLocal() as s:
        srv = await _get_or_404(s, server_id)
        data = payload.model_dump(exclude_unset=True)
        # Merge secrets: only overwrite the sub-blob that was provided.
        if "env" in data or "headers" in data:
            sec = decrypt_json(srv.secrets_encrypted)
            if "env" in data:
                sec["env"] = data.pop("env")
            if "headers" in data:
                sec["headers"] = data.pop("headers")
            srv.secrets_encrypted = _pack_secrets(sec.get("env"), sec.get("headers"))
        for k, v in data.items():
            setattr(srv, k, v)
        if srv.scope not in _VALID_SCOPES:
            raise HTTPException(400, "invalid scope")
        await s.commit()
        await s.refresh(srv)
        return _to_out(srv)


@router.delete("/servers/{server_id}", status_code=204)
async def delete_server(server_id: str):
    async with SessionLocal() as s:
        srv = await _get_or_404(s, server_id)
        await s.delete(srv)
        await s.commit()


@router.post("/servers/{server_id}/test", response_model=McpProbeOut)
async def test_server(server_id: str):
    async with SessionLocal() as s:
        await _get_or_404(s, server_id)
    result = await mcp_manager.probe_and_store(server_id, seed_policies=True)
    return McpProbeOut(
        ok=result.get("ok"), tools=result.get("tools", []), error=result.get("error")
    )


@router.get("/servers/{server_id}/policies", response_model=list[PolicyOut])
async def get_policies(server_id: str):
    async with SessionLocal() as s:
        await _get_or_404(s, server_id)
        rows = (
            await s.execute(
                select(McpToolPolicy)
                .where(McpToolPolicy.server_id == server_id)
                .order_by(McpToolPolicy.tool_name)
            )
        ).scalars().all()
    return [PolicyOut.model_validate(p) for p in rows]


@router.put("/servers/{server_id}/policies", response_model=list[PolicyOut])
async def set_policies(server_id: str, policies: list[PolicyIn]):
    for p in policies:
        if p.action not in _VALID_ACTIONS:
            raise HTTPException(400, f"action must be one of {sorted(_VALID_ACTIONS)}")
    async with SessionLocal() as s:
        await _get_or_404(s, server_id)
        await s.execute(
            delete(McpToolPolicy).where(McpToolPolicy.server_id == server_id)
        )
        for p in policies:
            s.add(
                McpToolPolicy(
                    server_id=server_id,
                    tool_name=p.tool_name,
                    classification=p.classification,
                    action=p.action,
                )
            )
        await s.commit()
        rows = (
            await s.execute(
                select(McpToolPolicy)
                .where(McpToolPolicy.server_id == server_id)
                .order_by(McpToolPolicy.tool_name)
            )
        ).scalars().all()
    return [PolicyOut.model_validate(p) for p in rows]


@router.get("/observability", response_model=McpObservabilityOut)
async def observability(days: int = 7):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    async with SessionLocal() as s:
        total_calls = (
            await s.execute(
                select(func.count()).where(McpCall.created_at >= since)
            )
        ).scalar_one()
        total_errors = (
            await s.execute(
                select(func.count()).where(
                    McpCall.created_at >= since, McpCall.is_error.is_(True)
                )
            )
        ).scalar_one()
        by_server_rows = (
            await s.execute(
                select(
                    McpCall.server,
                    func.count(),
                    func.sum(cast(McpCall.is_error, Integer)),
                )
                .where(McpCall.created_at >= since)
                .group_by(McpCall.server)
            )
        ).all()
        top_tool_rows = (
            await s.execute(
                select(McpCall.server, McpCall.tool, func.count())
                .where(McpCall.created_at >= since)
                .group_by(McpCall.server, McpCall.tool)
                .order_by(func.count().desc())
                .limit(10)
            )
        ).all()

    by_server = [
        {"server": srv, "calls": calls, "errors": int(errors or 0)}
        for srv, calls, errors in by_server_rows
    ]
    top_tools = [
        {"server": srv, "tool": tool, "calls": calls}
        for srv, tool, calls in top_tool_rows
    ]
    failure_rate = (total_errors / total_calls) if total_calls else 0.0
    return McpObservabilityOut(
        window_days=days,
        total_calls=total_calls,
        total_errors=total_errors,
        failure_rate=round(failure_rate, 3),
        by_server=by_server,
        top_tools=top_tools,
    )
