"""Team decisions in your own editor (phase 8c).

* ``GET/POST /me/editor-keys``, ``DELETE /me/editor-keys/{key_id}`` - the caller's own keys (cookie session; agent
  tokens 403). Only POST's answer carries the key text, once.
* ``GET /admin/editor-keys``, ``DELETE /admin/editor-keys/{key_id}`` - an admin lists and turns off anyone's key
  (never the key text).
* ``POST /mcp`` - the editor endpoint (``app.editor_mcp.protocol``), mounted OUTSIDE the cookie dependency: an
  ``Authorization: Bearer coe_...`` editor key only (a session cookie is never read here, so it is 401), and any
  request carrying an ``Origin`` header is refused (editors send none; it blocks web pages and DNS rebinding).
  GET is 405 (no route). Bodies are validated by hand (FastAPI's default 422 echoes the body back)."""

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select

from .. import auth
from ..auth import ensure_admin
from ..config import settings
from ..database import SessionLocal
from ..editor_mcp import keys as keys_mod
from ..editor_mcp import protocol
from ..models import EditorKey, Project, User

router = APIRouter(prefix="/me/editor-keys", tags=["editor"])
admin_router = APIRouter(prefix="/admin/editor-keys", tags=["editor"])
mcp_router = APIRouter(tags=["editor"])


BROWSER_ONLY = "Editor keys are managed from a browser session"


def _person(request: Request):
    """A person in a browser session: agent tokens are refused (also by the allowlist) and so is any other API
    token, like ``change_password`` (an editor key is made and turned off by the person, on screen)."""
    p = request.state.principal
    if p.is_agent:
        raise HTTPException(403, "Token not allowed for this route")
    if p.token is not None:
        raise HTTPException(403, BROWSER_ONLY)
    return p


def _admin(request: Request):
    _person(request)
    ensure_admin(request.state.principal)


async def _names(s, user: User) -> dict[str, str]:
    ids = await keys_mod.all_visible(s, user)
    if not ids:
        return {}
    rows = await s.execute(select(Project.id, Project.name).where(Project.id.in_(ids)))
    return {pid: name for pid, name in rows.all()}


@router.get("")
async def list_my_keys(request: Request):
    p = _person(request)
    async with SessionLocal() as s:
        rows = (await s.execute(select(EditorKey).where(EditorKey.user_id == p.user.id)
                                .order_by(EditorKey.created_at.desc()))).scalars().all()
        names = await _names(s, p.user)
        return {"enabled": await keys_mod.switch_on(s), "max_keys": keys_mod.MAX_KEYS,
                "days": list(keys_mod.DAYS), "keys": [keys_mod.shape(k, names) for k in rows]}


def _base_url(request: Request) -> str:
    return (settings.public_url or "").rstrip("/") or str(request.base_url).rstrip("/")


@router.post("", status_code=201)
async def create_my_key(request: Request, response: Response, data: Any = Body(None)):
    p = _person(request)
    try:
        name, days, projects = keys_mod.validate_new(data)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    async with SessionLocal() as s:
        if not await keys_mod.switch_on(s):
            raise HTTPException(403, keys_mod.OFF)
        if await keys_mod.live_count(s, p.user.id) >= keys_mod.MAX_KEYS:
            raise HTTPException(422, keys_mod.TOO_MANY)
        try:
            row, raw = await keys_mod.create(s, p.user, name, days, projects)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        # the cap again, with this key in: a create that raced another past the first check is undone
        if await keys_mod.live_count(s, p.user.id) > keys_mod.MAX_KEYS:
            await s.rollback()
            raise HTTPException(422, keys_mod.TOO_MANY)
        await s.commit()
        response.headers["Cache-Control"] = "no-store"  # the key text is shown once; never kept by a cache
        return {"id": row.id, "name": row.name, "prefix": row.prefix, "key": raw, "url": _base_url(request),
                "expires_at": keys_mod.iso(row.expires_at), "projects": row.projects}


async def _revoke(s, key: EditorKey | None) -> None:
    if key is None:
        raise HTTPException(404, "Not found")
    if key.revoked_at is None:
        key.revoked_at = keys_mod._now()
        await s.commit()


@router.delete("/{key_id}", status_code=204)
async def revoke_my_key(key_id: str, request: Request):
    p = _person(request)
    async with SessionLocal() as s:
        key = await s.get(EditorKey, key_id)
        await _revoke(s, key if key is not None and key.user_id == p.user.id else None)
    return Response(status_code=204)


@admin_router.get("")
async def list_all_keys(request: Request):
    _admin(request)
    async with SessionLocal() as s:
        rows = (await s.execute(select(EditorKey, User).join(User, User.id == EditorKey.user_id)
                                .order_by(EditorKey.created_at.desc()))).all()
        return [{"id": k.id, "user_id": k.user_id, "owner_name": u.name or u.email, "name": k.name,
                 "prefix": k.prefix, "created_at": keys_mod.iso(k.created_at),
                 "last_used_at": keys_mod.iso(k.last_used_at), "expires_at": keys_mod.iso(k.expires_at),
                 "revoked_at": keys_mod.iso(k.revoked_at), "use_count": k.use_count or 0,
                 "projects": list(k.projects) if k.projects is not None else None} for k, u in rows]


@admin_router.delete("/{key_id}", status_code=204)
async def revoke_any_key(key_id: str, request: Request):
    _admin(request)
    async with SessionLocal() as s:
        await _revoke(s, await s.get(EditorKey, key_id))
    return Response(status_code=204)


# --------------------------------------------------------------------------- the editor endpoint


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _editor_bearer(request: Request) -> str:
    scheme, _, raw = (request.headers.get("authorization") or "").partition(" ")
    raw = raw.strip()
    if scheme.lower() != "bearer" or not raw:
        raise HTTPException(401, protocol.NO_KEY, headers={"WWW-Authenticate": "Bearer"})
    return raw


async def _body(request: Request) -> bytes:
    size = request.headers.get("content-length")
    if size is not None and (not size.isdigit() or int(size) > protocol.BODY_MAX):
        raise HTTPException(413, protocol.TOO_LARGE)
    chunks, got = [], 0
    async for chunk in request.stream():
        got += len(chunk)
        if got > protocol.BODY_MAX:
            raise HTTPException(413, protocol.TOO_LARGE)
        chunks.append(chunk)
    return b"".join(chunks)


@mcp_router.post("/mcp")
async def editor_endpoint(request: Request):
    if "origin" in request.headers:
        raise HTTPException(403, protocol.ORIGIN_REFUSED)
    raw = _editor_bearer(request)
    async with SessionLocal() as s:
        # The key is looked up first: a valid key never meets the address limit (behind a reverse proxy every
        # editor shares one address). Keys hold 256 random bits; the limit only keeps wrong-key load down.
        found = await keys_mod.resolve(s, raw)
        if found is None:
            if raw.startswith("co_"):  # an agent's API token is never an editor key (the agent allowlist's answer)
                tok = await auth.resolve_token(s, raw)
                if tok is not None and auth.Principal(*tok).is_agent:
                    raise HTTPException(403, "Token not allowed for this route")
            ip = _ip(request)
            if keys_mod.ip_blocked(ip):
                raise HTTPException(429, keys_mod.TOO_MANY_BAD)
            keys_mod.note_bad(ip)
            raise HTTPException(401, protocol.NO_KEY, headers={"WWW-Authenticate": "Bearer"})
        user, key = found
        if not await keys_mod.switch_on(s):
            raise HTTPException(403, keys_mod.OFF)
        if not keys_mod.allow_call(key.id):
            raise HTTPException(429, keys_mod.TOO_MANY_CALLS)
        body = await _body(request)
        await keys_mod.touch(s, key)
        status, payload = await protocol.handle(s, user, key, body, request.app.version)
    if payload is None:
        return Response(status_code=202)
    return JSONResponse(payload, status_code=status)
