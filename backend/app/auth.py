"""Auth core: password hashing, server-side sessions, hashed API tokens.

Convention: every function takes an AsyncSession `s` and NEVER commits; it
flushes where needed and the caller owns the transaction (commit/rollback).
Raw session ids and API tokens are returned once and only their sha256 is stored.
"""
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request, Response, WebSocket
from sqlalchemy import delete, select, update

from .config import settings
from .database import SessionLocal
from .models import ApiToken, AuthSession, Task, User, _now

_ph = PasswordHasher()
MIN_PASSWORD_LEN = 10
AGENT_TOKEN_MAX_TTL_SECONDS = 24 * 3600  # hard cap for agent-task tokens


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_password(pw: str) -> None:
    if len(pw) < MIN_PASSWORD_LEN:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LEN} characters")


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(hash: str, pw: str) -> bool:
    try:
        return _ph.verify(hash, pw)
    except (VerificationError, InvalidHashError):
        return False


def _sha(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _aware(dt: datetime) -> datetime:
    # SQLite returns naive datetimes; treat them as UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def create_session(s, user: User, user_agent: str | None) -> str:
    raw = secrets.token_urlsafe(32)
    now = _now()
    s.add(
        AuthSession(
            id_hash=_sha(raw),
            user_id=user.id,
            created_at=now,
            expires_at=now + timedelta(days=settings.session_ttl_days),
            last_seen_at=now,
            user_agent=(user_agent or None) and user_agent[:400],
        )
    )
    await s.flush()
    return raw


SLIDE_AFTER_SECONDS = 60  # refresh a session at most once a minute


async def resolve_session(s, raw: str) -> User | None:
    found = await resolve_session_slide(s, raw)
    return found[0] if found else None


async def resolve_session_slide(s, raw: str) -> tuple[User, bool] | None:
    """Return (user, slid). `slid` means expiry was extended (and last_seen_at
    written), so the caller should re-send the cookie with a fresh max_age."""
    sess = await s.get(AuthSession, _sha(raw))
    if sess is None:
        return None
    now = _now()
    if _aware(sess.expires_at) <= now:
        return None
    user = await s.get(User, sess.user_id)
    if user is None or not user.active:
        return None
    slid = False
    if (now - _aware(sess.last_seen_at)).total_seconds() >= SLIDE_AFTER_SECONDS:
        sess.expires_at = now + timedelta(days=settings.session_ttl_days)
        sess.last_seen_at = now
        slid = True
        await s.flush()
    return user, slid


async def revoke_user_sessions(s, user_id: str, except_raw: str | None = None) -> None:
    stmt = delete(AuthSession).where(AuthSession.user_id == user_id)
    if except_raw is not None:
        stmt = stmt.where(AuthSession.id_hash != _sha(except_raw))
    await s.execute(stmt)
    await s.flush()


async def mint_token(
    s, user_id: str, purpose: str, task_id: str | None, ttl_seconds: int
) -> str:
    if purpose == "agent-task":
        ttl_seconds = min(ttl_seconds, AGENT_TOKEN_MAX_TTL_SECONDS)
    raw = "co_" + secrets.token_urlsafe(32)
    s.add(
        ApiToken(
            token_hash=_sha(raw),
            user_id=user_id,
            purpose=purpose,
            task_id=task_id,
            expires_at=_now() + timedelta(seconds=ttl_seconds),
        )
    )
    await s.flush()
    return raw


async def resolve_token(s, raw: str) -> tuple[User, ApiToken] | None:
    tok = (
        await s.execute(select(ApiToken).where(ApiToken.token_hash == _sha(raw)))
    ).scalar_one_or_none()
    if tok is None or tok.revoked_at is not None or _aware(tok.expires_at) <= _now():
        return None
    user = await s.get(User, tok.user_id)
    if user is None or not user.active:
        return None
    return user, tok


async def revoke_task_tokens(s, task_id: str) -> None:
    await s.execute(
        update(ApiToken)
        .where(ApiToken.task_id == task_id, ApiToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    await s.flush()


@dataclass
class Principal:
    user: User
    token: ApiToken | None = None

    @property
    def is_admin(self) -> bool:
        return self.user.role == "admin"

    @property
    def is_agent(self) -> bool:
        return self.token is not None and self.token.purpose == "agent-task"


# --------------------------------------------------------------------------
# Request dependencies
# --------------------------------------------------------------------------

SESSION_COOKIE = "co_session"


def set_session_cookie(request: Request, response: Response, raw: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        raw,
        httponly=True,
        samesite="lax",
        secure=(request.url.scheme == "https"),
        max_age=settings.session_ttl_days * 86400,
        path="/",
    )

CSRF_HEADER = "X-Requested-With"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# The only routes an agent-task token may call, by (method, route template).
# Dispatch is further checked in its handler (parent_task_id == token.task_id).
_AGENT_DISPATCH = ("POST", "/agents/dispatch")
# The agent's own notes (phase 3a): the handler requires token.task_id == task_id.
_AGENT_NOTES = ("PUT", "/tasks/{task_id}/notes")
# The agent's own questions (phase 3b): file and read; the handlers require token.task_id == task_id.
# Never answer or dismiss (those routes stay outside the allowlist).
_AGENT_QUESTIONS = {("POST", "/tasks/{task_id}/questions"), ("GET", "/tasks/{task_id}/questions")}
# A suggested decision for the project's log (phase 4b): own task only (handler), a person reviews it.
_AGENT_PROPOSALS = ("POST", "/tasks/{task_id}/decision-proposals")
_AGENT_TASK_READS = {("GET", "/tasks/{task_id}"), ("GET", "/tasks/{task_id}/events")}
# co-guard's hook (Global Constraints): file/poll its own task's approval
# requests and report decisions. The handlers confine each call to the
# token's OWN task (not its thread): body task_id, or the request's task.
_AGENT_GUARD = {
    ("POST", "/guard/report"),
    ("POST", "/guard/approvals"),
    ("GET", "/guard/approvals/{approval_id}"),
}

_templates: dict[tuple[int, int], str] = {}


def route_template(request: Request) -> str | None:
    """Full path template (e.g. "/tasks/{task_id}") of the matched route.

    Older FastAPI puts a prefixed copy of the route in scope["route"], so its
    .path is already complete; newer FastAPI (lazy included routers) puts the
    router-local original there, so resolve its effective path from the app."""
    route = request.scope.get("route")
    if route is None:
        return None
    try:
        from fastapi.routing import iter_route_contexts
    except ImportError:  # pragma: no cover - older FastAPI
        return getattr(route, "path", None)
    app = request.scope.get("app")
    key = (id(app), id(route))
    if key not in _templates and app is not None:
        for rc in iter_route_contexts(app.routes):
            path = rc.path or getattr(rc.original_route, "path", None)
            _templates[(id(app), id(rc.original_route))] = path
    return _templates.get(key, getattr(route, "path", None))


def _bearer(request: Request) -> str | None:
    # Other schemes (e.g. a reverse proxy's Basic auth) are ignored so the
    # browser's session cookie still applies.
    scheme, _, raw = (request.headers.get("authorization") or "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    if not raw.strip():
        raise HTTPException(401, "Not authenticated")
    return raw.strip()


async def _agent_may_read(s, tok: ApiToken, task_id: str | None) -> bool:
    if not tok.task_id or not task_id:
        return False
    if task_id == tok.task_id:
        return True
    target = await s.get(Task, task_id)
    if target is None:
        return False
    if target.parent_task_id == tok.task_id:
        return True
    own = await s.get(Task, tok.task_id)
    own_root = own and (own.root_id or own.id)
    return bool(own_root) and (target.root_id or target.id) == own_root


async def current_principal(request: Request) -> Principal:
    """Authenticate via `Authorization: Bearer co_…` or the session cookie.

    401 without valid credentials; 403 for a cookie write without the CSRF
    header, or for an agent-task token outside its allowlist."""
    principal = getattr(request.state, "principal", None)
    if principal is not None:
        return principal
    raw_token = _bearer(request)
    async with SessionLocal() as s:
        if raw_token is not None:
            found = await resolve_token(s, raw_token)
            if found is None:
                raise HTTPException(401, "Not authenticated")
            principal = Principal(*found)
            if principal.is_agent:
                key = (request.method, route_template(request))
                if key == _AGENT_DISPATCH:
                    pass  # parent_task_id checked by the dispatch handler
                elif key == _AGENT_NOTES or key == _AGENT_PROPOSALS or key in _AGENT_QUESTIONS:
                    if not principal.token.task_id:
                        raise HTTPException(403, "Token not allowed for this route")
                    # own task checked by the notes / questions handlers
                elif key in _AGENT_GUARD:
                    if not principal.token.task_id:
                        raise HTTPException(403, "Token not allowed for this route")
                    # own task checked by the guard handlers
                elif key in _AGENT_TASK_READS:
                    if not await _agent_may_read(
                        s, principal.token, request.path_params.get("task_id")
                    ):
                        raise HTTPException(403, "Token not allowed for this task")
                else:
                    raise HTTPException(403, "Token not allowed for this route")
        else:
            raw = request.cookies.get(SESSION_COOKIE)
            found = await resolve_session_slide(s, raw) if raw else None
            if found is None:
                raise HTTPException(401, "Not authenticated")
            user, slid = found
            if slid:
                request.state.refresh_session_cookie = raw
            if request.method not in _SAFE_METHODS and not request.headers.get(CSRF_HEADER):
                raise HTTPException(403, f"missing {CSRF_HEADER}")
            principal = Principal(user)
            await s.commit()  # persist sliding-expiry refresh
    request.state.principal = principal
    return principal


def ensure_admin(p: Principal) -> None:
    if p.is_agent or not p.is_admin:
        raise HTTPException(403, "Admin only")


async def require_admin(p: Principal = Depends(current_principal)) -> Principal:
    ensure_admin(p)
    return p


async def ws_principal(websocket: WebSocket) -> Principal | None:
    """Cookie-only auth for WebSockets (agent tokens are not accepted)."""
    raw = websocket.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    async with SessionLocal() as s:
        user = await resolve_session(s, raw)
        if user is None:
            return None
        await s.commit()
    return Principal(user)
