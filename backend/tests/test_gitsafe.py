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
