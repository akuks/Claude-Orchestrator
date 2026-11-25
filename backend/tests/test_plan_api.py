"""Phase 7c: /plan/status (mine), /plan/status/all (admins), DELETE /plan/status/{owner} ("Try now"), and
workflow steps waiting for a plan reset (held, never failed)."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from app import planlimits as pl
from app.constants import Status
from app.database import SessionLocal
from app.models import PlanLimitStatus, Task, WorkflowRun
from app.workflow_runner import WorkflowRunner
from _runs7a import (  # noqa: F401 - fixtures
    FakeWorker, _end_runs, _finish_tasks, _run, _tasks, agent, agents, fakes, has,
)
from conftest import login_as


def _now():
    return datetime.now(timezone.utc)


def _user(kind="oauth_token"):
    c = login_as(f"pla-{uuid.uuid4().hex[:10]}@test.local")
    assert c.put("/me/credentials", json={"claude_token": "t" + uuid.uuid4().hex,
                                          "claude_kind": kind}).status_code == 200
    return c


def _ref(uid):
    async def go():
        async with SessionLocal() as s:
            return (await pl.credential_owner(s, uid))["ref"]
    return asyncio.run(go())


def _store(owner, status, resets, ref=None, api_key=False):
    asyncio.run(pl.record({"owner": owner, "api_key": api_key, "ref": ref},
                          {"status": status, "resets_at": resets, "utilization": 0.97,
                           "raw": {"status": status}}, "task-x"))


def test_my_status_shapes(client):
    u = _user()
    empty = u.get("/plan/status").json()
    assert empty == {"owner": u.user_id, "api_key": False, "plan_applies": True, "status": None}
    resets = (_now() + timedelta(hours=1)).replace(microsecond=0)
    _store(u.user_id, "rejected", resets, ref=_ref(u.user_id))
    me = u.get("/plan/status").json()
    st = me["status"]
    assert set(st) == {"credential_owner", "status", "resets_at", "utilization", "raw", "api_key", "seen_at",
                       "task_id", "held", "banner", "confirmed", "hold_until"}
    assert st["status"] == "rejected" and st["held"] is True and st["utilization"] == 0.97
    assert datetime.fromisoformat(st["resets_at"]) == resets
    assert st["banner"]["kind"] == "limit"
    assert st["banner"]["text"] == f"Your Claude plan limit was reached. Work starts again at " \
                                   f"{resets + timedelta(minutes=1):%H:%M}."
    # a new credential: the old one's status is not this person's any more
    u.put("/me/credentials", json={"claude_token": "n" + uuid.uuid4().hex, "claude_kind": "oauth_token"})
    assert u.get("/plan/status").json()["status"] is None
    client.delete(f"/plan/status/{u.user_id}")


def test_api_key_and_fallback_users(client):
    k = _user("api_key")
    assert k.get("/plan/status").json() == {"owner": k.user_id, "api_key": True, "plan_applies": False,
                                            "status": None}
    f = login_as(f"pla-fb-{uuid.uuid4().hex[:8]}@test.local")  # no own credential: the org key
    me = f.get("/plan/status").json()
    assert me["owner"] == "org" and me["api_key"] is True and me["plan_applies"] is False


def test_all_is_admin_only_and_names_people(client, member_client):
    u = _user()
    _store(u.user_id, "allowed_warning", _now() + timedelta(hours=2))
    assert member_client.get("/plan/status/all").status_code == 403
    assert member_client.delete(f"/plan/status/{u.user_id}").status_code == 403
    rows = client.get("/plan/status/all").json()
    mine = [r for r in rows if r["credential_owner"] == u.user_id]
    assert len(mine) == 1 and mine[0]["user_id"] == u.user_id and mine[0]["name"]
    assert mine[0]["banner"]["kind"] == "warning" and mine[0]["held"] is False
    r = client.delete(f"/plan/status/{u.user_id}")
    assert r.json() == {"removed": True, "tasks": [], "woken": []}
    assert client.delete(f"/plan/status/{u.user_id}").json()["removed"] is False


def test_try_now_for_org_clears_only_org_waits(client):
    plan_user = _user()
    fb_user = login_as(f"pla-fb2-{uuid.uuid4().hex[:8]}@test.local")
    later = _now() + timedelta(hours=1)

    async def mk():
        ids = {}
        async with SessionLocal() as s:
            for who, uid in (("plan", plan_user.user_id), ("fb", fb_user.user_id)):
                tid = uuid.uuid4().hex
                s.add(Task(id=tid, title="t", prompt="p", root_id=tid, status=Status.QUEUED, created_by=uid,
                           not_before=later, wait_reason="Paused — x"))
                ids[who] = tid
            await s.commit()
        return ids

    ids = asyncio.run(mk())
    try:
        r = client.delete("/plan/status/org").json()
        assert ids["fb"] in r["tasks"] and ids["plan"] not in r["tasks"]
        r = client.delete(f"/plan/status/{plan_user.user_id}").json()
        assert ids["plan"] in r["tasks"]
    finally:
        async def end():
            async with SessionLocal() as s:
                for tid in ids.values():
                    t = await s.get(Task, tid)
                    t.status = Status.CANCELLED
                await s.commit()
        asyncio.run(end())


# ---- workflow steps (Review Focus 4) --------------------------------------------------------------------


async def _make_run(stages, owner):
    async with SessionLocal() as s:
        run = WorkflowRun(workflow_id="w", name="r7a", input="the input", stages=stages, project_ids=[],
                          status="pending", current_stage=0, stage_state={}, created_by=owner)
        s.add(run)
        await s.commit()
        return run.id


def test_workflow_step_is_held_while_the_plan_is_at_its_limit(client, fakes, agents):
    a, _ = agents
    u = _user()
    resets = (_now() + timedelta(hours=1)).replace(microsecond=0)
    ref = _ref(u.user_id)
    _store(u.user_id, "rejected", resets, ref=ref)

    async def go():
        rid = await _make_run([agent("A", a)], u.user_id)
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        run = await _run(rid)
        held = dict(run.stage_state["0"]), run.status, has(run, "held: waiting for the Claude plan limit to reset")
        assert await _tasks(rid) == []
        await r._advance(rid)  # still held: nothing starts, nothing fails
        assert await _tasks(rid) == []
        # the limit is over (Try now / reset passed): the step starts
        await pl.record({"owner": u.user_id, "api_key": False, "ref": ref},
                        {"status": "allowed", "resets_at": None, "utilization": None, "raw": {}}, None)
        await r._advance(rid)
        started = await _tasks(rid)
        run = await _run(rid)
        return held, started, dict(run.stage_state["0"]), run.status

    (st, run_status, logged), started, after, after_status = asyncio.run(go())
    assert st["status"] == "held" and st["plan_wait"] is True
    assert st["reason"] == pl.paused_text(resets)
    assert datetime.fromisoformat(st["not_before"]) == resets + timedelta(minutes=1)
    assert run_status == "running" and logged
    assert len(started) == 1 and after["status"] == "running" and "plan_wait" not in after
    assert "not_before" not in after
    assert after_status == "running"
    client.delete(f"/plan/status/{u.user_id}")


def test_workflow_task_waiting_for_the_reset_shows_on_the_step(client, fakes, agents):
    """A step whose task hit the limit mid-run: the task waits queued; the step stays at work with the
    sentence, never failed, and finishes normally once the task does."""
    a, _ = agents
    u = _user()
    nb = (_now() + timedelta(hours=1)).replace(microsecond=0)

    async def go():
        rid = await _make_run([agent("A", a)], u.user_id)
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        (t,) = await _tasks(rid)
        async with SessionLocal() as s:
            row = await s.get(Task, t.id)
            row.status = Status.QUEUED
            row.not_before = nb
            row.wait_reason = "Paused — your Claude plan limit was reached. Starts again at 14:05."
            await s.commit()
        await r._advance(rid)
        run = await _run(rid)
        waiting = dict(run.stage_state["0"]), run.status
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        run = await _run(rid)
        return waiting, dict(run.stage_state["0"]), run.status

    (st, status), done, final = asyncio.run(go())
    assert st["status"] == "running" and st["plan_wait"] is True and st["reason"].startswith("Paused")
    assert datetime.fromisoformat(st["not_before"]) == nb
    assert status == "running"
    assert done["status"] == "done" and "plan_wait" not in done and "reason" not in done
    assert "not_before" not in done
    assert final == "completed"


def test_other_holds_drop_the_plan_marks():
    """M4: a pre-flight or budget hold that follows a plan hold is not shown as a plan wait."""
    from app import workflow_runner as wr

    row = PlanLimitStatus(credential_owner="x", status="rejected", resets_at=_now() + timedelta(hours=1),
                          raw={}, seen_at=_now(), api_key=False, confirmed=False)
    run = WorkflowRun(workflow_id="w", name="m4", input="", stages=[{"name": "A", "items": []}],
                      project_ids=[], status="running", current_stage=0, stage_state={}, log=[], budget_usd=1.0)
    wr._plan_hold(run, 0, {}, row)
    assert run.stage_state["0"]["plan_wait"] is True and run.stage_state["0"]["not_before"]
    asyncio.run(wr._hold(None, run, 0, dict(run.stage_state["0"]), "waiting for the brief"))
    st = run.stage_state["0"]
    assert st["status"] == "held" and st["reason"] == "waiting for the brief"
    assert "plan_wait" not in st and "not_before" not in st
    wr._plan_hold(run, 0, dict(st), row)
    wr._budget_hold(run, 0, dict(run.stage_state["0"]), 2.0)
    st = run.stage_state["0"]
    assert st["status"] == "budget" and "plan_wait" not in st and "not_before" not in st
