"""Personal credentials (/me/credentials, the caller's own record only) and the
admin-managed org fallback key (/admin/org-fallback).

Write-only: responses carry presence/kind, never a secret. Bodies are validated
here rather than by FastAPI because its default 422 echoes the request body
(`input`), which would hand a submitted secret straight back."""

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import IntegrityError

from .. import auth, credentials
from ..database import SessionLocal
from ..models import OrgSetting, User, UserCredential
from ..schemas import (
    CredentialStatus,
    CredentialsIn,
    CredentialTestIn,
    CredentialTestOut,
    OrgFallbackIn,
    OrgFallbackOut,
)

router = APIRouter(prefix="/me/credentials", tags=["credentials"])
admin_router = APIRouter(
    prefix="/admin", tags=["credentials"], dependencies=[Depends(auth.require_admin)]
)


def _parse(model: type[BaseModel], data: Any):
    try:
        return model.model_validate(data)
    except ValidationError as e:
        # Location and message only: no `input`/`ctx`, which can carry the secret.
        raise HTTPException(
            422,
            [{"loc": ["body", *e_["loc"]], "msg": e_["msg"], "type": e_["type"]} for e_ in e.errors()],
        )


def _uid(request: Request) -> str:
    return request.state.principal.user.id


async def _status(s, user_id: str) -> CredentialStatus:
    return CredentialStatus(**await credentials.credential_status(s, user_id))


@router.get("", response_model=CredentialStatus)
async def get_my_credentials(request: Request):
    async with SessionLocal() as s:
        return await _status(s, _uid(request))


def _apply(row: UserCredential, body: CredentialsIn) -> None:
    given = body.model_fields_set
    if "claude_token" in given:
        # Replaces the previous Claude secret entirely: one kind at a time.
        row.claude_kind = body.claude_kind
        row.claude_enc = credentials.seal(body.claude_token)
    if "github_token" in given:
        row.github_enc = credentials.seal(body.github_token)
    if "git_name" in given:
        row.git_name = body.git_name
    if "git_email" in given:
        row.git_email = body.git_email
    row.updated_at = datetime.now(timezone.utc)


@router.put("", response_model=CredentialStatus)
async def put_my_credentials(request: Request, data: Any = Body(...)):
    body = _parse(CredentialsIn, data)
    uid = _uid(request)
    for attempt in range(2):
        async with SessionLocal() as s:
            row = await s.get(UserCredential, uid)
            if row is None:
                row = UserCredential(user_id=uid)
                s.add(row)
            _apply(row, body)
            try:
                await s.commit()
            except IntegrityError:
                # A concurrent first PUT created the row: retry as an update.
                await s.rollback()
                if attempt:
                    raise
                continue
            return await _status(s, uid)


async def _clear(request: Request, *fields: str) -> CredentialStatus:
    uid = _uid(request)
    async with SessionLocal() as s:
        row = await s.get(UserCredential, uid)
        if row is not None:
            for f in fields:
                setattr(row, f, None)
            row.updated_at = datetime.now(timezone.utc)
            await s.commit()
        return await _status(s, uid)


@router.delete("/claude", response_model=CredentialStatus)
async def delete_my_claude(request: Request):
    return await _clear(request, "claude_kind", "claude_enc")


@router.delete("/github", response_model=CredentialStatus)
async def delete_my_github(request: Request):
    return await _clear(request, "github_enc")


# Users with a connection test in flight: one at a time each, so a user can't
# fan out claude subprocesses (single event loop: check-and-add is atomic).
_probing: set[str] = set()


@router.post("/test", response_model=CredentialTestOut)
async def test_my_credentials(request: Request, data: Any = Body(...)):
    body = _parse(CredentialTestIn, data)
    uid = _uid(request)
    if uid in _probing:
        raise HTTPException(429, "A connection test is already running")
    _probing.add(uid)
    try:
        # Read what's needed, then release the session before the (slow) probe.
        async with SessionLocal() as s:
            if body.which == "claude":
                probe = await credentials.prepare_claude_probe(s, uid)
            else:
                probe = await credentials.prepare_github_probe(s, uid)
        ok, detail = await probe()
    finally:
        _probing.discard(uid)
    return CredentialTestOut(ok=ok, detail=detail)


# --------------------------------------------------------------------------- admin


async def _fallback_out(s) -> OrgFallbackOut:
    row = await s.get(OrgSetting, credentials.FALLBACK_KEY)
    if row is None:
        return OrgFallbackOut(set=False)
    who = await s.get(User, row.updated_by) if row.updated_by else None
    return OrgFallbackOut(
        set=True, updated_at=row.updated_at, updated_by=who.email if who else row.updated_by
    )


@admin_router.get("/org-fallback", response_model=OrgFallbackOut)
async def get_org_fallback():
    async with SessionLocal() as s:
        return await _fallback_out(s)


@admin_router.put("/org-fallback", response_model=OrgFallbackOut)
async def put_org_fallback(request: Request, data: Any = Body(...)):
    body = _parse(OrgFallbackIn, data)
    uid = _uid(request)
    for attempt in range(2):
        async with SessionLocal() as s:
            row = await s.get(OrgSetting, credentials.FALLBACK_KEY)
            if row is None:
                row = OrgSetting(key=credentials.FALLBACK_KEY)
                s.add(row)
            row.value_enc = credentials.seal(body.api_key)
            row.updated_at = datetime.now(timezone.utc)
            row.updated_by = uid
            try:
                await s.commit()
            except IntegrityError:
                await s.rollback()
                if attempt:
                    raise
                continue
            return await _fallback_out(s)


@admin_router.delete("/org-fallback", response_model=OrgFallbackOut)
async def delete_org_fallback():
    async with SessionLocal() as s:
        row = await s.get(OrgSetting, credentials.FALLBACK_KEY)
        if row is not None:
            await s.delete(row)
            await s.commit()
        return await _fallback_out(s)
