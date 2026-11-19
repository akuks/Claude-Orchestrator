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
from sqlalchemy import delete, select, update

from .config import settings
from .models import ApiToken, AuthSession, User, _now

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


async def resolve_session(s, raw: str) -> User | None:
    sess = await s.get(AuthSession, _sha(raw))
    if sess is None:
        return None
    now = _now()
    if _aware(sess.expires_at) <= now:
        return None
    user = await s.get(User, sess.user_id)
    if user is None or not user.active:
        return None
    sess.expires_at = now + timedelta(days=settings.session_ttl_days)
    sess.last_seen_at = now
    await s.flush()
    return user


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
