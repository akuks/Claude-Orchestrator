"""Phase 4c task 4: the brief's prompt text and its scope in the co-guard policy."""

import os

from app.briefs import render, scope

FULL = {"goal": "Add the export", "can_change": ["src/**", "tests/**"], "cannot_change": ["migrations/**", "**/*.lock"],
        "done": "tests pass", "stop": "when blocked", "setup": "make deps", "log": "notes.md", "retries": 0,
        "tie_break": "ask", "outputs": ["pr_url", "report"]}
MIN = {"goal": "G", "can_change": [], "cannot_change": [], "done": "D", "stop": "S", "setup": None, "log": None,
       "retries": None, "tie_break": None, "outputs": []}


def test_brief_text_full():
    bound = {"task_id": "T0", "can_change": ["src/**"], "cannot_change": ["src/gen/**"]}
    assert render.brief_text(FULL, [bound]).split("\n") == [
        render.BRIEF_FIRST_LINE, "Goal: Add the export", "CAN change: src/**, tests/**",
        "CANNOT change: migrations/**, **/*.lock", "Done when: tests pass", "Stop and report when: when blocked",
        "Setup: make deps", "Log: notes.md", "Retries: 0", "Tie-break: ask", "Outputs: pr_url, report",
        "Also bound by an enclosing brief (task T0): CAN change src/**; CANNOT change src/gen/**",
        render.REPORT_LINE]


def test_brief_text_minimal():
    lines = render.brief_text(MIN, []).split("\n")
    assert lines == [render.BRIEF_FIRST_LINE, "Goal: G", "CAN change: anything not under CANNOT change",
                     "CANNOT change: nothing listed", "Done when: D", "Stop and report when: S", render.REPORT_LINE]
    assert lines[-1].startswith("End your report with Outputs")


def test_shell_root_prefix():
    f = scope.shell_root_prefix
    assert f("migrations") == "migrations" and f("migrations/**") == "migrations"
    assert f("src/config.py") == "src/config.py"
    assert f("**/*.lock") is None and f("src/*.ts") is None and f("a?/**") is None and f("**") is None


def test_layers_own_first():
    b = {"task_id": "P", "can_change": ["src/**"], "cannot_change": ["db"]}
    assert scope.layers("T", FULL, [b]) == [
        {"task": "T", "can": ["src/**", "tests/**"], "cannot": ["migrations/**", "**/*.lock"]},
        {"task": "P", "can": ["src/**"], "cannot": ["db"]}]


def test_apply(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    existing = {"path": str(proj / "vendor"), "reason": "upstream", "source": "project"}
    policy = {"project_dir": str(proj), "readonly_paths": [existing]}
    ls = scope.layers("T", FULL, [{"task_id": "P", "can_change": [], "cannot_change": ["db", "migrations"]}])
    ev = scope.apply(policy, ls)
    assert policy["brief"] == {"layers": ls}
    real = os.path.realpath
    assert policy["readonly_paths"] == [
        existing,
        {"path": real(proj / "migrations"), "reason": "the task's brief says CANNOT change migrations/**",
         "source": "brief"},
        {"path": real(proj / "db"), "reason": "the task's brief says CANNOT change db", "source": "brief"}]
    assert ev == {"event": "brief_scope", "shell_roots": 2, "file_tools_only": ["**/*.lock"]}


def test_apply_parent_cannot_the_child_omits(tmp_path):
    """The no-widening guarantee: a bound's CANNOT becomes a root and a layer even if the child's own omits it."""
    policy = {"project_dir": str(tmp_path), "readonly_paths": []}
    own = {**MIN, "cannot_change": ["tmp"]}
    ls = scope.layers("C", own, [{"task_id": "P", "can_change": ["src/**"], "cannot_change": ["migrations/**"]}])
    scope.apply(policy, ls)
    assert [e["path"] for e in policy["readonly_paths"]] == [os.path.realpath(tmp_path / "tmp"),
                                                             os.path.realpath(tmp_path / "migrations")]
    assert policy["brief"]["layers"][1] == {"task": "P", "can": ["src/**"], "cannot": ["migrations/**"]}
