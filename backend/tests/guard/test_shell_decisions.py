"""Phase 7d Task 2 (spec section 4, forge F45): recorded decisions shown before a shell command that writes files.
The guard returns the files an allowed shell command writes with its verdict (the first 50, sorted) and the hook
looks the decisions up for them exactly as for Edit/Write."""

import os
import statistics
import time

import pytest

from app.guard import co_hook, rules
from test_co_hook import Run, plugin, stub  # noqa: F401


def _d(n, title, touches):
    return {"n": n, "kind": "decision", "title": title, "why": "", "see": "", "touches": touches,
            "decided_at": "2026-11-20"}


DECISIONS = [_d(12, "Money in cents", ["src/billing.py"]), _d(13, "Docs", ["docs/**"])]


def shell(cmd, tool="Bash", session="s1", tid="tu1", cwd=None):
    p = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {"command": cmd}, "tool_use_id": tid,
         "session_id": session}
    if cwd:
        p["cwd"] = cwd
    return p


def ctx(out):
    return (out or {}).get("hookSpecificOutput", {}).get("additionalContext")


@pytest.fixture
def srun(plugin, tmp_path, stub):  # noqa: F811
    r = Run(plugin, tmp_path, stub.base, decisions=DECISIONS)
    (r.project / "src").mkdir()
    (r.project / "src" / "billing.py").write_text("x")
    return r


# ---- the verdict carries the written files ----------------------------------------------------------


def _policy(tmp_path):
    proj = tmp_path / "p"
    (proj / "src").mkdir(parents=True, exist_ok=True)
    return {"project_dir": str(proj), "cwd": str(proj), "home": str(tmp_path / "home"), "protected_paths": []}, proj


def test_allowed_command_returns_its_written_files(tmp_path):
    pol, proj = _policy(tmp_path)
    v = rules.evaluate("Bash", {"command": "echo a > src/b.py; printf x | tee src/a.py >/dev/null"}, pol, str(proj))
    real = os.path.realpath(proj)
    assert v.decision == "allow"
    assert v.writes == [f"{real}/src/a.py", f"{real}/src/b.py"]
    assert v.to_dict()["writes"] == v.writes
    assert rules.evaluate("Monitor", {"command": "echo a > src/c.py"}, pol, str(proj)).writes == \
        [f"{real}/src/c.py"]


def test_no_writes_for_other_tools_or_a_command_that_is_not_allowed(tmp_path):
    pol, proj = _policy(tmp_path)
    assert rules.evaluate("Bash", {"command": "ls -la"}, pol, str(proj)).writes == []
    assert rules.evaluate("Edit", {"file_path": str(proj / "src" / "a.py"), "old_string": "a", "new_string": "b"},
                          pol, str(proj)).writes == []
    v = rules.evaluate("Bash", {"command": "echo a > src/a.py; git reset --hard"}, pol, str(proj))
    assert v.decision == "approve" and v.writes == []
    v = rules.evaluate("Bash", {"command": "echo a > src/a.py; rm -rf /"}, pol, str(proj))
    assert v.decision == "deny" and v.writes == []


def test_five_hundred_files_return_the_first_fifty_sorted(tmp_path):
    pol, proj = _policy(tmp_path)
    names = [f"src/f{i:03d}.txt" for i in range(500)]
    v = rules.evaluate("Bash", {"command": "echo x | tee " + " ".join(reversed(names))}, pol, str(proj))
    real = os.path.realpath(proj)
    assert v.decision == "allow" and len(v.writes) == rules.WRITES_MAX == 50
    assert v.writes == [f"{real}/{n}" for n in names[:50]]


# ---- the hook shows the decisions -------------------------------------------------------------------


def test_redirect_into_a_covered_file_shows_its_decision_once(srun):
    out = srun(shell("echo 'TOTAL = 1' > src/billing.py", cwd=str(srun.project)))
    text = ctx(out)
    assert text.startswith(co_hook.DECISIONS_FRAME) and "D-012 (decision, 2026-11-20): Money in cents" in text
    assert (out["hookSpecificOutput"].get("permissionDecision") in (None, "allow"))
    rec = [r for r in srun.spool() if r.get("nudge") == "decisions"]
    assert rec[-1]["ids"] == [12] and rec[-1]["path"] == "src/billing.py"
    # the same conversation is not shown it again, before a shell edit or a file edit
    assert srun(shell("echo y >> src/billing.py", tid="tu2", cwd=str(srun.project))) is None
    edit = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_use_id": "tu3", "session_id": "s1",
            "cwd": str(srun.project), "tool_input": {"file_path": str(srun.project / "src" / "billing.py"),
                                                     "old_string": "x", "new_string": "y"}}
    assert srun(edit) is None
    # another conversation is
    assert "D-012" in ctx(srun(shell("echo y > src/billing.py", session="s2", cwd=str(srun.project))))


def test_monitor_and_heredoc_and_folder_rules(srun):
    (srun.project / "docs").mkdir()
    out = srun(shell("cat > docs/guide.md <<'EOF'\nhello\nEOF", tool="Monitor", cwd=str(srun.project)))
    assert "D-013 (decision, 2026-11-20): Docs" in ctx(out)


def test_reads_and_outside_writes_get_no_note(srun, tmp_path):
    assert srun(shell("cat src/billing.py | wc -l", cwd=str(srun.project))) is None
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert srun(shell(f"echo x > {outside}/billing.py", cwd=str(srun.project))) is None
    assert [r for r in srun.spool() if r.get("nudge") == "decisions"] == []


def test_a_command_needing_approval_gets_no_note_before_it(srun, stub):
    stub.statuses = ["denied"]
    out = srun(shell("echo x > src/billing.py; git reset --hard", cwd=str(srun.project)))
    assert (out or {}).get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    assert [r for r in srun.spool() if r.get("nudge") == "decisions"] == []


def test_only_fifty_files_are_looked_up(tmp_path, monkeypatch):
    monkeypatch.setenv("CO_TASK_DIR", str(tmp_path))
    proj = tmp_path / "p"
    proj.mkdir()
    writes = [str(proj / f"f{i:03d}.txt") for i in range(500)]
    pol = {"cwd": str(proj), "decisions": [_d(1, "Late", ["f499.txt"]), _d(2, "Early", ["f010.txt"])]}
    seen = []
    real = co_hook._rel_target

    def counting(policy, payload, root_key="cwd"):
        seen.append(payload)
        return real(policy, payload, root_key)

    monkeypatch.setattr(co_hook, "_rel_target", counting)
    out = co_hook._decision_notes(pol, {"tool_name": "Bash", "session_id": "s1"}, "Bash", writes)
    assert len(seen) == co_hook.SHELL_WRITES_MAX == 50
    assert "D-002" in out and "D-001" not in out
    assert co_hook._decision_notes(pol, {"tool_name": "Bash", "session_id": "s2"}, "Bash", None) is None
    assert co_hook._decision_notes(pol, {"tool_name": "Read", "session_id": "s3"}, "Read", writes) is None


def test_a_command_writing_500_files_stays_within_the_hook_budget(plugin, tmp_path, stub):  # noqa: F811
    decisions = [_d(i, f"Rule {i}", [f"src/f{i:03d}.txt", "src/**/x*.md"]) for i in range(1, 201)]
    run = Run(plugin, tmp_path, stub.base, decisions=decisions)
    (run.project / "src").mkdir()
    cmd = "echo x | tee " + " ".join(f"src/f{i:03d}.txt" for i in range(500))
    times = []
    for i in range(5):
        t0 = time.monotonic()
        out = run(shell(cmd, session=f"s{i}", tid=f"t{i}", cwd=str(run.project)))
        times.append(time.monotonic() - t0)
        assert "D-001" in ctx(out)
    assert statistics.median(times) < 1.0, times
    rec = [r for r in run.spool() if r.get("nudge") == "decisions"][-1]
    assert len(rec["ids"]) <= co_hook.DECISIONS_MAX_NOTES
