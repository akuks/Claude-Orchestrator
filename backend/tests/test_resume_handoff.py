"""Phase 3a task 3: the thread handoff - temp-root helpers, the log formatter,
step derivation, the builder and renderer, snapshots (versioning, the run's
`since`), the heartbeat loop, handoff.md, the routes and thread delete."""

import asyncio
import json
import os
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

import conftest
from app import auth
from app.database import SessionLocal
from app.guard import rules
from app.main import app
from app.models import Task, TaskEvent, TaskHandoff
from conftest import login_as, wait_task


@pytest.fixture(autouse=True)
def _no_lessons_job(monkeypatch):
    """Closing a handoff starts a background lessons job (own tests in test_proposals_lessons); on SQLite its
    reads would race the next write of these tests."""
    from app.decisions import after_run

    async def noop(root_id, closer_id):
        return None

    monkeypatch.setattr(after_run, "lessons_after_close", noop)

POLICY = {"project_dir": "/work/proj", "home": "/home/someone"}


# ---- temp roots (rules.py) ---------------------------------------------------------------


def test_temp_root_helpers():
    roots = rules.temp_roots(POLICY)
    assert isinstance(roots, list) and "/tmp" in roots
    assert rules.path_in_temp("/tmp/a.html", POLICY)
    assert not rules.path_in_temp("/work/proj/a.html", POLICY)
    assert not rules.path_in_temp("/tmp", POLICY)
    assert not rules.path_in_temp("", POLICY)
    # a temp root that holds the project is no temp root
    assert not rules.path_in_temp("/tmp/proj/a.html", {"project_dir": "/tmp/proj", "home": "/home/someone"})
    # _Ctx.in_temp keeps its delete semantics
    ctx = rules._Ctx(POLICY, "/work/proj")
    assert ctx.temp_roots == set(rules.temp_roots(POLICY))
    assert ctx.in_temp(rules._real("/tmp/x"), True)
    assert not ctx.in_temp(rules._real("/tmp"), True)
    assert ctx.in_temp(rules._real("/tmp"), False)
    assert not ctx.in_temp("/work/proj/x", True)


# ---- helpers ---------------------------------------------------------------------------------

T0 = datetime(2026, 11, 22, 10, 0, 0, tzinfo=timezone.utc)


def ev(etype, payload=None, at=None, **kw):
    """An event dict as the handoff reads it (seq follows creation order)."""
    ev.seq = getattr(ev, "seq", 0) + 1
    return {"type": etype, "payload": dict(payload or {}, **kw), "created_at": at or T0, "seq": ev.seq}


def use(name, inp, tid=None, parent=None, at=None):
    return ev("tool_use", {"name": name, "input": inp, "id": tid or uuid.uuid4().hex[:8], "parent": parent}, at)


def result(tid, content, is_error=False, parent=None, at=None):
    return ev("tool_result", {"tool_use_id": tid, "content": content, "is_error": is_error, "parent": parent}, at)


def gh_token():
    return "ghp_" + "a" * 36


def ant_key():
    return "sk-ant-" + "b" * 40


# ---- logfmt ----------------------------------------------------------------------------------


def test_logfmt_kinds():
    from app.resume import logfmt

    at = datetime(2026, 11, 22, 9, 8, 7, tzinfo=timezone.utc)
    cases = [
        (ev("text_output", {"text": "hello there"}, at), "SAY", "hello there"),
        (use("Bash", {"command": "ls -la", "description": "list"}, at=at), "BASH", "list :: ls -la"),
        (use("Edit", {"file_path": "/w/a.py"}, at=at), "EDIT", "/w/a.py"),
        (use("MultiEdit", {"file_path": "/w/b.py"}, at=at), "EDIT", "/w/b.py"),
        (use("Write", {"file_path": "/w/c.py"}, at=at), "WRITE", "/w/c.py"),
        (use("NotebookEdit", {"notebook_path": "/w/n.ipynb"}, at=at), "WRITE", "/w/n.ipynb"),
        (use("mcp__db__query", {"sql": "select 1"}, at=at), "MCP", "mcp__db__query"),
        (use("Agent", {"subagent_type": "explorer", "description": "find it"}, at=at), "AGENT+", "explorer"),
        (result("t1", "boom", is_error=True, at=at), "FAIL", "boom"),
        (result("t2", "[main 1a2b3c4] add thing\n 1 file changed", at=at), "COMMIT", "1a2b3c4"),
        (ev("guard", {"event": "PreToolUse", "decision": "deny", "tool": "Bash", "summary": "rm -rf ~"}, at),
         "BLOCK", "rm -rf ~"),
        (ev("guard", {"event": "PreToolUse", "decision": "approved", "summary": "git push"}, at), "ALLOW", "git push"),
        (ev("guard", {"event": "PreToolUse", "decision": "granted", "summary": "npm i"}, at), "ALLOW", "npm i"),
        (ev("guard", {"event": "PreToolUse", "decision": "deny", "status": "filed", "summary": "deploy"}, at),
         "WAIT", "deploy"),
        (ev("system", {"subtype": "compact_boundary"}, at), "COMPACT", ""),
        (ev("retry", {"attempt": 1, "reason": "transient error"}, at), "RETRY", "transient error"),
        (ev("error", {"message": "it broke"}, at), "ERROR", "it broke"),
        (ev("completed", {"status": "completed"}, at), "END", "completed"),
        (ev("recovery", {"why": "server restart", "at": at.isoformat(), "in_flight": 1, "orphan_killed": False,
                         "recoveries": 1, "gave_up": False}, at), "CUTOFF", "server restart"),
        (ev("recovery", {"why": "server restart", "at": at.isoformat(), "in_flight": 0, "orphan_killed": False,
                         "recoveries": 3, "gave_up": True}, at), "CUTOFF", "gave up"),
        (ev("fresh_session", {"reason": "previous session not available"}, at), "FRESH", "previous session"),
    ]
    for e, kind, detail in cases:
        line = logfmt.line(e)
        assert line is not None, (kind, e)
        assert line.startswith(f"09:08:07 {kind}"), (kind, line)
        assert detail in line, (kind, line)
    # a helper's line, and what gives no line
    helper = logfmt.line(use("Bash", {"command": "ls"}, parent="tuAgent", at=at))
    assert helper.startswith("09:08:07 [helper] BASH")
    assert logfmt.line(result("t3", "plain output", at=at)) is None
    assert logfmt.line(ev("started", {"model": "x"}, at)) is None
    assert logfmt.line(use("Read", {"file_path": "/x"}, at=at)) is None
    assert logfmt.line(ev("system", {"subtype": "init"}, at)) is None
    # details are redacted, cleaned and at most 160 chars
    long = logfmt.line(ev("text_output", {"text": "x" * 150 + " " + gh_token() + "\u202e" + "y" * 300}, at))
    assert gh_token() not in long and gh_token()[:5] not in long and "\u202e" not in long
    assert len(long.split(" ", 2)[2]) <= 160
    cut = logfmt.line(ev("text_output", {"text": "z" * 150 + gh_token()}, at))
    assert "***" in cut and gh_token()[:5] not in cut


def test_logfmt_tail_caps():
    from app.resume import logfmt

    evs = [ev("text_output", {"text": f"line {i} " + "w" * 100}) for i in range(100)]
    tail = logfmt.tail(evs)
    lines = tail.splitlines()
    assert len(lines) <= logfmt.LOG_TAIL_LINES and len(tail) <= logfmt.LOG_TAIL_CHARS
    assert "line 99" in lines[-1]
    small = logfmt.tail(evs, lines=5, chars=250)
    assert len(small) <= 250 and "line 99" in small


# ---- step derivation -------------------------------------------------------------------------


def test_step_from_todowrite(tmp_path):
    from app.resume import handoff

    old = use("TodoWrite", {"todos": [{"content": "old", "status": "in_progress", "activeForm": "Doing old"}]})
    todos = [
        {"content": "Read the code", "status": "completed", "activeForm": "Reading the code"},
        {"content": "Write the parser", "status": "in_progress", "activeForm": "Writing the parser"},
        {"content": "Test it", "status": "pending", "activeForm": "Testing it"},
        {"content": "Ship it", "status": "pending", "activeForm": "Shipping it"},
    ]
    st = handoff.step_progress([old, use("TodoWrite", {"todos": todos})], tmp_path, None, None)
    assert (st["n"], st["m"], st["text"], st["source"]) == (2, 4, "Writing the parser", "todo")
    assert st["next"] == "Test it" and st["done"] == ["Read the code"]


def test_step_from_taskcreate_update(tmp_path):
    from app.resume import handoff

    evs = [use("TaskCreate", {"subject": s}) for s in ("Plan", "Build", "Check")]
    evs += [use("TaskUpdate", {"taskId": "1", "status": "completed"}),
            use("TaskUpdate", {"taskId": "2", "status": "in_progress"})]
    st = handoff.step_progress(evs, tmp_path, None, None)
    assert (st["n"], st["m"], st["text"], st["source"]) == (2, 3, "Build", "tasks")
    assert st["next"] == "Check" and st["done"] == ["Plan"]


def _plan_file(ws, name="PLAN.md", mtime=None):
    f = ws / name
    f.write_text("# Plan\n- [x] first step\n- [ ] second step\n- [ ] third step\n")
    if mtime is not None:
        os.utime(f, (mtime, mtime))
    return f


def test_step_from_plan_file_and_notes_newer_wins(tmp_path):
    from app.resume import handoff

    f = _plan_file(tmp_path, mtime=T0.timestamp())
    evs = [use("Write", {"file_path": "PLAN.md", "content": "..."})]
    st = handoff.step_progress(evs, tmp_path, None, None)
    assert (st["n"], st["m"], st["text"], st["source"]) == (2, 3, "second step", "plan")
    assert st["next"] == "third step" and st["done"] == ["first step"]
    notes = "Task: parser\nStep: 5 of 7: wiring the CLI\nNext: tests"
    newer = handoff.step_progress(evs, tmp_path, notes, T0 + timedelta(minutes=5))
    assert (newer["n"], newer["m"], newer["text"], newer["source"]) == (5, 7, "wiring the CLI", "notes")
    assert newer["next"] == "tests"
    older = handoff.step_progress(evs, tmp_path, notes, T0 - timedelta(minutes=5))
    assert older["source"] == "plan"
    # every box checked: the plan is done, not on its last item
    f.write_text("- [x] a\n- [x] b\n")
    st = handoff.step_progress(evs, tmp_path, None, None)
    assert (st["n"], st["m"], st["text"], st["next"]) == (2, 2, "all steps done", None)
    # fewer than 2 boxes, or too big: not a plan
    f.write_text("- [ ] only one\n")
    assert handoff.step_progress(evs, tmp_path, None, None) is None
    f.write_text("- [ ] a\n- [ ] b\n" + "x" * handoff.PLAN_FILE_MAX)
    assert handoff.step_progress(evs, tmp_path, None, None) is None


def test_step_from_superpowers_ledger(tmp_path):
    from app.resume import handoff

    plans = tmp_path / "docs" / "superpowers" / "plans"
    plans.mkdir(parents=True)
    (plans / "2026-11-22-thing.md").write_text(
        "# Thing\n### Task 1: Groundwork\n- [ ] a\n- [ ] b\n### Task 2: The parser\n- [ ] c\n### Task 3: The CLI\n")
    sdd = tmp_path / ".superpowers" / "sdd" / "2026-11-22-thing"
    sdd.mkdir(parents=True)
    (sdd / "progress.md").write_text("Task 1: dispatched\nTask 1: complete (commits a..b)\nTask 2: dispatched\n")
    evs = [use("Write", {"file_path": str(plans / "2026-11-22-thing.md")})]
    st = handoff.step_progress(evs, tmp_path, None, None)
    assert (st["n"], st["m"], st["text"], st["source"]) == (2, 3, "The parser", "superpowers")
    assert st["next"] == "The CLI" and st["done"] == ["Groundwork"]
    # writing only the ledger finds its plan too
    evs = [use("Edit", {"file_path": ".superpowers/sdd/2026-11-22-thing/progress.md"})]
    assert handoff.step_progress(evs, tmp_path, None, None)["source"] == "superpowers"
    # a plan written in the project dir (not the workspace) is found there
    evs = [use("Write", {"file_path": str(plans / "2026-11-22-thing.md")})]
    ws = tmp_path / "ws"
    ws.mkdir()
    assert handoff.step_progress(evs, ws, None, None, [str(tmp_path)])["source"] == "superpowers"
    # ... but a tree outside the workspace, the project dir and the source dirs is never read
    assert handoff.step_progress(evs, ws, None, None) is None
    assert handoff.step_progress(evs, ws, None, None, [str(ws)]) is None
    ledger_out = [use("Edit", {"file_path": str(sdd / "progress.md")})]  # a progress.md outside them: ignored
    assert handoff.step_progress(ledger_out, ws, None, None, [str(ws / "src")]) is None
    assert handoff.step_progress(ledger_out, ws, None, None, [str(tmp_path)])["source"] == "superpowers"


def test_step_from_notes_header_and_last_prompt(tmp_path):
    from app.resume import handoff

    notes = "Task: port the parser\nStep: 3 of 5: x\nNext: wire it up\nsome more words"
    st = handoff.step_progress([], tmp_path, notes, T0)
    assert (st["n"], st["m"], st["text"], st["next"], st["source"]) == (3, 5, "x", "wire it up", "notes")
    assert handoff.step_progress([], tmp_path, "just words", T0) is None
    # priority 6: nothing else, so the step is the last user prompt
    content = handoff.build(_input(steps=[_step("user", "do the thing")]))
    assert content["step"]["text"] == "do the thing" and content["step"]["n"] is None
    assert content["step"]["source"] == "prompt"


# ---- builder inputs --------------------------------------------------------------------------


def _step(origin, prompt, sid=None, **kw):
    sid = sid or uuid.uuid4().hex
    base = {"id": sid, "title": prompt[:40], "prompt": prompt, "origin": origin, "status": "completed",
            "created_at": T0, "started_at": T0, "completed_at": T0 + timedelta(minutes=1), "result_text": None,
            "notes": None, "notes_updated_at": None}
    base.update(kw)
    return base


def _input(**kw):
    from app.resume import gitfacts, handoff

    base = dict(
        steps=[_step("user", "build it")], step_events=[],
        facts=gitfacts.Facts(True, "main", "abc123def456", False, 0, False, None),
        outside={"commits": [], "rewritten": False}, sources=[], waiting=[], children={}, others=[],
        trust="own", workspace="/work/ws", project_dir="/work/ws", policy={"project_dir": "/work/ws",
                                                                          "home": "/home/someone"},
        now=T0 + timedelta(minutes=2), reason="run ended", secrets=[],
    )
    base.update(kw)
    return handoff.HandoffInput(**base)


CONTENT_KEYS = {
    "as_of", "reason", "step_task_id", "step_status", "ended", "branch", "head", "dirty", "dirty_capped",
    "no_commits", "review", "sources", "task", "before", "step", "done", "next", "running", "waiting", "children",
    "last_request", "last_reply", "elsewhere", "outside_docs", "notes", "recent", "files", "others",
    "outside_commits", "history_rewritten", "more"}


def test_task_line_is_last_user_prompt():
    from app.resume import handoff

    steps = [
        _step("user", "first ask", created_at=T0),
        _step("guard", "co-guard follow-up: a person approved git push", created_at=T0 + timedelta(seconds=1)),
        _step("continue", "Continue the task from where it stopped.", created_at=T0 + timedelta(seconds=2)),
        _step("user", "second ask", created_at=T0 + timedelta(seconds=3)),
        _step("guard", "co-guard follow-up: again", created_at=T0 + timedelta(seconds=4)),
    ]
    evs = [ev("text_output", {"text": "Approved: you may run X"})]
    content = handoff.build(_input(steps=steps, step_events=evs))
    assert set(content) == CONTENT_KEYS
    assert content["task"] == "second ask" and content["last_request"] == "second ask"
    assert content["before"] == ["first ask"]
    assert content["step_task_id"] == steps[-1]["id"]
    assert "Approved" not in content["task"] and content["last_reply"] == "Approved: you may run X"
    assert content["more"] == f"GET /tasks/{steps[-1]['id']}/events"


def test_files_and_outside():
    from app.resume import handoff

    evs = [
        use("Write", {"file_path": "/work/ws/a.py"}),
        use("Edit", {"file_path": "b.py"}),
        use("Write", {"file_path": "/tmp/report.html"}),
        use("Write", {"file_path": "/tmp/x.py"}),
        use("Write", {"file_path": "/srv/other/x.py"}),
        use("Edit", {"file_path": "/work/proj/lib/c.py"}),
        use("Write", {"file_path": "/work/ws/a.py"}),
        use("Read", {"file_path": "/etc/hosts"}),
    ]
    out = handoff.files_touched(evs, "/work/ws", "/work/proj", POLICY)
    assert out["files"] == ["a.py", "/work/proj/lib/c.py", "b.py"]  # newest first, once each
    assert [p.endswith("/tmp/report.html") for p in out["outside_docs"]] == [True]
    assert not any(p.endswith("x.py") for p in out["files"] + out["outside_docs"])
    assert "/srv/other" in out["elsewhere"]
    many = [use("Write", {"file_path": f"/work/ws/f{i}.py"}) for i in range(80)]
    assert len(handoff.files_touched(many, "/work/ws", "/work/ws", POLICY)["files"]) == handoff.FILES_MAX


def test_running_at_cutoff():
    from app.resume import handoff

    evs = [
        ev("guard", {"event": "Stop", "background": [{"id": "b0", "what": "old"}]}),
        use("Agent", {"subagent_type": "explorer", "description": "find it"}, tid="ag1"),
        use("Agent", {"subagent_type": "explorer", "description": "done one"}, tid="ag2"),
        result("ag2", "found"),
        use("Bash", {"command": "sleep 1", "run_in_background": True}, tid="bg0"),
        ev("guard", {"event": "Stop", "background": [{"id": "b1", "what": "npm run dev"}]}),
        use("Bash", {"command": "npm run watch", "description": "watch", "run_in_background": True}, tid="bg1"),
        use("Bash", {"command": "ls"}, tid="fg"),
    ]
    running = handoff.running_at_cutoff(evs)
    assert len(running) == 3, running
    assert any("find it" in r for r in running) and any("npm run dev" in r for r in running)
    assert any("npm run watch" in r for r in running)
    assert not any("old" in r or "sleep 1" in r or "done one" in r for r in running)


# ---- render ------------------------------------------------------------------------------------


def _words(n, w="ab"):
    return " ".join([w] * n)


def _full_content(**over):
    from app.resume import handoff

    steps = [_step("user", "first ask"), _step("user", "second ask", notes=_words(100, "word"),
                                                notes_updated_at=T0, result_text=_words(80, "rr"))]
    evs = [use("Write", {"file_path": f"/work/ws/file{i}.py"}) for i in range(10)]
    evs += [ev("text_output", {"text": f"said {i}"}) for i in range(12)]
    content = handoff.build(_input(steps=steps, step_events=evs, others=["Other run one", "Other run two"]))
    content.update(over)
    return content


def test_render_caps_and_trims():
    from app.resume import handoff, texts

    c = _full_content()
    for k, v in c.items():  # every field but the notes is at most 200 characters
        if k == "notes":
            continue
        for s in (v if isinstance(v, list) else [v]):
            if isinstance(s, str):
                assert len(s) <= handoff.FIELD_MAX, k
    assert len(c["notes"]["text"]) <= handoff.NOTES_MAX_CHARS
    assert c["notes"]["text"] == _words(100, "word")  # the stored notes reach the handoff whole
    r0 = handoff.render(c)
    assert r0.splitlines()[0] == texts.HANDOFF_HEAD
    assert "said 11" in r0 and "file3.py" in r0 and "Other run one" in r0
    r1 = handoff.render(c, 1)
    assert "said 11" not in r1 and "file3.py" in r1  # 1: recent actions dropped
    r2 = handoff.render(c, 2)
    assert "file9.py" in r2 and "file5.py" in r2 and "file4.py" not in r2  # 2: files down to 5 (newest)
    assert len(c["notes"]["text"].split()) > 60 and len(c["last_reply"].split()) > 35
    r3 = handoff.render(c, 3)
    notes_line = [ln for ln in r3.splitlines() if ln.startswith("Claude's notes")][0]
    reply_line = [ln for ln in r3.splitlines() if ln.startswith("Claude's last reply")][0]
    assert notes_line.count("word") == 60 and reply_line.count("rr") == 35  # 3: notes 60, reply 35 words
    assert "Other run one" in r3
    r4 = handoff.render(c, 4)
    assert "Other run one" not in r4 and notes_line in r4  # 4: also active dropped
    # 900 words: at most 600
    big = _full_content(before=[_words(30, "bb")] * 4, done=[_words(30, "dd")] * 10,
                        files=[f"/work/ws/{'p' * 20}/{_words(8, 'ff')}{i}" for i in range(50)])
    assert len(handoff.render(big, 0).split()) <= handoff.HANDOFF_MAX_WORDS
    assert handoff.render(big).splitlines()[0] == texts.HANDOFF_HEAD


def test_render_lists_newest_8_files():
    """F111 (forge v0.7.0): render lists the newest FILES_SHOWN (8) files,
    FILES_TRIMMED (5) from level 2; the stored list (the card's) stays whole."""
    from app.resume import handoff

    evs = [use("Write", {"file_path": f"/work/ws/file{i}.py"}) for i in range(12)]
    c = handoff.build(_input(steps=[_step("user", "ask")], step_events=evs))
    assert len(c["files"]) == 12 and handoff.FILES_SHOWN == 8

    def listed(text):
        lines = text.splitlines()
        i = lines.index("Files touched (newest first):") + 1
        out = []
        while i < len(lines) and lines[i].startswith("- ") and ".py" in lines[i]:
            out.append(lines[i])
            i += 1
        return out

    r0 = handoff.render(c, 0)
    assert [ln[2:].rsplit("/", 1)[-1] for ln in listed(r0)] == [f"file{i}.py" for i in range(11, 3, -1)]
    assert "- (and 4 more)" in r0
    r2 = handoff.render(c, 2)
    assert len(listed(r2)) == 5 and "- (and 7 more)" in r2


def test_handoff_redacted_and_cleaned():
    from app.resume import handoff

    tok, key = gh_token(), ant_key()
    notes = f"keep {tok} and {key}\u202e and \u200b done"
    # a token straddling the 200-char cut: redacted first, so no prefix survives
    straddle = "s" * 190 + " " + key
    steps = [_step("user", f"use {tok}", notes=notes, notes_updated_at=T0, result_text=f"reply {key}\u202e")]
    content = handoff.build(_input(steps=steps, step_events=[ev("text_output", {"text": straddle})],
                                   secrets=["a-run-secret-value"]))
    text = handoff.render(content)
    for blob in (json.dumps(content, ensure_ascii=False), text):
        assert tok not in blob and key not in blob
        assert tok[:5] not in blob and key[:8] not in blob
        assert "\u202e" not in blob and "\u200b" not in blob
        assert "***" in blob
    steps[-1]["result_text"] = None
    content = handoff.build(_input(steps=steps, step_events=[ev("text_output", {"text": straddle})]))
    assert content["last_reply"].endswith("***") and "sk-an" not in content["last_reply"]
    assert len(content["last_reply"]) <= handoff.FIELD_MAX
    steps[-1]["notes"] = "n" * (handoff.NOTES_MAX_CHARS - 5) + " " + tok
    content = handoff.build(_input(steps=steps))
    assert len(content["notes"]["text"]) <= handoff.NOTES_MAX_CHARS and "ghp_a" not in content["notes"]["text"]
    assert content["notes"]["text"].endswith("***")
    # the run's exact secrets too
    steps[-1]["notes"] = "the value is a-run-secret-value"
    content = handoff.build(_input(steps=steps, secrets=["a-run-secret-value"]))
    assert "a-run-secret-value" not in content["notes"]["text"] and "***" in content["notes"]["text"]


def test_notes_shape():
    from app.resume import handoff

    at = T0 + timedelta(seconds=30)
    steps = [_step("user", "one", notes="older notes", notes_updated_at=T0),
             _step("user", "two", notes="newest notes", notes_updated_at=at),
             _step("user", "three")]
    content = handoff.build(_input(steps=steps))
    assert content["notes"] == {"text": "newest notes", "at": at.isoformat()}
    assert handoff.build(_input(steps=[_step("user", "x")]))["notes"] is None


# ---- snapshots (DB) --------------------------------------------------------------------------

GIT_ENV = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, env=GIT_ENV, check=True, capture_output=True,
                          text=True).stdout.strip()


def commit(cwd, name, msg=None):
    (cwd / name).write_text(name)
    git(cwd, "add", name)
    out = git(cwd, "commit", "-m", msg or name)
    return out, git(cwd, "rev-parse", "HEAD")


def run(coro):
    return asyncio.run(coro)


def mk_task(**kw):
    tid = kw.pop("id", None) or uuid.uuid4().hex

    async def go():
        async with SessionLocal() as s:
            t = Task(id=tid, title=kw.pop("title", "t"), prompt=kw.pop("prompt", "do it"),
                     root_id=kw.pop("root_id", tid), status=kw.pop("status", "completed"), **kw)
            s.add(t)
            await s.commit()

    run(go())
    return tid


def add_events(task_id, evs):
    async def go():
        async with SessionLocal() as s:
            top = (await s.execute(select(func.max(TaskEvent.seq)).where(TaskEvent.task_id == task_id))).scalar()
            for seq, (etype, payload) in enumerate(evs, start=(top or 0) + 1):
                s.add(TaskEvent(task_id=task_id, seq=seq, type=etype, payload=payload))
            await s.commit()

    run(go())


def stored_row(task_id):
    async def go():
        async with SessionLocal() as s:
            t = await s.get(Task, task_id)
            return (await s.execute(select(TaskHandoff).where(TaskHandoff.root_id == (t.root_id or t.id)))
                    ).scalar_one_or_none()

    return run(go())


def test_outside_since_held_for_the_run(client, tmp_path):
    from app.resume import handoff

    d = tmp_path / "repo"
    d.mkdir()
    git(d, "init", "-q", "-b", "main")
    _, h1 = commit(d, "a.txt")
    step1 = mk_task(workspace_dir=str(d), prompt="step one", head_at_start=h1[:12])
    run(handoff.snapshot(step1, "run ended"))
    assert stored_row(step1).content["head"] == h1[:12]
    _, h2 = commit(d, "person.txt", "a person's commit")  # C, between the steps
    step2 = mk_task(workspace_dir=str(d), prompt="step two", root_id=step1, parent_task_id=step1,
                    status="running", head_at_start=h2[:12], created_at=datetime.now(timezone.utc))
    since = run(handoff.boundary(step2))  # fixed once, at spawn
    assert since == h1[:12]
    first = run(handoff.snapshot(step2, "working", since=since))
    assert [c["subject"] for c in first.content["outside_commits"]] == ["a person's commit"]
    out, h3 = commit(d, "own.txt", "the run's own commit")  # C3
    add_events(step2, [("tool_use", {"name": "Bash", "input": {"command": "git commit -m x"}, "id": "c3"}),
                       ("tool_result", {"tool_use_id": "c3", "content": out, "is_error": False})])
    second = run(handoff.snapshot(step2, "working", since=since))
    assert [c["subject"] for c in second.content["outside_commits"]] == ["a person's commit"]
    assert second.content["head"] == h3[:12]
    assert any(x.startswith("commit ") and "the run's own commit" in x for x in second.content["done"])
    # without the held base, the stored head (H3 now) would hide C
    assert run(handoff.snapshot(step2, "working")).content["outside_commits"] == []
    # a rewritten branch: history changed, no list
    git(d, "reset", "-q", "--hard", h1)
    commit(d, "other.txt")
    rw = run(handoff.snapshot(step2, "working", since=h3[:12]))
    assert rw.content["history_rewritten"] is True and rw.content["outside_commits"] == []
    assert "history changed" in rw.text


def test_refresh_keeps_outside_base(client, tmp_path):
    """I4: a person's Refresh (or notes save) between steps never moves the
    outside-commit base, so their hand commit reaches the next follow-up."""
    from app.resume import handoff, prompting

    d = tmp_path / "repo"
    d.mkdir()
    git(d, "init", "-q", "-b", "main")
    _, h1 = commit(d, "a.txt")
    step1 = mk_task(workspace_dir=str(d), prompt="step one", head_at_start=h1[:12])
    run(handoff.snapshot(step1, "run ended"))
    _, h2 = commit(d, "person.txt", "a person's hand commit")
    refreshed = run(handoff.snapshot(step1, "manual"))
    run(handoff.snapshot(step1, "notes"))
    assert refreshed.content["head"] == h2[:12]
    step2 = mk_task(workspace_dir=str(d), prompt="step two", root_id=step1, parent_task_id=step1,
                    status="queued", created_at=datetime.now(timezone.utc))

    async def outside():
        async with SessionLocal() as s:
            return await prompting._outside(await s.get(Task, step2))

    line = run(outside())
    assert line and h2[:7] in line and "a person's hand commit" in line
    assert stored_row(step1).content["base_head"] == h1[:12]
    # a run's own snapshot moves the base to the new head
    ended = run(handoff.snapshot(step1, "run ended"))
    assert ended.content["base_head"] == ended.content["head"] == h2[:12]
    assert run(handoff.boundary(step2)) == h2[:12]


def test_snapshot_versioning_and_prev(client, tmp_path):
    from app.resume import handoff

    tid = mk_task(workspace_dir=str(tmp_path), prompt="version me")
    a = run(handoff.snapshot(tid, "run ended"))
    assert a.version == 1 and a.prev_text is None and a.status == "open"
    b = run(handoff.snapshot(tid, "run ended"))
    assert b.version == 1 and b.prev_text is None  # only as_of changed
    add_events(tid, [("text_output", {"text": "new progress"})])
    c = run(handoff.snapshot(tid, "run ended"))
    assert c.version == 2 and c.prev_text == b.text and c.prev_content["last_reply"] is None
    assert c.content["last_reply"] == "new progress"

    async def close():
        async with SessionLocal() as s:
            row = await s.get(TaskHandoff, c.id)
            row.status = "closed"
            await s.commit()

    run(close())
    assert run(handoff.snapshot(tid, "manual")).status == "open"  # a later snapshot reopens it


# ---- the worker: spawn, heartbeat, finish ------------------------------------------------------


def task_row(task_id):
    async def go():
        async with SessionLocal() as s:
            return await s.get(Task, task_id)

    return run(go())


def until(fn, timeout=15.0, step=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        got = fn()
        if got:
            return got
        time.sleep(step)
    return fn()


def wait_handoff(step_id, reason="run ended"):
    """The stored handoff of this step and reason (bounded wait). _finish stores it before the terminal
    status (test_finish_snapshot_lands_before_the_terminal_status pins that); the wait stays as a guard."""
    got = until(lambda: (r := stored_row(step_id)) is not None and r.task_id == step_id
                and r.reason == reason and r)
    assert got, f"no {reason!r} handoff stored for step {step_id}"
    return got


def start(client, prompt, **body):
    r = client.post("/tasks", json={"prompt": prompt, **body})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_heartbeat_loop_cadence(client, monkeypatch):
    from app.guard import runtime
    from app.resume import handoff, texts

    monkeypatch.setattr(handoff, "HEARTBEAT_SECONDS", 0.2)
    monkeypatch.setattr(handoff, "HANDOFF_EVERY_SECONDS", 0.4)
    tid = start(client, "beat [hang]")
    first = until(lambda: task_row(tid).heartbeat_at)
    assert first is not None
    assert until(lambda: (task_row(tid).heartbeat_at or first) > first)
    row = until(lambda: (r := stored_row(tid)) is not None and r.reason == "working" and r)
    assert row and row.content["step_status"] == "running"
    md = runtime.task_dir(tid) / handoff.HANDOFF_FILE
    assert until(lambda: md.exists())
    assert (md.stat().st_mode & 0o777) == 0o600
    assert md.read_text().startswith(texts.HANDOFF_HEAD)
    assert client.post(f"/tasks/{tid}/cancel").status_code == 200
    assert wait_task(client, tid)["status"] == "cancelled"
    wait_handoff(tid, "cut off")  # the finish snapshot


def test_finish_reason_cut_off_vs_run_ended(client):
    from app.constants import Status
    from app.worker import WorkerManager

    done = wait_task(client, start(client, "a plain run"))
    assert done["status"] == "completed"
    row = wait_handoff(done["id"])
    assert row.reason == "run ended" and row.content["step_status"] == "completed"
    assert row.content["ended"] == "session ended (completed)"
    assert WorkerManager._finish_reason(Status.CANCELLED, None) == "cut off"
    assert WorkerManager._finish_reason(Status.FAILED, "Task exceeded time limit") == "cut off"
    assert WorkerManager._finish_reason(Status.FAILED, "boom") == "run ended"
    assert WorkerManager._finish_reason(Status.COMPLETED, None) == "run ended"


def _slow_finish_snapshot(monkeypatch):
    """Slow the finish snapshot down, so a status committed before it would be seen without it."""
    from app.resume import handoff

    real = handoff.snapshot

    async def slow(task_id, reason, **kw):
        if reason in ("run ended", "cut off"):
            await asyncio.sleep(0.6)
        return await real(task_id, reason, **kw)

    monkeypatch.setattr(handoff, "snapshot", slow)


def _at_terminal(tid):
    """The stored handoff read the moment the step's terminal status is visible."""
    deadline = time.time() + 20.0
    while time.time() < deadline:
        if task_row(tid).status in ("completed", "failed", "cancelled"):
            return stored_row(tid)
        time.sleep(0.02)
    raise AssertionError(f"step {tid} never ended")


def test_finish_snapshot_lands_before_the_terminal_status(client, monkeypatch):
    _slow_finish_snapshot(monkeypatch)
    tid = start(client, "snapshot first")
    row = _at_terminal(tid)
    assert row is not None and row.task_id == tid and row.reason == "run ended"
    # written before the commit, it still records the terminal status
    assert row.content["step_status"] == "completed"
    assert row.content["ended"] == "session ended (completed)"
    # a follow-up issued right away composes with the finish handoff
    key = conftest.env_probe_key()
    fu = client.post(f"/tasks/{tid}/followup", json={"prompt": f"next [env-probe:{key}]"})
    assert fu.status_code == 201, fu.text
    wait_task(client, fu.json()["id"])
    copied = conftest.read_probe_file(key, "handoff")
    assert copied is not None and "Session: session ended (completed)." in copied


def test_cut_off_snapshot_lands_before_the_cancelled_status(client, monkeypatch):
    _slow_finish_snapshot(monkeypatch)
    tid = start(client, "cut first [hang]")
    assert until(lambda: task_row(tid).run_pgid)  # the process is registered, so cancel signals it
    assert client.post(f"/tasks/{tid}/cancel").status_code == 200
    row = _at_terminal(tid)
    assert row is not None and row.task_id == tid and row.reason == "cut off"
    assert row.content["step_status"] == "cancelled" and row.content["ended"] == "cut off"


def test_handoff_md_written_at_spawn(client):
    from app.resume import texts

    root = wait_task(client, start(client, "md root"))
    assert root["status"] == "completed"
    wait_handoff(root["id"])
    key = conftest.env_probe_key()
    fu = client.post(f"/tasks/{root['id']}/followup", json={"prompt": f"next [env-probe:{key}]"})
    assert fu.status_code == 201, fu.text
    wait_task(client, fu.json()["id"])
    copied = conftest.read_probe_file(key, "handoff")
    assert copied is not None and copied.startswith(texts.HANDOFF_HEAD) and "md root" in copied
    # a new thread has no stored handoff, so no file
    key2 = conftest.env_probe_key()
    wait_task(client, start(client, f"fresh [env-probe:{key2}]"))
    assert conftest.read_env_probe(key2) is not None and conftest.read_probe_file(key2, "handoff") is None


def test_finish_snapshots_and_thread_delete_removes(client):
    done = wait_task(client, start(client, "delete me"))
    wait_handoff(done["id"])
    fu = client.post(f"/tasks/{done['id']}/followup", json={"prompt": "and more"})
    step2 = wait_task(client, fu.json()["id"])
    row = wait_handoff(step2["id"])
    assert row.task_id == step2["id"] and row.content["task"] == "and more"
    assert row.content["before"] == ["delete me"]
    assert client.delete(f"/tasks/{done['id']}").status_code == 204

    async def count():
        async with SessionLocal() as s:
            return (await s.execute(select(func.count()).select_from(TaskHandoff)
                                    .where(TaskHandoff.root_id == done["id"]))).scalar()

    assert run(count()) == 0


# ---- routes ------------------------------------------------------------------------------------


def test_handoff_routes_permissions(client):
    tag = uuid.uuid4().hex[:8]
    p = client.post("/projects", json={"name": f"ho-{tag}"}).json()
    owner = login_as(f"ho-owner-{tag}@test.local")
    viewer = login_as(f"ho-view-{tag}@test.local")
    outsider = login_as(f"ho-out-{tag}@test.local")
    r = client.put(f"/projects/{p['id']}/members", json=[
        {"subject_type": "user", "subject_id": owner.user_id, "role": "contributor"},
        {"subject_type": "user", "subject_id": viewer.user_id, "role": "contributor"},
    ])
    assert r.status_code == 200, r.text
    r = owner.post("/tasks", json={"prompt": "routes", "project_id": p["id"]})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    wait_task(owner, tid)
    # the handoff row itself (stored before the terminal status; the wait is a guard)
    assert until(lambda: (r := stored_row(tid)) is not None and r.reason == "run ended")
    got = viewer.get(f"/tasks/{tid}/handoff")
    assert got.status_code == 200, got.text
    body = got.json()
    assert set(body) == {"content", "text", "version", "status", "reason", "saved_at", "prev_available"}
    assert body["status"] == "open" and body["reason"] == "run ended" and body["prev_available"] is False
    assert viewer.post(f"/tasks/{tid}/handoff/close").status_code == 403
    assert viewer.post(f"/tasks/{tid}/handoff/refresh").status_code == 403
    closed = owner.post(f"/tasks/{tid}/handoff/close")
    assert closed.status_code == 200 and closed.json()["status"] == "closed"
    fresh = owner.post(f"/tasks/{tid}/handoff/refresh")
    assert fresh.status_code == 200 and fresh.json()["reason"] == "manual" and fresh.json()["status"] == "open"
    assert outsider.get(f"/tasks/{tid}/handoff").status_code == 404
    assert outsider.post(f"/tasks/{tid}/handoff/close").status_code == 404

    async def mint():
        async with SessionLocal() as s:
            raw = await auth.mint_token(s, owner.user_id, "agent-task", tid, 3600)
            await s.commit()
            return raw

    agent = TestClient(app)
    agent.headers["Authorization"] = f"Bearer {run(mint())}"
    assert agent.get(f"/tasks/{tid}/handoff").status_code == 403
    assert agent.post(f"/tasks/{tid}/handoff/refresh").status_code == 403
    # a thread without a handoff
    bare = mk_task(created_by=owner.user_id, project_id=p["id"])
    assert owner.get(f"/tasks/{bare}/handoff").status_code == 404
    assert owner.post(f"/tasks/{bare}/handoff/close").status_code == 404


# ---- stream fixtures ---------------------------------------------------------------------------


def _handoff(client, task_id):
    r = client.get(f"/tasks/{task_id}/handoff")
    assert r.status_code == 200, r.text
    return r.json()


def test_fixture_todo_and_taskcreate(client):
    todo = wait_task(client, start(client, "todo [stream-fixture:todo]"))
    assert todo["status"] == "completed", todo.get("error")
    wait_handoff(todo["id"])
    st = _handoff(client, todo["id"])["content"]["step"]
    assert (st["n"], st["m"], st["text"]) == (2, 4, "Writing the parser")
    tasks = wait_task(client, start(client, "tasks [stream-fixture:taskcreate]"))
    assert tasks["status"] == "completed", tasks.get("error")
    wait_handoff(tasks["id"])
    st = _handoff(client, tasks["id"])["content"]["step"]
    assert (st["n"], st["m"], st["text"]) == (2, 3, "Build")
    assert "Step 2 of 3: Build" in _handoff(client, tasks["id"])["text"]


def test_fixture_plan_then_newer_notes(client):
    done = wait_task(client, start(client, "plan [stream-fixture:plan]"))
    assert done["status"] == "completed", done.get("error")
    wait_handoff(done["id"])
    ws = task_row(done["id"]).workspace_dir
    _plan_file(__import__("pathlib").Path(ws), mtime=time.time() - 60)
    st = client.post(f"/tasks/{done['id']}/handoff/refresh").json()["content"]["step"]
    assert (st["n"], st["m"], st["text"], st["source"]) == (2, 3, "second step", "plan")

    async def note():
        async with SessionLocal() as s:
            t = await s.get(Task, done["id"])
            t.notes, t.notes_updated_at = "Step: 4 of 9: from the notes", datetime.now(timezone.utc)
            await s.commit()

    run(note())
    content = client.post(f"/tasks/{done['id']}/handoff/refresh").json()["content"]
    assert (content["step"]["n"], content["step"]["source"]) == (4, "notes")
    assert content["notes"]["text"] == "Step: 4 of 9: from the notes"


def test_fixture_commit_bg_and_outside_doc(client):
    from app.guard import runtime

    c = wait_task(client, start(client, "commit [stream-fixture:commit]"))
    assert c["status"] == "completed", c.get("error")
    wait_handoff(c["id"])
    content = _handoff(client, c["id"])["content"]
    assert "commit 1a2b3c4 add feature" in content["done"]
    assert any(" COMMIT [main 1a2b3c4] add feature" in ln for ln in content["recent"])
    b = wait_task(client, start(client, "bg [stream-fixture:bg]"))
    assert b["status"] == "completed", b.get("error")
    wait_handoff(b["id"])
    running = _handoff(client, b["id"])["content"]["running"]
    assert running == ["helper explorer: map the repo", "background: npm run dev"]
    o = wait_task(client, start(client, "doc [stream-fixture:outside_doc]"))
    assert o["status"] == "completed", o.get("error")
    wait_handoff(o["id"])
    content = _handoff(client, o["id"])["content"]
    ws = task_row(o["id"]).workspace_dir
    policy = {"project_dir": ws, "home": runtime.real_home(), "tmpdir": os.environ.get("TMPDIR")}
    report = "/tmp/co-handoff-fixture-report.html"
    assert not any("x.py" in p for p in content["files"] + content["outside_docs"])
    if rules.path_in_temp(report, policy):
        assert content["outside_docs"] == [report] and "/tmp" in content["elsewhere"]
        assert "may vanish" in _handoff(client, o["id"])["text"]
    else:  # the workspace itself is under /tmp (Linux CI), so /tmp is no temp root for it
        assert content["outside_docs"] == [] and content["elsewhere"] == ["/tmp"]


def test_snapshot_others_include_a_legacy_row(client, tmp_path):
    from app.resume import handoff

    owner = login_as(f"ho-legacy-{uuid.uuid4().hex[:8]}@test.local")
    root = mk_task(workspace_dir=str(tmp_path), status="running", created_by=owner.user_id)
    mk_task(workspace_dir=str(tmp_path), title="legacy run", status="running", created_by=owner.user_id, root_id=None)
    assert run(handoff.snapshot(root, "working")).content["others"] == ["legacy run"]  # a NULL root_id is another run
    legacy = mk_task(workspace_dir=str(tmp_path / "solo"), status="running", created_by=owner.user_id, root_id=None)
    assert run(handoff.snapshot(legacy, "working")).content["others"] == []  # never itself


def test_snapshot_children_waiting_others(client, tmp_path):
    from app.models import ActionApproval
    from app.resume import handoff

    tag = uuid.uuid4().hex[:8]
    owner = login_as(f"ho-kids-{tag}@test.local")
    stranger = login_as(f"ho-str-{tag}@test.local")
    ws = str(tmp_path)
    root = mk_task(workspace_dir=ws, prompt="lead", status="running", created_by=owner.user_id,
                   started_at=datetime.now(timezone.utc))
    for st in ("running", "failed", "failed"):
        mk_task(workspace_dir=ws, root_id=root, parent_task_id=root, origin="agent", status=st,
                created_by=owner.user_id)
    mk_task(workspace_dir=ws, title="my other run", status="running", created_by=owner.user_id)
    mk_task(workspace_dir=ws, title="secret run", status="running", created_by=stranger.user_id)

    async def approval():
        async with SessionLocal() as s:
            s.add(ActionApproval(task_id=root, tool="Bash", summary="git push origin main", status="pending",
                                 expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)))
            s.add(ActionApproval(task_id=root, tool="Bash", summary="old one", status="pending",
                                 expires_at=datetime.now(timezone.utc) - timedelta(minutes=5)))
            await s.commit()

    run(approval())
    row = run(handoff.snapshot(root, "working"))
    c = row.content
    assert c["step_task_id"] == root  # children are counted, never described
    assert c["children"] == {"running": 1, "failed": 2}
    assert c["waiting"] == ["Bash: git push origin main"]
    assert sorted(c["others"]) == ["a run you cannot see", "my other run"]
    assert "Child tasks: 2 failed, 1 running" in row.text and "Waiting on you: Bash: git push" in row.text
    # a child's finish still describes the main line
    kid = mk_task(workspace_dir=ws, root_id=root, parent_task_id=root, origin="agent", status="completed")
    assert run(handoff.snapshot(kid, "run ended")).content["step_task_id"] == root


# ---- fix round 1: cut-off finishes redact with the run's exact secrets ------------------------


def _cut_off_run(client, monkeypatch, how):
    """A [hang] run in a project whose env holds a run secret that is not
    token-shaped; the secret is in the prompt and in the last reply."""
    secret = "plainpw-" + uuid.uuid4().hex  # built at runtime, no token shape
    r = client.post("/projects", json={"name": f"ho-cut-{uuid.uuid4().hex[:8]}",
                                       "env": {"PROBE_DB_PASSWORD": secret}})
    assert r.status_code == 201, r.text
    if how == "timeout":
        from app.config import settings

        monkeypatch.setattr(settings, "task_timeout_seconds", 3)
    tid = start(client, f"[hang] use {secret}", project_id=r.json()["id"])
    assert until(lambda: any(e["type"] == "text_output" for e in client.get(f"/tasks/{tid}/events").json()))
    if how == "cancel":
        assert client.post(f"/tasks/{tid}/cancel").status_code == 200
    done = wait_task(client, tid)
    assert done["status"] == ("cancelled" if how == "cancel" else "failed"), done.get("error")
    row = until(lambda: (x := stored_row(tid)) is not None and x.reason == "cut off" and x)
    return secret, row


@pytest.mark.parametrize("how", ["cancel", "timeout"])
def test_cut_off_handoff_redacts_run_secrets(client, monkeypatch, how):
    secret, row = _cut_off_run(client, monkeypatch, how)
    assert row.content["ended"] == "cut off"
    assert row.content["last_reply"] and row.content["last_reply"].startswith("waiting")
    for blob in (json.dumps(row.content), row.text):
        assert secret not in blob and secret[:12] not in blob
        assert "***" in blob
    assert "***" in row.content["task"] and "***" in row.content["last_reply"]
