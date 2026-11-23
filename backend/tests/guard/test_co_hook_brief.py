"""Phase 4c Task 5: co-guard enforces a task brief's CAN / CANNOT change on file tools."""

import io
import json
import os

import pytest

from app.guard import co_hook
from test_co_hook import Run, bash, decision, plugin, reason, stub  # noqa: F401

BRIEF = {"layers": [{"task": "T1", "can": ["src/**"], "cannot": ["src/secret/**", "migrations"]}]}
TAIL = "Stay inside the brief; if the change is needed, stop and report why."
CAN_TXT = "this changes README.md, which is outside what the task's brief says CAN change (src/**)"


@pytest.fixture
def brun(plugin, tmp_path, stub):  # noqa: F811
    r = Run(plugin, tmp_path, stub.base, brief=BRIEF)
    (r.project / "src").mkdir()
    (r.project / "migrations").mkdir()
    return r


def edit(run, path, tool="Edit", agent=None):
    if tool == "NotebookEdit":
        ti = {"notebook_path": path, "new_source": "x"}
    elif tool == "MultiEdit":
        ti = {"file_path": path, "edits": [{"old_string": "a", "new_string": "b"}]}
    elif tool == "Write":
        ti = {"file_path": path, "content": "x"}
    else:
        ti = {"file_path": path, "old_string": "a", "new_string": "b"}
    p = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": ti, "tool_use_id": "tu1",
         "session_id": "s1", "cwd": str(run.project)}
    if agent:
        p["agent_id"] = agent
    return p


def set_brief(run, brief, **extra):
    run.policy["brief"] = brief
    run.policy.update(extra)
    (run.tdir / "policy.json").write_text(json.dumps(run.policy))


def test_can_allows(brun):
    assert decision(brun(edit(brun, "src/a.py"))) is None


def test_outside_can_denied(brun):
    out = brun(edit(brun, "README.md", "Write"))
    assert decision(out) == "deny"
    assert reason(out) == f"CO-GUARD: blocked — {CAN_TXT} (brief-scope). {TAIL}"
    rec = [r for r in brun.spool() if r.get("decision") == "deny"][0]
    assert rec["rule_ids"] == ["brief-scope"] and rec["reason"] == CAN_TXT and rec["tool"] == "Write"


def test_cannot_wins(brun):
    out = brun(edit(brun, "src/secret/k.py"))
    assert reason(out) == ("CO-GUARD: blocked — this changes src/secret/k.py, which the task's brief says CANNOT "
                           f"change (src/secret/**) (brief-scope). {TAIL}")


def test_escapes(brun, tmp_path):
    os.symlink(str(brun.project / "migrations"), str(brun.project / "src" / "link"))
    for tool, path in [("Edit", "src/../migrations/x.py"), ("Edit", str(brun.project / "migrations" / "x.py")),
                       ("Edit", "src/link/x.py"), ("NotebookEdit", "migrations/n.ipynb"),
                       ("MultiEdit", "migrations/x.py"), ("Write", "./migrations/../migrations/y")]:
        out = brun(edit(brun, path, tool))
        assert decision(out) == "deny" and "(brief-scope)" in reason(out), path
    assert decision(brun(edit(brun, "migrations/x.py", agent="a1"))) == "deny"


@pytest.mark.skipif(not co_hook.CASE_INSENSITIVE, reason="case-sensitive platform")
def test_case_variant(brun):
    assert decision(brun(edit(brun, "MIGRATIONS/x.py"))) == "deny"


def test_outside_project_untouched(brun, tmp_path):
    for path in ["/tmp/x", str(brun.tdir / "handoff.md"), str(tmp_path / "other" / "x")]:
        out = brun(edit(brun, path, "Write"))
        assert "brief-scope" not in json.dumps(out), path
    assert all("brief-scope" not in json.dumps(r) for r in brun.spool())


def test_bound_layer(brun):
    layers = BRIEF["layers"] + [{"task": "P", "can": [], "cannot": ["src/b.py"]}]
    set_brief(brun, {"layers": layers})
    out = brun(edit(brun, "src/b.py"))
    assert "which an enclosing brief (task P) says CANNOT change (src/b.py)" in reason(out)


def test_child_omits_parent_cannot_and_widens_can(brun):
    parent = {"task": "P", "can": ["src/**"], "cannot": ["src/keep/**"]}
    child = {"task": "T1", "can": ["**"], "cannot": []}
    set_brief(brun, {"layers": [child, parent]})
    assert decision(brun(edit(brun, "src/keep/x.py"))) == "deny"
    out = brun(edit(brun, "docs/x.md"))
    assert "an enclosing brief (task P) says CAN change (src/**)" in reason(out)
    assert decision(brun(edit(brun, "src/ok.py"))) is None


def test_guard_off_still_enforced(brun):
    set_brief(brun, BRIEF, guard_enabled=False)
    assert decision(brun(edit(brun, "README.md"))) == "deny"


def test_empty_can_unrestricted(brun):
    set_brief(brun, {"layers": [{"task": "T1", "can": [], "cannot": ["x"]}]})
    assert decision(brun(edit(brun, "README.md"))) is None
    assert decision(brun(edit(brun, "x/y.py"))) == "deny"


@pytest.mark.parametrize("bad", [
    "x", None, {}, {"layers": "x"}, {"layers": []}, {"layers": ["x"]},
    {"layers": [{"task": "T1", "can": "src/**", "cannot": []}]},
    {"layers": [{"task": "T1", "can": [5], "cannot": []}]},
    {"layers": [{"task": "T1", "can": [], "cannot": [None]}]},
    {"layers": [{"task": 1, "can": [], "cannot": []}]},
])
def test_malformed_fails_closed(brun, bad):
    set_brief(brun, bad)
    for tool in co_hook.BRIEF_TOOLS:
        out = brun(edit(brun, "src/a.py", tool))
        assert decision(out) == "deny"
        assert co_hook.BRIEF_MALFORMED in reason(out)
    assert "brief-scope" not in json.dumps(brun(bash("ls")))


def test_check_crash_denies(brun, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(co_hook, "matches", boom)
    monkeypatch.setenv("CO_POLICY_FILE", str(brun.tdir / "policy.json"))
    monkeypatch.setenv("CO_TASK_DIR", str(brun.tdir))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(edit(brun, "src/a.py"))))
    assert co_hook.main(["co_hook.py", "PreToolUse"]) == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_no_brief_no_change(plugin, tmp_path, stub):  # noqa: F811
    r = Run(plugin, tmp_path, stub.base)
    assert decision(r(edit(r, "README.md", "Write"))) is None


def test_notebook_path_wins_over_file_path(brun):
    p = edit(brun, "migrations/n.ipynb", "NotebookEdit")
    p["tool_input"]["file_path"] = "src/a.py"
    assert decision(brun(p)) == "deny"


def test_missing_project_dir_fails_closed(brun):
    set_brief(brun, BRIEF, project_dir=None)
    assert co_hook.BRIEF_MALFORMED in reason(brun(edit(brun, "src/a.py")))


@pytest.mark.parametrize("glob", ["", "a" * 301, "*" * 31])
def test_unusable_glob_is_malformed(brun, glob):
    for lay in ({"task": "T1", "can": [glob], "cannot": []}, {"task": "T1", "can": [], "cannot": [glob]}):
        set_brief(brun, {"layers": [lay]})
        assert co_hook.BRIEF_MALFORMED in reason(brun(edit(brun, "src/a.py")))


def test_symlinked_parent_outside_project_into_cannot(brun, tmp_path):
    os.symlink(str(brun.project / "migrations"), str(tmp_path / "ln"))
    assert decision(brun(edit(brun, str(tmp_path / "ln" / "x.py")))) == "deny"


def test_as_given_form_catches_symlink_out_of_project(brun, tmp_path):
    (tmp_path / "away").mkdir()
    os.symlink(str(tmp_path / "away"), str(brun.project / "src" / "out"))
    set_brief(brun, {"layers": [{"task": "T1", "can": [], "cannot": ["src/out/**"]}]})
    assert decision(brun(edit(brun, "src/out/x.py", "Write"))) == "deny"


def test_nfc_dodge():
    import unicodedata
    nfd = unicodedata.normalize("NFD", "src/caf\u00e9/x")
    pol = {"project_dir": "/p", "brief": {"layers": [{"task": "T", "can": [], "cannot": ["src/caf\u00e9/**"]}]}}
    pay = {"tool_name": "Write", "cwd": "/p", "tool_input": {"file_path": "/p/" + nfd}}
    old = co_hook.CASE_INSENSITIVE
    try:
        co_hook.CASE_INSENSITIVE = True
        assert co_hook.brief_denial(pol, pay, "Write")
    finally:
        co_hook.CASE_INSENSITIVE = old


def test_decision_notes_still_shown_with_brief(plugin, tmp_path, stub):  # noqa: F811
    d = [{"n": 15, "kind": "rule", "title": "Typed", "why": "", "see": "", "touches": ["src/**"],
          "decided_at": "2026-11-20"}]
    r = Run(plugin, tmp_path, stub.base, brief=BRIEF, decisions=d, decisions_index="x")
    out = r(edit(r, "src/a.py"))
    assert decision(out) is None and "Typed" in json.dumps(out)
