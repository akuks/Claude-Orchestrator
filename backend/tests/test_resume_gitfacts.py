"""Resume git facts (Task 2): read-only facts, commit attribution, the gitsafe
allowlist additions, and the /projects/{id}/git-state route."""

import asyncio
import os
import subprocess
import uuid

import pytest
from fastapi.testclient import TestClient

from app import auth, gitsafe
from app.main import app
from app.resume import gitfacts
from conftest import login_as

ENV = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
       "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def g(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, check=True, capture_output=True, text=True).stdout.strip()


def commit(cwd, name, msg=None):
    (cwd / name).write_text(name)
    g(cwd, "add", name)
    return g(cwd, "commit", "-q", "-m", msg or name) or g(cwd, "rev-parse", "HEAD")


def repo(tmp_path, name="r"):
    d = tmp_path / name
    d.mkdir()
    g(d, "init", "-q", "-b", "main")
    return d


def head(d):
    return g(d, "rev-parse", "HEAD")


def test_facts_clean_dirty_unborn(tmp_path):
    d = repo(tmp_path)
    f = gitfacts.facts(d, gitfacts.Budget())
    assert f.is_repo and f.no_commits and f.head is None and f.branch == "main"
    commit(d, "a.txt")
    f = gitfacts.facts(d, gitfacts.Budget())
    assert (f.is_repo, f.no_commits, f.dirty, f.dirty_capped, f.error) == (True, False, 0, False, None)
    assert f.head == head(d)[:12] and f.branch == "main"
    for i in range(3):
        (d / f"u{i}.txt").write_text("x")
    (d / "a.txt").write_text("changed")
    assert gitfacts.facts(d, gitfacts.Budget()).dirty == 4
    plain = tmp_path / "plain"
    plain.mkdir()
    f = gitfacts.facts(plain, gitfacts.Budget())
    assert f.is_repo is False and f.dirty is None


def test_dirty_cap(tmp_path):
    d = repo(tmp_path)
    commit(d, "a.txt")
    for i in range(2005):
        (d / f"f{i}").write_text("")
    f = gitfacts.facts(d, gitfacts.Budget())
    assert f.dirty == 2000 and f.dirty_capped is True


def test_own_shas_parses_commit_output():
    texts = ["[main abc1234] fix", "[main (root-commit) 1a2b3c4] init", "[detached HEAD 9f8e7d6] wip"]
    assert gitfacts.own_shas(texts) == {"abc1234", "1a2b3c4", "9f8e7d6"}
    assert gitfacts.own_shas(["see [the docs] and [1] and [note abc1234 here]", "x [a b] 1234567"]) == set()
    assert gitfacts.own_shas(["intro\n[feat/x deadbeef] msg"]) == {"deadbeef"}


def test_outside_commits_attribution(tmp_path):
    d = repo(tmp_path)
    commit(d, "base.txt")
    start = head(d)
    g(d, "commit", "-q", "--allow-empty", "-m", "own")
    c1 = head(d)
    commit(d, "other.txt", "other process")
    c2 = head(d)
    own = {c1[:7]}
    r = gitfacts.outside_commits(d, since=start[:12], head=c2[:12], own=own, budget=gitfacts.Budget())
    assert [c.sha for c in r["commits"]] == [c2] and r["rewritten"] is False
    assert r["commits"][0].subject == "other process"
    # a commit made between steps is listed
    commit(d, "between.txt", "between")
    c3 = head(d)
    r = gitfacts.outside_commits(d, since=c2[:12], head=c3[:12], own=set(), budget=gitfacts.Budget())
    assert [c.sha for c in r["commits"]] == [c3]
    # nothing to compare
    assert gitfacts.outside_commits(d, since=None, head=c3[:12], own=set(), budget=gitfacts.Budget()) == {
        "commits": [], "rewritten": False}
    assert gitfacts.outside_commits(d, since=c3[:12], head=c3[:12], own=set(), budget=gitfacts.Budget())["commits"] == []
    # rewritten history
    g(d, "reset", "-q", "--hard", "HEAD~1")
    commit(d, "new.txt", "after reset")
    r = gitfacts.outside_commits(d, since=c3[:12], head=head(d)[:12], own=set(), budget=gitfacts.Budget())
    assert r["rewritten"] is True and r["commits"] == []


@pytest.mark.parametrize("args", [
    ("status", "--porcelain=v2"), ("status", "-uno"), ("status",),
    ("status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames", "--ignored"),
    ("log", "--all", "main"), ("log", "-p"), ("log", "..HEAD"), ("log", "--format=%G?"),
    ("merge-base", "a", "b"), ("merge-base", "--is-ancestor", "main", "HEAD"),
    ("merge-base", "--is-ancestor", "abcdef1"), ("symbolic-ref", "HEAD"), ("symbolic-ref", "-q", "HEAD"),
    ("rev-parse", "--short", "HEAD"), ("rev-list", "--all", "-"), ("log", "ABCDEF1"), ("log", "abc123"),
    ("log", "--output=/tmp/x", "HEAD"),
])
def test_allowlist_refuses(args):
    with pytest.raises(ValueError):
        gitsafe.check_args(args)


def test_allowlist_accepts_new_commands():
    sha, sha2 = "a" * 7, "b" * 40
    for args in [
        ("status", "-z", "--no-renames", "--untracked-files=all", "--porcelain=v1"),
        ("merge-base", "--is-ancestor", sha, sha2), ("symbolic-ref", "-q", "--short", "HEAD"),
        ("log", "--format=%H%x1f%s", "--max-count=50", f"{sha}..{sha2}"), ("rev-list", "--count", "HEAD"),
    ]:
        gitsafe.check_args(args)


def test_new_commands_cannot_fetch_or_run_hooks(tmp_path):
    d = repo(tmp_path)
    commit(d, "a.txt")
    marker = tmp_path / "ran"
    script = tmp_path / "evil.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\n")
    script.chmod(0o755)
    g(d, "config", "core.fsmonitor", str(script))
    g(d, "config", "core.sshCommand", str(script))
    g(d, "config", "core.hooksPath", str(tmp_path / "hooks"))
    g(d, "config", "remote.origin.url", f"ext::{script}")
    g(d, "config", "remote.origin.promisor", "true")
    g(d, "config", "extensions.partialClone", "origin")
    (tmp_path / "hooks").mkdir()
    for h in ("post-checkout", "reference-transaction", "post-index-change"):
        (tmp_path / "hooks" / h).write_text(f"#!/bin/sh\ntouch {marker}\n")
        (tmp_path / "hooks" / h).chmod(0o755)
    (d / "u.txt").write_text("x")
    f = gitfacts.facts(d, gitfacts.Budget())
    assert f.dirty == 1
    h = head(d)
    gitfacts.commits_between(d, h[:7], h, gitfacts.Budget())
    gitfacts.is_ancestor(d, h[:7], h, gitfacts.Budget())
    assert not marker.exists()
    # fetch/pull/checkout themselves are not on the allowlist
    for cmd in (("fetch",), ("fetch", "origin"), ("checkout", "-"), ("status", "--no-lazy-fetch")):
        with pytest.raises(ValueError):
            gitsafe.run_git(d, *cmd, timeout=5)


def test_timeout_is_unknown(tmp_path, monkeypatch):
    d = repo(tmp_path)
    commit(d, "a.txt")
    monkeypatch.setattr(gitsafe, "run_git", lambda *a, **k: gitsafe.GitResult(False, "", True, "timed out"))
    f = gitfacts.facts(d, gitfacts.Budget())
    assert f.dirty is None and f.error
    assert gitfacts.commits_between(d, "a" * 7, "b" * 7, gitfacts.Budget()) is None
    assert gitfacts.is_ancestor(d, "a" * 7, "b" * 7, gitfacts.Budget()) is None
    assert gitfacts.outside_commits(d, since="a" * 7, head="b" * 7, own=set(), budget=gitfacts.Budget()) == {
        "commits": [], "rewritten": False}
    spent = gitfacts.Budget(0)
    assert spent.left() == 0


def test_git_state_route(client, tmp_path):
    d = repo(tmp_path, "proj")
    commit(d, "a.txt")
    (d / "u.txt").write_text("x")
    r = client.post("/projects", json={"name": f"gs-{uuid.uuid4().hex[:8]}", "directory": str(d)})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    r = client.get(f"/projects/{pid}/git-state")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"is_repo", "no_commits", "branch", "head", "dirty", "dirty_capped", "error"}
    assert body["is_repo"] is True and body["dirty"] == 1 and body["branch"] == "main" and body["head"] == head(d)[:12]
    outsider = login_as(f"gs-{uuid.uuid4().hex[:8]}@test.local")
    assert outsider.get(f"/projects/{pid}/git-state").status_code == 404

    async def mint():
        from app.database import SessionLocal
        from app.models import Task
        tid = uuid.uuid4().hex
        async with SessionLocal() as s:
            s.add(Task(id=tid, title="t", prompt="p", root_id=tid, status="running", project_id=pid,
                       created_by=outsider.user_id))
            await s.commit()
            raw = await auth.mint_token(s, outsider.user_id, "agent-task", tid, 3600)
            await s.commit()
            return raw

    agent = TestClient(app)
    agent.headers["Authorization"] = f"Bearer {asyncio.run(mint())}"
    assert agent.get(f"/projects/{pid}/git-state").status_code == 403


def test_crafted_subject_makes_no_fake_commit(tmp_path):
    d = repo(tmp_path)
    commit(d, "base.txt")
    base = head(d)
    subject = "evil\rA\x1eB\u2028C\u2029D\x85E \u202eRTL\u200bZW"
    commit(d, "x.txt", subject)
    h = head(d)
    cs = gitfacts.commits_between(d, base[:7], h, gitfacts.Budget())
    assert [c.sha for c in cs] == [h]
    assert "\u202e" not in cs[0].subject and "\u200b" not in cs[0].subject
    assert cs[0].subject.startswith("evil")
    r = gitfacts.outside_commits(d, since=base[:12], head=h[:12], own=set(), budget=gitfacts.Budget())
    assert [c.sha for c in r["commits"]] == [h]


def test_spent_budget_is_unknown_without_running_git(tmp_path, monkeypatch):
    d = repo(tmp_path)
    commit(d, "a.txt")
    monkeypatch.setattr(gitsafe, "run_git", lambda *a, **k: pytest.fail("git ran"))
    f = gitfacts.facts(d, gitfacts.Budget(0))
    assert f.dirty is None and f.error
