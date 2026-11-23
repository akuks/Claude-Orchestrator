"""Glob matcher: one case table against the server module and the co-guard copy."""
import re
import time
from pathlib import Path

import pytest

from app.decisions import globmatch
from app.guard import co_hook

IMPLS = [pytest.param(globmatch, id="server"), pytest.param(co_hook, id="co_hook")]

# (pattern, path, ci, specificity)
CASES = [
    ("src/**/*.py", "src/a.py", False, 2),
    ("src/**/*.py", "src/x/y/a.py", False, 2),
    ("src/**/*.py", "src/a.pyc", False, None),
    ("*.md", "README.md", False, 2),
    ("*.md", "docs/a.md", False, None),
    ("docs/*", "docs/a", False, 2),
    ("docs/*", "docs/a/b", False, None),
    ("src/auth", "src/auth", False, 0),
    ("src/auth", "src/auth/x.py", False, 1),
    ("src/auth", "src/authz.py", False, None),
    ("src/auth/", "src/auth/x.py", False, 1),
    ("a?c.txt", "abc.txt", False, 2),
    ("a?c.txt", "a/c.txt", False, None),
    ("a?c.txt", "ac.txt", False, None),
    ("**", "anything/at/all.py", False, 2),
    ("**", "x", False, 2),
    ("**/x.py", "x.py", False, 2),
    ("**/x.py", "a/b/x.py", False, 2),
    ("a/**", "a/b/c", False, 2),
    ("*a*b*", "xaxxbx", False, 2),
    ("*a*b*", "xbxa", False, None),
    ("Src/Auth", "src/auth/x.py", True, 1),
    ("Src/Auth", "src/auth/x.py", False, None),
    ("SRC/*.PY", "src/a.py", True, 2),
    ("SRC/*.PY", "src/a.py", False, None),
    ("", "a", False, None),
    ("x" * 301, "x" * 301, False, None),
    ("a" * 300, "a" * 300, False, 0),
    ("*" * 31, "abc", False, None),
    ("*" * 30, "abc", False, 2),
    ("?" * 29 + "*", "a" * 31, False, 2),
]


@pytest.mark.parametrize("impl", IMPLS)
@pytest.mark.parametrize("pattern,path,ci,want", CASES)
def test_table(impl, pattern, path, ci, want):
    assert impl.specificity(pattern, path, ci=ci) == want
    assert impl.matches(pattern, path, ci=ci) is (want is not None)


@pytest.mark.parametrize("impl", IMPLS)
def test_default_case_follows_platform(impl):
    assert impl.matches("A/B", "a/b") is impl.CASE_INSENSITIVE


@pytest.mark.parametrize("impl", IMPLS)
def test_specificity_ordering(impl):
    path = "src/auth/x.py"
    s = [impl.specificity(p, path, ci=False) for p in ("src/auth/x.py", "src/auth", "src/**/*.py")]
    assert s == [0, 1, 2]


def _fast(fn, bound=0.05):
    w, c = time.perf_counter(), time.process_time()
    fn()
    return min(time.perf_counter() - w, time.process_time() - c) < bound


@pytest.mark.parametrize("impl", IMPLS)
def test_pathological_is_fast(impl):
    deep = "/".join(f"d{i}" for i in range(60)) + "/x.py"
    assert _fast(lambda: impl.matches("**/" * 40 + "x.py", deep, ci=False))
    assert impl.matches("**/" * 40 + "x.py", deep, ci=False) is False  # 40+ wildcards: invalid, matches nothing
    assert _fast(lambda: impl.matches("**/" * 15 + "x.py", deep, ci=False))
    assert impl.matches("**/" * 15 + "x.py", deep, ci=False) is True
    assert _fast(lambda: impl.matches("*a" * 15, "a" * 5000, ci=False))
    assert not impl.matches("a" * 5000, "a" * 5000, ci=False)
    pats = [f"src/mod{i}/**/*.py" if i % 2 else f"lib/{i}/*/f?.txt" for i in range(20)]
    paths = [f"src/mod{i}/a/b/c{i}.py" for i in range(50)]

    def many():
        for _ in range(200):
            for p in pats:
                for q in paths:
                    impl.matches(p, q, ci=False)

    assert _fast(many, 2.0)  # 200k calls; per call well under the 50 ms budget
    assert _fast(lambda: [impl.matches(p, q, ci=False) for p in pats for q in paths])


def _block(path):
    text = Path(path).read_text()
    m = re.search(r"^GLOB_PATTERN_MAX = .*?^def matches\(.*?\n    return [^\n]*\n", text, re.S | re.M)
    assert m
    return m.group(0)


def test_copies_identical():
    assert _block(globmatch.__file__) == _block(co_hook.__file__)
