"""Phase 4c task 3: brief validation (every 422 text) and the inheritance bounds."""

import pytest

from app.briefs.validate import child_scope, clean_brief

OK = {"goal": "Add the export", "can_change": ["src/**"], "cannot_change": ["migrations"],
      "done": "tests pass", "stop": "when blocked"}


def bad(**kw):
    out = dict(OK)
    for k, v in kw.items():
        if v is KeyError:
            out.pop(k)
        else:
            out[k] = v
    return out


CASES = [
    ("x", "the brief must be an object"),
    ([], "the brief must be an object"),
    ({}, "the brief needs goal"),
    (bad(goal="​"), "the brief needs goal"),
    (bad(goal=KeyError), "the brief needs goal"),
    (bad(can_change=KeyError), "the brief needs can_change"),
    (bad(cannot_change=KeyError), "the brief needs cannot_change"),
    (bad(done=KeyError), "the brief needs done"),
    (bad(stop=KeyError), "the brief needs stop"),
    (bad(colour=1), "unknown brief field: colour"),
    (bad(stop="never"), 'stop must say when to stop, not "never"'),
    (bad(stop="Never."), 'stop must say when to stop, not "never"'),
    (bad(stop=" NEVER! "), 'stop must say when to stop, not "never"'),
    (bad(retries=True), "retries must be a whole number from 0 to 10"),
    (bad(retries="3"), "retries must be a whole number from 0 to 10"),
    (bad(retries=-1), "retries must be a whole number from 0 to 10"),
    (bad(retries=11), "retries must be a whole number from 0 to 10"),
    (bad(retries=2.0), "retries must be a whole number from 0 to 10"),
    (bad(can_change="src/**"), "can_change must be a list of paths"),
    (bad(can_change=["/etc"]),
     "a can_change entry must be a relative path without a leading / or ~ or a backslash: /etc"),
    (bad(cannot_change=["../x"]), "a cannot_change entry must not contain a '..' segment: ../x"),
    (bad(can_change=["a" + "*" * 31]), "a can_change entry may have at most 30 wildcards"),
    (bad(cannot_change=["?" * 31]), "a cannot_change entry may have at most 30 wildcards"),
    (bad(can_change=[f"d{i}" for i in range(21)]), "can_change may have at most 20 entries"),
    (bad(cannot_change=[f"d{i}" for i in range(21)]), "cannot_change may have at most 20 entries"),
    (bad(outputs=[f"o{i}" for i in range(11)]), "outputs may have at most 10 names"),
    (bad(goal="x" * 2001), "goal may be at most 2000 characters"),
    (bad(done="x" * 1001), "done may be at most 1000 characters"),
    (bad(setup="x" * 1001), "setup may be at most 1000 characters"),
    (bad(log="x" * 501), "log may be at most 500 characters"),
    (bad(tie_break="x" * 501), "tie_break may be at most 500 characters"),
]


@pytest.mark.parametrize("raw,detail", CASES)
def test_bad_brief(raw, detail):
    with pytest.raises(ValueError) as e:
        clean_brief(raw)
    assert str(e.value) == detail


def test_none_is_no_brief():
    assert clean_brief(None) is None


def test_valid_brief_is_normalised():
    token = "ghp_" + "a" * 36
    b = clean_brief(bad(goal=f"use {token} to push", can_change=["./src//a/**", "src/a/**", "docs"],
                        outputs=["report", "report", "a  b"], retries=3))
    assert b["can_change"] == ["src/a/**", "docs"]
    assert token not in b["goal"]
    assert b["outputs"] == ["report", "a b"] and b["retries"] == 3
    assert b["setup"] is None and b["log"] is None and b["tie_break"] is None
    assert set(b) == {"goal", "can_change", "cannot_change", "done", "stop", "setup", "log", "retries",
                      "tie_break", "outputs"}
    assert clean_brief(b) == b  # idempotent


def test_empty_globs_allowed():
    b = clean_brief(bad(can_change=[], cannot_change=[]))
    assert b["can_change"] == [] and b["cannot_change"] == []


def _b(can, cannot):
    return clean_brief(bad(can_change=can, cannot_change=cannot))


def test_child_scope():
    own = _b(["src/a/**"], [])
    parent = _b(["src/**"], ["migrations"])
    assert child_scope(None, None, "P", own) == (own, [])
    assert child_scope(None, None, "P", None) == (None, [])
    assert child_scope(parent, [], "P", None) == (parent, [])
    assert child_scope(parent, [{"task_id": "G", "can_change": ["x"], "cannot_change": []}], "P", None) == (
        parent, [{"task_id": "G", "can_change": ["x"], "cannot_change": []}])
    brief, bounds = child_scope(parent, None, "P", own)
    assert brief == own
    assert bounds == [{"task_id": "P", "can_change": ["src/**"], "cannot_change": ["migrations"]}]
    # the same scope again is not repeated
    _, again = child_scope(parent, bounds, "Q", own)
    assert again == bounds
    # a fifth bound
    four = [{"task_id": f"T{i}", "can_change": [f"d{i}"], "cannot_change": []} for i in range(4)]
    with pytest.raises(ValueError) as e:
        child_scope(parent, four, "P", own)
    assert str(e.value) == "briefs nest at most 5 deep"
