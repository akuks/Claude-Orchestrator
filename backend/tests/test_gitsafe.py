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
    gitsafe.check_args(("diff", "--name-only", SHA, "HEAD"))
    gitsafe.check_args(("diff", "--name-only", SHA[:7], "HEAD"))
    gitsafe.check_args(("rev-list", "--max-parents=0", "HEAD"))
    gitsafe.check_args(("merge-base", "--is-ancestor", SHA, SHA[:12]))


@pytest.mark.parametrize("args", [
    ("diff", "--name-only", "HEAD"),
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
