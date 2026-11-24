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
