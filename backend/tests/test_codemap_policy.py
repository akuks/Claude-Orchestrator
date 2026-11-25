"""Phase 6b Task 3: the policy keys co-guard reads for code map answers (``code_map``, ``code_map_refs``) and the
spool line -> ``code_map`` run-log event."""

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone

import pytest

from app import gitsafe
from app.database import SessionLocal
from app.guard import runtime as guard_runtime
from app.models import McpServer, ProjectIndex, ProjectMember, Task
from test_guard_policy_inputs import _add, _gather, _project, _run, _task, _uid, _user

SHA = "1" * 40
REF_SHA = "2" * 40
UNKNOWN = {"state": "unknown", "behind": None, "changed": [], "more": 0, "head": None}
GIT_ENV = {"PATH": os.environ.get("PATH", ""), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "HOME": os.devnull}


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], env=GIT_ENV, capture_output=True, text=True,
                          timeout=60, check=True).stdout.strip()


def _commit(repo, name, text):
    (Path(repo) / name).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c")
    return _git(repo, "rev-parse", "HEAD")


def _index(pid, status="ok", sha=SHA):
    return ProjectIndex(project_id=pid, status=status, sha=sha,
                        indexed_at=datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc))


@pytest.fixture
def server(client):
    """A code map server for the projects named in ``names`` (scope project: other tests never see it)."""
    made = []

    def make(project_name, enabled=True, command="npx", args=("-y", "codebase-memory-mcp@0.12.1")):
        m = McpServer(name=f"Code map {_uid()}", scope="project", project=project_name, command=command,
                      args=list(args), enabled=enabled)
        _add(m)
        made.append(m.id)
        return m

    yield make

    async def drop():
        async with SessionLocal() as s:
            for mid in made:
                row = await s.get(McpServer, mid)
                if row is not None:
                    await s.delete(row)
            await s.commit()

    _run(drop())


def test_keys_present_when_on_ok_and_registered(client, tmp_path, server):
    u = _user()
    p = _project(tmp_path, code_map=True)
    _add(u, p, _index(p.id))
    m = server(p.name)
    pol = _gather(_task(p.id), p, u.id).policy
    prefix = "mcp__" + m.name.replace(" ", "_") + "__"
    assert pol["code_map"] == {"sha": SHA, "indexed_at": "2026-11-24T10:00:00+00:00", "prefix": prefix,
                               "fresh": UNKNOWN}  # the folder is not a repository
    assert pol["code_map_refs"] == [] and pol["code_map_unmapped"] == []
    json.dumps(pol)  # the policy file is JSON


def test_ok_without_a_commit_still_gets_the_key(client, tmp_path, server):
    """An ``ok`` index with no sha (no commit to compare): the key is there with sha None, so the hook says
    "freshness unknown" instead of saying nothing."""
    u = _user()
    p = _project(tmp_path, code_map=True)
    _add(u, p, _index(p.id, sha=None))
    server(p.name)
    pol = _gather(_task(p.id), p, u.id).policy
    assert pol["code_map"]["sha"] is None and pol["code_map"]["prefix"].startswith("mcp__")
    assert pol["code_map"]["fresh"] == UNKNOWN
    assert pol["code_map_refs"] == []
    json.dumps(pol)


def test_reference_without_a_commit_is_left_out(client, tmp_path, server):
    owner = _user()
    p = _project(tmp_path, code_map=True)
    ref = _project(tmp_path, name=f"nosha-{_uid()}", code_map=True)
    p.reference_project_ids = [ref.id]
    _add(owner, p, ref, _index(p.id), _index(ref.id, sha=None),
         *[ProjectMember(project_id=x.id, subject_type="user", subject_id=owner.id, role="contributor")
           for x in (p, ref)])
    server(p.name)
    pol = _gather(_task(p.id), p, owner.id).policy
    assert pol["code_map_refs"] == [] and pol["code_map_unmapped"] == [ref.name]


@pytest.mark.parametrize("case", ["off", "no_row", "failed", "running", "no_server", "disabled_server",
                                  "other_project_server", "not_code_map_server"])
def test_keys_absent_otherwise(client, tmp_path, server, case):
    u = _user()
    p = _project(tmp_path, code_map=case != "off")
    rows = [u, p]
    if case != "no_row":
        rows.append(_index(p.id, status={"failed": "failed", "running": "running"}.get(case, "ok"),
                           sha=SHA))
    _add(*rows)
    if case == "disabled_server":
        server(p.name, enabled=False)
    elif case == "other_project_server":
        server(f"other-{_uid()}")
    elif case == "not_code_map_server":
        server(p.name, args=("-y", "@playwright/mcp@0.0.86"))
    elif case != "no_server":
        server(p.name)
    pol = _gather(_task(p.id), p, u.id).policy
    assert "code_map" not in pol and "code_map_refs" not in pol and "code_map_unmapped" not in pol


def test_old_project_gets_nothing(client, tmp_path, server):
    u = _user()
    p = _project(tmp_path)  # pre-0018: the switch reads false, no index row
    _add(u, p)
    server(p.name)
    pol = _gather(_task(p.id), p, u.id).policy
    assert p.code_map is False and "code_map" not in pol and "code_map_refs" not in pol


def test_reference_listed_only_with_its_own_ok_index(client, tmp_path, server):
    owner = _user()
    p = _project(tmp_path, code_map=True)
    mapped = _project(tmp_path, name=f"mapped-{_uid()}", code_map=True)
    unmapped = _project(tmp_path, name=f"unmapped-{_uid()}", code_map=True)
    switched_off = _project(tmp_path, name=f"off-{_uid()}", code_map=False)
    p.reference_project_ids = [mapped.id, unmapped.id, switched_off.id]
    _add(owner, p, mapped, unmapped, switched_off, _index(p.id), _index(mapped.id, sha=REF_SHA),
         _index(unmapped.id, status="failed"), _index(switched_off.id, sha=REF_SHA),
         *[ProjectMember(project_id=x.id, subject_type="user", subject_id=owner.id, role="contributor")
           for x in (p, mapped, unmapped, switched_off)])
    server(p.name)
    inp = _gather(_task(p.id), p, owner.id)
    assert inp.policy["code_map_refs"] == [{"dir": os.path.realpath(mapped.directory), "sha": REF_SHA,
                                            "changed": False}]
    assert inp.policy["code_map_unmapped"] == [unmapped.name, switched_off.name]
    assert len(inp.add_dirs) == 3


# ---- phase 7d: freshness worked out at task start ------------------------------------------------


needs_git = pytest.mark.skipif(not gitsafe.git_available(), reason="git is not installed")


@needs_git
def test_fresh_and_reference_changed_from_real_repositories(client, tmp_path, server):
    owner = _user()
    p = _project(tmp_path, code_map=True)
    moved = _project(tmp_path, name=f"moved-{_uid()}", code_map=True)
    still = _project(tmp_path, name=f"still-{_uid()}", code_map=True)
    for x in (p, moved, still):
        _git(x.directory, "init", "-q", "-b", "main")
    built = _commit(p.directory, "a.py", "1")
    head = _commit(p.directory, "b.py", "1")
    old_ref = _commit(moved.directory, "r.py", "1")
    _commit(moved.directory, "r.py", "2")
    still_sha = _commit(still.directory, "s.py", "1")
    p.reference_project_ids = [moved.id, still.id]
    _add(owner, p, moved, still, _index(p.id, sha=built), _index(moved.id, sha=old_ref),
         _index(still.id, sha=still_sha),
         *[ProjectMember(project_id=x.id, subject_type="user", subject_id=owner.id, role="contributor")
           for x in (p, moved, still)])
    server(p.name)
    pol = _gather(_task(p.id), p, owner.id).policy
    assert pol["code_map"]["fresh"] == {"state": "ahead", "behind": 1, "changed": ["b.py"], "more": 0,
                                        "head": head}
    assert [(r["sha"], r["changed"]) for r in pol["code_map_refs"]] == [(old_ref, True), (still_sha, False)]
    assert pol["code_map_unmapped"] == []
    json.dumps(pol)


def test_a_slow_check_does_not_hold_the_task_up(client, tmp_path, server, monkeypatch):
    from app.codemap import freshness
    from app.guard import policy_inputs

    u = _user()
    p = _project(tmp_path, code_map=True)
    _add(u, p, _index(p.id))
    server(p.name)

    def slow(*a, **k):
        time.sleep(1.0)
        return {"state": "current", "behind": 0, "changed": [], "more": 0, "head": SHA}

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(freshness, "freshness", slow)
    monkeypatch.setattr(policy_inputs, "FRESH_WAIT", 0.2)
    from app.models import Project

    async def go():  # timed inside the loop: asyncio.run itself waits for the worker thread on its way out
        async with SessionLocal() as s:
            proj = await s.get(Project, p.id)
            t0 = time.monotonic()
            got = await policy_inputs.gather(s, task=_task(p.id), project=proj, owner_id=u.id, mcp=None)
            return got.policy, time.monotonic() - t0

    pol, took = _run(go())
    assert took < 0.9, took
    assert pol["code_map"]["fresh"] == UNKNOWN
    monkeypatch.setattr(freshness, "freshness", boom)
    assert _gather(_task(p.id), p, u.id).policy["code_map"]["fresh"] == UNKNOWN


# ---- spool -> code_map run-log event -----------------------------------------------------------


def test_spool_line_becomes_code_map_event(client):
    from app import worker as worker_mod
    from app.usage.tracker import UsageTracker

    tid = uuid.uuid4().hex

    async def mk():
        async with SessionLocal() as s:
            s.add(Task(id=tid, title="t", prompt="p", root_id=tid, status="running"))
            await s.commit()

    _run(mk())
    w = worker_mod.WorkerManager()
    tracker = w._usage[tid] = UsageTracker()
    w._usage_spool[tid] = w._new_usage_spool()
    emitted = []

    async def emit(etype, payload):
        emitted.append((etype, payload))

    d = guard_runtime.make_task_dir(tid)
    with open(d / guard_runtime.SPOOL_NAME, "w", encoding="utf-8") as f:
        for rec in ({"event": "PostToolUse", "nudge": "code_map", "fresh": True, "decisions": [12], "old_code": False,
                     "injected": 120, "ts": 1.0},
                    {"event": "PreToolUse", "nudge": "decisions", "ids": [12], "path": "src/a.py", "ts": 1.5,
                     "partial": True},
                    {"event": "PostToolUse", "nudge": "code_map", "fresh": None, "decisions": [], "old_code": True,
                     "injected": 30, "ts": 2.0, "partial": True},
                    {"event": "PostToolUse", "nudge": "code_map", "fresh": None, "decisions": [], "ts": 2.5,
                     "partial": "yes"},
                    {"event": "PostToolUse", "nudge": "code_map", "fresh": 1, "decisions": [], "ts": 3.0},
                    {"event": "PostToolUse", "nudge": "code_map", "fresh": False, "decisions": "x", "ts": 4.0}):
            f.write(json.dumps(rec) + "\n")
    _run(w._ingest_usage_spool(tid, emit))
    _run(w._ingest_guard_spool(tid, emit))  # the final ingest: never again, never a guard event
    assert [(t, p) for t, p in emitted if t in ("code_map", "decision_note")] == [
        ("code_map", {"fresh": True, "decisions": [12], "old_code": False}),
        ("decision_note", {"ids": [12], "path": "src/a.py", "partial": True}),
        ("code_map", {"fresh": None, "decisions": [], "old_code": True, "partial": True}),
        ("code_map", {"fresh": None, "decisions": [], "old_code": False}),  # only a real true counts
    ]
    assert not [p for t, p in emitted if t == "guard"]
    assert tracker.injected_chars == 150
