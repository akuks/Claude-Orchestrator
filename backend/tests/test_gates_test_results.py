import time
from pathlib import Path

import pytest

from app.gates import test_results as tr
from app.gates.test_results import TestResult, parse, progress, progress_line

FIX = Path(__file__).parent / "fixtures" / "gate_results"


def fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "name,expected",
    [
        ("node_test.txt", TestResult("node-test", 1, 3, 1, 5, ("divides by zero", "keeps order"))),
        ("tap.txt", TestResult("tap", 2, 2, 0, 4, ("divides", "orders"))),
        ("jest.txt", TestResult("jest", 10, 2, 1, 13, (
            "suite › divides", "suite › orders", "src/broken.test.js (test suite failed to run)"))),
        ("vitest.txt", TestResult("vitest", 10, 2, 1, 13, (
            "src/a.test.ts > suite > divides", "src/a.test.ts > suite > orders"))),
        ("pytest.txt", TestResult("pytest", 10, 2, 2, 14, (
            "tests/test_a.py::test_div", "tests/test_b.py::test_x", "tests/test_c.py::test_plain"))),
        ("playwright.txt", TestResult("playwright", 52, 5, 3, 61, (
            "[chromium] › tests/login.spec.ts:12:5 › login › shows an error",
            "[chromium] › tests/login.spec.ts:20:5 › login › locks out"))),
        ("mocha.txt", TestResult("mocha", 8, 2, 0, 10, ("suite divides", "other thing"))),
        ("go.txt", TestResult("go", 1, 2, 0, 3, ("TestA", "TestA/sub"))),
    ],
)
def test_runner_summaries(name, expected):
    assert parse(fx(name)) == expected


def test_cut_off_pytest_counts_per_test_lines():
    r = parse(fx("pytest_cutoff.txt"))
    assert (r.runner, r.passed, r.failed, r.skipped) == ("pytest", 6, 2, 1)
    assert progress(fx("pytest_cutoff.txt")) == (9, 63, 2)


def test_unknown_runner_all_none():
    assert parse(fx("unknown.txt")) == TestResult(None, None, None, None, None, ())
    assert parse("") == TestResult(None, None, None, None, None, ())
    assert progress(fx("unknown.txt")) is None


def test_name_caps():
    text = "\n".join(f"FAILED tests/t.py::t{i}" for i in range(60))
    text += "\n=== 60 failed in 1.0s ===\n"
    r = parse(text)
    assert len(r.failing) == tr.MAX_NAMES == 50
    assert r.failing[-1] == "tests/t.py::t49"
    long = "FAILED tests/t.py::" + "x" * 300 + "\n=== 1 failed in 1.0s ===\n"
    (name,) = parse(long).failing
    assert len(name) == tr.MAX_NAME == 160


def test_only_last_max_input_parsed(monkeypatch):
    monkeypatch.setattr(tr, "MAX_INPUT", 200)
    head = "=== 9 failed in 1.0s ===\n"
    tail = "=== 2 passed in 1.0s ===\n"
    text = head + "\n" * 300 + tail
    r = parse(text)
    assert (r.passed, r.failed) == (2, 0)


def test_constants():
    assert (tr.MAX_INPUT, tr.MAX_LINE) == (20 * 1024 * 1024, 4000)


@pytest.mark.parametrize("text", ["=" * 5_000_000, ("a" * 100_000 + "\n") * 50,
                                  " " * 3_000_000 + "x", ("1 " * 2000 + "\n") * 500],
                         ids=["equals", "long-lines", "spaces", "digits"])
def test_hostile_input_is_fast(text):
    t0 = time.monotonic()
    parse(text)
    progress(text)
    assert time.monotonic() - t0 < 1.0


def test_progress_partial_runs():
    assert progress(fx("vitest_partial.txt")) == (4, None, 1)
    assert progress(fx("pytest_cutoff.txt")) == (9, 63, 2)


def test_progress_line():
    assert progress_line((31, 63, 0)) == "31 of 63 tests done, 0 failed"
    assert progress_line((31, None, 0)) == "31 tests done, 0 failed"


def test_go_package_lines_only_gives_unknown_counts():
    r = parse("ok  \texample.com/a\t0.5s\nok  \texample.com/b\t(cached)\n")
    assert r == TestResult("go", None, None, None, None, ())
    assert progress("ok  \texample.com/a\t0.5s\n") is None


def test_go_verbose_still_counts():
    r = parse("--- PASS: TestA (0.00s)\nPASS\nok  \texample.com/a\t0.5s\n")
    assert (r.runner, r.passed, r.total) == ("go", 1, 1)


def test_many_short_lines_are_fast():
    text = "ab\n" * 7_000_000  # about 21 MB
    t0 = time.monotonic()
    parse(text)
    assert time.monotonic() - t0 < 2.0


def test_progress_pytest_q_dots():
    assert progress("collected 5 items\n\n..F.s                 [ 80%]\n") == (5, 5, 1)
    assert progress("..F.s [ 80%]\n.. [100%]\n") == (7, None, 1)


def test_progress_pytest_verbose():
    text = (
        "collected 5 items\n"
        "tests/test_a.py::test_one PASSED                [ 20%]\n"
        "tests/test_a.py::test_two[a b] FAILED           [ 40%]\n"
        "tests/test_a.py::test_three SKIPPED (why)       [ 60%]\n"
    )
    assert progress(text) == (3, 5, 1)
    text = text.replace("SKIPPED (why)", "SKIPPED")
    assert progress(text) == (3, 5, 1)
    assert parse(text).failing == ("tests/test_a.py::test_two[a b]",)
