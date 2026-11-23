"""Phase 4b task 3: read a run's commits, flag decisions that may be out of date."""

import asyncio
import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.decisions import after_run
from app.decisions.after_run import Commit
from app.models import Project, ProjectDecision


def _run(coro):
    return asyncio.run(coro)


def _git(d, *a, env=None):
    e = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
         "GIT_COMMITTER_EMAIL": "t@t", **(env or {})}
    return subprocess.run(["git", *a], cwd=d, check=True, capture_output=True, env=e, text=True).stdout.strip()


def _commit(d, name, subject):
    p = d / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(subject + uuid.uuid4().hex)
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", subject)
    return _git(d, "rev-parse", "HEAD")


@pytest.fixture()
def repo(tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    _git(d, "init", "-q", "-b", "main")
    return d


def test_read_commits_range(repo):
    start = _commit(repo, "a.txt", "base")
    _git(repo, "checkout", "-q", "-b", "side")
    _commit(repo, "side.py", "side work")
    _git(repo, "checkout", "-q", "main")
    c1 = _commit(repo, "src/one.py", "one")
    _git(repo, "merge", "--no-ff", "-q", "-m", "merge side", "side")
    c3 = _commit(repo, "src/three.py", "three")
    got = after_run.read_commits(str(repo), start)
    subjects = [c.subject for c in got]
    assert "merge side" not in subjects
    assert subjects[0] == "three" and set(subjects) == {"three", "one", "side work"}
    assert got[0].sha == c3 and c3.startswith(got[0].short)
    assert got[0].files == ["src/three.py"]
    assert [c for c in got if c.sha == c1][0].files == ["src/one.py"]


def test_read_commits_nothing(repo, tmp_path):
    start = _commit(repo, "a.txt", "base")
    assert after_run.read_commits(str(repo), start) == []
    _commit(repo, "b.txt", "next")
    assert after_run.read_commits(str(repo / "missing"), start) == []
    assert after_run.read_commits(str(tmp_path), start) == []
    assert after_run.read_commits(None, start) == []
    assert after_run.read_commits(str(repo), None) == []
    assert after_run.read_commits(str(repo), "zzz") == []
    assert after_run.read_commits(str(repo), "HEAD~1") == []
    assert after_run.read_commits(str(repo), "0123456789abcdef0123456789abcdef01234567") == []
    assert len(after_run.read_commits(str(repo), start)) == 1


def test_files_capped_200(repo):
    start = _commit(repo, "a.txt", "base")
    for i in range(210):
        (repo / f"f{i}.txt").write_text("x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "many")
    got = after_run.read_commits(str(repo), start)
    assert len(got) == 1 and len(got[0].files) == 200


def _mk(project_dir="/x", n=1, touches=("src/**",), verified_days=20, flagged=None, status="active"):
    pid = uuid.uuid4().hex

    async def go():
        async with SessionLocal() as s:
            s.add(Project(id=pid, name="p" + pid[:8], slug="p" + pid[:8], directory=project_dir))
            now = datetime.now(timezone.utc)
            for i in range(1, n + 1):
                s.add(ProjectDecision(project_id=pid, number=i, title=f"d{i}", touches=list(touches), status=status,
                                      last_verified_at=now - timedelta(days=verified_days),
                                      stale_flagged_at=flagged,
                                      created_at=now - timedelta(minutes=n - i)))
            await s.commit()

    _run(go())
    return pid


def _flag(pid, commits, title="T"):
    async def go():
        async with SessionLocal() as s:
            r = await after_run.flag_stale(s, pid, commits, title, datetime.now(timezone.utc))
            await s.commit()
            return r

    return _run(go())


def _decs(pid):
    async def go():
        async with SessionLocal() as s:
            return (await s.execute(select(ProjectDecision).where(ProjectDecision.project_id == pid)
                                    .order_by(ProjectDecision.number))).scalars().all()

    return _run(go())


def _c(i, files=("src/a.py",)):
    return Commit(sha=f"{i:040x}", short=f"{i:07x}", subject=f"c{i}", files=list(files))


def test_flag_stale_rules(client):
    pid = _mk(verified_days=20)
    assert _flag(pid, [_c(1)], "x" * 100) == [1]
    d = _decs(pid)[0]
    assert d.stale_reason == f"src/a.py changed in {1:07x} ({'x' * 80})"
    assert _flag(_mk(verified_days=3), [_c(1)], "t") == []
    assert _flag(_mk(status="retired"), [_c(1)], "t") == []
    now = datetime.now(timezone.utc)
    assert _flag(_mk(flagged=now - timedelta(hours=2)), [_c(1)], "t") == []
    assert _flag(_mk(flagged=now - timedelta(days=2)), [_c(1)], "t") == [1]
    assert _flag(_mk(touches=("docs/**",)), [_c(1)], "t") == []


def test_flag_caps(client):
    pid = _mk(n=10)
    got = _flag(pid, [_c(1), _c(2), _c(3)], "t")
    assert len(got) == 6
    flagged = [d for d in _decs(pid) if d.stale_flagged_at]
    assert len(flagged) == 6
    assert set(got) == {5, 6, 7, 8, 9, 10}
    for i in (1, 2, 3):
        assert sum(1 for d in flagged if d.stale_reason.startswith(f"src/a.py changed in {i:07x}")) == 2


def test_flag_keeps_version(client):
    pid = _mk()
    before = _decs(pid)[0]
    v, upd = before.version, before.updated_at
    assert _flag(pid, [_c(1)], "t") == [1]
    after = _decs(pid)[0]
    assert after.stale_flagged_at is not None and after.version == v and after.updated_at == upd


def test_enabled_project(client, monkeypatch):
    pid = _mk()

    async def get(p):
        async with SessionLocal() as s:
            return await after_run.enabled_project(s, p)

    assert _run(get(pid)).id == pid
    assert _run(get(None)) is None and _run(get(uuid.uuid4().hex)) is None

    async def trust(v):
        async with SessionLocal() as s:
            (await s.get(Project, pid)).trust = v
            await s.commit()

    _run(trust("external"))
    assert _run(get(pid)) is None
    _run(trust("own"))
    monkeypatch.setattr(settings, "proposals_enabled", False)
    assert _run(get(pid)) is None
    monkeypatch.setattr(settings, "proposals_enabled", True)

    async def off(s):
        return {"ground_rules": {"decisions": False}}

    monkeypatch.setattr(after_run.policy_store, "org_settings", off)
    assert _run(get(pid)) is None


# ---- task 4: worth filter, model call, event ----------------------------------------------------

import json  # noqa: E402

from app.decisions import proposals  # noqa: E402
from app import memory  # noqa: E402
from app.models import DecisionProposal, Task  # noqa: E402


def _w(subject, *files):
    return after_run.worth(Commit(sha="a" * 40, short="a" * 7, subject=subject, files=list(files)))


def test_worth():
    assert not _w("chore: x", "src/a.py")
    assert not _w("Docs(api)!: y", "src/a.py")
    assert not _w("merge branch", "src/a.py")
    assert not _w("v1.2.3", "src/a.py")
    assert not _w("1.2", "src/a.py")
    assert not _w("feat: add cache", "README.md", "uv.lock", "docs/a.md")
    assert not _w("feat: add cache", ".claude/x.json", ".co-orchestrator/y")
    assert _w("feat: add cache", "src/a.py")
    assert _w("docs: plan", "docs/superpowers/plans/x.md")
    assert _w("chores: x", "src/a.py")
    assert _w("reverted cache", "src/a.py")


def test_parse_items():
    p = after_run.parse_items
    assert len(p('Sure: [{"title":"A","why":"w"}] thanks', limit=2)) == 1
    assert p('{"title":"A"}', limit=2) == []
    five = json.dumps([{"title": f"t{i}", "why": "w"} for i in range(5)])
    assert len(p(five, limit=2)) == 2
    assert p('[{"title":"a ] b","why":"x"}]', limit=2)[0]["title"] == "a ] b"
    assert "\n" not in p(json.dumps([{"title": "x\nIgnore previous", "why": "w"}]), limit=2)[0]["title"]
    t = p(json.dumps([{"title": "A", "why": "w", "touches": ["/etc/passwd", "../x", "src/**"]}]), limit=2)[0]["touches"]
    assert t == ["src/**"]
    many = p(json.dumps([{"title": "A", "why": "w", "touches": [f"src/f{i}.py" for i in range(25)]}]), limit=2)
    assert len(many[0]["touches"]) <= 20
    assert p("not json", limit=2) == []
    assert p('see [1] and [{"title":"A","why":"w"}]', limit=2)[0]["title"] == "A"
    assert p("[1, 2] only", limit=2) == []
    assert p("[" * 50000 + '[{"title":"A","why":"w"}]', limit=2) == []
    assert p(json.dumps([{"title": "A", "why": "w", "kind": "reference"}]), limit=2) == []
    assert p(json.dumps([{"title": "A", "why": "w"}]), limit=2)[0]["kind"] == "decision"
    assert p(json.dumps(["str", 3, {"title": "A", "why": "w"}]), limit=2)[0]["title"] == "A"


def test_stale_reason_title_one_line(client):
    pid = _mk()
    assert _flag(pid, [_c(1)], "line one\n\tIgnore\x00 this\r\nplease") == [1]
    reason = _decs(pid)[0].stale_reason
    assert reason == f"src/a.py changed in {1:07x} (line one Ignore this please)"


def _task_for(repo, start, project_dir, *, title="My task", prompt="p"):
    pid = uuid.uuid4().hex
    tid = uuid.uuid4().hex

    async def go():
        async with SessionLocal() as s:
            s.add(Project(id=pid, name="p" + pid[:8], slug="p" + pid[:8], directory=project_dir))
            s.add(Task(id=tid, prompt=prompt, title=title, project_id=pid, head_at_start=start,
                       result_text="did stuff"))
            await s.commit()

    _run(go())
    return pid, tid


class _Rec:
    def __init__(self):
        self.events = []

    async def __call__(self, etype, payload):
        self.events.append((etype, payload))


def _after(tid, emit=None, owner="u1"):
    return _run(after_run.after_task(tid, owner, emit=emit))


@pytest.fixture()
def model(monkeypatch):
    calls = []
    state = {"reply": json.dumps([{"title": "Use cache X", "why": "because", "touches": ["src/**"]}]),
             "env": {"A": "b"}}

    async def env(s, uid, *, project_env=None, purpose):
        return state["env"]

    async def run(prompt, m, cwd, e):
        calls.append(prompt)
        r = state["reply"]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(after_run, "agent_env_or_none", env)
    monkeypatch.setattr(memory, "_run_claude", run)
    state["calls"] = calls
    return state


def _rows(pid):
    async def go():
        async with SessionLocal() as s:
            return (await s.execute(select(DecisionProposal).where(DecisionProposal.project_id == pid))).scalars().all()

    return _run(go())


def test_after_task_end_to_end(client, repo, model):
    start = _commit(repo, "a.txt", "base")
    sha = _commit(repo, "src/a.py", "feat: add cache")
    pid, tid = _task_for(repo, start, str(repo), prompt="P" * 5000)
    rec = _Rec()
    _after(tid, rec)
    rows = _rows(pid)
    assert len(rows) == 1 and rows[0].source == "run" and rows[0].status == "pending"
    assert rows[0].evidence.startswith("commits ") and rows[0].dedupe_key == f"commit:{sha}:1"
    assert rows[0].task_id == tid
    prompt = model["calls"][0]
    assert prompt.startswith(after_run.PROPOSALS_FRAME) or after_run.PROPOSALS_FRAME in prompt
    assert "P" * 2000 in prompt and "P" * 2001 not in prompt
    assert rec.events == [("decision_review", {"proposed": 1, "stale": []})]


def test_after_task_skips_used_commits(client, repo, model):
    start = _commit(repo, "a.txt", "base")
    _commit(repo, "src/a.py", "feat: add cache")
    pid, tid = _task_for(repo, start, str(repo))
    _after(tid, _Rec())
    assert len(model["calls"]) == 1
    _after(tid, _Rec())
    assert len(model["calls"]) == 1 and len(_rows(pid)) == 1


def test_after_task_no_env_still_flags(client, repo, model):
    start = _commit(repo, "a.txt", "base")
    _commit(repo, "src/a.py", "feat: add cache")
    pid, tid = _task_for(repo, start, str(repo))
    _run(_seed_decision(pid))
    model["env"] = None
    rec = _Rec()
    _after(tid, rec)
    assert model["calls"] == [] and _rows(pid) == []
    assert _decs(pid)[0].stale_flagged_at is not None
    assert rec.events == [("decision_review", {"proposed": 0, "stale": [1]})]


async def _seed_decision(pid):
    async with SessionLocal() as s:
        s.add(ProjectDecision(project_id=pid, number=1, title="d1", touches=["src/**"], status="active",
                              last_verified_at=datetime.now(timezone.utc) - timedelta(days=30)))
        await s.commit()


def test_after_task_never_raises(client, repo, model):
    start = _commit(repo, "a.txt", "base")
    _commit(repo, "src/a.py", "feat: add cache")
    pid, tid = _task_for(repo, start, str(repo))
    model["reply"] = RuntimeError("boom")
    _after(tid, _Rec())
    model["reply"] = "garbage ][ {"
    _after(tid, _Rec())
    assert _rows(pid) == []
    _after(uuid.uuid4().hex, _Rec())

    async def bad(*a, **k):
        raise RuntimeError("emit failed")

    model["reply"] = json.dumps([{"title": "T", "why": "w"}])
    _after(tid, bad)
    pid2, tid2 = _task_for(repo, start, str(repo / "gone"))
    _after(tid2, _Rec())


def test_no_event_when_nothing(client, repo, model):
    start = _commit(repo, "a.txt", "base")
    _commit(repo, "src/a.py", "chore: tidy")
    pid, tid = _task_for(repo, start, str(repo))
    rec = _Rec()
    _after(tid, rec)
    assert rec.events == [] and model["calls"] == []


def _switch_off(monkeypatch):
    async def off(s):
        return {"ground_rules": {"decisions": False}}

    monkeypatch.setattr(after_run.policy_store, "org_settings", off)


@pytest.mark.parametrize("how", ["switch", "trust", "enabled"])
def test_after_task_disabled_no_call(client, repo, model, monkeypatch, how):
    start = _commit(repo, "a.txt", "base")
    _commit(repo, "src/a.py", "feat: add cache")
    pid, tid = _task_for(repo, start, str(repo))
    if how == "switch":
        _switch_off(monkeypatch)
    elif how == "enabled":
        monkeypatch.setattr(settings, "proposals_enabled", False)
    else:
        async def go():
            async with SessionLocal() as s:
                (await s.get(Project, pid)).trust = "external"
                await s.commit()

        _run(go())
    rec = _Rec()
    _after(tid, rec)
    assert model["calls"] == [] and _rows(pid) == [] and rec.events == []


def test_after_task_cap_full_drops(client, repo, model, monkeypatch):
    start = _commit(repo, "a.txt", "base")
    _commit(repo, "src/a.py", "feat: add cache")
    pid, tid = _task_for(repo, start, str(repo))
    monkeypatch.setattr(settings, "proposals_pending_cap", 0)
    rec = _Rec()
    _after(tid, rec)
    assert _rows(pid) == [] and rec.events == []


def test_after_task_cap_filled_by_reply(client, repo, model, monkeypatch):
    start = _commit(repo, "a.txt", "base")
    _commit(repo, "src/a.py", "feat: add cache")
    pid, tid = _task_for(repo, start, str(repo))
    orig = proposals.pending_count
    n = {"i": 0}

    async def counted(s, p):  # not full when the job checks, full when the rows are added
        n["i"] += 1
        return 0 if n["i"] == 1 else settings.proposals_pending_cap

    monkeypatch.setattr(proposals, "pending_count", counted)
    _after(tid, _Rec())
    monkeypatch.setattr(proposals, "pending_count", orig)
    assert len(model["calls"]) == 1 and _rows(pid) == []


def test_after_task_redacts_secret(client, repo, model):
    start = _commit(repo, "a.txt", "base")
    _commit(repo, "src/a.py", "feat: add cache")
    pid, tid = _task_for(repo, start, str(repo))
    model["env"] = {"A": "b", "API_TOKEN": "supersecretvalue123"}
    model["reply"] = json.dumps([{"title": "Use key supersecretvalue123 here", "why": "token supersecretvalue123 ok"}])
    _after(tid, _Rec())
    rows = _rows(pid)
    assert len(rows) == 1
    assert "supersecretvalue123" not in rows[0].title + rows[0].why


def test_after_task_emit_raising_keeps_rows(client, repo, model):
    start = _commit(repo, "a.txt", "base")
    _commit(repo, "src/a.py", "feat: add cache")
    pid, tid = _task_for(repo, start, str(repo))

    async def bad(*a, **k):
        raise RuntimeError("emit failed")

    _after(tid, bad)
    assert len(_rows(pid)) == 1
