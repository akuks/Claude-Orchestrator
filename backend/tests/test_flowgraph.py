"""Phase 7b Task 1: pure dependency helpers and validation sentences."""

from app import flowgraph as fg


def S(*afters):
    return [({"name": f"S{i+1}"} if a is None else {"name": f"S{i+1}", "after": a}) for i, a in enumerate(afters)]


def test_deps_default_is_step_before():
    st = S(None, None, None)
    assert fg.deps(st, 0) == [] and fg.deps(st, 1) == [0] and fg.deps(st, 2) == [1]


def test_deps_explicit_and_empty():
    st = S(None, [], [0, 1])
    assert fg.deps(st, 1) == [] and fg.deps(st, 2) == [0, 1]


def test_ready_lowest_first_excludes_started_and_done():
    st = S([], [], [0, 1], None)
    assert fg.ready(st, set(), set()) == [0, 1]
    assert fg.ready(st, {0}, {1}) == []
    assert fg.ready(st, {0, 1}, set()) == [2]
    assert fg.ready(st, {0, 1, 2}, set()) == [3]
    assert fg.ready(S(None, None), set(), set()) == [0]


def test_dependants_transitive_only_later():
    st = S([], [0], [0], [1, 2], [])
    assert fg.dependants(st, 0) == {1, 2, 3}
    assert fg.dependants(st, 1) == {3}
    assert fg.dependants(st, 4) == set()
    assert fg.dependants(S(None, None, None), 1) == {2}


def test_depth():
    assert fg.depth(S([], [], [0, 1], [2])) == [0, 0, 1, 2]
    assert fg.depth(S(None, None, None)) == [0, 1, 2]
    assert fg.depth([]) == []


def test_validate_ok():
    assert fg.validate(S([], [0], [0], [1, 2]), 2) == []
    assert fg.validate(S(None, None), None) == []


def test_validate_sentences_all_at_once():
    st = S(None, [0, 0], [1], [0], [0, 8, 4], [5])
    # 1-based: step 2 lists step 1 twice; step 5 waits for itself and step 9 (missing); step 6 waits for step 6
    msgs = fg.validate(st, 2)
    assert "Step 2 lists step 1 twice." in msgs
    msgs = fg.validate([{"name": "a", "after": [0]}, {"name": "b", "after": [3]}, {"name": "c", "after": [8]},
                        {"name": "d", "after": [3]}], 2)
    assert msgs == ["Step 1 waits for itself.", "Step 2 waits for step 4, which comes later.",
                    "Step 3 waits for step 9, which does not exist.", "Step 4 waits for itself."]


def test_validate_bad_values_and_max_parallel():
    assert fg.validate(S(None, ["x"]), 2) == ["Step 2 waits for something that is not a step number."]
    assert fg.validate(S(None, 1), 2) == ["Step 2: the steps it waits for must be a list."]
    assert fg.validate(S(None, [-1]), 2) == ["Step 2 waits for step 0, which does not exist."]
    for bad in (0, 5, "2", True, 1.5):
        assert fg.validate(S(None), bad) == ["Run at most 1 to 4 steps at once."]
    for ok in (1, 4, None):
        assert fg.validate(S(None), ok) == []


def test_max_parallel_helpers():
    assert fg.get_max_parallel([{"name": "a"}]) == 2 and fg.get_max_parallel([]) == 2
    st = [{"name": "a", "max_parallel": 3}]
    assert fg.get_max_parallel(st) == 3
