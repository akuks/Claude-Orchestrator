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
