"""Phase 3a task 6: the prompt composer (the spec table, rows 1-8), the
resume-or-fresh decision (credentials, the sender, the session file),
Continue, and the one thread-step builder.

Token fixtures are built at runtime (F14); no key-shaped literal is written."""

import asyncio
import os
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

import conftest
from app import auth, crypto
from app.config import settings
from app.constants import Status
from app.database import SessionLocal
from app.main import app
from app.models import ActionApproval, ActionGrant, Project, Task, TaskEvent, TaskHandoff
from app.resume import prompting
from app.resume.texts import (
    CONTINUE_PROMPT,
    CRED_NOTE,
    HANDOFF_HEAD,
    LOG_HEAD,
    NO_COMMITS_LINE,
    RECOVERED_CONTINUE,
    REQUEST_LINE,
)
from app.worker import WorkerManager
from conftest import env_probe_key, login_as, read_probe_args, wait_task

GIT_ENV = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def _uid():
    return uuid.uuid4().hex[:8]


def gh_token():
    return "ghp_" + "a" * 36


def ant_key():
    return "sk-ant-" + "b" * 40


def run(coro):
    return asyncio.run(coro)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, env=GIT_ENV, check=True, capture_output=True,
                          text=True).stdout.strip()


def commit(cwd, name, msg=None):
    (Path(cwd) / name).write_text(name)
    git(cwd, "add", name)
    git(cwd, "commit", "-m", msg or name)
    return git(cwd, "rev-parse", "HEAD")


def mk_task(**kw):
    tid = kw.pop("id", None) or uuid.uuid4().hex

    async def go():
        async with SessionLocal() as s:
            s.add(Task(id=tid, title=kw.pop("title", "t"), prompt=kw.pop("prompt", "do it"),
                       root_id=kw.pop("root_id", tid), status=kw.pop("status", "completed"), **kw))
            await s.commit()

    run(go())
    return tid


def get_task(tid):
    async def go():
        async with SessionLocal() as s:
            return await s.get(Task, tid)

    return run(go())


def set_task(tid, **fields):
    async def go():
        async with SessionLocal() as s:
            t = await s.get(Task, tid)
            for k, v in fields.items():
                setattr(t, k, v)
            await s.commit()

    run(go())


def add_events(task_id, evs):
    async def go():
        async with SessionLocal() as s:
            top = (await s.execute(select(func.max(TaskEvent.seq)).where(TaskEvent.task_id == task_id))).scalar()
            for seq, (etype, payload) in enumerate(evs, start=(top or 0) + 1):
                s.add(TaskEvent(task_id=task_id, seq=seq, type=etype, payload=payload))
            await s.commit()

    run(go())


def events(task_id, etype=None):
    async def go():
        async with SessionLocal() as s:
            q = select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.seq)
            return list((await s.execute(q)).scalars().all())

    return [e for e in run(go()) if etype is None or e.type == etype]


def wait_handoff(step_id, reason="run ended"):
    """_finish commits the status before its snapshot: wait (bounded, 15 s) for the stored
    handoff that belongs to this step and reason before a follow-up reads it."""
    def row():
        async def go():
            async with SessionLocal() as s:
                t = await s.get(Task, step_id)
                return (await s.execute(select(TaskHandoff).where(TaskHandoff.root_id == (t.root_id or t.id)))
                        ).scalar_one_or_none()

        return run(go())

    deadline = time.time() + 15.0
    while time.time() < deadline:
        r = row()
        if r is not None and r.task_id == step_id and r.reason == reason:
            return r
        time.sleep(0.1)
    raise AssertionError(f"no {reason!r} handoff stored for step {step_id}")


def put_handoff(root_id, text):
    async def go():
        async with SessionLocal() as s:
            row = (await s.execute(select(TaskHandoff).where(TaskHandoff.root_id == root_id))).scalar_one_or_none()
            if row is None:
                row = TaskHandoff(root_id=root_id, version=1)
                s.add(row)
            row.text, row.content, row.task_id = text, {}, root_id
            await s.commit()

    run(go())


def compose(tid, base_prompt=None, *, resumed, recovery_block=None, secrets=()):
    async def go():
        async with SessionLocal() as s:
            t = await s.get(Task, tid)
            return await prompting.compose(
                s, t, base_prompt=t.prompt if base_prompt is None else base_prompt, resumed=resumed,
                wanted_resume=prompting.wanted_resume(t), recovery_block=recovery_block, secrets=list(secrets))

    return run(go())


def session_file(user_id, session_id):
    d = Path(settings.workspaces_dir) / "_users" / user_id / "claude" / "projects" / "some-cwd"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{session_id}.jsonl").write_text("{}\n")


def probe_prompt(key):
    f = conftest._PROBES / f"{key}.prompt"
    return f.read_text() if f.exists() else None


def _project(admin, **extra):
    r = admin.post("/projects", json={"name": f"rp-{_uid()}", **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _grant(admin, pid, user_id, role):
    keep = [{k: m[k] for k in ("subject_type", "subject_id", "role")}
            for m in admin.get(f"/projects/{pid}/members").json() if m["subject_id"] != user_id]
    keep.append({"subject_type": "user", "subject_id": user_id, "role": role})
    r = admin.put(f"/projects/{pid}/members", json=keep)
    assert r.status_code == 200, r.text


# ---- wanted_resume -------------------------------------------------------------------------------


def test_wanted_resume():
    def t(**kw):
        base = {"id": "s", "parent_task_id": "p", "root_id": "r", "origin": "user", "recovery": None}
        return SimpleNamespace(**{**base, **kw})

    assert prompting.wanted_resume(t()) is True  # a follow-up
    assert prompting.wanted_resume(t(origin="continue")) and prompting.wanted_resume(t(origin="guard"))
    assert prompting.wanted_resume(t(origin="agent")) is False  # a dispatched child
    assert prompting.wanted_resume(t(root_id="s")) is False  # a duplicate starts its own thread
    assert prompting.wanted_resume(t(parent_task_id=None, root_id="s")) is False  # a new thread


# ---- compose: the table --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def thread(client, tmp_path_factory):
    """A project thread: root R (a finished user step with events and a
    stored handoff) in a git repo with one commit."""
    owner = login_as(f"rp-th-{_uid()}@test.local")
    pid = _project(client, instructions="Always run the linter.")
    ws = tmp_path_factory.mktemp("thread")
    git(ws, "init", "-q")
    commit(ws, "a.txt")
    root = mk_task(prompt="build the parser", project_id=pid, workspace_dir=str(ws), created_by=owner.user_id,
                   session_id="sess-r")
    add_events(root, [("text_output", {"text": "parser half done"})])
    put_handoff(root, f"{HANDOFF_HEAD}\nTask: build the parser")
    return SimpleNamespace(owner=owner, pid=pid, ws=str(ws), root=root)


def _step(thread, origin, prompt="more please", **kw):
    return mk_task(prompt=prompt, project_id=thread.pid, workspace_dir=thread.ws, created_by=thread.owner.user_id,
                   parent_task_id=thread.root, root_id=thread.root, origin=origin, status="queued", **kw)


RECORD = "co-orchestrator: this run was cut off at 10:00 UTC (server restart). What follows is a record."

ROWS = {
    # row: (blocks present, blocks absent)
    1: ({"record", "log_tail"}, {"handoff", "project_context"}),
    2: ({"handoff", "record", "log_tail"}, {"project_context"}),
    3: ({"handoff", "log_tail"}, {"project_context", "record"}),
    4: ({"project_context", "handoff", "log_tail", "request"}, {"record"}),
    5: (set(), {"handoff", "log_tail", "project_context"}),
    6: ({"project_context", "handoff"}, {"log_tail", "record"}),
    7: (set(), {"handoff", "log_tail", "project_context"}),
    8: ({"project_context", "no_commits"}, {"handoff", "log_tail"}),
}


@pytest.mark.parametrize("row", sorted(ROWS))
def test_compose_modes(thread, row, tmp_path):
    present, absent = ROWS[row]
    block = None
    if row in (1, 2):  # a recovered step: its own events are the log tail
        tid = mk_task(prompt="build the parser", project_id=thread.pid, workspace_dir=thread.ws,
                      created_by=thread.owner.user_id, origin="user", status="queued",
                      recovery={"pending": True}, session_id="sess-x", resume_session_id="sess-x")
        add_events(tid, [("tool_use", {"name": "Bash", "input": {"command": "make migrate"}, "id": "t1"})])
        put_handoff(tid, f"{HANDOFF_HEAD}\nTask: build the parser")
        block = RECORD
        resumed = row == 1
    elif row in (3, 4):
        tid = _step(thread, "continue", prompt=CONTINUE_PROMPT)
        resumed = row == 3
    elif row in (5, 6):
        tid = _step(thread, "user")
        resumed = row == 5
    elif row == 7:
        tid = _step(thread, "guard", prompt="co-guard: a person approved request x.\nThen continue the task.")
        resumed = False
    else:  # a new thread in an unborn repo
        git(tmp_path, "init", "-q")
        tid = mk_task(prompt="start the thing", project_id=thread.pid, workspace_dir=str(tmp_path),
                      created_by=thread.owner.user_id, status="queued")
        resumed = False
    base = get_task(tid).prompt
    prompt, log = compose(tid, resumed=resumed, recovery_block=block)
    got = set(log["blocks"])
    assert present <= got, (row, got)
    assert not (absent & got), (row, got)
    if "handoff" in got:
        assert HANDOFF_HEAD in prompt
    else:
        assert HANDOFF_HEAD not in prompt
    if "log_tail" in got:
        assert LOG_HEAD in prompt
    if "project_context" in got:
        assert "Always run the linter." in prompt
    if row == 1:
        assert prompt.endswith(RECOVERED_CONTINUE) and RECORD in prompt and "make migrate" in prompt
    if row == 2:
        assert prompt.index(HANDOFF_HEAD) < prompt.index(RECORD) < prompt.index(LOG_HEAD)
        assert prompt.endswith("# Task\n\nbuild the parser")
    if row == 3:
        assert prompt.endswith(CONTINUE_PROMPT) and "parser half done" in prompt  # the parent's log
    if row == 4:
        assert CONTINUE_PROMPT in prompt
        assert prompt.endswith("co-orchestrator: the request was: build the parser")
    if row == 5:
        assert prompt == base  # no outside commits: unchanged
    if row == 6:
        assert prompt.endswith("# Task\n\nmore please")
    if row == 7:
        assert prompt == base  # byte for byte
        assert compose(tid, resumed=True)[0] == base
        # a recovered guard step still gets its record (rows 1-2 come first)
        assert RECORD in compose(tid, resumed=True, recovery_block=RECORD)[0]
    if row == 8:
        assert NO_COMMITS_LINE in prompt and prompt.endswith("# Task\n\nstart the thing")


def test_injected_blocks_redacted(thread):
    """Handoff (with notes) and log tail carry a GitHub token, an Anthropic
    key, a run secret and hidden characters: none survive."""
    gh, ak, run_secret = gh_token(), ant_key(), "hunter-" + "q" * 12
    root = mk_task(prompt="the request", created_by=thread.owner.user_id, workspace_dir=thread.ws)
    put_handoff(root, f"{HANDOFF_HEAD}\nNotes: token {gh} and {run_secret}\u202e here\u200b\nTask: x")
    add_events(root, [("text_output", {"text": f"key {ak} and {run_secret}\u202e"})])
    tid = mk_task(prompt=CONTINUE_PROMPT, origin="continue", parent_task_id=root, root_id=root,
                  created_by=thread.owner.user_id, workspace_dir=thread.ws, status="queued")
    prompt, log = compose(tid, resumed=False, secrets=[run_secret])
    assert {"handoff", "log_tail", "request"} <= set(log["blocks"])
    for value in (gh, ak, run_secret):
        assert value not in prompt
    assert "***" in prompt
    assert "\u202e" not in prompt and "\u200b" not in prompt


@pytest.mark.parametrize("kept", [4, 7, 12, 30])
def test_token_straddling_the_request_cut(thread, kept):
    """An Anthropic key straddles the request block's REQUEST_MAX cut with
    ``kept`` of its characters inside it: redacted before the cut, no prefix
    of 4+ characters survives (a cut-first order would leave ak[:kept])."""
    ak = ant_key()
    head = REQUEST_LINE.format(request="")
    request = "p" * (prompting.REQUEST_MAX - len(head) - kept) + ak
    start = len(head) + request.index(ak)
    assert start < prompting.REQUEST_MAX < start + len(ak)  # the key really straddles the cut
    assert start + kept == prompting.REQUEST_MAX
    root = mk_task(prompt=request, created_by=thread.owner.user_id, workspace_dir=thread.ws)
    put_handoff(root, f"{HANDOFF_HEAD}\nTask: x")
    tid = mk_task(prompt=CONTINUE_PROMPT, origin="continue", parent_task_id=root, root_id=root,
                  created_by=thread.owner.user_id, workspace_dir=thread.ws, status="queued")
    prompt, log = compose(tid, resumed=False)
    assert "request" in log["blocks"]
    assert "sk-a" not in CONTINUE_PROMPT + HANDOFF_HEAD  # no other source of the prefix
    for n in range(4, len(ak) + 1):
        assert ak[:n] not in prompt, n
    assert prompt.endswith(head + "p" * (prompting.REQUEST_MAX - len(head) - kept) + "***"[:kept])


# ---- others, no commits, outside commits ---------------------------------------------------------


def test_no_commits_line_and_others(client, tmp_path):
    tag = _uid()
    owner = login_as(f"rp-oth-{tag}@test.local")
    stranger = login_as(f"rp-str-{tag}@test.local")
    git(tmp_path, "init", "-q")
    ws = str(tmp_path)
    now = datetime.now(timezone.utc)
    mk_task(title="my other run", workspace_dir=ws, status="running", created_by=owner.user_id, started_at=now)
    mk_task(title="secret run", workspace_dir=ws, status="running", created_by=stranger.user_id, started_at=now)
    tid = mk_task(prompt="new work", workspace_dir=ws, status="queued", created_by=owner.user_id)
    prompt, log = compose(tid, resumed=False)
    assert {"others", "no_commits"} <= set(log["blocks"])
    assert NO_COMMITS_LINE in prompt
    assert '"my other run"' in prompt and "1 run(s) you cannot see" in prompt
    assert "secret run" not in prompt
    # with a commit, no no-commits line
    commit(tmp_path, "a.txt")
    prompt, log = compose(tid, resumed=False)
    assert "no_commits" not in log["blocks"] and NO_COMMITS_LINE not in prompt


def test_outside_line_on_user_followup(client, tmp_path):
    owner = login_as(f"rp-out-{_uid()}@test.local")
    git(tmp_path, "init", "-q")
    head = commit(tmp_path, "a.txt")
    root = mk_task(prompt="first", workspace_dir=str(tmp_path), created_by=owner.user_id, session_id="s1")

    async def store():
        async with SessionLocal() as s:
            s.add(TaskHandoff(root_id=root, task_id=root, version=1, text="h", content={"head": head[:12]}))
            await s.commit()

    run(store())
    fu = mk_task(prompt="next bit", workspace_dir=str(tmp_path), created_by=owner.user_id, parent_task_id=root,
                 root_id=root, origin="user", resume_session_id="s1", status="queued")
    assert compose(fu, resumed=True) == ("next bit", {"blocks": [], "project": {}})  # nothing outside
    sha = commit(tmp_path, "b.txt", "hotfix by hand")
    prompt, log = compose(fu, resumed=True)
    assert log["blocks"] == ["outside"]
    assert prompt.startswith("next bit\n\nco-orchestrator: commits were made outside this thread")
    assert f"{sha[:7]} hotfix by hand" in prompt


# ---- the resume-or-fresh decision, through the worker --------------------------------------------


def _set_claude(user_client, token):
    r = user_client.put("/me/credentials", json={"claude_token": token, "claude_kind": "oauth_token"})
    assert r.status_code == 200, r.text


def test_credentials_change_forces_fresh(client):
    owner = login_as(f"rp-cred-{_uid()}@test.local")
    tok_a, tok_b = f"fake-oauth-{uuid.uuid4().hex}", f"fake-oauth-{uuid.uuid4().hex}"
    _set_claude(owner, tok_a)
    r = owner.post("/tasks", json={"prompt": "step one"})
    assert r.status_code == 201, r.text
    one = wait_task(owner, r.json()["id"])
    assert one["status"] == "completed" and one["session_id"]
    wait_handoff(one["id"])
    ref_a = get_task(one["id"]).credential_ref
    assert ref_a == crypto.fingerprint("CLAUDE_CODE_OAUTH_TOKEN", tok_a)
    session_file(owner.user_id, one["session_id"])
    _set_claude(owner, tok_b)
    key = env_probe_key()
    r = owner.post(f"/tasks/{one['id']}/followup", json={"prompt": f"step two [prompt-probe:{key}] [env-probe:{key}]"})
    assert r.status_code == 201, r.text
    two = wait_task(owner, r.json()["id"])
    assert two["status"] == "completed", two.get("error")
    args = read_probe_args(key)
    assert args is not None and "--resume" not in args
    assert HANDOFF_HEAD in probe_prompt(key)
    ev = events(two["id"], "fresh_session")
    assert [e.payload for e in ev] == [{"reason": CRED_NOTE, "credentials_changed": True}]
    assert get_task(two["id"]).credential_ref == crypto.fingerprint("CLAUDE_CODE_OAUTH_TOKEN", tok_b) != ref_a
    # same credential, session file present: it resumes
    key2 = env_probe_key()
    session_file(owner.user_id, two["session_id"])
    r = owner.post(f"/tasks/{two['id']}/followup", json={"prompt": f"step three [env-probe:{key2}]"})
    three = wait_task(owner, r.json()["id"])
    assert "--resume" in read_probe_args(key2) and not events(three["id"], "fresh_session")


def test_recovered_step_credentials_changed_goes_fresh(client, monkeypatch):
    owner = login_as(f"rp-rec-{_uid()}@test.local")
    tok_a, tok_b = f"fake-oauth-{uuid.uuid4().hex}", f"fake-oauth-{uuid.uuid4().hex}"
    _set_claude(owner, tok_b)  # the owner's credential is now B
    key = env_probe_key()
    sid = f"s-{_uid()}"
    tid = mk_task(prompt=f"original work [prompt-probe:{key}] [env-probe:{key}]", created_by=owner.user_id,
                  status="failed", session_id=sid, resume_session_id=sid, recovery={"pending": True},
                  credential_ref=crypto.fingerprint("CLAUDE_CODE_OAUTH_TOKEN", tok_a))
    session_file(owner.user_id, sid)
    add_events(tid, [("tool_use", {"name": "Bash", "input": {"command": "make migrate"}, "id": "t1"})])
    put_handoff(tid, f"{HANDOFF_HEAD}\nTask: original work")
    monkeypatch.setattr(WorkerManager, "_recovery_block",
                        lambda self, t: RECORD if prompting.recovering(t) else None)
    assert owner.post(f"/tasks/{tid}/retry").status_code == 200  # queues it, keeping the recovery state
    done = wait_task(owner, tid)
    assert done["status"] == "completed", done.get("error")
    args = read_probe_args(key)
    assert args is not None and "--resume" not in args
    prompt = probe_prompt(key)
    assert prompt.index(HANDOFF_HEAD) < prompt.index(RECORD) < prompt.index(LOG_HEAD)  # row 2
    assert "make migrate" in prompt and prompt.endswith(f"# Task\n\noriginal work [prompt-probe:{key}] [env-probe:{key}]")
    assert [e.payload for e in events(tid, "fresh_session")] == [{"reason": CRED_NOTE, "credentials_changed": True}]
    assert get_task(tid).credential_ref == crypto.fingerprint("CLAUDE_CODE_OAUTH_TOKEN", tok_b)


def test_missing_session_gets_handoff_not_bare(client):
    owner = login_as(f"rp-miss-{_uid()}@test.local")
    r = owner.post("/tasks", json={"prompt": "parent work"})
    parent = wait_task(owner, r.json()["id"])
    assert parent["status"] == "completed" and parent["session_id"]
    wait_handoff(parent["id"])
    key = env_probe_key()
    r = owner.post(f"/tasks/{parent['id']}/followup", json={"prompt": f"more [prompt-probe:{key}] [env-probe:{key}]"})
    done = wait_task(owner, r.json()["id"])
    assert done["status"] == "completed", done.get("error")
    assert "--resume" not in read_probe_args(key)
    assert [e.payload for e in events(done["id"], "fresh_session")] == [{"reason": "previous session not available"}]
    prompt = probe_prompt(key)
    assert HANDOFF_HEAD in prompt and prompt.endswith(f"# Task\n\nmore [prompt-probe:{key}] [env-probe:{key}]")


def test_other_sender_fresh_with_handoff(client):
    tag = _uid()
    pid = _project(client)
    owner = login_as(f"rp-own-{tag}@test.local")
    other = login_as(f"rp-oth2-{tag}@test.local")
    _grant(client, pid, owner.user_id, "contributor")
    _grant(client, pid, other.user_id, "maintainer")
    r = owner.post("/tasks", json={"prompt": "owner work", "project_id": pid})
    parent = wait_task(owner, r.json()["id"])
    assert parent["status"] == "completed" and parent["session_id"]
    wait_handoff(parent["id"])
    session_file(owner.user_id, parent["session_id"])
    key = env_probe_key()
    r = other.post(f"/tasks/{parent['id']}/followup", json={"prompt": f"my turn [prompt-probe:{key}] [env-probe:{key}]"})
    assert r.status_code == 201, r.text
    assert get_task(r.json()["id"]).resume_session_id is None  # the router cleared it
    done = wait_task(other, r.json()["id"])
    assert done["status"] == "completed", done.get("error")
    assert "--resume" not in read_probe_args(key)
    assert [e.payload for e in events(done["id"], "fresh_session")] == [
        {"reason": "a different person continues this thread"}]
    assert HANDOFF_HEAD in probe_prompt(key)


# ---- _step_from ---------------------------------------------------------------------------------


STEP_FIELDS = ("title", "prompt", "project", "project_id", "priority", "tags", "model", "max_turns",
               "parent_task_id", "origin", "root_id", "resume_session_id", "workspace_dir", "status", "created_by")


def test_followup_uses_step_from(client, monkeypatch):
    from app.routers import tasks as tasks_router

    parent = Task(id="p" * 32, title="x", prompt="x", project="proj", project_id="pid", priority="high",
                  tags=["a"], model="opus", max_turns=7, root_id="r" * 32, workspace_dir="/w", session_id="s")
    step = tasks_router._step_from(parent, prompt="go on", origin="user", resume="s", created_by="u")
    assert len(STEP_FIELDS) == 15
    assert {f: getattr(step, f) for f in STEP_FIELDS} == {
        "title": "go on", "prompt": "go on", "project": "proj", "project_id": "pid", "priority": "high",
        "tags": ["a"], "model": "opus", "max_turns": 7, "parent_task_id": "p" * 32, "origin": "user",
        "root_id": "r" * 32, "resume_session_id": "s", "workspace_dir": "/w", "status": Status.QUEUED,
        "created_by": "u"}
    assert step.tags is not parent.tags
    calls = []
    real = tasks_router._step_from
    monkeypatch.setattr(tasks_router, "_step_from", lambda *a, **kw: calls.append(kw) or real(*a, **kw))
    owner = login_as(f"rp-sf-{_uid()}@test.local")
    pid = mk_task(prompt="root", created_by=owner.user_id, session_id="s1")
    monkeypatch.setattr(app.state.worker, "submit", _noop_submit)
    r = owner.post(f"/tasks/{pid}/followup", json={"prompt": "again"})
    assert r.status_code == 201, r.text
    assert calls == [{"prompt": "again", "origin": "user", "resume": "s1", "created_by": owner.user_id}]


async def _noop_submit(task_id, priority, created_at):
    return None


# ---- Continue -----------------------------------------------------------------------------------


@pytest.fixture
def submitted(monkeypatch):
    got = []

    async def submit(task_id, priority, created_at):
        got.append(task_id)

    monkeypatch.setattr(app.state.worker, "submit", submit)
    return got


def _bearer(user_id, task_id):
    async def go():
        async with SessionLocal() as s:
            raw = await auth.mint_token(s, user_id, "agent-task", task_id, 3600)
            await s.commit()
            return raw

    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {run(go())}"
    return c


def test_continue_endpoint(client, submitted):
    tag = _uid()
    owner = login_as(f"rp-cont-{tag}@test.local")
    outsider = login_as(f"rp-cout-{tag}@test.local")

    def failed(**kw):
        return mk_task(prompt="the work", status=kw.pop("status", "failed"), created_by=owner.user_id,
                       session_id=kw.pop("session_id", f"s-{_uid()}"), **kw)

    tid = failed()
    r = owner.post(f"/tasks/{tid}/continue", json={"mode": "auto"})
    assert r.status_code == 201, r.text
    child = r.json()
    assert child["origin"] == "continue" and child["prompt"] == CONTINUE_PROMPT
    assert child["parent_task_id"] == tid and child["root_id"] == tid
    assert get_task(child["id"]).resume_session_id == get_task(tid).session_id  # the owner resumes
    assert submitted == [child["id"]]
    assert run(_handoff_reason(tid)) == "continue"  # snapshotted before the step
    # the thread now has an active (queued) step
    assert owner.post(f"/tasks/{tid}/continue", json={}).status_code == 409
    # default mode is auto; fresh never resumes
    tid = failed()
    r = owner.post(f"/tasks/{tid}/continue", json={"mode": "fresh"})
    assert r.status_code == 201 and get_task(r.json()["id"]).resume_session_id is None
    # active -> 409; origin=agent -> 409; bad mode -> 400
    assert owner.post(f"/tasks/{failed(status='running')}/continue", json={}).status_code == 409
    root = failed()
    kid = failed(origin="agent", parent_task_id=root, root_id=root)
    assert owner.post(f"/tasks/{kid}/continue", json={}).status_code == 409
    assert owner.post(f"/tasks/{failed()}/continue", json={"mode": "later"}).status_code == 400
    # a non-owner (admin may manage): resume -> 409, auto -> fresh
    tid = failed()
    assert client.post(f"/tasks/{tid}/continue", json={"mode": "resume"}).status_code == 409
    r = client.post(f"/tasks/{tid}/continue", json={"mode": "auto"})
    assert r.status_code == 201 and get_task(r.json()["id"]).resume_session_id is None
    assert get_task(r.json()["id"]).created_by != owner.user_id
    # the owner without a session: resume -> 409, auto -> fresh
    tid = failed(session_id=None)
    assert owner.post(f"/tasks/{tid}/continue", json={"mode": "resume"}).status_code == 409
    r = owner.post(f"/tasks/{tid}/continue", json={})
    assert r.status_code == 201 and get_task(r.json()["id"]).resume_session_id is None
    # an outsider cannot see it; an agent token is refused
    tid = failed()
    assert outsider.post(f"/tasks/{tid}/continue", json={}).status_code == 404
    assert _bearer(owner.user_id, tid).post(f"/tasks/{tid}/continue", json={}).status_code == 403


def test_continue_409_before_snapshot_when_a_step_is_active(client, submitted):
    """M2: a thread with a live step gets 409 before any snapshot is taken
    (none describes the live step without its secrets or moves its base)."""
    from app.resume import handoff

    owner = login_as(f"rp-c409-{_uid()}@test.local")
    tid = mk_task(prompt="the work", status="failed", created_by=owner.user_id, session_id=f"s-{_uid()}")
    run(handoff.snapshot(tid, "run ended"))
    mk_task(prompt="sibling", status="running", root_id=tid, parent_task_id=tid, created_by=owner.user_id)

    async def row():
        async with SessionLocal() as s:
            h = (await s.execute(select(TaskHandoff).where(TaskHandoff.root_id == tid))).scalar_one()
            return h.version, h.saved_at, h.reason

    before = run(row())
    r = owner.post(f"/tasks/{tid}/continue", json={})
    assert r.status_code == 409 and r.json()["detail"] == "A step of this thread is still active"
    assert run(row()) == before and submitted == []


async def _handoff_reason(tid):
    async with SessionLocal() as s:
        row = (await s.execute(select(TaskHandoff).where(TaskHandoff.root_id == tid))).scalar_one_or_none()
        return row.reason if row is not None else None


def test_continue_joins_guard_deny_and_resume(client, submitted):
    from test_guard_grants import _block, gk

    tag = _uid()
    pid = _project(client)
    owner = login_as(f"rp-jo-{tag}@test.local")
    maint = login_as(f"rp-jm-{tag}@test.local")
    _grant(client, pid, owner.user_id, "maintainer")
    _grant(client, pid, maint.user_id, "maintainer")

    async def resume_mode():
        async with SessionLocal() as s:
            (await s.get(Project, pid)).on_no_answer = "deny_and_resume"
            await s.commit()

    run(resume_mode())

    def file_request(tid):
        body = {"task_id": tid, "rule_ids": ["git-reset-hard"], "tool": "Bash", "summary": "git reset --hard",
                "cwd": "/w", "reason": "discards local changes", "command_hash": "a" * 64,
                "grant_key": gk("git reset --hard")}
        r = _bearer(owner.user_id, tid).post("/guard/approvals", json=body)
        assert r.status_code in (200, 201), r.text
        rid = r.json()["id"]
        async def expire():
            async with SessionLocal() as s:
                (await s.get(ActionApproval, rid)).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                (await s.get(Task, tid)).status = "completed"
                await s.commit()

        run(expire())
        return rid

    # the owner's queued Continue is joined
    tid = mk_task(prompt="p", project_id=pid, status="running", created_by=owner.user_id, session_id=f"s-{tag}")
    rid = file_request(tid)
    r = owner.post(f"/tasks/{tid}/continue", json={})
    assert r.status_code == 201, r.text
    cont = r.json()["id"]
    r = maint.post(f"/guard/approvals/{rid}/approve")
    assert r.status_code == 200, r.text
    assert r.json()["followup_task_id"] == cont and submitted == [cont]  # joined, not a second step
    assert get_task(cont).prompt == CONTINUE_PROMPT + "\n\n" + _block(rid)
    grants = run(_grants(cont))
    assert [(g.scope, g.key) for g in grants] == [("once", gk("git reset --hard"))]
    # another person's queued Continue is an active step: the approval waits for it
    tid = mk_task(prompt="p", project_id=pid, status="running", created_by=owner.user_id, session_id=f"t-{tag}")
    rid = file_request(tid)
    other = maint.post(f"/tasks/{tid}/continue", json={}).json()["id"]
    r = maint.post(f"/guard/approvals/{rid}/approve")
    assert r.status_code == 200, r.text
    assert r.json()["followup_task_id"] is None and get_task(other).prompt == CONTINUE_PROMPT


async def _grants(tid):
    async with SessionLocal() as s:
        return list((await s.execute(select(ActionGrant).where(ActionGrant.task_id == tid))).scalars().all())


# ---- can_manage / can_resume on the task reads ---------------------------------------------------


def test_can_manage_matches_continue(client, submitted):
    tag = _uid()
    owner = login_as(f"rp-fo-{tag}@test.local")
    maint = login_as(f"rp-fm-{tag}@test.local")
    contrib = login_as(f"rp-fc-{tag}@test.local")
    outsider = login_as(f"rp-fx-{tag}@test.local")
    lapsed = login_as(f"rp-fl-{tag}@test.local")
    pid = _project(client)
    for u, role in ((owner, "contributor"), (maint, "maintainer"), (contrib, "contributor")):
        _grant(client, pid, u.user_id, role)

    def failed(by):
        return mk_task(prompt="work", status="failed", created_by=by.user_id, session_id=f"s-{_uid()}",
                       project_id=pid)

    # (viewer, task creator) -> (can_manage, can_resume)
    cases = [
        ("owner", owner, owner, True, True),
        ("maintainer", maint, owner, True, False),
        ("admin", client, owner, True, False),
        ("contributor", contrib, owner, False, False),
    ]
    for name, viewer, creator, manage, resume in cases:
        tid = failed(creator)
        t = viewer.get(f"/tasks/{tid}")
        assert t.status_code == 200, name
        assert (t.json()["can_manage"], t.json()["can_resume"]) == (manage, resume), name
        th = viewer.get(f"/tasks/{tid}/thread").json()
        assert [(x["can_manage"], x["can_resume"]) for x in th] == [(manage, resume)], name
        r = viewer.post(f"/tasks/{tid}/continue", json={"mode": "fresh"})
        assert (r.status_code == 201) == manage, (name, r.status_code)
        if not manage:
            assert r.status_code == 403
    # an outsider cannot see the task at all
    tid = failed(owner)
    assert outsider.get(f"/tasks/{tid}").status_code == 404
    assert outsider.post(f"/tasks/{tid}/continue", json={}).status_code == 404
    # a creator who lost every project role: the flag and the endpoint agree (403)
    tid = failed(lapsed)
    t = lapsed.get(f"/tasks/{tid}")
    if t.status_code == 200:
        assert t.json()["can_manage"] is False and t.json()["can_resume"] is False
        assert lapsed.post(f"/tasks/{tid}/continue", json={}).status_code == 403
    # the owner of a step with no session cannot resume
    tid = mk_task(prompt="w", status="failed", created_by=owner.user_id, project_id=pid)
    assert owner.get(f"/tasks/{tid}").json()["can_resume"] is False
