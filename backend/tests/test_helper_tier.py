"""Phase 7e (F102): a helper's model mapped to the org's tier names, shown in the run log as
"AGENT+ <kind> [<tier>]: <description>"."""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app import tiers
from app.database import SessionLocal
from app.models import TaskEvent
from app.resume import logfmt
from conftest import wait_task

AT = datetime(2026, 11, 25, 9, 8, 7, tzinfo=timezone.utc)


def test_tier_of():
    d = dict(tiers.DEFAULT_TIERS)
    assert tiers.tier_of("sonnet", d) == "standard"
    assert tiers.tier_of("opus", d) == "strongest"
    assert tiers.tier_of("claude-haiku-4-5-20251001", d) == "light"
    assert tiers.tier_of("claude-opus-5-5[1m]", d) == "strongest"
    assert tiers.tier_of("claude-fable-5", d) is None
    assert tiers.tier_of("gpt-x", d) is None
    for junk in (None, "", "inherit", "<synthetic>", 5, ["opus"]):
        assert tiers.tier_of(junk, d) is None
    custom = {"strongest": "claude-opus-5-5", "standard": "claude-sonnet-4-5", "light": "haiku"}
    assert tiers.tier_of("claude-opus-5-5", custom) == "strongest"  # exact id
    assert tiers.tier_of("opus", custom) == "strongest"  # alias -> the tier whose id is that family
    assert tiers.tier_of("claude-sonnet-4-5-20250929", custom) == "standard"
    # two tiers on the same model: the first (strongest) names it
    assert tiers.tier_of("opus", {"strongest": "opus", "standard": "opus", "light": "haiku"}) == "strongest"


def _use(inp, tid="tuA", **extra):
    return {"type": "tool_use", "payload": {"name": "Agent", "input": inp, "id": tid, "parent": None, **extra},
            "created_at": AT}


def test_logfmt_agent_line_with_and_without_tier():
    inp = {"subagent_type": "Explore", "description": "look around"}
    assert logfmt.line(_use(inp)) == "09:08:07 AGENT+ Explore: look around"
    assert logfmt.line(_use(inp, tier="standard")) == "09:08:07 AGENT+ Explore [standard]: look around"
    assert logfmt.line(_use({"subagent_type": "Explore"}, tier="light")) == "09:08:07 AGENT+ Explore [light]"
    # an unknown tier value is never shown
    assert logfmt.line(_use(inp, tier="huge")) == "09:08:07 AGENT+ Explore: look around"
    # the tier learnt later (the helper's first message) joins the earlier line; its own event has no line
    start = {"type": "subagent_start", "payload": {"tool_use_id": "tuB", "tier": "light"}, "created_at": AT}
    lines = logfmt.all_lines([_use(inp, tid="tuB"), start])
    assert lines == ["09:08:07 AGENT+ Explore [light]: look around"]
    assert logfmt.tail([_use(inp, tid="tuB"), start]) == "09:08:07 AGENT+ Explore [light]: look around"
    assert logfmt.line(start) is None


def _events(task_id):
    async def go():
        async with SessionLocal() as s:
            return (await s.execute(select(TaskEvent).where(TaskEvent.task_id == task_id)
                                    .order_by(TaskEvent.seq))).scalars().all()

    return asyncio.run(go())


def test_run_records_helper_tiers(client):
    r = client.post("/tasks", json={"prompt": "go [stream-fixture:helper_tier]"})
    assert r.status_code == 201, r.text
    done = wait_task(client, r.json()["id"])
    assert done["status"] == "completed", done.get("error")
    evs = _events(done["id"])
    uses = {e.payload["id"]: e.payload for e in evs if e.type == "tool_use"}
    assert uses["tuA"]["tier"] == "standard"  # the call's own model input
    assert "tier" not in uses["tuB"] and "tier" not in uses["tuC"]
    starts = [e.payload for e in evs if e.type == "subagent_start"]
    # tuB: its first real message's model (the <synthetic> one is skipped, later ones ignored); tuC unknown
    assert starts == [{"tool_use_id": "tuB", "tier": "light", "model": "claude-haiku-4-5-20251001"}]
    lines = logfmt.all_lines([{"type": e.type, "payload": e.payload, "created_at": e.created_at} for e in evs])
    agent = [ln for ln in lines if "AGENT+" in ln]
    assert [ln.split(" ", 1)[1] for ln in agent] == ["AGENT+ Explore [standard]: look around",
                                                    "AGENT+ general-purpose [light]: write tests",
                                                    "AGENT+ odd: odd one"]
