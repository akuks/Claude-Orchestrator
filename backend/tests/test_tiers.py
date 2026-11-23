"""Phase 4c Task 1: org model tiers and tier-based model resolution."""

import asyncio
import uuid

import pytest

from app import scheduler, tiers
from app.database import SessionLocal
from app.main import app
from app.models import Agent, Schedule, Task, WorkflowRun


def _tag():
    return uuid.uuid4().hex[:8]


def test_defaults():
    assert tiers.normalized(None) == tiers.DEFAULT_TIERS
    assert tiers.normalized({"light": 5}) == tiers.DEFAULT_TIERS
    assert tiers.normalized({"light": "sonnet", "fast": "opus"}) == {**tiers.DEFAULT_TIERS, "light": "sonnet"}
    assert tiers.TIERS == ("strongest", "standard", "light")


def test_validate():
    out = tiers.validate({"light": "claude-haiku-4-5"})
    assert out == {**tiers.DEFAULT_TIERS, "light": "claude-haiku-4-5"}
    assert tiers.validate({"standard": "claude-sonnet-4-5[1m]"})["standard"] == "claude-sonnet-4-5[1m]"
    with pytest.raises(ValueError, match="^unknown model tier: fast$"):
        tiers.validate({"fast": "opus"})
    for bad in ("gpt-4", "claude-x; rm", "", 5):
        with pytest.raises(ValueError) as e:
            tiers.validate({"light": bad})
        assert str(e.value) == "model_tiers.light must be opus, sonnet, haiku or a claude-… model id"
    with pytest.raises(ValueError, match="^model_tiers must be an object$"):
        tiers.validate([])


def test_tier_values_and_valid_agent_model():
    t = {"strongest": "opus", "standard": "opus", "light": "claude-haiku-4-5"}
    assert tiers.tier_values(t) == ["opus", "claude-haiku-4-5"]
    assert tiers.valid_agent_model("sonnet", t)
    assert tiers.valid_agent_model("claude-haiku-4-5", t)
    assert not tiers.valid_agent_model("claude-other-1", t)


def _set_tiers(client, value):
    r = client.put("/guard/org-settings", json={"model_tiers": value})
    assert r.status_code == 200, r.text
    return r.json()


def test_agent_model_resolution(client):
    async def go():
        async with SessionLocal() as s:
            assert await tiers.agent_model(s, Agent(name="a", tier="light", model="")) == "haiku"
            assert await tiers.agent_model(s, Agent(name="a", tier="light", model="opus")) == "opus"
            assert await tiers.agent_model(s, Agent(name="a", tier=None, model="")) is None
            assert await tiers.agent_model(s, Agent(name="a", tier="bogus", model="")) is None

    asyncio.run(go())
    try:
        _set_tiers(client, {"light": "sonnet"})

        async def again():
            async with SessionLocal() as s:
                return await tiers.agent_model(s, Agent(name="a", tier="light", model=""))

        assert asyncio.run(again()) == "sonnet"
    finally:
        _set_tiers(client, {})


def test_run_uses_tier_model(client):
    a = client.post("/agents", json={"name": f"t-{_tag()}", "system_prompt": "x", "default_prompt": "go",
                                      "tier": "light"})
    assert a.status_code == 201, a.text
    assert a.json()["tier"] == "light"
    aid = a.json()["id"]
    try:
        assert client.post(f"/agents/{aid}/run", json={}).json()["model"] == "haiku"
        _set_tiers(client, {"light": "sonnet"})
        assert client.post(f"/agents/{aid}/run", json={}).json()["model"] == "sonnet"
    finally:
        _set_tiers(client, {})
    # neither tier nor model: the project default
    p = client.post("/projects", json={"name": f"tp-{_tag()}", "default_model": "opus"}).json()
    b = client.post("/agents", json={"name": f"t-{_tag()}", "system_prompt": "x", "default_prompt": "go"}).json()
    assert b["tier"] is None
    assert client.post(f"/agents/{b['id']}/run", json={"project_id": p["id"]}).json()["model"] == "opus"


def test_dispatch_bulk_schedule_workflow_use_tier(client):
    tag = _tag()
    lead = client.post("/agents", json={"name": f"lead-{tag}", "system_prompt": "x", "default_prompt": "go",
                                         "coordinator": True}).json()
    a = client.post("/agents", json={"name": f"w-{tag}", "system_prompt": "x", "default_prompt": "go",
                                      "tier": "strongest"}).json()
    parent = client.post(f"/agents/{lead['id']}/run", json={}).json()
    kids = client.post("/agents/dispatch", json={"parent_task_id": parent["id"],
                                                  "items": [{"agent": a["id"], "prompt": "x"}]}).json()
    assert [k["model"] for k in kids] == ["opus"]
    bulk = client.post(f"/agents/{a['id']}/bulk", json={"prompts": ["one", "two"]}).json()
    assert [t["model"] for t in bulk] == ["opus", "opus"]
    wf = client.post("/workflows", json={"name": f"wf-{tag}", "stages": [
        {"name": "s0", "items": [{"agent": a["name"], "prompt": "a"}]}]}).json()

    async def scenario():
        async with SessionLocal() as s:
            sch = Schedule(name="s", cron="0 8 * * *", prompt="p", agent_id=a["id"], enabled=False)
            s.add(sch)
            await s.flush()
            built = await scheduler.build_from_schedule(s, sch, "now")
            sched_model = built.model
            await s.rollback()
        async with SessionLocal() as s:
            run = WorkflowRun(workflow_id=wf["id"], name=wf["name"], input="i",
                              stages=wf["stages"], status="pending", current_stage=0)
            s.add(run)
            await s.commit()
            run_id = run.id
        await app.state.workflow_runner.start_run(run_id)
        async with SessionLocal() as s:
            from sqlalchemy import select
            models = (await s.execute(select(Task.model).where(Task.workflow_run_id == run_id))).scalars().all()
        return sched_model, models

    sched_model, models = asyncio.run(scenario())
    assert sched_model == "opus"
    assert models == ["opus"]


def test_agent_tier_validation(client):
    body = {"name": f"v-{_tag()}", "system_prompt": "x", "default_prompt": "go"}
    r = client.post("/agents", json={**body, "tier": "huge"})
    assert r.status_code == 400
    assert r.json()["detail"] == "Invalid tier. Use one of strongest, standard, light"
    assert client.post("/agents", json={**body, "model": "claude-haiku-4-5"}).status_code == 400
    try:
        _set_tiers(client, {"light": "claude-haiku-4-5"})
        r = client.post("/agents", json={**body, "model": "claude-haiku-4-5"})
        assert r.status_code == 201, r.text
        aid = r.json()["id"]
        assert client.patch(f"/agents/{aid}", json={"tier": "nope"}).status_code == 400
        assert client.patch(f"/agents/{aid}", json={"tier": "standard"}).json()["tier"] == "standard"
        assert client.patch(f"/agents/{aid}", json={"model": "claude-zzz-1"}).status_code == 400
    finally:
        _set_tiers(client, {})
