"""Phase 7d Task 2: how fresh a code map is (spec section 3), worked out with the safe git helper at task start -
real temporary repositories: current, ahead with the changed files, a rewritten history, a shallow clone, no git,
a diff of 1,000 files - and the policy keys co-guard reads."""

import os
import subprocess
import time

import pytest

from app import gitsafe
from app.codemap import freshness

ENV = {"PATH": os.environ.get("PATH", ""), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
       "HOME": os.devnull}

pytestmark = pytest.mark.skipif(not gitsafe.git_available(), reason="git is not installed")


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], env=ENV, capture_output=True, text=True, timeout=60,
                       check=True)
    return r.stdout.strip()


def commit(repo, files, msg="c"):
    for name, text in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    return r


def test_current(repo):
    sha = commit(repo, {"a.py": "1"})
    f = freshness.freshness(str(repo), sha)
    assert f == {"state": "current", "behind": 0, "changed": [], "more": 0, "head": sha}
    # a short sha that HEAD starts with is the same commit
    assert freshness.freshness(str(repo), sha[:7])["state"] == "current"


def test_current_with_uncommitted_and_untracked_files(repo):
    sha = commit(repo, {"a.py": "1"})
    (repo / "a.py").write_text("2")
    (repo / "new").mkdir()
    (repo / "new" / "b.py").write_text("x")
    f = freshness.freshness(str(repo), sha)
    assert f["state"] == "current" and f["behind"] == 0
    assert sorted(f["changed"]) == ["a.py", "new/b.py"] and f["more"] == 0


def test_ahead_lists_committed_then_uncommitted_files(repo):
    sha = commit(repo, {"a.py": "1", "b/c.py": "1"})
    commit(repo, {"a.py": "2"})
    head = commit(repo, {"b/c.py": "2", "d.py": "x"})
    (repo / "e.py").write_text("untracked")
    (repo / "a.py").write_text("dirty")  # committed and dirty: listed once
    f = freshness.freshness(str(repo), sha)
    assert f["state"] == "ahead" and f["behind"] == 2 and f["head"] == head
    assert f["changed"] == ["a.py", "b/c.py", "d.py", "e.py"] and f["more"] == 0


def test_a_thousand_files_are_capped_at_ten_and_quick(repo):
    sha = commit(repo, {"seed.txt": "x"})
    commit(repo, {f"src/f{i:04d}.py": str(i) for i in range(1000)})
    t0 = time.monotonic()
    f = freshness.freshness(str(repo), sha)
    elapsed = time.monotonic() - t0
    assert f["state"] == "ahead" and f["behind"] == 1
    assert len(f["changed"]) == 10 and f["more"] == 990
    assert f["changed"][0] == "src/f0000.py"
    assert elapsed < freshness.BUDGET + 0.5, elapsed


def test_rewritten_history_is_a_different_history(repo):
    commit(repo, {"a.py": "1"})
    old = commit(repo, {"a.py": "2"}, msg="old")
    git(repo, "commit", "-q", "--amend", "-m", "rewritten")  # old still exists, but is not an ancestor
    f = freshness.freshness(str(repo), old)
    assert f["state"] == "different" and f["behind"] is None and f["changed"] == [] and f["more"] == 0


def test_a_sha_this_repository_never_had_is_a_different_history(repo, tmp_path):
    commit(repo, {"a.py": "1"})
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q", "-b", "main")
    foreign = commit(other, {"z.py": "1"})
    assert freshness.freshness(str(repo), foreign)["state"] == "different"


def test_shallow_clone_cannot_be_checked(repo, tmp_path):
    first = commit(repo, {"a.py": "1"})
    commit(repo, {"a.py": "2"})
    commit(repo, {"a.py": "3"})
    clone = tmp_path / "shallow"
    subprocess.run(["git", "-c", "protocol.file.allow=always", "clone", "-q", "--depth", "1",
                    f"file://{repo}", str(clone)], env=ENV, check=True, capture_output=True, timeout=60)
    assert (clone / ".git" / "shallow").exists()
    f = freshness.freshness(str(clone), first)  # the indexed commit is past the shallow edge
    assert f["state"] == "unknown" and f["behind"] is None and f["changed"] == []


def test_no_git_not_a_repo_bad_sha_and_missing_folder(repo, tmp_path, monkeypatch):
    sha = commit(repo, {"a.py": "1"})
    plain = tmp_path / "plain"
    plain.mkdir()
    unknown = {"state": "unknown", "behind": None, "changed": [], "more": 0, "head": None}
    assert freshness.freshness(str(plain), sha) == unknown
    assert freshness.freshness(str(tmp_path / "gone"), sha) == unknown
    assert freshness.freshness(str(repo), None) == unknown
    assert freshness.freshness(str(repo), "HEAD") == unknown
    assert freshness.freshness(str(repo), "zz" * 20) == unknown
    assert freshness.freshness(None, sha) == unknown
    monkeypatch.setenv("PATH", "")  # no git executable at all
    assert freshness.freshness(str(repo), sha) == unknown


def test_without_files_no_diff_or_status_is_run(repo, monkeypatch):
    sha = commit(repo, {"a.py": "1"})
    commit(repo, {"a.py": "2"})
    seen = []
    real = gitsafe.run_git

    def spy(d, *args, timeout, strip=True):
        seen.append(args[0])
        return real(d, *args, timeout=timeout, strip=strip)

    monkeypatch.setattr(freshness.gitsafe, "run_git", spy)
    f = freshness.freshness(str(repo), sha, files=False)
    assert f["state"] == "ahead" and f["behind"] == 1 and f["changed"] == []
    assert "diff" not in seen and "status" not in seen


def test_the_total_budget_bounds_every_call(repo, monkeypatch):
    sha = commit(repo, {"a.py": "1"})
    commit(repo, {"a.py": "2"})
    timeouts = []
    real = gitsafe.run_git

    def slow(d, *args, timeout, strip=True):
        timeouts.append(timeout)
        time.sleep(0.2)
        return real(d, *args, timeout=timeout, strip=strip)

    monkeypatch.setattr(freshness.gitsafe, "run_git", slow)
    t0 = time.monotonic()
    f = freshness.freshness(str(repo), sha, budget=0.3)
    assert time.monotonic() - t0 < 1.0
    assert f["state"] == "unknown"  # the budget ran out before the answer was known
    assert all(t <= 0.3 for t in timeouts)


def test_a_timed_out_call_is_unknown(repo, monkeypatch):
    sha = commit(repo, {"a.py": "1"})
    commit(repo, {"a.py": "2"})
    monkeypatch.setattr(freshness.gitsafe, "run_git",
                        lambda d, *a, timeout, strip=True: gitsafe.GitResult(False, "", True, "timed out"))
    assert freshness.freshness(str(repo), sha)["state"] == "unknown"


def test_only_allow_listed_git_commands_are_used(repo):
    sha = commit(repo, {"a.py": "1"})
    commit(repo, {"a.py": "2"})
    calls = []
    real = gitsafe.run_git

    def spy(d, *args, timeout, strip=True):
        gitsafe.check_args(args)
        calls.append(args)
        return real(d, *args, timeout=timeout, strip=strip)

    import unittest.mock as um

    with um.patch.object(freshness.gitsafe, "run_git", spy):
        freshness.freshness(str(repo), sha)
        git(repo, "commit", "-q", "--amend", "-m", "x")
        freshness.freshness(str(repo), sha)
    assert {a[0] for a in calls} <= {"rev-parse", "merge-base", "rev-list", "diff", "status"}


def test_odd_names_are_raw_and_listed_once(repo):
    sha = commit(repo, {"a.py": "1"})
    commit(repo, {"we ird\nname.txt": "1"})
    (repo / "we ird\nname.txt").write_text("dirty")  # committed-changed and dirty: once, never C-quoted
    f = freshness.freshness(str(repo), sha)
    assert f["changed"] == ["we ird\nname.txt"]


def test_a_first_status_name_starting_with_a_space_keeps_it(repo):
    sha = commit(repo, {" lead.py": "1"})
    (repo / " lead.py").write_text("2")
    assert freshness.freshness(str(repo), sha)["changed"] == [" lead.py"]


def test_one_call_stays_within_what_is_left(repo, monkeypatch):
    sha = commit(repo, {"a.py": "1"})
    commit(repo, {"a.py": "2"})
    seen = []
    real = gitsafe.run_git

    def spy(d, *args, timeout, strip=True):
        seen.append(timeout)
        return real(d, *args, timeout=timeout, strip=strip)

    monkeypatch.setattr(freshness.gitsafe, "run_git", spy)
    freshness.freshness(str(repo), sha, budget=1.0)
    assert seen and all(t <= 0.5 for t in seen)  # run_git runs two processes, each with the timeout


def test_long_and_odd_names_are_cut(repo):
    sha = commit(repo, {"a.py": "1"})
    commit(repo, {("x" * 250) + ".py": "1"})
    f = freshness.freshness(str(repo), sha)
    assert all(len(n) <= freshness.NAME_MAX for n in f["changed"])
