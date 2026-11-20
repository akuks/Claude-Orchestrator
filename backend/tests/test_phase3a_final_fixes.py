"""Phase 3a final review fixes.

Git ignores the system config and only uses gh as a GitHub credential helper
(when the user has a token); a follow-up whose parent session file is gone
starts fresh; the Claude probe runs outside the backend checkout; the webhook
creator fallback needs a project role; schedules expose why they are paused.
Every credential here is an obviously fake test string."""

import asyncio
import datetime
import hashlib
import hmac
import json
import subprocess
import uuid
from pathlib import Path

from sqlalchemy import select

from app import agent_env
from app.config import settings
from app.database import SessionLocal
from app.models import Project, Schedule, Task, TaskEvent
from conftest import env_probe_key, login_as, read_probe_args, wait_task

FALLBACK = "sk-test-fallback"


def _uid():
    return uuid.uuid4().hex[:8]


def _fake(kind: str) -> str:
    return f"fake-{kind}-{uuid.uuid4().hex}"


def _project(admin, **extra):
    r = admin.post("/projects", json={"name": f"ff-{_uid()}", **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _grant(admin, pid, user_id, role):
    keep = [
        {k: m[k] for k in ("subject_type", "subject_id", "role")}
        for m in admin.get(f"/projects/{pid}/members").json()
        if m["subject_id"] != user_id
    ]
    if role:
        keep.append({"subject_type": "user", "subject_id": user_id, "role": role})
    r = admin.put(f"/projects/{pid}/members", json=keep)
    assert r.status_code == 200, r.text


def _build(user_id, project_env=None):
    async def go():
        async with SessionLocal() as s:
            return await agent_env.build_agent_env(s, user_id, project_env=project_env)

    return asyncio.run(go())


def _events(task_id):
    async def go():
        async with SessionLocal() as s:
            return (
                await s.execute(
                    select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.seq)
                )
            ).scalars().all()

    return asyncio.run(go())


# ---- 1. git: no system config, gh helper only with a token -------------------

def _helpers(env, cwd):
    r = subprocess.run(
        ["git", "config", "--show-origin", "--get-all", "credential.helper"],
        env=dict(env), cwd=cwd, capture_output=True, text=True,
    )
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def test_git_ignores_system_config_and_scopes_helper(client, tmp_path):
    u = login_as(f"ff-git-{_uid()}@test.local")
    env = _build(u.user_id, project_env={"GIT_CONFIG_NOSYSTEM": "0"})
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"  # project env can't undo it
    cfg_path = Path(env["GIT_CONFIG_GLOBAL"])
    cfg = cfg_path.read_text()
    assert "gh auth git-credential" not in cfg
    assert "[credential]" in cfg and "helper =" in cfg
    lines = _helpers(env, tmp_path)
    assert lines, "the per-user reset line is visible"
    assert all(ln.startswith(f"file:{cfg_path}\t") for ln in lines), lines
    assert all(ln.split("\t", 1)[1] == "" for ln in lines), lines  # no helper

    gh = _fake("gh")
    r = u.put("/me/credentials", json={"github_token": gh})
    assert r.status_code == 200, r.status_code
    env = _build(u.user_id, project_env={"GIT_CONFIG_NOSYSTEM": "0"})
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    cfg = cfg_path.read_text()
    assert '[credential "https://github.com"]' in cfg
    assert "helper = !gh auth git-credential" in cfg
    assert gh not in cfg  # gh reads GH_TOKEN from env; no secret in the file
    for ln in _helpers(env, tmp_path):
        assert ln.startswith(f"file:{cfg_path}\t"), ln
    r = subprocess.run(
        ["git", "config", "--get-urlmatch", "credential.helper", "https://github.com"],
        env=dict(env), cwd=tmp_path, capture_output=True, text=True,
    )
    assert r.stdout.strip() == "!gh auth git-credential"


def test_git_config_nosystem_is_blocked_in_project_env():
    assert "GIT_CONFIG_NOSYSTEM" in agent_env.PROJECT_ENV_BLOCKED


# ---- 2. follow-up resume only when the session file exists -------------------

def _thread_parent(client):
    pid = _project(client)
    owner = login_as(f"ff-fu-{_uid()}@test.local")
    _grant(client, pid, owner.user_id, "contributor")
    t = owner.post("/tasks", json={"prompt": "parent work", "project_id": pid})
    assert t.status_code == 201, t.text
    done = wait_task(owner, t.json()["id"])
    assert done["status"] == "completed", done.get("error")
    assert done["session_id"]
    return owner, done


def _followup(owner, parent):
    key = env_probe_key()
    r = owner.post(f"/tasks/{parent['id']}/followup", json={"prompt": f"more [env-probe:{key}]"})
    assert r.status_code == 201, r.text
    done = wait_task(owner, r.json()["id"])
    assert done["status"] == "completed", done.get("error")
    return done, read_probe_args(key)


def test_followup_without_session_file_starts_fresh(client):
    owner, parent = _thread_parent(client)
    done, args = _followup(owner, parent)
    assert args is not None
    assert "--resume" not in args
    ev = [e for e in _events(done["id"]) if e.type == "fresh_session"]
    assert len(ev) == 1
    assert ev[0].payload == {"reason": "previous session not available"}


def test_followup_with_session_file_resumes(client):
    owner, parent = _thread_parent(client)
    sdir = (
        Path(settings.workspaces_dir) / "_users" / owner.user_id
        / "claude" / "projects" / "some-cwd"
    )
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / f"{parent['session_id']}.jsonl").write_text("{}\n")
    done, args = _followup(owner, parent)
    assert args is not None
    i = args.index("--resume")
    assert args[i + 1] == parent["session_id"]
    assert not [e for e in _events(done["id"]) if e.type == "fresh_session"]


# ---- 3. Claude probe runs in the per-user root ------------------------------

def test_claude_probe_cwd_is_user_root(client, tmp_path, monkeypatch):
    m = login_as(f"ff-cwd-{_uid()}@test.local")
    m.put("/me/credentials", json={"claude_token": _fake("oauth"), "claude_kind": "oauth_token"})
    out = tmp_path / "cwd.txt"
    script = tmp_path / "probe.sh"
    script.write_text(f'#!/usr/bin/env bash\npwd -P > "{out}"\nexit 0\n')
    script.chmod(0o755)
    monkeypatch.setattr(settings, "claude_bin", str(script))
    r = m.post("/me/credentials/test", json={"which": "claude"})
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    expected = (Path(settings.workspaces_dir) / "_users" / m.user_id).resolve()
    assert out.read_text().strip() == str(expected)


# ---- 4. webhook creator fallback needs a project role -----------------------

def _pr_hook(client, repo):
    body = json.dumps({
        "action": "opened",
        "number": 9,
        "repository": {"full_name": repo},
        "pull_request": {"number": 9, "head": {"ref": "feat"}, "base": {"ref": "main"}},
    }).encode()
    sig = "sha256=" + hmac.new(b"testsecret", body, hashlib.sha256).hexdigest()
    r = client.post(
        "/webhooks/github", content=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": sig},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_webhook_creator_fallback_requires_role(client):
    repo = f"acme/ff-{_uid()}"
    pid = _project(client, github_repo=repo, auto_review_prs=True)
    creator = login_as(f"ff-wc-{_uid()}@test.local")

    async def set_creator():
        async with SessionLocal() as s:
            p = await s.get(Project, pid)
            p.created_by = creator.user_id
            p.automation_owner_id = None
            await s.commit()

    asyncio.run(set_creator())

    async def count():
        async with SessionLocal() as s:
            return len((await s.execute(select(Task.id).where(Task.project_id == pid))).all())

    # Creator with no role on the project: ignored.
    assert _pr_hook(client, repo) == {"ok": True, "ignored": "no automation owner"}
    assert asyncio.run(count()) == 0

    # With any role (contributor) the creator fallback applies.
    _grant(client, pid, creator.user_id, "contributor")
    out = _pr_hook(client, repo)
    assert "task_id" in out, out

    async def created_by():
        async with SessionLocal() as s:
            return (await s.get(Task, out["task_id"])).created_by

    assert asyncio.run(created_by()) == creator.user_id


# ---- 5/6. schedules expose why they are paused ------------------------------

def test_schedule_blocked_reason_in_api(client):
    pid = _project(client)
    u = login_as(f"ff-sch-{_uid()}@test.local")
    _grant(client, pid, u.user_id, "contributor")
    r = u.post("/schedules", json={
        "name": f"ff-sch-{_uid()}", "cron": "0 3 * * *", "prompt": "hi", "project_id": pid,
    })
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert r.json()["blocked_reason"] is None
    assert u.get(f"/schedules/{sid}").json()["blocked_reason"] is None

    assert client.delete("/admin/org-fallback").status_code in (200, 204)
    try:
        assert u.get(f"/schedules/{sid}").json()["blocked_reason"] == "owner has no Claude credential"
        row = next(x for x in u.get("/schedules").json() if x["id"] == sid)
        assert row["blocked_reason"] == "owner has no Claude credential"
    finally:
        r = client.put("/admin/org-fallback", json={"api_key": FALLBACK})
        assert r.status_code == 200, r.status_code
    assert u.get(f"/schedules/{sid}").json()["blocked_reason"] is None


def test_ownerless_schedule_is_blocked(client):
    import app.scheduler as sched_mod

    r = client.post("/schedules", json={
        "name": f"ff-own-{_uid()}", "cron": "0 3 * * *", "prompt": "hi",
    })
    assert r.status_code == 201, r.text
    sid = r.json()["id"]

    async def orphan():
        async with SessionLocal() as s:
            row = await s.get(Schedule, sid)
            row.created_by = None
            row.next_run_at = datetime.datetime(2000, 1, 1)
            await s.commit()
            return await sched_mod.owner_block_reason(s, row)

    assert asyncio.run(orphan()) == "schedule has no owner"
    assert client.get(f"/schedules/{sid}").json()["blocked_reason"] == "schedule has no owner"
    assert client.post(f"/schedules/{sid}/run").status_code == 409
