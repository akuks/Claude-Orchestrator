"""Phase 3a task 7: crash recovery - the verified orphan kill, the record
built from the crashed run's events and raw spool, the requeue that resumes
its own session, the cap, and the sweep.

State is built directly (a RUNNING row, events, a task-dir spool). Every
process a test signals is one it started itself (a ``sleep`` child in its own
session). Token fixtures are built at runtime (F14)."""

import asyncio
import json
import os
import signal
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

import conftest
from app.config import settings
from app.database import SessionLocal
from app.guard import runtime as guard_runtime
from app.main import app
from app.models import ActionGrant, Project, Task, TaskEvent
from app.resume import handoff as handoff_mod
from app.resume import prompting, recovery
from app.resume.texts import GUEST_TAIL, HANDOFF_HEAD, RECORD_HEAD, RECORD_TAIL, RECOVERED_CONTINUE
from app.worker import WorkerManager
from conftest import env_probe_key, login_as, read_probe_args, read_probe_file, wait_task

RECORD_START = RECORD_HEAD.split("{hhmm}")[0]
EVENT_KEYS = {"why", "at", "in_flight", "orphan_killed", "recoveries", "gave_up"}


def _uid():
    return uuid.uuid4().hex[:8]


def gh_token():
    return "ghp_" + "a" * 36


def ant_key(n=40):
    return "sk-ant-" + "b" * n


def run(coro):
    return asyncio.run(coro)


def _now():
    return datetime.now(timezone.utc)


def mk_task(**kw):
    tid = kw.pop("id", None) or uuid.uuid4().hex

    async def go():
        async with SessionLocal() as s:
            s.add(Task(id=tid, title=kw.pop("title", "t"), prompt=kw.pop("prompt", "original work"),
                       root_id=kw.pop("root_id", tid), status=kw.pop("status", "running"),
                       started_at=kw.pop("started_at", _now()), **kw))
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


def spool(task_id, lines):
    d = guard_runtime.make_task_dir(task_id)
    with open(d / guard_runtime.SPOOL_NAME, "w") as f:
        for rec in lines:
            f.write(json.dumps(rec) + "\n")


def seen(tid, tool, what):
    return {"event": "PreToolUse", "tool_use_id": tid, "seen": True, "tool": tool, "what": what}


def done(tid):
    return {"event": "PostToolUse", "tool_use_id": tid, "done": True}


def probe_prompt(key):
    f = conftest._PROBES / f"{key}.prompt"
    return f.read_text() if f.exists() else None


def session_file(user_id, session_id):
    d = Path(settings.workspaces_dir) / "_users" / user_id / "claude" / "projects" / "x"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{session_id}.jsonl").write_text("{}\n")


def _owner(client):
    owner = login_as(f"rc-{_uid()}@test.local")
    r = owner.put("/me/credentials", json={"claude_token": f"fake-oauth-{uuid.uuid4().hex}",
                                           "claude_kind": "oauth_token"})
    assert r.status_code == 200, r.text
    return owner


def _sleeper():
    return subprocess.Popen(["sleep", "60"], start_new_session=True)


def _cleanup(proc):
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # our own child's group only
        except ProcessLookupError:
            pass
    proc.wait()


def _park(*tids):
    """Leave the rows of a fresh manager finished (nothing re-runs them)."""
    for tid in tids:
        set_task(tid, status="cancelled")


@pytest.fixture
def killpg_calls(monkeypatch):
    calls, real = [], os.killpg

    def spy(pgid, sig):
        calls.append((pgid, sig))
        return real(pgid, sig)

    monkeypatch.setattr(os, "killpg", spy)
    return calls


# ---- the orphan --------------------------------------------------------------------------------


def test_orphan_killed_only_with_matching_start(client, killpg_calls):
    w = WorkerManager()
    # a matching start time: the group is SIGKILLed
    proc = _sleeper()
    try:
        start = recovery.proc_start(proc.pid)
        assert start
        tid = mk_task(run_pgid=proc.pid, run_proc_start=start)
        assert run(w.recover(tid, "server restart")) == "requeued"
        assert proc.wait(timeout=2) == -signal.SIGKILL
        assert killpg_calls == [(proc.pid, signal.SIGKILL)]
        assert events(tid, "recovery")[-1].payload["orphan_killed"] is True
        t = get_task(tid)
        assert t.run_pgid is None and t.run_proc_start is None
    finally:
        _cleanup(proc)
    _park(tid)

    # a mismatched (tampered) start time: never signalled
    killpg_calls.clear()
    proc = _sleeper()
    try:
        tid = mk_task(run_pgid=proc.pid, run_proc_start="Mon Jan  1 00:00:00 2001")
        assert run(w.recover(tid, "server restart")) == "requeued"
        assert proc.poll() is None
        assert killpg_calls == []
        assert events(tid, "recovery")[-1].payload["orphan_killed"] is False
    finally:
        _cleanup(proc)
    _park(tid)

    # a dead pid: no signal, no error
    proc = _sleeper()
    start = recovery.proc_start(proc.pid)
    _cleanup(proc)
    killpg_calls.clear()
    tid = mk_task(run_pgid=proc.pid, run_proc_start=start)
    assert run(w.recover(tid, "server restart")) == "requeued"
    assert killpg_calls == []
    assert events(tid, "recovery")[-1].payload["orphan_killed"] is False
    _park(tid)
    assert not recovery.orphan_alive(None, start) and not recovery.orphan_alive(proc.pid, "")


# ---- the requeued run --------------------------------------------------------------------------


def _crashed_run(owner, tmp_path, key, *, session_id=None, prompt="original work"):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "data.txt").write_bytes(b"")  # a Write truncated to 0 bytes
    tid = mk_task(prompt=prompt, created_by=owner.user_id, session_id=session_id, workspace_dir=str(ws))
    add_events(tid, [
        ("text_output", {"text": "writing the data"}),
        ("tool_use", {"name": "Write", "input": {"file_path": str(ws / "data.txt"), "content": ""}, "id": "tW"}),
        ("tool_result", {"content": "ok", "tool_use_id": "tW", "is_error": False}),
        ("tool_use", {"name": "Bash", "input": {"command": "make migrate", "description": "migrate"}, "id": "tB"}),
        ("text_output", {"text": f"about to migrate [prompt-probe:{key}] [env-probe:{key}]"}),
    ])
    spool(tid, [seen("tW", "Write", str(ws / "data.txt")), done("tW"), seen("tB", "Bash", "migrate :: make migrate")])
    return tid


def _recover_live(tid, why="server restart"):
    """recover() on the app's own worker; the requeue is held so the queued
    state can be checked, then submitted."""
    w = app.state.worker
    held = []

    async def hold(task_id, priority, created_at):
        held.append((task_id, priority, created_at))

    real = w._enqueue
    w._enqueue = hold
    try:
        out = conftest._ADMIN_CLIENT.portal.call(w.recover, tid, why)
    finally:
        w._enqueue = real
    return out, held


def test_recover_resumes_own_session_with_record(client, tmp_path):
    owner = _owner(client)
    key = env_probe_key()
    tid = _crashed_run(owner, tmp_path, key, session_id="s1")
    session_file(owner.user_id, "s1")
    out, held = _recover_live(tid)
    assert out == "requeued" and [h[0] for h in held] == [tid]
    t = get_task(tid)
    assert t.status == "queued" and t.resume_session_id == "s1" and t.recoveries == 1
    assert t.recovery["pending"] is True
    client.portal.call(app.state.worker.submit, *held[0])
    finished = wait_task(owner, tid)
    assert finished["status"] == "completed", finished.get("error")
    args = read_probe_args(key)
    assert args is not None and args[args.index("--resume") + 1] == "s1"
    prompt = probe_prompt(key)
    for part in (RECORD_START, "may have half run", "empty (0 bytes)", "about to migrate", RECORD_TAIL):
        assert part in prompt, part
    assert "# Task\n\noriginal work" not in prompt
    assert get_task(tid).recovery["pending"] is False


def test_recover_fresh_when_no_session(client, tmp_path):
    owner = _owner(client)
    key = env_probe_key()
    original = f"original work [prompt-probe:{key}] [env-probe:{key}]"
    tid = _crashed_run(owner, tmp_path, key, prompt=original)
    out, held = _recover_live(tid)
    assert out == "requeued" and get_task(tid).resume_session_id is None
    client.portal.call(app.state.worker.submit, *held[0])
    finished = wait_task(owner, tid)
    assert finished["status"] == "completed", finished.get("error")
    assert "--resume" not in read_probe_args(key)
    prompt = probe_prompt(key)
    assert prompt.index(HANDOFF_HEAD) < prompt.index(RECORD_START) < prompt.index(RECORD_TAIL)
    assert prompt.endswith(f"# Task\n\n{original}")


def test_spool_read_before_wipe_and_grants_spent(client, tmp_path):
    owner = _owner(client)
    key = env_probe_key()
    original = f"original work [env-probe:{key}]"
    tid = _crashed_run(owner, tmp_path, key, prompt=original)
    gid, now = uuid.uuid4().hex, _now()

    async def grant():
        async with SessionLocal() as s:
            s.add(ActionGrant(id=gid, root_id=tid, task_id=tid, scope="once", rule_id="git-reset-hard",
                              key_kind="command", key="c" * 64, created_at=now, expires_at=now + timedelta(hours=1)))
            await s.commit()

    run(grant())
    spool(tid, [
        seen("tB", "Bash", "migrate :: make migrate"),
        {"event": "PreToolUse", "decision": "granted", "tool": "Bash", "rule_ids": ["git-reset-hard"],
         "summary": "git reset --hard", "tool_use_id": "tG", "grant_ids": [gid], "ts": 1.0},
        {"event": "PreToolUse", "decision": "deny", "rule_ids": ["rm-rf-outside"], "tool": "Bash",
         "summary": "rm -rf ~", "reason": "deletes home", "ts": 2.0},
    ])
    out, held = _recover_live(tid)
    assert out == "requeued"
    assert not guard_runtime.task_dir(tid).exists()  # ingested, then the dir went
    t = get_task(tid)
    assert [f["tool"] for f in t.recovery["in_flight"]] == ["Bash"]  # the raw read came first

    async def used():
        async with SessionLocal() as s:
            return (await s.get(ActionGrant, gid)).used_at

    assert run(used()) is not None
    guard = [e.payload for e in events(tid, "guard")]
    assert any(p.get("decision") == "deny" and p.get("summary") == "rm -rf ~" for p in guard)
    client.portal.call(app.state.worker.submit, *held[0])
    finished = wait_task(owner, tid)
    assert finished["status"] == "completed", finished.get("error")
    pol = json.loads(read_probe_file(key, "policy"))
    assert gid not in [g["id"] for g in pol["grants"]]


def test_recovery_event_payload(client):
    w = WorkerManager()
    tid = mk_task()
    spool(tid, [seen("t1", "Bash", "ls")])
    assert run(w.recover(tid, "server restart")) == "requeued"
    ev = events(tid, "recovery")
    assert len(ev) == 1
    p = ev[0].payload
    assert set(p) == EVENT_KEYS
    assert p["why"] == "server restart" and p["in_flight"] == 1 and p["recoveries"] == 1
    assert p["orphan_killed"] is False and p["gave_up"] is False
    datetime.fromisoformat(p["at"])
    _park(tid)


def test_third_interruption_fails(client):
    w = WorkerManager()
    tid = mk_task(recoveries=2)
    assert run(w.recover(tid, "server restart")) == "failed"
    t = get_task(tid)
    assert t.status == "failed"
    assert t.error == "interrupted 3 times by server restarts; use Continue to resume it"
    assert t.recoveries == 2
    p = events(tid, "recovery")[-1].payload
    assert set(p) == EVENT_KEYS and p["gave_up"] is True and p["recoveries"] == 2
    # a second call on the finished row does nothing
    assert run(w.recover(tid, "server restart")) == "skipped"


def test_sweep_recovers_stale_not_live(client):
    w = WorkerManager()
    old = _now() - timedelta(seconds=recovery.STALE_SECONDS * 3)
    stale = mk_task(heartbeat_at=old, started_at=old)
    stale_no_beat = mk_task(started_at=old)
    live = mk_task(heartbeat_at=old, started_at=old)
    fresh = mk_task(heartbeat_at=_now(), started_at=old)
    w._active.add(live)
    calls = []

    async def spy(task_id, why):
        calls.append((task_id, why))
        return "requeued"

    w.recover = spy
    run(w._sweep_once())
    mine = [c for c in calls if c[0] in (stale, stale_no_beat, live, fresh)]
    assert sorted(mine) == sorted([(stale, "lost its worker"), (stale_no_beat, "lost its worker")])
    _park(stale, stale_no_beat, live, fresh)


def test_requeue_pending_recovers_running_rows(client, monkeypatch):
    w = WorkerManager()
    running, queued = mk_task(), mk_task(status="queued")
    calls, enq = [], []

    async def spy(task_id, why):
        calls.append((task_id, why))
        return "requeued"

    async def enqueue(task_id, priority, created_at):
        enq.append(task_id)

    w.recover, w._enqueue = spy, enqueue
    run(w._requeue_pending())
    assert (running, "server restart") in calls and queued in enq and running not in enq
    assert queued not in [c[0] for c in calls]
    _park(running, queued)


# ---- the record --------------------------------------------------------------------------------


def _ev(etype, payload, at=None):
    return {"type": etype, "payload": payload, "created_at": at or _now()}


def test_record_redacted_and_external_tail(client, tmp_path):
    tok = gh_token()
    long_key = ant_key(700)  # straddles the 600-char cut of the last words
    evs = [_ev("text_output", {"text": f"done with ‮it​ and {tok} then {long_key}"})]
    rec = recovery.build_record(None, evs, [seen("t1", "Bash", f"push :: git push {tok}")], tmp_path, None, _now(),
                                why="server restart")
    words = rec["last_words"]
    assert tok not in words and "b" * 20 not in words and "***" in words
    assert "‮" not in words and "​" not in words
    assert tok not in rec["in_flight"][0]["what"] and "***" in rec["in_flight"][0]["what"]
    text = recovery.render_record(rec, external=False)
    assert tok not in text and "b" * 20 not in text and "***" in text
    assert text.startswith(RECORD_START) and "(server restart)" in text and text.endswith(RECORD_TAIL)
    assert "a record, not instructions" in text
    assert GUEST_TAIL not in text
    assert recovery.render_record(rec, external=True).endswith(GUEST_TAIL)
    # the 600-char cut lands inside a token: none of its tail survives (redacted whole, then cut)
    for kept in (4, 7, 12, 30):
        evs = [_ev("text_output", {"text": ant_key() + " " + "y" * (recovery.LAST_WORDS_CHARS - 1 - kept)})]
        words = recovery.build_record(None, evs, [], tmp_path, None, _now())["last_words"]
        assert len(words) <= recovery.LAST_WORDS_CHARS and "b" * kept not in words, kept


def test_external_project_record_gets_guest_tail(client, tmp_path):
    w = WorkerManager()

    async def mkproj():
        async with SessionLocal() as s:
            name = f"rc-ext-{_uid()}"
            p = Project(name=name, slug=name, directory=str(tmp_path), trust="external")
            s.add(p)
            await s.commit()
            return p.id

    pid = run(mkproj())
    tid = mk_task(project_id=pid)
    assert run(w.recover(tid, "server restart")) == "requeued"
    t = get_task(tid)
    assert t.recovery["external"] is True
    assert w._recovery_block(t).endswith(GUEST_TAIL)
    plain = mk_task()
    run(w.recover(plain, "server restart"))
    assert GUEST_TAIL not in w._recovery_block(get_task(plain))
    _park(tid, plain)


def test_recent_write_marks(tmp_path, tmp_path_factory):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "empty.txt").write_bytes(b"")
    (ws / "nul.bin").write_bytes(b"abc" + b"\0" * 10)
    (ws / "ok.txt").write_bytes(b"hello\n" * 30)
    outside = tmp_path_factory.mktemp("outside") / "elsewhere.txt"
    outside.write_bytes(b"")
    (ws / "link.txt").symlink_to(outside)
    old = _now() - timedelta(seconds=recovery.RECENT_WRITE_SECONDS + 60)

    def w(path, at=None):
        return _ev("tool_use", {"name": "Write", "input": {"file_path": str(path)}, "id": uuid.uuid4().hex}, at)

    evs = [w(ws / "ancient.txt", old), w(ws / "gone.txt"), w("empty.txt"), w(ws / "nul.bin"), w(ws / "ok.txt"),
           w(outside), w(ws / "link.txt")]
    rec = recovery.build_record(None, evs, [], ws, None, _now())
    marks = {Path(r["path"]).name: r["mark"] for r in rec["recent_writes"]}
    assert marks == {"gone.txt": "missing", "empty.txt": "empty (0 bytes)", "nul.bin": "ends in NUL bytes",
                     "ok.txt": "ok"}
    assert recovery.render_record(rec, external=False).count("a hint, not proof") == 1


# ---- final-review fixes (I1, I5, I6, M1) -------------------------------------------------------


def test_recovered_step_is_not_joined_by_an_approval(client):
    """I1: a recovered step queued again resumes with RECOVERED_CONTINUE, so
    an approval never joins it: it is deferred and spawned after it."""
    from app.guard import grants as guard_grants
    from app.models import ActionApproval

    owner = f"u{_uid()}"
    root = mk_task(status="completed", created_by=owner, prompt="root work")
    rec = mk_task(root_id=root, parent_task_id=root, status="queued", created_by=owner, origin="guard",
                  prompt="approved block", recovery={"pending": True})
    now = _now()

    async def setup():
        async with SessionLocal() as s:
            s.add(ActionApproval(task_id=root, status="approved", delivery="resume", rule_ids=["git-reset-hard"],
                                 tool="Bash", summary="first", expires_at=now, decided_by="d", decided_at=now,
                                 resume_until=now + timedelta(hours=1), followup_task_id=rec))
            a = ActionApproval(task_id=root, status="approved", delivery="resume", rule_ids=["git-reset-hard"],
                               tool="Bash", summary="git reset --hard HEAD~1", grant_key="c" * 64,
                               expires_at=now, decided_by="d", decided_at=now, resume_until=now + timedelta(hours=1))
            s.add(a)
            await s.commit()
            return a.id

    aid = run(setup())

    async def approve():
        async with guard_grants.followup_lock():
            async with SessionLocal() as s:
                a = await s.get(ActionApproval, aid)
                out = await guard_grants.create_resume_followup(s, a, "d")
                await s.commit()
                return out

    assert run(approve()) is None
    assert get_task(rec).prompt == "approved block"

    async def approval():
        async with SessionLocal() as s:
            return await s.get(ActionApproval, aid)

    assert run(approval()).followup_task_id is None
    set_task(rec, status="completed")
    fu = run(guard_grants.after_task_finished(rec))
    assert fu and fu != rec
    t = get_task(fu)
    assert t.origin == "guard" and "git reset --hard HEAD~1" in t.prompt
    assert run(approval()).followup_task_id == fu
    _park(fu)


def test_compose_failure_keeps_the_record(client, tmp_path, monkeypatch):
    """I5: a failed compose still puts the record into a recovered, resumed
    step (never a bare re-run) and leaves it pending."""
    owner = _owner(client)
    key = env_probe_key()
    tid = _crashed_run(owner, tmp_path, key, session_id="s1")
    session_file(owner.user_id, "s1")
    out, held = _recover_live(tid)
    assert out == "requeued"

    async def boom(*a, **kw):
        raise RuntimeError("compose broke")

    monkeypatch.setattr(prompting, "compose", boom)
    client.portal.call(app.state.worker.submit, *held[0])
    finished = wait_task(owner, tid)
    assert finished["status"] == "completed", finished.get("error")
    prompt = probe_prompt(key)
    assert RECORD_START in prompt and prompt.endswith(RECOVERED_CONTINUE)
    assert "original work" not in prompt
    assert get_task(tid).recovery["pending"] is True


def test_recovery_event_failure_still_enqueues(client, monkeypatch):
    """I6: a failed recovery event after the QUEUED commit never strands the row."""
    w = WorkerManager()
    tid = mk_task()
    enq = []

    async def enqueue(task_id, priority, created_at):
        enq.append(task_id)

    real = w._emit_offline

    async def emit(task_id, etype, payload):
        if etype == "recovery":
            raise RuntimeError("db hiccup")
        await real(task_id, etype, payload)

    w._enqueue, w._emit_offline = enqueue, emit
    assert run(w.recover(tid, "server restart")) == "requeued"
    assert enq == [tid] and get_task(tid).status == "queued"
    _park(tid)


def test_recover_skips_a_row_that_changed(client, monkeypatch):
    """M1: a row no longer RUNNING after the awaits gets no record and no requeue."""
    w = WorkerManager()
    tid = mk_task()
    enq = []

    async def enqueue(task_id, priority, created_at):
        enq.append(task_id)

    async def snap(task_id, reason, **kw):
        async with SessionLocal() as s:
            (await s.get(Task, task_id)).status = "cancelled"
            await s.commit()

    w._enqueue = enqueue
    monkeypatch.setattr(handoff_mod, "snapshot", snap)
    assert run(w.recover(tid, "server restart")) == "skipped"
    t = get_task(tid)
    assert t.status == "cancelled" and enq == [] and not t.recovery
