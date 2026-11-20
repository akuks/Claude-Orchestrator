"""Schedules pause without credentials; project automation owner (Phase 3a, Task 4).

A schedule whose owner has no Claude credential (and no org fallback) is
skipped with a reason and run-now returns 409; a project's automation owner
must be a maintainer (or admin) and webhook PR reviews run as them. Every
credential here is an obviously fake test string."""

import asyncio
import datetime
import hashlib
import hmac
import json
import logging
import uuid

from sqlalchemy import select

from app import agent_env
from app.database import SessionLocal
from app.models import Schedule, Task, User
from conftest import login_as

FALLBACK = "sk-test-fallback"


def _uid():
    return uuid.uuid4().hex[:8]


def _project(admin, **extra):
    r = admin.post("/projects", json={"name": f"ao-{_uid()}", **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _grant(admin, pid, user_id, role):
    keep = [
        {k: m[k] for k in ("subject_type", "subject_id", "role")}
        for m in admin.get(f"/projects/{pid}/members").json()
        if m["subject_id"] != user_id
    ]
    keep.append({"subject_type": "user", "subject_id": user_id, "role": role})
    r = admin.put(f"/projects/{pid}/members", json=keep)
    assert r.status_code == 200, r.text


def _set_active(user_id, active):
    async def go():
        async with SessionLocal() as s:
            (await s.get(User, user_id)).active = active
            await s.commit()

    asyncio.run(go())


class _Worker:
    async def submit(self, *a):
        pass


def test_schedule_skipped_without_credential(client, caplog):
    import app.scheduler as sched_mod

    pid = _project(client)
    u = login_as(f"ao-sch-{_uid()}@test.local")
    _grant(client, pid, u.user_id, "contributor")
    r = u.post("/schedules", json={
        "name": f"ao-sch-{_uid()}", "cron": "0 3 * * *", "prompt": "hi",
        "requires_approval": True, "project_id": pid,
    })
    assert r.status_code == 201, r.text
    sch = r.json()

    async def drive():
        async with SessionLocal() as s:
            row = await s.get(Schedule, sch["id"])
            row.next_run_at = datetime.datetime(2000, 1, 1)
            await s.commit()
        await sched_mod.Scheduler(_Worker())._tick()
        async with SessionLocal() as s:
            n = len((await s.execute(select(Task).where(Task.schedule_id == sch["id"]))).scalars().all())
            nxt = (await s.get(Schedule, sch["id"])).next_run_at
        return n, nxt

    assert client.delete("/admin/org-fallback").status_code in (200, 204)
    try:
        r = client.post(f"/schedules/{sch['id']}/run")
        assert r.status_code == 409
        assert r.json()["detail"] == "Schedule owner has no Claude credential"
        with caplog.at_level("WARNING", logger="app.scheduler"):
            n, nxt = asyncio.run(drive())
        assert n == 0
        assert nxt.replace(tzinfo=None) > datetime.datetime(2001, 1, 1)
        assert any("owner has no Claude credential" in m for m in caplog.messages)
    finally:
        r = client.put("/admin/org-fallback", json={"api_key": FALLBACK})
        assert r.status_code == 200, r.status_code

    # With the fallback back, the same schedule runs again.
    assert client.post(f"/schedules/{sch['id']}/run").status_code == 201
    # And with an own credential (no fallback) it runs too.
    r = u.put("/me/credentials", json={"claude_token": f"fake-{uuid.uuid4().hex}", "claude_kind": "api_key"})
    assert r.status_code == 200, r.status_code
    assert client.delete("/admin/org-fallback").status_code in (200, 204)
    try:
        assert client.post(f"/schedules/{sch['id']}/run").status_code == 201
    finally:
        r = client.put("/admin/org-fallback", json={"api_key": FALLBACK})
        assert r.status_code == 200, r.status_code


def test_automation_owner_validation(client):
    pid = _project(client)
    maint = login_as(f"ao-m-{_uid()}@test.local")
    contrib = login_as(f"ao-c-{_uid()}@test.local")
    outsider = login_as(f"ao-o-{_uid()}@test.local")
    team_maint = login_as(f"ao-t-{_uid()}@test.local")
    # The session admin (not a member); another admin would break last-admin tests.
    admin_id = client.get("/auth/me").json()["id"]
    _grant(client, pid, maint.user_id, "maintainer")
    _grant(client, pid, contrib.user_id, "contributor")
    r = client.post("/teams", json={"name": f"ao-team-{_uid()}"})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    assert client.put(f"/teams/{tid}/members", json={"user_ids": [team_maint.user_id]}).status_code == 200
    keep = [
        {k: m[k] for k in ("subject_type", "subject_id", "role")}
        for m in client.get(f"/projects/{pid}/members").json()
    ] + [{"subject_type": "team", "subject_id": tid, "role": "maintainer"}]
    assert client.put(f"/projects/{pid}/members", json=keep).status_code == 200

    # Default: unset (means project creator).
    assert client.get(f"/projects/{pid}").json()["automation_owner_id"] is None

    bad = "Automation owner must be a maintainer of this project"
    for uid in (contrib.user_id, outsider.user_id, "no-such-user"):
        r = maint.patch(f"/projects/{pid}", json={"automation_owner_id": uid})
        assert r.status_code == 400, r.text
        assert r.json()["detail"] == bad
    # A disabled maintainer can't be chosen either.
    other_maint = login_as(f"ao-m2-{_uid()}@test.local")
    _grant(client, pid, other_maint.user_id, "maintainer")
    _set_active(other_maint.user_id, False)
    try:
        r = maint.patch(f"/projects/{pid}", json={"automation_owner_id": other_maint.user_id})
        assert r.status_code == 400 and r.json()["detail"] == bad
    finally:
        _set_active(other_maint.user_id, True)

    # Contributors can't change it at all.
    assert contrib.patch(f"/projects/{pid}", json={"automation_owner_id": maint.user_id}).status_code == 403

    # Maintainer (direct or via team) or any admin is accepted; maintainers may set it.
    for uid in (maint.user_id, team_maint.user_id, admin_id):
        r = maint.patch(f"/projects/{pid}", json={"automation_owner_id": uid})
        assert r.status_code == 200, r.text
        assert r.json()["automation_owner_id"] == uid
    # null resets to the project creator.
    r = maint.patch(f"/projects/{pid}", json={"automation_owner_id": None})
    assert r.status_code == 200, r.text
    assert r.json()["automation_owner_id"] is None


def _pr_hook(client, repo):
    body = json.dumps({
        "action": "opened",
        "number": 7,
        "repository": {"full_name": repo},
        "pull_request": {"number": 7, "head": {"ref": "feat"}, "base": {"ref": "main"}},
    }).encode()
    sig = "sha256=" + hmac.new(b"testsecret", body, hashlib.sha256).hexdigest()
    r = client.post(
        "/webhooks/github", content=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": sig},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _created_by(task_id):
    async def go():
        async with SessionLocal() as s:
            return (await s.get(Task, task_id)).created_by

    return asyncio.run(go())


def test_webhook_runs_as_automation_owner(client):
    repo = f"acme/ao-{_uid()}"
    pid = _project(client, github_repo=repo, auto_review_prs=True)
    creator = client.get("/auth/me").json()["id"]

    # Default: the project creator.
    out = _pr_hook(client, repo)
    assert _created_by(out["task_id"]) == creator

    maint = login_as(f"ao-wh-{_uid()}@test.local")
    _grant(client, pid, maint.user_id, "maintainer")
    r = client.patch(f"/projects/{pid}", json={"automation_owner_id": maint.user_id})
    assert r.status_code == 200, r.text
    out = _pr_hook(client, repo)
    assert _created_by(out["task_id"]) == maint.user_id

    # An inactive automation owner falls back to the creator.
    _set_active(maint.user_id, False)
    try:
        out = _pr_hook(client, repo)
        assert _created_by(out["task_id"]) == creator
    finally:
        _set_active(maint.user_id, True)


def test_webhook_ignored_without_usable_owner(client):
    repo = f"acme/ao-{_uid()}"
    pid = _project(client, github_repo=repo, auto_review_prs=True)
    gone = "no-such-user-" + _uid()

    async def orphan():
        async with SessionLocal() as s:
            from app.models import Project

            p = await s.get(Project, pid)
            p.created_by = gone
            p.automation_owner_id = None
            await s.commit()

    asyncio.run(orphan())

    async def count():
        async with SessionLocal() as s:
            return len((await s.execute(select(Task.id).where(Task.project_id == pid))).all())

    out = _pr_hook(client, repo)
    assert out == {"ok": True, "ignored": "no automation owner"}
    assert asyncio.run(count()) == 0


# ---- Task 3 review follow-ups ------------------------------------------------

def test_agent_env_or_none_swallows_unexpected_errors(monkeypatch, caplog):
    async def boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(agent_env, "build_agent_env", boom)

    async def go():
        async with SessionLocal() as s:
            return await agent_env.agent_env_or_none(s, "user-xyz", purpose="memory update")

    with caplog.at_level(logging.WARNING, logger="app.agent_env"):
        assert asyncio.run(go()) is None
    assert any("memory update" in m and "user-xyz" in m for m in caplog.messages)
