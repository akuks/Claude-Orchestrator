"""Phase 4a Task 5: decision health — read-only git checks, gitsafe additions."""

import asyncio
import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app import gitsafe
from conftest import login_as
from app.decisions.health import health


def _git(d, *a, env=None):
    e = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
         "GIT_COMMITTER_EMAIL": "t@t", **(env or {})}
    subprocess.run(["git", *a], cwd=d, check=True, capture_output=True, env=e)


def _commit(d, name, subject, when):
    p = d / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(subject)
    _git(d, "add", "-A")
    iso = when.isoformat()
    _git(d, "commit", "-m", subject, env={"GIT_AUTHOR_DATE": iso, "GIT_COMMITTER_DATE": iso})


@pytest.fixture()
def repo(tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    _git(d, "init", "-q")
    return d


def _dec(n, touches=(), kind="decision", see=None, verified=None):
    return SimpleNamespace(number=n, touches=list(touches), kind=kind, see=see, status="active",
                           last_verified_at=verified or datetime.now(timezone.utc))


def _run(project, decs):
    return asyncio.run(health(project, decs))


def test_unmatched_and_matched(repo):
    now = datetime.now(timezone.utc)
    _commit(repo, "src/a.py", "init", now - timedelta(days=5))
    (repo / "new.txt").write_text("x")  # untracked, not ignored
    r = _run(SimpleNamespace(directory=str(repo)), [_dec(1, ["src/**", "gone/**"]), _dec(2, ["new.txt"])])
    assert r["available"] and r["git_error"] is None and r["ok"] == 1
    assert r["items"] == [{"number": 1, "display_id": "D-001", "touches_unmatched": ["gone/**"],
                           "see_missing": False, "changed_since_verified": None, "unverified_days": None}]


def test_see_missing_and_outside(repo, tmp_path):
    now = datetime.now(timezone.utc)
    _commit(repo, "docs/x.md", "init", now - timedelta(days=5))
    (tmp_path / "secret.txt").write_text("s")
    decs = [_dec(1, kind="reference", see="docs/x.md"), _dec(2, kind="reference", see="docs/nope.md"),
            _dec(3, kind="reference", see="../secret.txt")]
    r = _run(SimpleNamespace(directory=str(repo)), decs)
    assert [i["number"] for i in r["items"]] == [2, 3] and all(i["see_missing"] for i in r["items"])
    assert r["ok"] == 1  # D-001 has no touches and is fresh


def test_changed_since_verified(repo):
    now = datetime.now(timezone.utc)
    _commit(repo, "src/a.py", "before", now - timedelta(days=10))
    _commit(repo, "src/a.py", "after one", now - timedelta(days=2))
    _commit(repo, "other.py", "unrelated", now - timedelta(days=1))
    for i in range(4):
        _commit(repo, f"src/b{i}.py", f"after b{i}", now - timedelta(hours=10 - i))
    r = _run(SimpleNamespace(directory=str(repo)), [_dec(1, ["src/**"], verified=now - timedelta(days=5))])
    ch = r["items"][0]["changed_since_verified"]
    assert ch["count"] == 5 and len(ch["subjects"]) == 3 and "before" not in ch["subjects"]
    r = _run(SimpleNamespace(directory=str(repo)), [_dec(1, ["src/**"], verified=now)])
    assert r["items"] == [] and r["ok"] == 1


def test_unverified_days(repo):
    now = datetime.now(timezone.utc)
    _commit(repo, "a", "init", now - timedelta(days=40))
    r = _run(SimpleNamespace(directory=str(repo)), [_dec(1, verified=now - timedelta(days=31, hours=1)),
                                                    _dec(2, verified=now - timedelta(days=29))])
    assert [(i["number"], i["unverified_days"]) for i in r["items"]] == [(1, 31)] and r["ok"] == 1


def test_git_errors_and_unavailable(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    for directory in (str(plain), str(tmp_path / "missing")):
        r = _run(SimpleNamespace(directory=directory), [_dec(1, ["a"])])
        assert r["available"] and r["git_error"] and r["items"] == []
    assert _run(SimpleNamespace(directory=""), [_dec(1)]) == {"available": False}


def test_route(client, repo):
    now = datetime.now(timezone.utc)
    _commit(repo, "src/a.py", "init", now - timedelta(days=5))
    pid = client.post("/projects", json={"name": f"h-{uuid.uuid4().hex[:8]}", "directory": str(repo)}).json()["id"]
    base = f"/projects/{pid}/decisions"
    viewer = login_as(f"hv-{uuid.uuid4().hex[:8]}@test.local")
    client.put(f"/projects/{pid}/members", json=[
        {"subject_type": "user", "subject_id": viewer.user_id, "role": "contributor"}])
    assert client.post(base, json={"title": "t", "touches": ["nope/**"]}).status_code == 201
    r = viewer.get(f"{base}/health")
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["touches_unmatched"] == ["nope/**"]
    assert login_as(f"ho-{uuid.uuid4().hex[:8]}@test.local").get(f"{base}/health").status_code in (403, 404)


def test_gitsafe_new_forms():
    gitsafe.check_args(("ls-files", "-co", "--exclude-standard", "-z"))
    gitsafe.check_args(("ls-files", "-z", "--exclude-standard", "-co"))
    gitsafe.check_args(("log", "--name-only", "--format=%x1e%H%x1f%cI%x1f%s", "--since=2026-01-01T00:00:00+00:00"))
    for args in [("ls-files",), ("ls-files", "-co", "--exclude-standard"), ("ls-files", "-co", "--exclude-standard", "-z", "-s"),
                 ("ls-files", "-o", "-z"), ("ls-files", "--others", "-z", "--exclude-standard", "-c"),
                 ("log", "-p"), ("log", "--name-only", "-p"), ("log", "--output=/tmp/x")]:
        with pytest.raises(ValueError):
            gitsafe.check_args(args)


def test_subjects_redacted_cleaned_and_cut(repo):
    now = datetime.now(timezone.utc)
    _commit(repo, "src/a.py", "init", now - timedelta(days=20))
    tok = "ghp_" + "a1B2c3D4e5" * 4
    _commit(repo, "src/a.py", f"fix {tok} zero​width " + "x" * 200, now - timedelta(days=1))
    r = _run(SimpleNamespace(directory=str(repo)), [_dec(1, ["src/**"], verified=now - timedelta(days=5))])
    subj = r["items"][0]["changed_since_verified"]["subjects"][0]
    assert tok not in subj and "​" not in subj and len(subj) <= 120


def test_git_error_still_reports_see_missing(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    r = _run(SimpleNamespace(directory=str(d)), [_dec(1, kind="reference", see="nope.md"), _dec(2, ["a"])])
    assert r["git_error"] and r["ok"] == 1
    assert [(i["number"], i["see_missing"]) for i in r["items"]] == [(1, True)]


def test_too_many_files(monkeypatch, tmp_path):
    from app.decisions import health as h
    monkeypatch.setattr(h, "MAX_FILES", 10)
    monkeypatch.setattr(gitsafe, "run_git", lambda *a, **k: gitsafe.GitResult(
        True, "\0".join(f"f{i}" for i in range(11)), False))
    r = _run(SimpleNamespace(directory=str(tmp_path)), [_dec(1, ["a"])])
    assert r["git_error"] == "repository too large for the health check"


def test_large_repo_is_bounded(monkeypatch, tmp_path):
    import time
    files = [f"pkg{i % 200}/mod{i}/file{i}.py" for i in range(20000)]
    now = datetime.now(timezone.utc)
    log = "".join(f"\x1e{i:040x}\x1f{now.isoformat()}\x1f s{i}\n\n" + "\n".join(files[i * 20:i * 20 + 20]) + "\n"
                  for i in range(1000))

    def fake(root, *a, **k):
        return gitsafe.GitResult(True, "\0".join(files) if a[0] == "ls-files" else log, False)

    monkeypatch.setattr(gitsafe, "run_git", fake)
    decs = [_dec(n, [f"pkg{(n + j) % 200}/**/*.py" if j % 2 else f"nomatch{n}_{j}/**" for j in range(20)],
                 verified=now - timedelta(days=1)) for n in range(200)]
    t = time.process_time()
    r = _run(SimpleNamespace(directory=str(tmp_path)), decs)
    assert time.process_time() - t < 3 and r["git_error"] is None and len(r["items"]) == 200


def test_repo_without_commits(repo):
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x")  # untracked: ls-files -co still lists it
    old = datetime.now(timezone.utc) - timedelta(days=90)
    r = _run(SimpleNamespace(directory=str(repo)), [_dec(1, ["src/**"], verified=old), _dec(2, ["nope/**"])])
    assert r["git_error"] is None and r["ok"] == 1
    assert r["items"] == [{"number": 2, "display_id": "D-002", "touches_unmatched": ["nope/**"],
                           "see_missing": False, "changed_since_verified": None, "unverified_days": None}]


def test_route_runs_git_after_the_session_is_closed(client, repo, monkeypatch):
    from sqlalchemy import inspect

    from app.routers import decisions as router_mod

    seen = {}

    async def fake_health(project, rows):
        seen["detached"] = [inspect(x).detached for x in [project, *rows]]
        return {"available": True, "git_error": None, "ok": len(rows), "items": []}

    monkeypatch.setattr(router_mod, "decisions_health", fake_health)
    pid = client.post("/projects", json={"name": f"h-{uuid.uuid4().hex[:8]}", "directory": str(repo)}).json()["id"]
    assert client.post(f"/projects/{pid}/decisions", json={"title": "t", "touches": ["a"]}).status_code == 201
    r = client.get(f"/projects/{pid}/decisions/health")
    assert r.status_code == 200 and r.json()["ok"] == 1
    assert seen["detached"] == [True, True]
