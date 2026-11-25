import pytest

from app import gitsafe

DIFF = ("--no-ext-diff", "--no-textconv", "--binary", "HEAD")
OTHERS = ("--others", "--exclude-standard", "-z")


def test_new_forms_accepted_any_order():
    gitsafe.check_args(("diff", *DIFF))
    gitsafe.check_args(("diff", *reversed(DIFF)))
    gitsafe.check_args(("ls-files", *OTHERS))
    gitsafe.check_args(("ls-files", *reversed(OTHERS)))
    gitsafe.check_args(("ls-files", "-co", "--exclude-standard", "-z"))


@pytest.mark.parametrize("args", [
    ("diff", "HEAD"),
    ("diff", "--binary", "HEAD~1"),
    ("diff", *DIFF, "--output=x"),
    ("diff", *DIFF[:3]),
    ("ls-files", "--others"),
    ("ls-files", *OTHERS, "-s"),
])
def test_other_forms_rejected(args):
    with pytest.raises(ValueError):
        gitsafe.check_args(args)


SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.mark.parametrize("rev", [SHA, SHA[:7], "abcdef0"])
def test_commit_check_accepted(rev):
    gitsafe.check_args(("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"))


@pytest.mark.parametrize("args", [
    ("rev-parse", "--verify", "--quiet", f"{SHA[:6]}^{{commit}}"),  # too short
    ("rev-parse", "--verify", "--quiet", f"{SHA}0^{{commit}}"),  # 41 chars
    ("rev-parse", "--verify", "--quiet", f"{SHA.upper()}^{{commit}}"),
    ("rev-parse", "--verify", "--quiet", "HEAD^{commit}"),
    ("rev-parse", "--verify", "--quiet", f"{SHA}^{{tree}}"),
    ("rev-parse", "--verify", "--quiet", f"{SHA}^{{commit}}\n"),
    ("rev-parse", "--verify", "--quiet", f"{SHA}^{{commit}}x"),
    ("rev-parse", "--verify", "--quiet", f"{SHA}~1^{{commit}}"),
    ("rev-parse", "--quiet", "--verify", f"{SHA}^{{commit}}"),
    ("rev-parse", "--verify", f"{SHA}^{{commit}}"),
    ("rev-parse", "--verify", "--quiet", f"{SHA}^{{commit}}", "HEAD"),
    ("rev-parse", "--verify", "--quiet", "--end-of-options", f"{SHA}^{{commit}}"),
    ("rev-parse", "--verify", "--quiet", f"{SHA}^{{commit}}", f"{SHA}^{{commit}}"),
])
def test_commit_check_near_misses_rejected(args):
    with pytest.raises(ValueError):
        gitsafe.check_args(args)


# ---- phase 7d: the code map freshness reads and the root commit, each exact ----------------------


def test_freshness_forms_accepted():
    gitsafe.check_args(("diff", "--name-only", "-z", SHA, "HEAD"))
    gitsafe.check_args(("diff", "--name-only", "-z", SHA[:7], "HEAD"))
    gitsafe.check_args(("rev-list", "--max-parents=0", "HEAD"))
    gitsafe.check_args(("merge-base", "--is-ancestor", SHA, SHA[:12]))


@pytest.mark.parametrize("args", [
    ("diff", "--name-only", "HEAD"),
    ("diff", "--name-only", SHA, "HEAD"),  # only the -z form
    ("diff", "-z", "--name-only", SHA, "HEAD"),
    ("diff", "--name-only", "-z", SHA, "HEAD~1"),
    ("diff", "--name-only", "-z", "-z", SHA, "HEAD"),
    ("diff", "--no-ext-diff", "--name-only", "-z", SHA, "HEAD"),
    ("rev-list", "--max-parents=0", "HEAD", "HEAD"),
    ("diff", "--name-only", SHA),
    ("diff", "--name-only", "HEAD", SHA),
    ("diff", SHA, "HEAD", "--name-only"),
    ("diff", "--name-only", SHA, "HEAD", "--output=x"),
    ("diff", "--name-only", SHA, "HEAD", "--", "a.py"),
    ("diff", "--name-only", f"{SHA}..HEAD"),
    ("diff", "--name-only", SHA.upper(), "HEAD"),
    ("diff", "--name-only", SHA[:6], "HEAD"),
    ("diff", "--name-only", "main", "HEAD"),
    ("diff", "--name-only", SHA + "\n", "HEAD"),
    ("diff", "--name-status", SHA, "HEAD"),
    ("diff", "--name-only", SHA, SHA),
    ("diff", "--name-only", "--ext-diff", SHA, "HEAD"),
    ("rev-list", "--max-parents=0"),
    ("rev-list", "--max-parents=0", "--all"),
    ("rev-list", "--max-parents=0", SHA),
    ("rev-list", "--max-parents=1", "HEAD"),
    ("rev-list", "HEAD", "--max-parents=0"),
    ("log", "--max-parents=0", "HEAD"),
    ("rev-list", "--max-parents=0", "HEAD", "--format=%GS"),
    ("merge-base", SHA, SHA),
    ("merge-base", "--is-ancestor", SHA, "HEAD"),
    ("merge-base", "--is-ancestor", SHA),
    ("merge-base", "--octopus", SHA, SHA),
])
def test_freshness_near_misses_rejected(args):
    with pytest.raises(ValueError):
        gitsafe.check_args(args)


@pytest.mark.skipif(not gitsafe.git_available(), reason="git is not installed")
def test_name_only_diff_runs_nothing_from_a_hostile_repo(tmp_path):
    """The repo's own config and .gitattributes name programs (external diff, a diff driver and textconv, a filter
    driver, a pager, fsmonitor, hooks): none runs for the freshness reads, and the output keeps raw names."""
    import os
    import subprocess

    env = {"PATH": os.environ.get("PATH", ""), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "HOME": os.devnull}
    repo = tmp_path / "hostile"
    repo.mkdir()
    marker = tmp_path / "ran"
    evil = tmp_path / "evil.sh"
    evil.write_text(f"#!/bin/sh\necho ran >> {marker}\ncat\n")
    evil.chmod(0o755)

    def g(*args):
        return subprocess.run(["git", "-C", str(repo), *args], env=env, check=True, capture_output=True,
                              text=True, timeout=60).stdout.strip()

    g("init", "-q", "-b", "main")
    (repo / "a.txt").write_text("1")
    g("add", "-A")
    g("commit", "-q", "-m", "one")
    base = g("rev-parse", "HEAD")
    (repo / "a.txt").write_text("2")
    (repo / "we ird\nname.txt").write_text("x")
    g("add", "-A")
    g("commit", "-q", "-m", "two")
    for key in ("diff.external", "diff.evil.command", "diff.evil.textconv", "filter.evil.clean",
                "filter.evil.smudge", "filter.evil.process", "core.pager", "pager.diff", "pager.status",
                "core.fsmonitor", "core.hooksPath"):
        g("config", key, str(evil) if key != "core.hooksPath" else str(tmp_path))
    g("config", "filter.evil.required", "true")
    (tmp_path / "post-index-change").write_text(f"#!/bin/sh\necho ran >> {marker}\n")
    (tmp_path / "post-index-change").chmod(0o755)
    (repo / ".gitattributes").write_text("* diff=evil filter=evil\n")
    (repo / "a.txt").write_text("dirty")
    r = gitsafe.run_git(str(repo), "diff", "--name-only", "-z", base, "HEAD", timeout=10, strip=False)
    assert r.ok, r.err
    assert r.out.split("\0")[:2] == ["a.txt", "we ird\nname.txt"]
    assert gitsafe.run_git(str(repo), "rev-list", "--max-parents=0", "HEAD", timeout=10).out == base
    assert gitsafe.run_git(str(repo), "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames",
                           timeout=10).ok
    assert not marker.exists()


def test_strip_false_keeps_the_output():
    import shutil

    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    assert gitsafe.run_git(".", "--version", timeout=10, strip=False).out.endswith("\n")
    assert not gitsafe.run_git(".", "--version", timeout=10).out.endswith("\n")
