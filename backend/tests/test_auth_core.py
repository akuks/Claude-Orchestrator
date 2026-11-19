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


# ---- fix round 1 ----
def test_user_email_normalized_and_unique_case_insensitive():
    from sqlalchemy.exc import IntegrityError

    async def go(s):
        s.add(User(email="dup@x.com", name="a", password_hash="x"))
        await s.commit()
        s.add(User(email="Dup@X.com ", name="b", password_hash="x"))
        with pytest.raises(IntegrityError):
            await s.commit()
        await s.rollback()
        assert User(email=" MiXed@X.com ").email == "mixed@x.com"

    run(go)


def test_resolve_session_slides_expiry():
    async def go(s):
        u = await _user(s, "slide@x.com")
        raw = await auth.create_session(s, u, None)
        sess = await s.get(AuthSession, auth._sha(raw))
        sess.expires_at = _now() + timedelta(minutes=1)
        sess.last_seen_at = _now() - timedelta(days=1)
        await s.flush()
        before_seen = sess.last_seen_at
        assert await auth.resolve_session(s, raw) is not None
        want = _now() + timedelta(days=auth.settings.session_ttl_days)
        assert abs((auth._aware(sess.expires_at) - want).total_seconds()) < 60
        assert sess.last_seen_at > before_seen
        await s.commit()

    run(go)


def test_token_rejected_for_disabled_user_and_when_revoked():
    async def go(s):
        u = await _user(s, "tok2@x.com")
        raw = await auth.mint_token(s, u.id, "personal", None, 3600)
        assert await auth.resolve_token(s, raw) is not None
        _, tok = await auth.resolve_token(s, raw)
        tok.revoked_at = _now()
        await s.flush()
        assert await auth.resolve_token(s, raw) is None  # revoked, unexpired
        tok.revoked_at = None
        u.active = False
        await s.flush()
        assert await auth.resolve_token(s, raw) is None  # disabled user
        await s.commit()

    run(go)


def test_naive_datetimes_read_back_from_db():
    # Expiry comparison must cope with datetimes read back naive (SQLite).
    async def setup(s):
        u = await _user(s, "naive@x.com")
        good = await auth.create_session(s, u, None)
        bad = await auth.create_session(s, u, None)
        s_bad = await s.get(AuthSession, auth._sha(bad))
        s_bad.expires_at = _now() - timedelta(hours=1)
        tgood = await auth.mint_token(s, u.id, "personal", None, 3600)
        tbad = await auth.mint_token(s, u.id, "personal", None, 3600)
        t_row = (await s.execute(ApiToken.__table__.select().where(
            ApiToken.token_hash == auth._sha(tbad)))).first()
        await s.execute(ApiToken.__table__.update().where(
            ApiToken.token_hash == auth._sha(tbad)).values(expires_at=_now() - timedelta(hours=1)))
        await s.commit()
        return good, bad, tgood, tbad

    good, bad, tgood, tbad = run(setup)

    async def check(s):  # fresh session: values come from the DB
        assert await auth.resolve_session(s, good) is not None
        assert await auth.resolve_session(s, bad) is None
        assert await auth.resolve_token(s, tgood) is not None
        assert await auth.resolve_token(s, tbad) is None

    run(check)


def test_agent_task_token_ttl_capped_at_24h():
    async def go(s):
        u = await _user(s, "cap@x.com")
        raw = await auth.mint_token(s, u.id, "agent-task", "t9", 10 * 86400)
        _, tok = await auth.resolve_token(s, raw)
        assert (auth._aware(tok.expires_at) - _now()).total_seconds() <= 86400 + 5
        raw2 = await auth.mint_token(s, u.id, "personal", None, 10 * 86400)
        _, tok2 = await auth.resolve_token(s, raw2)
        assert (auth._aware(tok2.expires_at) - _now()).total_seconds() > 86400 * 9
        await s.commit()

    run(go)
