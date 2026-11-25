"""Phase 7b Task 1: {{prev}} for steps with an explicit `after`."""

from app.workflow_runner import substitute

OUT = {0: "zero", 1: "one", 2: "two"}
NAMED = {0: {"a": "A0"}, 1: {"a": "A1", "b": "B1"}}
NAMES = {0: "First", 1: "Second", 2: "Third"}


def sub(p, after, **kw):
    return substitute(p, "IN", OUT, NAMED, after=after, stage_names=NAMES, **kw)


def test_no_after_is_unchanged():
    assert substitute("{{prev}}|{{prev.a}}", "IN", OUT, NAMED) == "two|"
    assert substitute("{{prev.b}}", "IN", OUT, NAMED) == ""
    assert substitute("{{prev}}", "IN", {0: "x"}, None) == "x"
    assert substitute("go", "IN", {}) == "go\n\nIN"


def test_single_after_plain():
    assert sub("{{prev}}", [0]) == "zero"
    assert sub("{{prev.a}}", [1]) == "A1"


def test_several_after_headed_in_step_order():
    assert sub("{{prev}}", [1, 0]) == "From First:\nzero\n\nFrom Second:\none"


def test_prev_name_first_that_has_it():
    assert sub("{{prev.a}}", [0, 1]) == "A0"
    assert sub("{{prev.b}}", [0, 1]) == "B1"
    assert sub("{{prev.zz}}", [0, 1]) == ""


def test_empty_after_empty_prev_and_stageN_unchanged():
    assert sub("[{{prev}}][{{prev.a}}]{{stage3}}", []) == "[][]two"


def test_single_pass():
    out = substitute("{{prev}}", "IN", {0: "{{input}}"}, None, after=[0], stage_names={0: "F"})
    assert out == "{{input}}"
