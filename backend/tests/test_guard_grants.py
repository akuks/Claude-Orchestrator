"""co-guard server grants, thread scope and deny-and-resume follow-ups
(Phase 2, Task 6): Review Focus 4 (server half) and 5.

Tasks are inserted directly (RUNNING, or finished for the resume phase); agent
tokens are minted for them exactly as the worker does. The worker's submit is
replaced by a recorder, so no follow-up actually runs."""

import asyncio
import hashlib
import json
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import auth, notifications
from app.constants import Status
from app.database import SessionLocal
from app.guard import co_hook
from app.guard import grants as guard_grants
from app.guard import runtime as guard_runtime
from app.main import app
from app.models import ActionApproval, ActionGrant, Project, Task
from app.routers.guard import DECIDE_MARGIN, GIVING_UP
from conftest import login_as

CO_TOKEN_SHAPE = "co_" + "Z" * 43
GK = hashlib.sha256(b"shell\0git reset --hard").hexdigest()


def _uid():
    return uuid.uuid4().hex[:8]


def _run(coro):
    return asyncio.run(coro)


def _mint(user_id, task_id):
    async def go():
        async with SessionLocal() as s:
            raw = await auth.mint_token(s, user_id, "agent-task", task_id, 3600)
            await s.commit()
            return raw

    return _run(go())


def _bearer(raw):
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {raw}"
    return c


def _task(created_by, project_id=None, status="running", **kw):
    tid = uuid.uuid4().hex

    async def go():
        async with SessionLocal() as s:
            s.add(Task(id=tid, title=kw.pop("title", f"t-{tid[:6]}"), prompt="p", project_id=project_id,
                       root_id=kw.pop("root_id", tid), status=status, created_by=created_by, **kw))
            await s.commit()

    _run(go())
    return tid


def _set(model, pk, **fields):
    async def go():
        async with SessionLocal() as s:
            row = await s.get(model, pk)
            for k, v in fields.items():
                setattr(row, k, v)
            await s.commit()

    _run(go())


def _get(model, pk):
    async def go():
        async with SessionLocal() as s:
            return await s.get(model, pk)

    return _run(go())


def _rows(model, *where):
    async def go():
        async with SessionLocal() as s:
            return list((await s.execute(select(model).where(*where))).scalars().all())

    return _run(go())


def _body(task_id, **over):
    b = {"task_id": task_id, "rule_ids": ["git-reset-hard"], "tool": "Bash", "summary": "git reset --hard",
         "cwd": "/w", "reason": "discards local changes", "command_hash": "a" * 64, "grant_key": GK}
    b.update(over)
    return b


def _file(agent, task_id, key=None, **over):
    headers = {"Idempotency-Key": key} if key else {}
    return agent.post("/guard/approvals", json=_body(task_id, **over), headers=headers)


@pytest.fixture(scope="module")
def world(client):
    """Project P (on_no_answer deny) and R (deny_and_resume): owner O
    (maintainer, owns the tasks), second maintainer M, contributor C, outsider X."""
    tag = _uid()
    p = client.post("/projects", json={"name": f"gg-{tag}"}).json()
    r = client.post("/projects", json={"name": f"gg-r-{tag}"}).json()
    o = login_as(f"gg-owner-{tag}@test.local")
    m = login_as(f"gg-maint-{tag}@test.local")
    c = login_as(f"gg-contrib-{tag}@test.local")
    x = login_as(f"gg-out-{tag}@test.local")
    for proj in (p, r):
        res = client.put(f"/projects/{proj['id']}/members", json=[
            {"subject_type": "user", "subject_id": o.user_id, "role": "maintainer"},
            {"subject_type": "user", "subject_id": m.user_id, "role": "maintainer"},
            {"subject_type": "user", "subject_id": c.user_id, "role": "contributor"},
        ])
        assert res.status_code == 200, res.text
    _set(Project, r["id"], on_no_answer="deny_and_resume")
    return {"p": p, "r": r, "o": o, "m": m, "c": c, "x": x, "admin": client}


@pytest.fixture
def task(world):
    tid = _task(world["o"].user_id, world["p"]["id"])
    return tid, _bearer(_mint(world["o"].user_id, tid))


@pytest.fixture
def rtask(world):
    """A running task of O in the deny_and_resume project R."""
    tid = _task(world["o"].user_id, world["r"]["id"], session_id=f"sess-{_uid()}")
    return tid, _bearer(_mint(world["o"].user_id, tid))


@pytest.fixture
def submitted(monkeypatch):
    got = []

    async def submit(task_id, priority, created_at):
        got.append(task_id)

    monkeypatch.setattr(app.state.worker, "submit", submit)
    return got


@pytest.fixture
def no_webhooks(monkeypatch):
    sent = []
    monkeypatch.setattr(notifications, "post_in_background", lambda *a: sent.append(a))
    return sent


def _step(world, root, status="running", project="p", **kw):
    """Another step of thread ``root`` (a follow-up), with its agent client."""
    tid = _task(world["o"].user_id, world[project]["id"], status=status, root_id=root, parent_task_id=root, **kw)
    return tid, _bearer(_mint(world["o"].user_id, tid))


def _approve_run(world, agent, tid, **over):
    rid = _file(agent, tid, **over).json()["id"]
    r = world["m"].post(f"/guard/approvals/{rid}/approve", json={"scope": "run"})
    assert r.status_code == 200, r.text
    return rid, r.json()


def _past(seconds=1):
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


# ---- scope keys ----------------------------------------------------------------------------------


def test_scope_keys_match_the_hook():
    tool_keys = guard_grants.scope_keys(["db-write", "mcp-require-approval", "db-write"], "mcp__PG__query", GK)
    assert tool_keys == [("db-write", "tool", "mcp__pg__query"), ("mcp-require-approval", "tool", "mcp__pg__query")]
    assert guard_grants.scope_keys(["guest-quarantine", "sql-destroy", "org.x"], "Bash", GK) == [
        ("guest-quarantine", "rule", "*"), ("sql-destroy", "command", GK), ("org.x", "command", GK)]
    assert guard_grants.scope_keys(["db-write"], "Bash", GK) == [("db-write", "command", GK)]
    for rid in ("guest-quarantine", "db-write", "unknown-tool"):
        assert guard_grants.scope_keys([rid], "mcp__a__b", GK)[0][1:] == co_hook.scope_keys(rid, "mcp__a__b", GK)
    assert co_hook.grant_key("Bash", {"command": "git reset --hard", "description": "x"}) == GK


def test_hard_rules_are_never_grantable():
    assert not guard_grants.grantable("readonly-source") and not guard_grants.grantable("self-protect")
    assert not guard_grants.grantable("no-such-rule")
    assert guard_grants.grantable("rm-rf-outside") and guard_grants.grantable("repo.x")


# ---- run grants and POST cover -------------------------------------------------------------------


def test_approve_for_run_creates_one_grant_per_rule(world, task):
    tid, agent = task
    rid, card = _approve_run(world, agent, tid, rule_ids=["git-reset-hard", "guest-quarantine"])
    assert card["scope"] == "run" and len(card["grant_ids"]) == 2
    gs = sorted(_rows(ActionGrant, ActionGrant.approval_id == rid), key=lambda g: g.rule_id)
    assert [(g.rule_id, g.key_kind, g.key, g.scope, g.root_id) for g in gs] == [
        ("git-reset-hard", "command", GK, "run", tid), ("guest-quarantine", "rule", "*", "run", tid)]
    g = gs[0]
    ttl = (g.expires_at - g.created_at).total_seconds()
    assert ttl == guard_grants.RUN_GRANT_TTL and g.created_by == world["m"].user_id
    # the inline hook sees an ordinary approval
    assert agent.get(f"/guard/approvals/{rid}").json()["status"] == "approved"


def test_card_shows_run_keys(world, task):
    tid, agent = task
    rid = _file(agent, tid, rule_ids=["guest-quarantine", "readonly-source"]).json()["id"]
    card = world["m"].get(f"/guard/approvals/{rid}").json()
    assert card["run_keys"] == [{"rule_id": "guest-quarantine", "key_kind": "rule", "key": "*"}]
    assert card["delivery"] == "inline" and card["resume_until"] is None and card["followup_task_id"] is None


def test_same_thread_call_is_auto_approved_and_recorded(world, task, no_webhooks):
    tid, agent = task
    _, card = _approve_run(world, agent, tid)
    step, step_agent = _step(world, tid)
    r = _file(step_agent, step, key="k1", summary="git reset --hard " + CO_TOKEN_SHAPE, command_hash="c" * 64)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["status"] == "approved" and out["command_hash"] == "c" * 64 and out["grant_ids"] == card["grant_ids"]
    row = _get(ActionApproval, out["id"])
    assert row.status == "approved" and row.task_id == step and row.decided_by == world["m"].user_id
    assert row.decision_reason == f"covered by run grant {card['grant_ids'][0]}"
    assert row.grant_ids == card["grant_ids"] and CO_TOKEN_SHAPE not in row.summary  # redacted
    assert _get(ActionGrant, card["grant_ids"][0]).use_count == 1
    assert no_webhooks == []  # no notification for a covered call
    # the hook's poll (if any) reads approved
    assert step_agent.get(f"/guard/approvals/{out['id']}").json()["status"] == "approved"


def test_thread_b_is_not_covered(world, task):
    tid, agent = task
    _approve_run(world, agent, tid)
    other = _task(world["o"].user_id, world["p"]["id"])
    r = _file(_bearer(_mint(world["o"].user_id, other)), other)
    assert r.status_code == 201 and r.json()["status"] == "pending"


@pytest.mark.parametrize("over", [
    {"grant_key": "d" * 64},  # another command
    {"rule_ids": ["git-reset-hard", "db-write"]},  # a second rule in the same command
    {"rule_ids": ["sql-destroy"]},  # another rule
])
def test_cover_needs_every_rule_and_the_same_key(world, task, over):
    tid, agent = task
    _approve_run(world, agent, tid)
    step, step_agent = _step(world, tid)
    assert _file(step_agent, step, **over).json()["status"] == "pending"


def test_db_write_run_grant_does_not_cover_sql_destroy(world, task):
    tid, agent = task
    _approve_run(world, agent, tid, rule_ids=["db-write"], tool="mcp__pg__query")
    step, step_agent = _step(world, tid)
    assert _file(step_agent, step, rule_ids=["db-write"], tool="mcp__pg__query",
                 grant_key="e" * 64).json()["status"] == "approved"  # the tool key: any input
    assert _file(step_agent, step, rule_ids=["sql-destroy"], tool="mcp__pg__query").json()["status"] == "pending"


def test_quarantine_grant_covers_the_rule_only(world, task):
    tid, agent = task
    _approve_run(world, agent, tid, rule_ids=["guest-quarantine"], grant_key="1" * 64)
    step, step_agent = _step(world, tid)
    assert _file(step_agent, step, rule_ids=["guest-quarantine"], grant_key="2" * 64).json()["status"] == "approved"
    assert _file(step_agent, step, rule_ids=["guest-quarantine", "git-reset-hard"],
                 grant_key="3" * 64).json()["status"] == "pending"


def test_hard_rule_is_never_covered(world, task):
    tid, agent = task
    rid = _file(agent, tid, rule_ids=["readonly-source"]).json()["id"]
    r = world["m"].post(f"/guard/approvals/{rid}/approve", json={"scope": "run"})
    assert r.status_code == 409 and _get(ActionApproval, rid).status == "pending"  # nothing recorded

    async def cover():
        async with SessionLocal() as s:
            s.add(ActionGrant(root_id=tid, scope="run", rule_id="readonly-source", key_kind="command", key=GK,
                              created_at=datetime.now(timezone.utc),
                              expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
            await s.commit()
            return await guard_grants.thread_cover(s, root_id=tid, rule_ids=["readonly-source"], tool="Bash",
                                                   grant_key=GK, now=datetime.now(timezone.utc))

    assert _run(cover()) is None


def test_run_scope_needs_a_grant_key(world, task):
    tid, agent = task
    rid = _file(agent, tid, grant_key="").json()["id"]
    r = world["m"].post(f"/guard/approvals/{rid}/approve", json={"scope": "run"})
    assert r.status_code == 409 and "once" in r.json()["detail"]
    assert world["m"].post(f"/guard/approvals/{rid}/approve").status_code == 200  # once still works


def test_grant_key_must_be_hex(task):
    tid, agent = task
    assert _file(agent, tid, grant_key="xyz").status_code == 422
    assert _file(agent, tid, grant_key="A" * 64).status_code == 422


def test_idempotency_compares_the_grant_key(task):
    tid, agent = task
    assert _file(agent, tid, key="gk").status_code == 201
    assert _file(agent, tid, key="gk").status_code == 200
    r = _file(agent, tid, key="gk", grant_key="f" * 64)
    assert r.status_code == 409 and r.json()["detail"] == "conflicting request"


@pytest.mark.parametrize("change", ["expired", "revoked"])
def test_expired_or_revoked_grants_stop_post_answers(world, task, change):
    tid, agent = task
    _, card = _approve_run(world, agent, tid)
    gid = card["grant_ids"][0]
    step, step_agent = _step(world, tid)
    if change == "revoked":
        r = world["m"].post(f"/guard/grants/{gid}/revoke")
        assert r.status_code == 200 and r.json()["revoked_by"] == world["m"].user_id and not r.json()["active"]
        assert world["m"].post(f"/guard/grants/{gid}/revoke").status_code == 409
    else:
        _set(ActionGrant, gid, expires_at=_past())
    assert _file(step_agent, step).json()["status"] == "pending"


# ---- grants list and revoke (people only) --------------------------------------------------------


def test_grants_list_and_permissions(world, task):
    tid, agent = task
    _, card = _approve_run(world, agent, tid)
    gid = card["grant_ids"][0]
    step, _ = _step(world, tid)
    for who, can in (("m", True), ("admin", True), ("o", False), ("c", False)):
        r = world[who].get("/guard/grants", params={"root_id": step})  # any step names its thread
        assert r.status_code == 200, (who, r.text)
        assert [(g["id"], g["can_revoke"]) for g in r.json()] == [(gid, can)], who
    assert world["x"].get("/guard/grants", params={"root_id": tid}).status_code == 404
    # without root_id: every grant whose task the viewer can see
    assert gid in [g["id"] for g in world["m"].get("/guard/grants").json()]
    assert gid in [g["id"] for g in world["c"].get("/guard/grants").json()]
    assert gid not in [g["id"] for g in world["x"].get("/guard/grants").json()]
    assert world["x"].post(f"/guard/grants/{gid}/revoke").status_code == 404
    assert world["o"].post(f"/guard/grants/{gid}/revoke").status_code == 403  # R25: the task owner
    assert world["c"].post(f"/guard/grants/{gid}/revoke").status_code == 403
    assert world["m"].post("/guard/grants/nope/revoke").status_code == 404
    _set(ActionGrant, gid, expires_at=_past())
    assert world["m"].get("/guard/grants", params={"root_id": tid}).json() == []
    old = world["m"].get("/guard/grants", params={"root_id": tid, "include_inactive": True}).json()
    assert [g["id"] for g in old] == [gid] and not old[0]["active"] and not old[0]["can_revoke"]


def test_agent_tokens_cannot_approve_revoke_or_list_grants(world, task):
    tid, agent = task
    rid, card = _approve_run(world, agent, tid)
    other = _file(agent, tid, key="other").json()["id"]
    assert agent.post(f"/guard/approvals/{other}/approve", json={"scope": "run"}).status_code == 403
    assert agent.post(f"/guard/grants/{card['grant_ids'][0]}/revoke").status_code == 403
    assert agent.get("/guard/grants", params={"root_id": tid}).status_code == 403


# ---- /guard/report granted records ---------------------------------------------------------------


def _granted(agent, tid, ids):
    return agent.post("/guard/report", json={
        "task_id": tid, "event": "PreToolUse", "decision": "granted", "grant_ids": ids, "tool": "Bash",
        "rule_ids": ["git-reset-hard"], "summary": "git reset --hard", "tool_use_id": "tuG"})


def _live(tid):
    got = []

    async def emit(etype, payload):
        got.append(payload)

    app.state.worker._guard_live[tid] = {"emit": emit, "reported": Counter()}
    return got


def _once_grant(root, task_id, **kw):
    gid = uuid.uuid4().hex
    now = datetime.now(timezone.utc)

    async def go():
        async with SessionLocal() as s:
            s.add(ActionGrant(id=gid, root_id=root, task_id=task_id, scope="once", rule_id="git-reset-hard",
                              key_kind="command", key=GK, created_at=now, expires_at=now + timedelta(hours=1), **kw))
            await s.commit()

    _run(go())
    return gid


def test_granted_report_spends_a_once_grant_and_counts(world, task):
    tid, agent = task
    gid = _once_grant(tid, tid)
    got = _live(tid)
    try:
        r = _granted(agent, tid, [gid])
    finally:
        app.state.worker._guard_live.pop(tid, None)
    assert r.json() == {"accepted": True} and got[0]["decision"] == "granted" and got[0]["grant_ids"] == [gid]
    g = _get(ActionGrant, gid)
    assert g.used_at is not None and g.use_count == 1


def test_granted_report_with_a_run_grant_of_the_thread(world, task):
    tid, agent = task
    _, card = _approve_run(world, agent, tid)
    step, step_agent = _step(world, tid)
    _live(step)
    try:
        assert _granted(step_agent, step, card["grant_ids"]).json() == {"accepted": True}
    finally:
        app.state.worker._guard_live.pop(step, None)
    assert _get(ActionGrant, card["grant_ids"][0]).use_count == 1


def test_granted_report_refuses_foreign_or_dead_grants(world, task):
    tid, agent = task
    other = _task(world["o"].user_id, world["p"]["id"])
    sibling, _ = _step(world, tid)
    cases = {
        "other thread run": None,
        "sibling once": _once_grant(tid, sibling),
        "revoked": _once_grant(tid, tid, revoked_at=datetime.now(timezone.utc)),
        "other thread once": _once_grant(other, other),
    }
    oagent = _bearer(_mint(world["o"].user_id, other))
    _, card = _approve_run(world, oagent, other)
    cases["other thread run"] = card["grant_ids"][0]
    _live(tid)
    try:
        for name, gid in cases.items():
            assert _granted(agent, tid, [gid]).json() == {"accepted": False}, name
        assert _granted(agent, tid, []).json() == {"accepted": False}
        assert _granted(agent, tid, "x").json() == {"accepted": False}
    finally:
        app.state.worker._guard_live.pop(tid, None)
    assert _get(ActionGrant, cases["sibling once"]).used_at is None


def test_spool_ingestion_spends_unreported_once_grants(world, task):
    """Carry-over: durable, from the worker's ingestion (a retry wipes the spool)."""
    from app.worker import WorkerManager

    tid, _ = task
    reported, unreported = _once_grant(tid, tid), _once_grant(tid, tid)
    rec = {"event": "PreToolUse", "decision": "granted", "tool": "Bash", "rule_ids": ["git-reset-hard"],
           "summary": "git reset --hard", "tool_use_id": "tu1"}
    w = WorkerManager()
    got = []

    async def emit(etype, payload):
        got.append(payload)

    async def go():
        d = guard_runtime.make_task_dir(tid)
        with open(d / guard_runtime.SPOOL_NAME, "w") as f:
            f.write(json.dumps(dict(rec, grant_ids=[reported], ts=1.0)) + "\n")
            f.write(json.dumps(dict(rec, grant_ids=[unreported], tool_use_id="tu2", ts=2.0)) + "\n")
        w._guard_live[tid] = {"emit": emit, "reported": Counter()}
        assert await w.report_guard(tid, dict(rec, grant_ids=[reported]))
        await w._ingest_guard_spool(tid, emit)

    _run(go())
    assert [p["tool_use_id"] for p in got] == ["tu1", "tu2"]
    for gid in (reported, unreported):
        assert _get(ActionGrant, gid).used_at is not None
    assert _get(ActionGrant, unreported).use_count == 1


def test_leftover_spool_is_spent_before_a_new_attempt(world, task):
    """_prepare_guard spends a crashed attempt's spool before gather and the wipe."""
    from app.worker import WorkerManager

    tid, _ = task
    gid = _once_grant(tid, tid)

    async def go():
        d = guard_runtime.make_task_dir(tid)
        (d / guard_runtime.SPOOL_NAME).write_text(json.dumps({"event": "PreToolUse", "decision": "granted",
                                                              "grant_ids": [gid]}) + "\n")
        await WorkerManager._spend_spool_grants(tid, strict=True)
        guard_runtime.remove_task_dir(tid)

    _run(go())
    assert _get(ActionGrant, gid).used_at is not None


# ---- deny-and-resume ------------------------------------------------------------------------------


def _block(rid, rules="git-reset-hard", summary="git reset --hard"):
    return (f"co-guard: a person approved request {rid} ({rules}). You may now run this once, exactly as before:\n"
            f"```\n{summary}\n```\nThen continue the task.")


def _to_resume(rid, tid, status="completed"):
    """The inline wait is over (the hook gave up) and the step finished."""
    _set(ActionApproval, rid, expires_at=_past())
    _set(Task, tid, status=status)


def test_resume_until_only_with_deny_and_resume(world, task, rtask):
    tid, agent = task
    assert _file(agent, tid).json()["resume_until"] is None  # on_no_answer=deny: Phase 1
    rtid, ragent = rtask
    out = _file(ragent, rtid).json()
    until = datetime.fromisoformat(out["resume_until"])
    assert abs((until - datetime.now(timezone.utc)).total_seconds() - guard_grants.RESUME_WINDOW) < 30
    assert _file(ragent, rtid, key="nokey", grant_key="").json()["resume_until"] is None  # no grant key


def test_resume_phase_stays_pending_and_is_listed(world, rtask):
    rtid, ragent = rtask
    rid = _file(ragent, rtid).json()["id"]
    assert ragent.get(f"/guard/approvals/{rid}").json()["status"] == "pending"  # inline
    _to_resume(rid, rtid)
    assert ragent.get(f"/guard/approvals/{rid}").json()["status"] == "pending"
    assert _get(ActionApproval, rid).delivery == "resume"
    card = world["m"].get(f"/guard/approvals/{rid}").json()
    assert card["status"] == "pending" and card["delivery"] == "resume" and card["can_decide"]
    # the Home / badge count (the pending list) includes it
    assert rid in [a["id"] for a in world["m"].get("/guard/approvals").json()]
    assert rid not in [a["id"] for a in world["m"].get("/guard/approvals", params={"status": "expired"}).json()]


def test_resume_phase_expires_after_24h(world, rtask):
    rtid, ragent = rtask
    rid = _file(ragent, rtid).json()["id"]
    _to_resume(rid, rtid)
    _set(ActionApproval, rid, resume_until=_past())
    assert rid not in [a["id"] for a in world["m"].get("/guard/approvals").json()]
    assert rid in [a["id"] for a in world["m"].get("/guard/approvals", params={"status": "expired"}).json()]
    assert world["m"].post(f"/guard/approvals/{rid}/approve").status_code == 409
    assert _get(ActionApproval, rid).status == "expired"


def test_cancelled_parent_expires_the_request(world, rtask):
    rtid, ragent = rtask
    rid = _file(ragent, rtid).json()["id"]
    _to_resume(rid, rtid, status="cancelled")
    assert world["m"].post(f"/guard/approvals/{rid}/approve").status_code == 409
    assert _get(ActionApproval, rid).status == "expired"
    assert rid not in [a["id"] for a in world["m"].get("/guard/approvals").json()]


def test_no_followup_after_the_thread_was_cancelled(world, rtask, submitted):
    rtid, ragent = rtask
    rid = _file(ragent, rtid).json()["id"]
    _to_resume(rid, rtid)
    _step(world, rtid, status="cancelled", project="r")
    r = world["m"].post(f"/guard/approvals/{rid}/approve")
    assert r.status_code == 409 and "cancelled" in r.json()["detail"]
    assert _get(ActionApproval, rid).status == "pending" and submitted == []


def test_decision_inside_the_margin_is_409_giving_up(world, rtask):
    rtid, ragent = rtask
    rid = _file(ragent, rtid).json()["id"]
    _set(ActionApproval, rid, expires_at=datetime.now(timezone.utc) + timedelta(seconds=DECIDE_MARGIN - 1))
    for path in ("approve", "reject"):
        r = world["m"].post(f"/guard/approvals/{rid}/{path}")
        assert r.status_code == 409 and r.json()["detail"] == GIVING_UP
    assert _get(ActionApproval, rid).status == "pending"


def test_deny_mode_margin_is_unchanged(world, task):
    tid, agent = task
    rid = _file(agent, tid).json()["id"]
    _set(ActionApproval, rid, expires_at=datetime.now(timezone.utc) + timedelta(seconds=DECIDE_MARGIN - 1))
    r = world["m"].post(f"/guard/approvals/{rid}/approve")
    assert r.status_code == 409 and r.json()["detail"] == "expired"
    _set(ActionApproval, rid, expires_at=_past())
    assert agent.get(f"/guard/approvals/{rid}").json()["status"] == "expired"


def test_filed_report_keeps_the_request_pending(world, rtask, task):
    rtid, ragent = rtask
    rid = _file(ragent, rtid).json()["id"]
    rec = {"task_id": rtid, "event": "PreToolUse", "decision": "deny", "status": "filed", "request_id": rid}
    assert ragent.post("/guard/report", json=rec).status_code == 202
    row = _get(ActionApproval, rid)
    assert row.status == "pending" and row.delivery == "resume"
    # the hook gave up early: a decision now is a resume decision (no margin wait)
    assert world["m"].get(f"/guard/approvals/{rid}").json()["delivery"] == "resume"
    # a plain deny still expires (Phase 1), and "filed" without resume_until is a plain deny
    rid2 = _file(ragent, rtid, key="2").json()["id"]
    ragent.post("/guard/report", json=dict(rec, request_id=rid2, status="timeout"))
    assert _get(ActionApproval, rid2).status == "expired"
    tid, agent = task
    rid3 = _file(agent, tid).json()["id"]
    agent.post("/guard/report", json=dict(rec, task_id=tid, request_id=rid3))
    assert _get(ActionApproval, rid3).status == "expired"


def test_resume_approval_queues_one_followup_with_a_once_grant(world, rtask, submitted):
    rtid, ragent = rtask
    sess = _get(Task, rtid).session_id
    rid = _file(ragent, rtid, summary="git reset --hard " + CO_TOKEN_SHAPE).json()["id"]
    _to_resume(rid, rtid)
    r = world["m"].post(f"/guard/approvals/{rid}/approve")
    assert r.status_code == 200, r.text
    card = r.json()
    fu = card["followup_task_id"]
    assert fu and submitted == [fu] and card["scope"] == "once" and card["delivery"] == "resume"
    t = _get(Task, fu)
    assert t.status == Status.QUEUED and t.root_id == rtid and t.parent_task_id == rtid
    assert t.created_by == world["o"].user_id  # the thread owner's credentials
    assert t.resume_session_id == sess  # the thread's latest session
    assert CO_TOKEN_SHAPE not in t.prompt
    assert t.prompt == _block(rid, summary=_get(ActionApproval, rid).summary)
    gs = _rows(ActionGrant, ActionGrant.task_id == fu)
    assert [(g.scope, g.rule_id, g.key_kind, g.key, g.root_id) for g in gs] == [
        ("once", "git-reset-hard", "command", GK, rtid)]
    assert card["grant_ids"] == [gs[0].id]
    # the request's own hook is not answered by it
    assert ragent.get(f"/guard/approvals/{rid}").json()["status"] == "pending"


def test_second_resume_approval_joins_the_queued_followup(world, rtask, submitted):
    rtid, ragent = rtask
    a = _file(ragent, rtid, key="a").json()["id"]
    b = _file(ragent, rtid, key="b", rule_ids=["db-write"], summary="psql -h prod -c 'update t set x=1'",
              grant_key="9" * 64).json()["id"]
    _to_resume(a, rtid)
    _set(ActionApproval, b, expires_at=_past())
    fu = world["m"].post(f"/guard/approvals/{a}/approve").json()["followup_task_id"]
    r = world["admin"].post(f"/guard/approvals/{b}/approve")
    assert r.json()["followup_task_id"] == fu and submitted == [fu]  # joined, not queued twice
    prompt = _get(Task, fu).prompt
    assert prompt == _block(a) + "\n\n" + _block(b, "db-write", "psql -h prod -c 'update t set x=1'")
    assert sorted(g.key for g in _rows(ActionGrant, ActionGrant.task_id == fu)) == sorted([GK, "9" * 64])
    assert len(_rows(Task, Task.root_id == rtid, Task.id != rtid)) == 1


def test_resume_approval_is_deferred_while_a_step_is_active(world, rtask, submitted):
    rtid, ragent = rtask
    rid = _file(ragent, rtid).json()["id"]
    _set(ActionApproval, rid, expires_at=_past())  # the hook gave up; the step still runs
    card = world["m"].post(f"/guard/approvals/{rid}/approve").json()
    assert card["status"] == "approved" and card["followup_task_id"] is None and submitted == []
    assert _rows(ActionGrant, ActionGrant.approval_id == rid) == []
    # the step finishes: the worker's _finish spawns the deferred follow-up
    from app.worker import WorkerManager

    w = WorkerManager()
    w.submit = app.state.worker.submit
    _run(w._finish(rtid, Status.COMPLETED))
    fu = _get(ActionApproval, rid).followup_task_id
    assert fu and submitted == [fu]
    assert _get(Task, fu).prompt == _block(rid)
    assert [g.task_id for g in _rows(ActionGrant, ActionGrant.approval_id == rid)] == [fu]


def test_join_only_while_the_followup_is_still_queued(world, rtask, submitted):
    rtid, ragent = rtask
    a = _file(ragent, rtid, key="a").json()["id"]
    b = _file(ragent, rtid, key="b").json()["id"]
    _to_resume(a, rtid)
    _set(ActionApproval, b, expires_at=_past())
    fu = world["m"].post(f"/guard/approvals/{a}/approve").json()["followup_task_id"]
    _set(Task, fu, status="running")  # the worker took it
    assert world["m"].post(f"/guard/approvals/{b}/approve").json()["followup_task_id"] is None  # deferred
    assert _get(Task, fu).prompt == _block(a)
    _set(Task, fu, status="completed")
    assert _run(guard_grants.after_task_finished(fu, submit=app.state.worker.submit))
    assert submitted[-1] == _get(ActionApproval, b).followup_task_id != fu


def test_cancelled_step_drops_deferred_followups(world, rtask, submitted):
    rtid, ragent = rtask
    rid = _file(ragent, rtid).json()["id"]
    _set(ActionApproval, rid, expires_at=_past())
    world["m"].post(f"/guard/approvals/{rid}/approve")
    step, _ = _step(world, rtid, status="cancelled", project="r")
    _set(Task, rtid, status="completed")
    assert _run(guard_grants.after_task_finished(step, submit=app.state.worker.submit)) is None
    assert submitted == [] and _get(ActionApproval, rid).delivery == "dropped"
    assert _run(guard_grants.after_task_finished(rtid, submit=app.state.worker.submit)) is None


def test_resume_reject_only_records(world, rtask, submitted):
    rtid, ragent = rtask
    rid = _file(ragent, rtid).json()["id"]
    _to_resume(rid, rtid)
    r = world["m"].post(f"/guard/approvals/{rid}/reject", json={"reason": "no"})
    assert r.status_code == 200 and r.json()["status"] == "rejected" and r.json()["followup_task_id"] is None
    assert submitted == [] and _rows(ActionGrant, ActionGrant.approval_id == rid) == []
    assert len(_rows(Task, Task.root_id == rtid)) == 1


def test_resume_two_person_rule(world, rtask):
    rtid, ragent = rtask
    rid = _file(ragent, rtid).json()["id"]
    _to_resume(rid, rtid)
    assert world["o"].post(f"/guard/approvals/{rid}/approve").status_code == 403
    assert world["c"].post(f"/guard/approvals/{rid}/approve").status_code == 403
    assert world["x"].post(f"/guard/approvals/{rid}/approve").status_code == 404


def test_followup_runs_as_the_thread_owner_and_resumes_only_their_session(world, submitted):
    """A step by another user: the follow-up still runs on the thread owner's
    credentials; that user's session is not the owner's, so it starts fresh."""
    root = _task(world["o"].user_id, world["r"]["id"], status="completed", session_id="owner-sess")
    step = _task(world["c"].user_id, world["r"]["id"], root_id=root, parent_task_id=root, session_id="c-sess")
    agent = _bearer(_mint(world["c"].user_id, step))
    rid = _file(agent, step).json()["id"]
    _to_resume(rid, step)
    fu = world["m"].post(f"/guard/approvals/{rid}/approve").json()["followup_task_id"]
    t = _get(Task, fu)
    assert t.created_by == world["o"].user_id and t.parent_task_id == step and t.resume_session_id is None


def test_followup_resumes_the_latest_owner_session(world, submitted):
    root = _task(world["o"].user_id, world["r"]["id"], status="completed", session_id="old-sess")
    step = _task(world["o"].user_id, world["r"]["id"], root_id=root, parent_task_id=root, session_id="new-sess")
    agent = _bearer(_mint(world["o"].user_id, step))
    rid = _file(agent, step).json()["id"]
    _to_resume(rid, step)
    fu = world["m"].post(f"/guard/approvals/{rid}/approve").json()["followup_task_id"]
    assert _get(Task, fu).resume_session_id == "new-sess"


def test_followup_policy_carries_the_once_grant(world, rtask, submitted):
    from app.guard import policy_inputs

    rtid, ragent = rtask
    rid = _file(ragent, rtid).json()["id"]
    _to_resume(rid, rtid)
    fu = world["m"].post(f"/guard/approvals/{rid}/approve").json()["followup_task_id"]

    async def go():
        async with SessionLocal() as s:
            t = await s.get(Task, fu)
            return await policy_inputs.gather(s, task=t, project=None, owner_id=None, mcp=None)

    grants = _run(go()).policy["grants"]
    assert [(g["scope"], g["key"]) for g in grants] == [("once", GK)]
    # the hook would allow exactly this call
    hit = type("H", (), {"rule_id": "git-reset-hard", "hard": False})()
    import time
    assert co_hook.covering_grants([hit], "Bash", GK, grants, set(), time.time()) == [grants[0]["id"]]
    assert co_hook.covering_grants([hit], "Bash", GK, grants, {grants[0]["id"]}, time.time()) is None


def test_deleting_the_thread_removes_its_grants(world, client):
    root = _task(world["o"].user_id, world["p"]["id"], status="completed")
    agent_task = _task(world["o"].user_id, world["p"]["id"], root_id=root, parent_task_id=root)
    agent = _bearer(_mint(world["o"].user_id, agent_task))
    _, card = _approve_run(world, agent, agent_task)
    once = _once_grant(root, agent_task)
    _set(Task, agent_task, status="completed")
    assert client.delete(f"/tasks/{root}").status_code == 204
    assert _rows(ActionGrant, ActionGrant.root_id == root) == []
    assert _get(ActionGrant, once) is None and _get(ActionGrant, card["grant_ids"][0]) is None
