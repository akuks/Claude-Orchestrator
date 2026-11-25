"""Phase 7c: reading Claude plan-limit statuses (normalising, holding, texts, whose plan)."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app import planlimits as pl
from app.database import SessionLocal
from app.models import PlanLimitStatus
from conftest import login_as

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


def _row(status, resets=None, seen=None, ref=None, api_key=False, confirmed=False):
    return PlanLimitStatus(credential_owner="x", status=status, resets_at=resets, raw={},
                           seen_at=seen or NOW, credential_ref=ref, api_key=api_key, confirmed=confirmed)


# ---- resetsAt (Review Focus 1) ---------------------------------------------------------------------------


def test_resets_at_in_seconds_and_milliseconds():
    when = NOW + timedelta(hours=2)
    assert pl.normalise_resets_at(int(when.timestamp()), NOW) == when
    assert pl.normalise_resets_at(int(when.timestamp() * 1000), NOW) == when
    assert pl.normalise_resets_at(when.timestamp() + 0.5, NOW) == when + timedelta(seconds=0.5)


@pytest.mark.parametrize("value", [
    None, "1764072000", True, float("nan"), float("inf"), 0, -5,
    int((NOW + timedelta(days=9)).timestamp()),            # too far ahead
    int((NOW - timedelta(days=2)).timestamp() * 1000),     # too far back (ms)
    4102444800,                                            # 2100 in seconds
    10 ** 18,                                              # absurd
])
def test_resets_at_missing_or_absurd_is_none(value):
    assert pl.normalise_resets_at(value, NOW) is None


def test_resets_at_edges_of_the_window():
    assert pl.normalise_resets_at(int((NOW - timedelta(hours=23)).timestamp()), NOW) is not None
    assert pl.normalise_resets_at(int((NOW + timedelta(days=7, hours=23)).timestamp()), NOW) is not None


def test_reading_keeps_raw_and_utilisation_raw():
    r = pl.reading({"status": "allowed_warning", "resetsAt": int(NOW.timestamp()) + 60, "utilization": 87,
                    "rateLimitType": "five_hour"}, NOW)
    assert r["status"] == "allowed_warning" and r["utilization"] == 87.0
    assert r["resets_at"] == NOW + timedelta(seconds=60)
    assert r["raw"]["rateLimitType"] == "five_hour"  # stored as given, never read
    assert pl.reading(None, NOW)["status"] == "unknown"
    assert pl.reading({"status": "rejected", "utilization": "lots"}, NOW)["utilization"] is None


def test_raw_cap_counts_the_escaping():
    """M3: quotes and backslashes double when encoded; the stored JSON still fits 2 KB."""
    import json
    for junk in ('"' * 5000, "\\" * 5000, "\u00e9" * 3000, "\n\"x" * 2000):
        raw = pl.cap_raw({"status": "rejected", "junk": junk})
        assert raw["truncated"] is True
        assert len(json.dumps(raw).encode()) <= pl.RAW_MAX


def test_api_key_rows_never_hold():
    """I2: an API key's row (own key or the shared org key) is for display only."""
    assert not pl.is_holding(_row("rejected", NOW + timedelta(hours=1), api_key=True), NOW)
    assert pl.banner(_row("rejected", NOW + timedelta(hours=1), api_key=True), NOW) is None
    assert pl.banner(_row("allowed_warning", NOW + timedelta(hours=1), api_key=True), NOW) is None


def test_unconfirmed_long_hold_is_capped_at_six_hours():
    """M1: a hold more than 6 hours ahead counts in full only once confirmed."""
    far = NOW + timedelta(days=3)
    assert pl.hold_until(_row("rejected", far)) == NOW + timedelta(hours=6)
    assert pl.is_holding(_row("rejected", far), NOW + timedelta(hours=5))
    assert not pl.is_holding(_row("rejected", far), NOW + timedelta(hours=6, seconds=1))  # checked again
    assert pl.hold_until(_row("rejected", far, confirmed=True)) == far
    near = NOW + timedelta(hours=5)
    assert pl.hold_until(_row("rejected", near)) == near
    assert pl.hold_until(_row("allowed", near)) is None


def test_confirmation_by_sentence_or_a_second_run(client):
    owner = {"owner": "c-" + uuid.uuid4().hex[:8], "api_key": False, "ref": None}
    far = datetime.now(timezone.utc) + timedelta(days=2)
    rej = {"status": "rejected", "resets_at": far, "utilization": None, "raw": {}}

    async def go():
        out = []
        r = await pl.record(owner, rej, "t1", run_ref="run1")
        out.append(r.confirmed)
        r = await pl.record(owner, rej, "t1", run_ref="run1")  # the same run again
        out.append(r.confirmed)
        r = await pl.record(owner, rej, "t1", run_ref="run2")  # a second run says so too
        out.append(r.confirmed)
        await pl.record(owner, pl.reading({"status": "allowed"}), "t2", run_ref="run3")
        r = await pl.record(owner, rej, "t3", run_ref="run4", confirmed=True)  # limit sentence
        out.append((r.confirmed, r.task_id))
        return out

    assert asyncio.run(go()) == [False, False, True, (True, "t3")]


def test_raw_capped_at_2kb():
    big = {"status": "rejected", "junk": "x" * 5000}
    raw = pl.cap_raw(big)
    assert raw["truncated"] is True and len(raw["text"].encode()) <= pl.RAW_MAX
    small = {"status": "allowed"}
    assert pl.cap_raw(small) == small


# ---- what counts as a limit hit --------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "You've hit your session limit · resets 3pm", "You’ve hit your weekly limit",
    "you've hit your Opus limit", "You've hit your Sonnet limit"])
def test_limit_texts(text):
    assert pl.limit_text_in(text)


def test_not_limit_texts():
    assert not pl.limit_text_in("All done")
    assert not pl.limit_text_in(None)
    assert not pl.limit_text_in({"x": 1})
    assert pl.stderr_says_limit("Error: 429 rate_limit_error")
    assert not pl.stderr_says_limit("overloaded")


# ---- holding (Review Focus 3) ----------------------------------------------------------------------------


def test_only_a_rejected_status_with_a_reset_ahead_holds():
    assert pl.is_holding(_row("rejected", NOW + timedelta(minutes=5)), NOW)
    assert not pl.is_holding(_row("rejected", NOW - timedelta(seconds=1)), NOW)  # stale: expired
    assert not pl.is_holding(_row("rejected", None), NOW)
    assert not pl.is_holding(_row("allowed_warning", NOW + timedelta(hours=1)), NOW)
    assert not pl.is_holding(None, NOW)


def test_texts():
    resets = datetime(2026, 11, 25, 14, 4, tzinfo=timezone.utc)
    assert pl.paused_text(resets) == "Paused — your Claude plan limit was reached. Starts again at 14:05."
    b = pl.banner(_row("rejected", resets), NOW)
    assert b == {"kind": "limit", "text": "Your Claude plan limit was reached. Work starts again at 14:05.",
                 "resets_at": resets.isoformat()}
    w = pl.banner(_row("allowed_warning", resets, seen=NOW - timedelta(hours=1)), NOW)
    assert w["kind"] == "warning" and w["text"] == "Your Claude plan is close to its limit. It resets at 14:04."
    assert pl.banner(_row("allowed_warning", resets, seen=NOW - timedelta(hours=6)), NOW) is None  # old
    assert pl.banner(_row("allowed_warning", NOW - timedelta(minutes=1), seen=NOW - timedelta(hours=1)),
                     NOW) is None  # the window it warned about has reset
    assert pl.banner(_row("allowed"), NOW) is None
    assert pl.banner(_row("rejected", NOW - timedelta(minutes=1)), NOW) is None


# ---- whose plan ------------------------------------------------------------------------------------------


def _who(uid):
    async def go():
        async with SessionLocal() as s:
            return await pl.credential_owner(s, uid)
    return asyncio.run(go())


def test_owner_rules(client):
    oauth = login_as(f"pl-oauth-{uuid.uuid4().hex[:8]}@test.local")
    assert oauth.put("/me/credentials", json={"claude_token": "o" + uuid.uuid4().hex,
                                              "claude_kind": "oauth_token"}).status_code == 200
    key = login_as(f"pl-key-{uuid.uuid4().hex[:8]}@test.local")
    assert key.put("/me/credentials", json={"claude_token": "k" + uuid.uuid4().hex,
                                            "claude_kind": "api_key"}).status_code == 200
    none = login_as(f"pl-none-{uuid.uuid4().hex[:8]}@test.local")

    a = _who(oauth.user_id)
    assert a["owner"] == oauth.user_id and a["api_key"] is False and len(a["ref"]) == 24
    b = _who(key.user_id)
    assert b["owner"] == key.user_id and b["api_key"] is True
    c = _who(none.user_id)  # the org fallback key (set for the whole test session)
    assert c == {"owner": "org", "api_key": True, "ref": c["ref"]}
    assert _who(None) is None
    # no secret anywhere in the answer
    assert all(len(str(v)) <= 32 for v in a.values())


def test_hold_follows_the_credential(client):
    """A person who replaced their credential is not held by the old one's limit."""
    u = login_as(f"pl-ref-{uuid.uuid4().hex[:8]}@test.local")
    u.put("/me/credentials", json={"claude_token": "o" + uuid.uuid4().hex, "claude_kind": "oauth_token"})
    who = _who(u.user_id)
    later = datetime.now(timezone.utc) + timedelta(hours=1)

    async def go():
        await pl.record(who, {"status": "rejected", "resets_at": later, "utilization": 1.0, "raw": {}}, None)
        async with SessionLocal() as s:
            held = await pl.hold_for(s, who)
            other = await pl.hold_for(s, {**who, "ref": "0" * 24})
            stranger = await pl.hold_for(s, {"owner": "someone-else", "api_key": False, "ref": who["ref"]})
        return held, other, stranger

    held, other, stranger = asyncio.run(go())
    assert held is not None and other is None and stranger is None


def test_record_keeps_one_row_per_owner_latest_wins(client):
    owner = {"owner": "own-" + uuid.uuid4().hex[:8], "api_key": False, "ref": None}

    async def go():
        await pl.record(owner, pl.reading({"status": "allowed"}), "t1")
        await pl.record(owner, pl.reading({"status": "allowed_warning", "utilization": 0.9}), "t2")
        async with SessionLocal() as s:
            from sqlalchemy import select
            return (await s.execute(select(PlanLimitStatus).where(
                PlanLimitStatus.credential_owner == owner["owner"]))).scalars().all()

    rows = asyncio.run(go())
    assert len(rows) == 1 and rows[0].status == "allowed_warning" and rows[0].task_id == "t2"
    assert rows[0].utilization == 0.9
