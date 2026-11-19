import asyncio
from datetime import timedelta

import pytest

from app import auth
from app.database import SessionLocal, init_db
from app.models import ApiToken, AuthSession, User, _now


@pytest.fixture(scope="module", autouse=True)
def _db():
    asyncio.run(init_db())


def run(coro_fn):
    async def go():
        async with SessionLocal() as s:
            return await coro_fn(s)

    return asyncio.run(go())


async def _user(s, email, active=True, role="member"):
    u = User(email=auth.normalize_email(email), name="T", password_hash="x", role=role, active=active)
    s.add(u)
    await s.flush()
    return u


def test_normalize_email():
    assert auth.normalize_email(" A@B.com ") == "a@b.com"


def test_password_hash_roundtrip():
    h = auth.hash_password("correct horse battery")
    assert auth.verify_password(h, "correct horse battery") is True
    assert auth.verify_password(h, "wrong password!!") is False
    assert auth.verify_password("garbage", "x") is False
    with pytest.raises(ValueError):
        auth.validate_password("short")
    auth.validate_password("long enough pw")


def test_session_create_resolve_expire():
    async def go(s):
        u = await _user(s, "sess1@x.com")
        raw = await auth.create_session(s, u, "ua")
        row = (await s.execute(AuthSession.__table__.select().where(AuthSession.user_id == u.id))).first()
        assert row.id_hash != raw
        got = await auth.resolve_session(s, raw)
        assert got.id == u.id
        assert await auth.resolve_session(s, "nope") is None
        sess = await s.get(AuthSession, auth._sha(raw))
        sess.expires_at = _now() - timedelta(seconds=1)
        await s.flush()
        assert await auth.resolve_session(s, raw) is None
        await s.commit()

    run(go)


def test_disabled_user_session_rejected():
    async def go(s):
        u = await _user(s, "sess2@x.com")
        raw = await auth.create_session(s, u, None)
        assert await auth.resolve_session(s, raw) is not None
        u.active = False
        await s.flush()
        assert await auth.resolve_session(s, raw) is None
        await s.commit()

    run(go)


def test_revoke_user_sessions_keeps_current():
    async def go(s):
        u = await _user(s, "sess3@x.com")
        a = await auth.create_session(s, u, None)
        b = await auth.create_session(s, u, None)
        await auth.revoke_user_sessions(s, u.id, except_raw=a)
        assert await auth.resolve_session(s, a) is not None
        assert await auth.resolve_session(s, b) is None
        await auth.revoke_user_sessions(s, u.id)
        assert await auth.resolve_session(s, a) is None
        await s.commit()

    run(go)


def test_token_mint_resolve_revoke():
    async def go(s):
        u = await _user(s, "tok@x.com")
        raw = await auth.mint_token(s, u.id, "agent-task", "task1", 60)
        assert raw.startswith("co_")
        user, tok = await auth.resolve_token(s, raw)
        assert user.id == u.id and tok.task_id == "task1"
        p = auth.Principal(user, tok)
        assert p.is_agent and not p.is_admin
        assert auth.Principal(user, None).is_agent is False
        expired = await auth.mint_token(s, u.id, "personal", None, -1)
        assert await auth.resolve_token(s, expired) is None
        await auth.revoke_task_tokens(s, "task1")
        assert await auth.resolve_token(s, raw) is None
        assert await auth.resolve_token(s, "co_bogus") is None
        await s.commit()

    run(go)
