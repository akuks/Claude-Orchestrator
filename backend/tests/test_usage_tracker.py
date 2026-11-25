"""Phase 3b Task 1: the usage tracker (per-message dedup, helpers, context, largest
results) and the price table."""

import json

import pytest

from app.usage import prices
from app.usage.tracker import UsageTracker


def _asst(mid, usage, model="claude-opus-5-5", parent=None, content=None):
    msg = {"content": content or []}
    if mid is not None:
        msg["id"] = mid
    if model is not None:
        msg["model"] = model
    if usage is not None:
        msg["usage"] = usage
    obj = {"type": "assistant", "message": msg}
    if parent:
        obj["parent_tool_use_id"] = parent
    return obj


def _u(i=0, cw=0, cr=0, o=0):
    return {"input_tokens": i, "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr,
            "output_tokens": o}


def test_same_id_in_three_events_counts_once_with_last_usage():
    """Review Focus 1: one API message arriving as 3 events (growing output)."""
    t = UsageTracker()
    t.on_assistant(_asst("m1", _u(10, 100, 1000, 1)))
    t.on_assistant(_asst("m1", _u(10, 100, 1000, 20)))
    t.on_assistant(_asst("m1", _u(10, 100, 1000, 75)))
    snap = t.snapshot()
    assert snap["messages"] == 1
    assert snap["main"] == {"messages": 1, "input": 10, "cache_write": 100, "cache_read": 1000, "output": 75,
                            "reread_per_step": 1000}
    assert snap["models"]["claude-opus-5-5"]["output"] == 75
    t.on_assistant(_asst("m2", _u(5, 0, 1110, 3)))
    snap = t.snapshot()
    assert snap["messages"] == 2
    assert snap["main"] == {"messages": 2, "input": 15, "cache_write": 100, "cache_read": 2110, "output": 78,
                            "reread_per_step": 1055}


def test_helper_attribution_by_parent_tool_use_id():
    t = UsageTracker()
    t.on_tool_use_main("tuA", "Agent", {"subagent_type": "Explore", "description": "look"})
    t.on_tool_use_main("tuB", "Task", {"description": "write tests"})
    t.on_tool_use_main("tuC", "Task", {})
    t.on_tool_use_main("tuD", "Bash", {"command": "ls"})  # not a helper
    t.on_assistant(_asst("m0", _u(1, 0, 0, 1)))
    t.on_assistant(_asst("h1", _u(2, 0, 0, 2), parent="tuA"))
    t.on_assistant(_asst("h1", _u(2, 0, 0, 9), parent="tuA"))
    t.on_assistant(_asst("h2", _u(3, 0, 0, 3), parent="tuB"))
    t.on_assistant(_asst("h3", _u(4, 0, 0, 4), parent="tuC"))
    t.on_assistant(_asst("h4", _u(5, 0, 0, 5), parent="tuUnknown"))
    snap = t.snapshot()
    assert snap["main"]["messages"] == 1 and snap["main"]["input"] == 1
    h = snap["helpers"]
    assert h["Explore"] == {"messages": 1, "input": 2, "cache_write": 0, "cache_read": 0, "output": 9,
                            "reread_per_step": 0, "starts": 1}
    assert h["write tests"]["input"] == 3
    assert h["helper"]["messages"] == 2 and h["helper"]["input"] == 9
    assert "Bash" not in h
    # a helper message never sets the context size
    assert t.context_tokens == 1


def test_missing_usage_and_id_tolerated():
    t = UsageTracker()
    t.on_assistant({"type": "assistant"})
    t.on_assistant({"type": "assistant", "message": "junk"})
    t.on_assistant(_asst("m1", None))  # no usage
    t.on_assistant(_asst(None, _u(7, 0, 0, 1)))  # no id: its own message
    t.on_assistant(_asst(None, _u(8, 0, 0, 1)))
    t.on_assistant(_asst("m2", {"input_tokens": "x", "output_tokens": None}))  # junk numbers
    snap = t.snapshot()
    assert snap["main"]["messages"] == 3
    assert snap["main"]["input"] == 15
    json.dumps(snap)  # JSON-safe
    empty = UsageTracker()
    assert empty.context_tokens is None and empty.peak_context is None and empty.est_cost_usd is None
    assert empty.snapshot()["messages"] == 0


def test_context_is_latest_main_message_and_peak_is_kept():
    t = UsageTracker()
    t.on_assistant(_asst("m1", _u(10, 200, 3000, 5)))
    assert t.context_tokens == 3210 and t.peak_context == 3210
    t.on_assistant(_asst("m2", _u(20, 300, 9000, 5)))
    assert t.context_tokens == 9320 and t.peak_context == 9320
    t.on_assistant(_asst("h1", _u(50000, 0, 0, 5), parent="tuX"))
    assert t.context_tokens == 9320
    t.on_assistant(_asst("m3", _u(5, 0, 1000, 5)))  # after a compaction
    assert t.context_tokens == 1005 and t.peak_context == 9320


def test_largest_five_results_and_injected():
    t = UsageTracker()
    t.on_assistant(_asst("m1", _u(1), content=[{"type": "tool_use", "id": f"t{i}", "name": f"Tool{i}",
                                                 "input": {}} for i in range(7)]))
    for i, n in enumerate([10, 500, 30, 90000, 7, 2500, 400]):
        t.on_tool_result(f"t{i}", None, n)
    t.on_tool_result("tx", "Read", 600)
    largest = t.snapshot()["largest_results"]
    assert [r["chars"] for r in largest] == [90000, 2500, 600, 500, 400]
    assert largest[0] == {"tool": "Tool3", "chars": 90000, "id": "t3"}
    assert largest[2]["tool"] == "Read"
    t.add_injected(120)
    t.add_injected(30)
    assert t.snapshot()["injected_chars"] == 150


def test_estimate_running_cost():
    t = UsageTracker()
    t.on_assistant(_asst("m1", _u(1_000_000, 0, 0, 0)))
    assert t.est_cost_usd == pytest.approx(4.0)
    t.on_assistant(_asst("m2", _u(0, 0, 0, 1_000_000), model="claude-haiku-4-5-20251001"))
    assert t.est_cost_usd == pytest.approx(9.0)
    t.on_assistant(_asst("m3", _u(1, 0, 0, 0), model="gpt-x"))
    assert t.est_cost_usd is None  # an unknown model: no estimate


def test_prices_longest_prefix_and_unknown():
    assert prices.PRICES["claude-opus-5-5"] == (4, 20, 5, 0.20)
    assert prices.PRICES["claude-opus-5"] == (5, 25, 6.25, 0.50)
    m = 1_000_000
    assert prices.estimate("claude-opus-5-5-20990101", m, m, m, m) == pytest.approx(4 + 20 + 5 + 0.20)
    assert prices.estimate("claude-opus-5", m, 0, 0, 0) == pytest.approx(5)
    assert prices.estimate("claude-opus-5-20990101", 0, m, 0, 0) == pytest.approx(25)
    assert prices.estimate("claude-fable-5-2", 0, 0, m, m) == pytest.approx(10.20)
    assert prices.estimate("claude-haiku-5-5", m, m, m, m) == pytest.approx(0.10 + 0.50 + 0.125 + 0.01)
    assert prices.estimate("claude-test", m, 0, 0, 0) is None
    assert prices.estimate(None, m, 0, 0, 0) is None
    assert prices.estimate("", m, 0, 0, 0) is None


def test_prices_table_exact():
    assert prices.PRICES == {
        "claude-fable-5-2": (8, 40, 10, 0.20),
        "claude-fable-5-1": (10, 50, 12.5, 1),
        "claude-fable-5": (10, 50, 12.5, 1),
        "claude-opus-5-5": (4, 20, 5, 0.20),
        "claude-opus-5": (5, 25, 6.25, 0.50),
        "claude-opus-4-8": (5, 25, 6.25, 0.50),
        "claude-opus-4-7": (5, 25, 6.25, 0.50),
        "claude-opus-4-6": (5, 25, 6.25, 0.50),
        "claude-sonnet-5-5": (2, 10, 2.5, 0.10),
        "claude-sonnet-5": (2, 10, 2.5, 0.20),
        "claude-sonnet-4-6": (3, 15, 3.75, 0.30),
        "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
        "claude-haiku-4-5": (1, 5, 1.25, 0.10),
    }


def test_write_usage_file_atomic_0600(tmp_path, monkeypatch):
    import os
    import stat

    from app.guard import runtime as guard_runtime
    from app.usage import tracker as tracker_mod

    monkeypatch.setattr(guard_runtime, "task_dir", lambda tid: tmp_path / tid)
    assert tracker_mod.write_usage_file("T1", {"context_tokens": 1}) is False  # no task dir
    (tmp_path / "T1").mkdir()
    (tmp_path / "T1" / "usage.json.tmp").symlink_to(tmp_path / "planted")  # never followed
    assert tracker_mod.write_usage_file("T1", {"context_tokens": 5, "ts": 1.0}) is True
    p = tmp_path / "T1" / "usage.json"
    assert json.loads(p.read_text()) == {"context_tokens": 5, "ts": 1.0}
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert not (tmp_path / "planted").exists()


def test_helper_kind_and_tool_names_are_redacted_and_cleaned():
    """Final review M2: the helper kind (free model text) is stored in the breakdown and served to every
    viewer: run secrets and token shapes are masked and hidden characters stripped before it is kept."""
    secret = "hunter2-run-secret-value"
    t = UsageTracker(secrets=[secret])
    t.on_tool_use_main("tuA", "Agent", {"description": f"use {secret}​ now"})
    t.on_tool_use_main("tuB", "Task", {"subagent_type": "ghp_" + "a" * 36})
    t.on_tool_use_main("tuC", f"Bash‮{secret}", {})
    t.on_assistant(_asst("h1", _u(1, 0, 0, 1), parent="tuA"))
    t.on_assistant(_asst("h2", _u(1, 0, 0, 1), parent="tuB"))
    t.on_tool_result("tuC", None, 30_000)
    snap = t.snapshot()
    assert sorted(snap["helpers"]) == ["***", "use *** now"]
    assert snap["largest_results"][0]["tool"] == "Bash***"
    assert secret not in json.dumps(snap)


def test_zero_token_synthetic_message_keeps_the_estimate():
    """Final review M3: Claude Code's ``<synthetic>`` messages (an error or interrupt text) carry no tokens;
    they cost nothing and must not turn the estimate (and so the budget bands) off for the step."""
    t = UsageTracker()
    t.on_assistant(_asst("m1", _u(1_000_000, 0, 0, 0)))
    t.on_assistant(_asst("s1", _u(0, 0, 0, 0), model="<synthetic>"))
    assert t.est_cost_usd == pytest.approx(4.0)
    t.on_assistant(_asst("m2", _u(1, 0, 0, 0), model="gpt-x"))
    assert t.est_cost_usd is None  # tokens from an unknown model: no estimate
    only = UsageTracker()
    only.on_assistant(_asst("s1", _u(0, 0, 0, 0), model="<synthetic>"))
    assert only.est_cost_usd == 0.0


def test_synthetic_model_keeps_last_model():
    """3b parked item: a ``<synthetic>`` message never becomes the last model, and an unnamed message after it
    is priced as the real model."""
    t = UsageTracker()
    t.on_assistant(_asst("m1", _u(0, 0, 0, 0), model="claude-sonnet-5-5"))
    t.on_assistant(_asst("s1", _u(0, 0, 0, 0), model="<synthetic>"))
    assert t._last_model == "claude-sonnet-5-5"
    t.on_assistant(_asst("m2", _u(1_000_000, 0, 0, 0), model=None))
    assert t.est_cost_usd is not None
    assert t._msgs["id:m2"]["model"] == "claude-sonnet-5-5"
    assert t._msgs["id:s1"]["model"] == "claude-sonnet-5-5"


def test_reread_per_step_and_helper_starts():
    """Phase 7e (F96): per bucket cache_read / messages; per helper kind the number of starts."""
    t = UsageTracker()
    t.on_assistant(_asst("m1", _u(1, 0, 30_000, 1)))
    t.on_assistant(_asst("m2", _u(1, 0, 50_001, 1)))
    for i in range(3):
        t.on_tool_use_main(f"tu{i}", "Agent", {"subagent_type": "Explore"})
    for i in range(7):
        t.on_assistant(_asst(f"h{i}", _u(1, 0, 700, 1), parent=f"tu{i % 3}"))
    t.on_tool_use_main("tuQ", "Agent", {"subagent_type": "quiet"})  # started, no message yet
    t.on_assistant(_asst("hx", _u(1, 0, 10, 1), parent="tuGone"))  # unknown parent: "helper", no start counted
    snap = t.snapshot()
    assert snap["main"]["reread_per_step"] == 40_000  # 80 001 / 2, rounded
    assert snap["helpers"]["Explore"]["reread_per_step"] == 700 and snap["helpers"]["Explore"]["starts"] == 3
    assert snap["helpers"]["Explore"]["messages"] == 7
    assert snap["helpers"]["helper"]["starts"] == 0
    assert "quiet" not in snap["helpers"]
    assert UsageTracker().snapshot()["main"]["reread_per_step"] == 0
    json.dumps(snap)
