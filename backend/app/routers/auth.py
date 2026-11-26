import asyncio
import logging
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select

from .. import auth
from ..config import settings
from ..database import SessionLocal
from ..editor_mcp import keys as editor_keys
from ..models import AuthSession, User, _now
from ..schemas import LoginIn, PasswordChangeIn, SetupIn, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger(__name__)

COOKIE = auth.SESSION_COOKIE
_ph = PasswordHasher()
# Computed once; verified against on unknown emails so timing doesn't leak existence.
_DUMMY_HASH = _ph.hash("dummy-password-for-timing")
_BAD_CREDS = "Invalid email or password"

# Failed-login timestamps per (normalized email, client ip). In-memory: assumes a
# single-process server; a multi-worker deployment would need a shared store.
_failures: dict[tuple[str, str], deque] = defaultdict(deque)


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _recent(key: tuple[str, str]) -> deque:
    q = _failures[key]
    cutoff = time.monotonic() - settings.login_window_seconds
    while q and q[0] < cutoff:
        q.popleft()
    if not q:
        _failures.pop(key, None)
    return q


def _locked(key) -> bool:
    return len(_recent(key)) >= settings.login_max_failures


_set_cookie = auth.set_session_cookie


# Serializes the "no users yet" check + insert in setup and bootstrap_admin so
# concurrent first-admin creation can't yield two admins. Single-process server
# assumed (like the lockout). One lock per event loop (tests use several loops).
_setup_locks: dict = {}


def _setup_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    return _setup_locks.setdefault(loop, asyncio.Lock())


async def bootstrap_admin() -> None:
    """Create the env-configured admin when there are no users."""
    if not (settings.admin_email and settings.admin_password):
        return
    try:
        auth.validate_password(settings.admin_password)
    except ValueError as e:
        log.error("CO_ADMIN_PASSWORD rejected (%s); admin not created. Use the setup screen.", e)
        return
    async with _setup_lock(), SessionLocal() as s:
        if (await s.execute(select(func.count()).select_from(User))).scalar_one() > 0:
            return
        s.add(
            User(
                email=settings.admin_email,
                name="Admin",
                password_hash=auth.hash_password(settings.admin_password),
                role="admin",
            )
        )
        await s.commit()


async def assign_orphans_to(user_id: str) -> None:
    """Give every pre-ownership row (created_by NULL) to this user."""
    from sqlalchemy import update

    from ..models import (
        Agent, ContextDoc, Project, Schedule, Task, Template, Workflow, WorkflowRun,
    )

    async with SessionLocal() as s:
        for model in (Task, Schedule, Agent, Workflow, WorkflowRun, Project, Template, ContextDoc):
            await s.execute(
                update(model).where(model.created_by.is_(None)).values(created_by=user_id)
            )
        await s.commit()


async def assign_orphans_to_first_admin() -> None:
    """Startup: hand legacy rows to the earliest-created active admin, if any."""
    async with SessionLocal() as s:
        admin_id = (
            await s.execute(
                select(User.id)
                .where(User.role == "admin", User.active.is_(True))
                .order_by(User.created_at, User.id)
                .limit(1)
            )
        ).scalar_one_or_none()
    if admin_id:
        await assign_orphans_to(admin_id)


async def _user_count(s) -> int:
    return (await s.execute(select(func.count()).select_from(User))).scalar_one()


@router.get("/setup-status")
async def setup_status():
    async with SessionLocal() as s:
        return {"needs_setup": await _user_count(s) == 0}


@router.post("/setup", response_model=UserOut, status_code=201)
async def setup(body: SetupIn, request: Request, response: Response):
    try:
        auth.validate_password(body.password)
    except ValueError as e:
        raise HTTPException(422, str(e))
    async with _setup_lock(), SessionLocal() as s:
        if await _user_count(s) > 0:
            raise HTTPException(409, "Setup already completed")
        user = User(
            email=body.email,
            name=body.name,
            password_hash=auth.hash_password(body.password),
            role="admin",
            last_login_at=_now(),
        )
        s.add(user)
        await s.flush()
        raw = await auth.create_session(s, user, request.headers.get("user-agent"))
        await s.commit()
        admin_id = user.id
        _set_cookie(request, response, raw)
    await assign_orphans_to(admin_id)
    return user


@router.post("/login", response_model=UserOut)
async def login(body: LoginIn, request: Request, response: Response):
    email = auth.normalize_email(body.email)
    key = (email, _ip(request))
    if _locked(key):
        raise HTTPException(429, "Too many failed attempts; try again later")
    async with SessionLocal() as s:
        user = (await s.execute(select(User).where(User.email == email))).scalar_one_or_none()
        ok = auth.verify_password(user.password_hash if user else _DUMMY_HASH, body.password)
        if not (ok and user and user.active):
            _failures[key].append(time.monotonic())
            raise HTTPException(401, _BAD_CREDS)
        _failures.pop(key, None)
        if _ph.check_needs_rehash(user.password_hash):
            user.password_hash = auth.hash_password(body.password)
        user.last_login_at = _now()
        raw = await auth.create_session(s, user, request.headers.get("user-agent"))
        await s.commit()
        _set_cookie(request, response, raw)
        return user


@router.post("/logout", status_code=204, dependencies=[Depends(auth.current_principal)])
async def logout(request: Request, response: Response):
    raw = request.cookies.get(COOKIE)
    if raw:
        async with SessionLocal() as s:
            sess = await s.get(AuthSession, auth._sha(raw))
            if sess is not None:
                await s.delete(sess)
                await s.commit()
    response.delete_cookie(COOKIE, path="/")
    response.status_code = 204
    return response


@router.get("/me", response_model=UserOut)
async def me(p: auth.Principal = Depends(auth.current_principal)):
    return p.user


@router.post("/password", status_code=204)
async def change_password(
    body: PasswordChangeIn,
    request: Request,
    p: auth.Principal = Depends(auth.current_principal),
):
    # Cookie-only: the current browser session (raw id) is the one kept alive.
    raw = request.cookies.get(COOKIE)
    if p.token is not None or not raw:
        raise HTTPException(403, "Password change requires a browser session")
    async with SessionLocal() as s:
        user = await s.get(User, p.user.id)
        if not auth.verify_password(user.password_hash, body.current_password):
            raise HTTPException(401, "Current password is incorrect")
        try:
            auth.validate_password(body.new_password)
        except ValueError as e:
            raise HTTPException(422, str(e))
        user.password_hash = auth.hash_password(body.new_password)
        await auth.revoke_user_sessions(s, user.id, except_raw=raw)
        await editor_keys.revoke_user_keys(s, user.id)  # a key someone else minted on this account goes too
        await s.commit()
    return Response(status_code=204)
