"""Phase 3b task 2: co-guard's PostToolUse bands (budget 80 / 95 %, fresh session) read from the
worker's usage.json, each once per step, and the `injected` chars every nudge spools."""

import json
import time

import pytest

from app.guard import co_hook
from test_co_hook import FAKE_HOME, FAKE_PROJECT, Run, _ctx, _post, bash, decision, plugin, stub  # noqa: F401


def _usage(run, **fields):
    data = {"context_tokens": 1000, "est_cost_usd": 0.1, "max_budget_usd": 5, "fresh_session_tokens": None,
            "ts": time.time()}
    data.update(fields)
    (run.tdir / "usage.json").write_text(json.dumps(data))


def _bands(run):
    return [r for r in run.spool() if "band" in r]


def _fresh(n):
    return co_hook.FRESH_LINE.replace("{n}", str(n))


def test_fixed_texts():
    assert co_hook.BUDGET_80_LINE == ("co-orchestrator: this step has used about 80 % of its budget. "
                                      "Save your notes now.")
    assert co_hook.BUDGET_95_LINE == (
        "co-orchestrator: this step has used about 95 % of its budget. Finish the small step you are on, save "
        "your notes, start nothing new and end your turn.")
    assert co_hook.FRESH_LINE == (
        "co-orchestrator: the conversation is now {n}k tokens. Finish the small step you are on, save notes "
        "with what is next, and end your turn; the thread continues in a fresh session with your notes.")


def test_each_band_once(plugin, tmp_path, stub):
    run = Run(plugin, tmp_path, stub.base)
    _usage(run, est_cost_usd=3.0)
    assert _post(run, 1) is None
    _usage(run, est_cost_usd=4.0)  # 80 %
    assert _ctx(_post(run, 2)) == co_hook.BUDGET_80_LINE
    assert _post(run, 3) is None
    _usage(run, est_cost_usd=4.5)
    assert _post(run, 4) is None  # still 80-95: never again
    _usage(run, est_cost_usd=4.75)  # 95 %
    assert _ctx(_post(run, 5)) == co_hook.BUDGET_95_LINE
    _usage(run, est_cost_usd=6.0)
    assert _post(run, 6) is None
    bands = _bands(run)
    assert [(b["band"], b["value"], b["injected"]) for b in bands] == [
        ("budget80", 4.0, len(co_hook.BUDGET_80_LINE)), ("budget95", 4.75, len(co_hook.BUDGET_95_LINE))]
    assert all(b["event"] == "PostToolUse" and isinstance(b["ts"], float) for b in bands)


def test_95_in_one_call_skips_the_80_text(plugin, tmp_path, stub):
    run = Run(plugin, tmp_path, stub.base)
    _usage(run, est_cost_usd=4.9)
    assert _ctx(_post(run, 1)) == co_hook.BUDGET_95_LINE
    assert [b["band"] for b in _bands(run)] == ["budget80", "budget95"]
    assert sum(b["injected"] for b in _bands(run)) == len(co_hook.BUDGET_95_LINE)
    assert _post(run, 2) is None


def test_fresh_once(plugin, tmp_path, stub):
    run = Run(plugin, tmp_path, stub.base)
    _usage(run, context_tokens=149999, fresh_session_tokens=150000)
    assert _post(run, 1) is None
    _usage(run, context_tokens=152345, fresh_session_tokens=150000)
    assert _ctx(_post(run, 2)) == _fresh(152)
    _usage(run, context_tokens=200000, fresh_session_tokens=150000)
    assert _post(run, 3) is None
    assert [(b["band"], b["value"], b["injected"]) for b in _bands(run)] == [
        ("fresh", 152345, len(_fresh(152)))]


def test_fresh_and_budget_in_one_call(plugin, tmp_path, stub):
    run = Run(plugin, tmp_path, stub.base)
    _usage(run, est_cost_usd=4.0, context_tokens=160000, fresh_session_tokens=150000)
    assert _ctx(_post(run, 1)) == co_hook.BUDGET_80_LINE + "\n\n" + _fresh(160)


@pytest.mark.parametrize("fields", [
    {"ts": time.time() - 121},                  # stale
    {"ts": "now"},                              # ts not a number
    {"ts": True},
    {"ts": None},
    {"est_cost_usd": "4.5"},                    # non-number cost
    {"est_cost_usd": True},
    {"est_cost_usd": None},
    {"max_budget_usd": 0},                      # no budget
    {"max_budget_usd": -1},
    {"max_budget_usd": None},
    {"max_budget_usd": True},
    {"max_budget_usd": "5"},
])
def test_budget_band_needs_fresh_numbers(plugin, tmp_path, stub, fields):
    run = Run(plugin, tmp_path, stub.base)
    _usage(run, **dict({"est_cost_usd": 4.9}, **fields))
    assert _post(run, 1) is None
    assert _bands(run) == []


@pytest.mark.parametrize("fields", [
    {"ts": time.time() - 121},
    {"fresh_session_tokens": None},             # off
    {"fresh_session_tokens": 150000.0},         # not an int
    {"fresh_session_tokens": True},
    {"fresh_session_tokens": "150000"},
    {"fresh_session_tokens": 0},
    {"context_tokens": None},
    {"context_tokens": True},
    {"context_tokens": 200000.5},
    {"context_tokens": "200000"},
])
def test_fresh_band_needs_fresh_ints(plugin, tmp_path, stub, fields):
    run = Run(plugin, tmp_path, stub.base)
    _usage(run, **dict({"context_tokens": 200000, "fresh_session_tokens": 150000}, **fields))
    assert _post(run, 1) is None
    assert _bands(run) == []


@pytest.mark.parametrize("content", [None, "", "{not json", "[1, 2]", '"x"', "NaN", '{"ts": NaN}'])
def test_missing_or_malformed_usage_file(plugin, tmp_path, stub, content):
    run = Run(plugin, tmp_path, stub.base)
    if content is not None:
        (run.tdir / "usage.json").write_text(content)
    assert _post(run, 1) is None
    assert _bands(run) == [] and run.spool()[-1].get("done") is True  # no guard_error either


def test_nan_cost_is_no_number(plugin, tmp_path, stub):
    run = Run(plugin, tmp_path, stub.base)
    (run.tdir / "usage.json").write_text(
        '{"context_tokens": 1, "est_cost_usd": NaN, "max_budget_usd": 5, "fresh_session_tokens": null, '
        f'"ts": {time.time()}}}')
    assert _post(run, 1) is None and _bands(run) == []


def test_usage_dir_instead_of_file(plugin, tmp_path, stub):
    run = Run(plugin, tmp_path, stub.base)
    (run.tdir / "usage.json").mkdir()
    assert _post(run, 1) is None and _bands(run) == []


def test_band_joins_notes_with_a_blank_line(plugin, tmp_path, stub):
    run = Run(plugin, tmp_path, stub.base, notes=True, notes_after=2, project_dir=FAKE_PROJECT, home=FAKE_HOME)
    _usage(run, est_cost_usd=4.0)
    assert _ctx(_post(run, 1)) == co_hook.BUDGET_80_LINE
    out = _post(run, 2, tool="Write", tool_input={"file_path": "/tmp/r.pdf", "content": "x"})
    assert _ctx(out) == co_hook.NOTES_LINE + "\n\n" + co_hook.TEMP_DOC_LINE.replace("{path}", "/tmp/r.pdf")
    run2 = Run(plugin, tmp_path / "b", stub.base, notes=True, notes_after=1)
    _usage(run2, est_cost_usd=4.0)
    assert _ctx(_post(run2, 1)) == co_hook.NOTES_LINE + "\n\n" + co_hook.BUDGET_80_LINE


def test_band_not_shown_when_it_cannot_be_spooled(plugin, tmp_path, stub):
    run = Run(plugin, tmp_path, stub.base)
    _usage(run, est_cost_usd=4.9)
    (run.tdir / "spool.jsonl").mkdir()
    assert _post(run, 1) is None


def test_nudges_spool_injected(plugin, tmp_path, stub):
    run = Run(plugin, tmp_path, stub.base, notes=True, notes_after=1, project_dir=FAKE_PROJECT, home=FAKE_HOME)
    _post(run, 1, tool="Write", tool_input={"file_path": "/tmp/a.html", "content": "x"})
    notes = [r for r in run.spool() if r.get("nudge") == "notes"]
    assert [(r["event"], r["injected"]) for r in notes] == [("PostToolUse", len(co_hook.NOTES_LINE))]
    doc = [r for r in run.spool() if "outside_doc" in r]
    assert doc[0]["injected"] == len(co_hook.TEMP_DOC_LINE.replace("{path}", "/tmp/a.html"))
    # the commit reminder too
    run(bash("git commit -m x"))
    pre = [r for r in run.spool() if r.get("nudge") == "notes" and r["event"] == "PreToolUse"]
    assert [r["injected"] for r in pre] == [len(co_hook.NOTES_LINE)]
    # a call with no nudge spools no injected
    run2 = Run(plugin, tmp_path / "b", stub.base, notes=False)
    _post(run2, 1)
    assert not any("injected" in r for r in run2.spool())


def test_pre_tool_use_unchanged_by_a_band_crossing_usage_file(plugin, tmp_path, stub):
    run = Run(plugin, tmp_path, stub.base)
    _usage(run, est_cost_usd=4.9, context_tokens=200000, fresh_session_tokens=150000)
    assert run(bash("git status")) is None
    assert decision(run(bash("rm -rf ~"))) == "deny"
    assert _bands(run) == []  # PreToolUse never fires a band
    for content in ("{not json", '{"ts": "x"}'):
        (run.tdir / "usage.json").write_text(content)
        assert run(bash("git status")) is None
        assert decision(run(bash("rm -rf ~"))) == "deny"
