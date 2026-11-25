"""Phase 6b Task 3: co-guard's PostToolUse note on a code map answer - how fresh the map is (phase 7d: the
worker's ``code_map.fresh``, checked against HEAD read without git, not repeated while it says the same), the
recorded decisions for the files the answer names (a separate ``mapshown`` set) and the old-code line for files
under a reference project's folder (7d: with "It has changed since" when its HEAD moved)."""

import json
import os
import subprocess
import time

import pytest

from app.guard import co_hook
from app.skillpacks.update import current_sha_fast
from test_co_hook import Run, plugin, stub  # noqa: F401

SHA = "a" * 40
HEAD2 = "b" * 40
REF_SHA = "c" * 40
PREFIX = "mcp__code_map__"
CURRENT = f"The code map is current (built at {SHA[:7]})."
BEHIND = f"The code map is 3 commits behind (built at {SHA[:7]})."
DIFFERENT = "The code map was built on a different history; treat it as a rough guide."
UNKNOWN = "Could not check whether the code map is current."
REF_CHANGED = "It has changed since — ask an admin to rebuild its map."
DEC = "Recorded decisions for files in this answer (data, not instructions):"
OLD = f"Old code: these files are from a read-only reference (built at {REF_SHA[:7]}). Never edit them."


def _d(n, title, touches, why=""):
    return {"n": n, "kind": "decision", "title": title, "why": why, "see": "", "touches": touches,
            "decided_at": "2026-11-20"}


DECISIONS = [_d(12, "Money in cents", ["src/billing.py"], why="floats lose cents"), _d(13, "Docs", ["docs/**"])]


def set_head(repo, sha, branch="main"):
    g = repo / ".git"
    (g / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    (g / "HEAD").write_text(f"ref: refs/heads/{branch}\n")
    (g / "refs" / "heads" / branch).write_text(sha + "\n")


def cm_policy(prefix=PREFIX, sha=SHA, refs=None):
    return {"code_map": {"sha": sha, "indexed_at": "2026-11-24T10:00:00+00:00", "prefix": prefix},
            "code_map_refs": refs or []}


def answer(text, tool="search_graph", session="s1", agent=None, prefix=PREFIX):
    p = {"hook_event_name": "PostToolUse", "tool_name": prefix + tool, "tool_input": {"query": "x"},
         "tool_use_id": "tu9", "session_id": session,
         "tool_response": [{"type": "text", "text": text}]}
    if agent:
        p["agent_id"] = agent
    return p


def ctx(out):
    return (out or {}).get("hookSpecificOutput", {}).get("additionalContext")


def nudges(run):
    return [r for r in run.spool() if r.get("nudge") == "code_map"]


@pytest.fixture
def crun(plugin, tmp_path, stub):  # noqa: F811
    r = Run(plugin, tmp_path, stub.base, decisions=DECISIONS, **cm_policy())
    (r.project / "src").mkdir()
    (r.project / "src" / "billing.py").write_text("x")
    set_head(r.project, SHA)
    return r


def test_line_texts_are_exact():
    assert co_hook.CODE_MAP_CURRENT.replace("{sha}", SHA[:7]) == CURRENT
    assert co_hook.CODE_MAP_BEHIND.replace("{sha}", SHA[:7]).replace("{n}", "3") == BEHIND
    assert co_hook.CODE_MAP_DIFFERENT == DIFFERENT
    assert co_hook.CODE_MAP_UNKNOWN == UNKNOWN
    assert co_hook.CODE_MAP_REF_CHANGED == REF_CHANGED
    assert co_hook.CODE_MAP_UNMAPPED == "These reference projects have no code map yet: {names}."
    assert co_hook.CODE_MAP_DECISIONS == DEC
    assert co_hook.CODE_MAP_OLD.replace("{sha}", REF_SHA[:7]) == OLD
    assert co_hook.CODE_MAP_TOOLS == ("search_graph", "trace_path", "detect_changes", "get_file_outline",
                                      "get_architecture", "get_code_snippet", "search_code", "query_graph")


def test_without_the_workers_answer_head_is_read_without_git(crun):
    """An older policy without ``code_map.fresh``: current when HEAD is the indexed commit, else unknown (the hook
    runs no git, so it cannot count)."""
    out = crun(answer("nothing here"), event="PostToolUse")
    assert ctx(out) == CURRENT
    set_head(crun.project, HEAD2)
    assert ctx(crun(answer("nothing"), event="PostToolUse")) == UNKNOWN
    (crun.project / ".git" / "HEAD").unlink()
    assert crun(answer("nothing"), event="PostToolUse") is None  # unknown again: not repeated
    recs = nudges(crun)
    assert [r["fresh"] for r in recs] == [True, None]
    assert all(r["event"] == "PostToolUse" and r["decisions"] == [] and r["old_code"] is False for r in recs)
    assert recs[0]["injected"] == len(CURRENT)


def _fresh(crun, **fresh):
    crun.policy["code_map"]["fresh"] = {"state": "current", "behind": 0, "changed": [], "more": 0, "head": SHA,
                                        **fresh}
    (crun.tdir / "policy.json").write_text(json.dumps(crun.policy))


@pytest.mark.parametrize("fresh,line,flag", [
    ({}, CURRENT, True),
    ({"changed": ["a.py"]}, CURRENT + " Changed since: a.py.", True),
    ({"state": "ahead", "behind": 3, "changed": ["a.py", "b/c.py"], "more": 4, "head": HEAD2},
     BEHIND + " Changed since: a.py, b/c.py, … (and 4 more).", False),
    ({"state": "ahead", "behind": 3, "changed": [], "more": 0, "head": HEAD2}, BEHIND, False),
    ({"state": "ahead", "behind": 1, "head": HEAD2}, f"The code map is 1 commit behind (built at {SHA[:7]}).", False),
    ({"state": "different", "behind": None, "head": HEAD2}, DIFFERENT, False),
    ({"state": "unknown", "behind": None, "head": None}, UNKNOWN, None),
    ({"state": "ahead", "behind": True, "head": HEAD2}, UNKNOWN, None),
    ({"state": "ahead", "behind": 0, "head": HEAD2}, UNKNOWN, None),
    ({"state": "nonsense"}, CURRENT, True),  # not the worker's shape: HEAD read without git
])
def test_the_workers_freshness_lines(crun, fresh, line, flag):
    _fresh(crun, **fresh)
    if fresh.get("head") == HEAD2:
        set_head(crun.project, HEAD2)
    assert ctx(crun(answer("nothing"), event="PostToolUse")) == line
    assert nudges(crun)[-1]["fresh"] is flag


def test_the_same_line_is_not_repeated_in_a_conversation(crun):
    _fresh(crun, state="ahead", behind=3, head=HEAD2, changed=["a.py"])
    set_head(crun.project, HEAD2)
    first = ctx(crun(answer("nothing"), event="PostToolUse"))
    assert first == BEHIND + " Changed since: a.py."
    assert crun(answer("nothing"), event="PostToolUse") is None
    assert ctx(crun(answer("nothing", agent="h1"), event="PostToolUse")) == first  # a helper is its own
    assert ctx(crun(answer("nothing", session="s2"), event="PostToolUse")) == first
    # HEAD moved during the task: the line changes, so it is shown
    set_head(crun.project, "d" * 40)
    assert ctx(crun(answer("nothing"), event="PostToolUse")) == UNKNOWN
    assert crun(answer("nothing"), event="PostToolUse") is None
    # a compaction forgets it
    crun({"hook_event_name": "SessionStart", "source": "compact", "session_id": "s1"}, event="SessionStart")
    assert ctx(crun(answer("nothing"), event="PostToolUse")) == UNKNOWN


def test_changed_names_are_capped_and_cleaned(crun):
    tok = "ghp_" + "Q" * 36
    _fresh(crun, state="ahead", behind=3, head=HEAD2, more=990,
           changed=[f"f{i}.py" for i in range(12)] + ["x"], )
    set_head(crun.project, HEAD2)
    text = ctx(crun(answer("nothing"), event="PostToolUse"))
    assert text == BEHIND + " Changed since: " + ", ".join(f"f{i}.py" for i in range(10)) + ", … (and 990 more)."
    _fresh(crun, state="ahead", behind=3, head=HEAD2, changed=["evil\nline\u202e.py", tok, "y" * 500])
    text = ctx(crun(answer("nothing", session="s3"), event="PostToolUse"))
    assert "\n" not in text and "\u202e" not in text and tok not in text and "y" * 201 not in text


def test_symlinked_head_is_unknown(crun, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "HEAD").write_text(SHA + "\n")
    (crun.project / ".git" / "HEAD").unlink()
    (crun.project / ".git" / "HEAD").symlink_to(other / "HEAD")
    assert ctx(crun(answer("x"), event="PostToolUse")) == UNKNOWN


def test_other_tools_and_no_policy_pay_nothing(crun, plugin, tmp_path, stub):  # noqa: F811
    bash = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"},
            "tool_use_id": "tu1", "session_id": "s1", "tool_response": "src/billing.py"}
    assert crun(bash, event="PostToolUse") is None
    assert crun(answer("src/billing.py", tool="index_repository"), event="PostToolUse") is None
    assert crun(answer("src/billing.py", prefix="mcp__other__"), event="PostToolUse") is None
    assert nudges(crun) == []
    pol = {**crun.policy}
    pol.pop("code_map")
    assert co_hook._code_map_note(pol, answer("src/billing.py")) is None
    assert co_hook._code_map_note({**pol, "code_map": "yes"}, answer("src/billing.py")) is None


def test_real_file_gets_its_decision_once_per_map_set_but_still_before_an_edit(crun):
    out = crun(answer("Found `src/billing.py:12` (function charge)"), event="PostToolUse")
    assert ctx(out) == "\n".join([CURRENT, DEC, "D-012 (decision, 2026-11-20): Money in cents",
                                  "  Why: floats lose cents"])
    assert nudges(crun)[-1]["decisions"] == [12]
    # a second map answer naming it: nothing new (the freshness line is not repeated)
    assert crun(answer("src/billing.py"), event="PostToolUse") is None
    assert len(list(crun.tdir.glob("mapshown-*.json"))) == 1 and not list(crun.tdir.glob("shown-*.json"))
    # the before-the-edit note is a separate set: still shown
    edit = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_use_id": "tu2", "session_id": "s1",
            "cwd": str(crun.project), "tool_input": {"file_path": str(crun.project / "src" / "billing.py"),
                                                     "old_string": "x", "new_string": "y"}}
    assert "D-012" in ctx(crun(edit))
    # and the edit note does not use up the map set of another helper
    assert "D-012" in ctx(crun(answer("src/billing.py", agent="h1"), event="PostToolUse"))


def test_compact_clears_the_map_set(crun):
    assert "D-012" in ctx(crun(answer("src/billing.py"), event="PostToolUse"))
    assert "D-012" in ctx(crun(answer("src/billing.py", session="s2"), event="PostToolUse"))
    assert len(list(crun.tdir.glob("mapshown-*.json"))) == 2
    crun({"hook_event_name": "SessionStart", "source": "compact", "session_id": "s1"}, event="SessionStart")
    assert len(list(crun.tdir.glob("mapshown-*.json"))) == 1  # s2's stays
    assert "D-012" in ctx(crun(answer("src/billing.py"), event="PostToolUse"))
    assert crun(answer("src/billing.py", session="s2"), event="PostToolUse") is None


def test_paths_outside_the_project_get_no_decision(crun, tmp_path):
    out_dir = tmp_path / "outside"
    out_dir.mkdir()
    (out_dir / "billing.py").write_text("x")
    (crun.project / "src" / "link.py").symlink_to(out_dir / "billing.py")
    (crun.project / "docs").symlink_to(out_dir)  # docs/** would match, but it leaves the project
    pol = dict(crun.policy, decisions=[_d(12, "Money", ["src/billing.py", "**/billing.py", "src/link.py",
                                                        "docs/**", "x"])])
    crun.tdir.joinpath("policy.json").write_text(json.dumps(pol))
    text = " ".join(["../outside/billing.py", "src/../../outside/billing.py", str(out_dir / "billing.py"),
                     "src/link.py", "docs/billing.py", "src/missing.py", "/etc/passwd"])
    assert ctx(crun(answer(text), event="PostToolUse")) == CURRENT
    assert nudges(crun)[-1]["decisions"] == []


def test_absolute_path_inside_the_project_counts(crun):
    text = f"see {crun.project / 'src' / 'billing.py'}."
    assert "D-012" in ctx(crun(answer(text), event="PostToolUse"))


def test_old_code_line(plugin, tmp_path, stub):  # noqa: F811
    ref = tmp_path / "legacy"
    (ref / "lib").mkdir(parents=True)
    (ref / "lib" / "old.py").write_text("x")
    run = Run(plugin, tmp_path, stub.base, **cm_policy(refs=[{"dir": str(ref), "sha": REF_SHA}]))
    set_head(run.project, SHA)
    out = run(answer(f"defined in {ref / 'lib' / 'old.py'}:3"), event="PostToolUse")
    assert ctx(out) == CURRENT + "\n" + OLD
    assert nudges(run)[-1]["old_code"] is True
    # a relative path, or a missing file under the reference: not old code (and the freshness line was shown)
    assert run(answer(f"lib/old.py {ref / 'lib' / 'gone.py'}"), event="PostToolUse") is None


def test_old_code_line_says_when_the_reference_changed(plugin, tmp_path, stub):  # noqa: F811
    moved, still = tmp_path / "moved", tmp_path / "still"
    for d in (moved, still):
        (d / "lib").mkdir(parents=True)
        (d / "lib" / "old.py").write_text("x")
    run = Run(plugin, tmp_path, stub.base, **cm_policy(refs=[
        {"dir": str(moved), "sha": REF_SHA, "changed": True}, {"dir": str(still), "sha": HEAD2, "changed": False}]))
    set_head(run.project, SHA)
    out = ctx(run(answer(f"{moved / 'lib' / 'old.py'} {still / 'lib' / 'old.py'}"), event="PostToolUse"))
    assert out == "\n".join([CURRENT, OLD + " " + REF_CHANGED,
                             f"Old code: these files are from a read-only reference (built at {HEAD2[:7]}). "
                             "Never edit them."])
    # the old-code line is about this answer's files: shown each time, without the repeated freshness line
    assert ctx(run(answer(f"{moved / 'lib' / 'old.py'}"), event="PostToolUse")) == OLD + " " + REF_CHANGED


def test_references_without_a_map_listed_once_at_session_start(plugin, tmp_path, stub):  # noqa: F811
    run = Run(plugin, tmp_path, stub.base, code_map_unmapped=["legacy-api", "old\u202e-web"], **cm_policy())
    start = {"hook_event_name": "SessionStart", "source": "startup", "session_id": "s1"}
    line = "These reference projects have no code map yet: legacy-api, old-web."
    assert ctx(run(start, event="SessionStart")) == line
    assert run(dict(start, source="resume"), event="SessionStart") is None  # once per session
    assert ctx(run(dict(start, session_id="s2"), event="SessionStart")) == line
    out = ctx(run(dict(start, source="compact"), event="SessionStart"))  # a compaction forgot it
    assert out.endswith(line)
    assert [r["count"] for r in run.spool() if r.get("nudge") == "code_map_unmapped"] == [2, 2, 2]
    # no list, an empty one, or no code map: nothing
    for i, pol in enumerate(({"code_map_unmapped": []}, {"code_map_unmapped": "x"}, {"code_map": None})):
        r2 = Run(plugin, tmp_path / f"other{i}", stub.base, **{**cm_policy(), "code_map_unmapped": ["a"],
                                                                      **pol})
        assert r2(dict(start, session_id="s9"), event="SessionStart") is None


def test_whole_addition_is_capped_and_redacted(plugin, tmp_path, stub):  # noqa: F811
    tok = "ghp_" + "Z" * 36
    many = [_d(i, f"Rule {i} {tok}", ["src/**"], why="w " * 400) for i in range(1, 30)]
    run = Run(plugin, tmp_path, stub.base, decisions=many, **cm_policy())
    (run.project / "src").mkdir()
    (run.project / "src" / "a.py").write_text("x")
    set_head(run.project, SHA)
    text = ctx(run(answer("src/a.py"), event="PostToolUse"))
    assert len(text) <= 2000 and text.startswith(CURRENT + "\n" + DEC)
    assert tok not in text and "***" in text
    assert 1 <= len(nudges(run)[-1]["decisions"]) <= 3


def test_five_megabyte_response_is_scanned_to_200kb(tmp_path, monkeypatch):
    monkeypatch.setenv("CO_TASK_DIR", str(tmp_path))
    proj = tmp_path / "p"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "late.py").write_text("x")
    set_head(proj, SHA)
    pol = dict(cm_policy(), project_dir=str(proj), decisions=[_d(5, "Late", ["src/late.py"])])
    big = "x" * (300 * 1024) + " src/late.py " + "y " * (5 * 1024 * 1024 // 2)
    assert len(co_hook._response_text([{"type": "text", "text": big}])) <= 200 * 1024
    t0 = time.monotonic()
    out = co_hook._code_map_note(pol, answer(big))
    assert time.monotonic() - t0 < 0.5
    assert out == CURRENT  # the path past 200 KB was never seen
    # a structure is flattened too: a dict, nested lists, a plain string
    for resp in ({"content": [{"text": "src/late.py"}]}, "src/late.py", [[["src/late.py"]]]):
        p = dict(answer(""), tool_response=resp, session_id=repr(resp))
        assert "D-005" in co_hook._code_map_note(pol, p)


def test_ten_thousand_tokens_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("CO_TASK_DIR", str(tmp_path))
    proj = tmp_path / "p"
    proj.mkdir()
    set_head(proj, SHA)
    text = " ".join(f"dir{i}/f{i}.py" for i in range(10000))
    assert len(co_hook._map_tokens(text)) == co_hook.CODE_MAP_TOKENS_MAX
    real = os.path.realpath
    calls = []

    def counting(p, *a, **k):
        calls.append(p)
        return real(p, *a, **k)

    monkeypatch.setattr(co_hook.os.path, "realpath", counting)
    assert co_hook._code_map_note(dict(cm_policy(), project_dir=str(proj)), answer(text)) == CURRENT
    assert len(calls) <= co_hook.CODE_MAP_PATHS_MAX + 1  # the candidates and the project root


def test_tokens_split_on_delimiters_and_line_numbers():
    toks = co_hook._map_tokens('{"file":"src/a.py:12:4", (lib/b.js) [c.ts], d.md. 1.5 hello e.g')
    assert toks[:2] == ["src/a.py", "lib/b.js"] and "c.ts" in toks and "d.md" in toks
    assert "1.5" not in toks and "hello" not in toks and "12" not in toks


# ---- the stdlib HEAD reader matches the app's current_sha_fast ------------------------------------


def _repos(tmp_path):
    branch = tmp_path / "branch"
    set_head(branch, SHA)
    detached = tmp_path / "detached"
    (detached / ".git").mkdir(parents=True)
    (detached / ".git" / "HEAD").write_text(HEAD2 + "\n")
    packed = tmp_path / "packed"
    (packed / ".git").mkdir(parents=True)
    (packed / ".git" / "HEAD").write_text("ref: refs/heads/dev\n")
    (packed / ".git" / "packed-refs").write_text(f"# pack-refs with: peeled\n{REF_SHA} refs/heads/dev\n"
                                                 f"{SHA} refs/heads/main\n")
    bad_ref = tmp_path / "badref"
    (bad_ref / ".git").mkdir(parents=True)
    (bad_ref / ".git" / "HEAD").write_text("ref: refs/../../etc\n")
    not_repo = tmp_path / "plain"
    not_repo.mkdir()
    linked = tmp_path / "linked"
    set_head(linked, SHA)
    (linked / ".git" / "refs" / "heads" / "main").unlink()
    (linked / ".git" / "refs" / "heads" / "main").symlink_to(branch / ".git" / "refs" / "heads" / "main")
    out = [branch, detached, packed, bad_ref, not_repo, linked, tmp_path / "missing"]
    real = tmp_path / "real"
    real.mkdir()
    try:
        env = {"PATH": os.environ.get("PATH", ""), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t"}
        for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "one"], ["pack-refs", "--all"]):
            subprocess.run(["git", "-C", str(real), *args], check=True, env=env, capture_output=True, timeout=30)
        out.append(real)
    except (OSError, subprocess.SubprocessError):
        pass
    return out


def test_head_reader_parity(tmp_path):
    got = []
    for repo in _repos(tmp_path):
        assert co_hook._head_sha(str(repo)) == current_sha_fast(str(repo)), repo
        got.append(co_hook._head_sha(str(repo)))
    assert got[:7] == [SHA, HEAD2, REF_SHA, None, None, None, None]
    assert co_hook._head_sha(None) is None and co_hook._head_sha("") is None


# ---- bounded matching (review round 1) ------------------------------------------------------------

EVIL = "/".join(["**", "*"] * 10) + "/zz"  # 30 wildcards, inside the matcher caps, never matches


def _evil_decisions():
    return [_d(i, f"Evil {i}", [EVIL] * 20) for i in range(1, 201)]


def test_adversarial_patterns_on_deep_paths_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("CO_TASK_DIR", str(tmp_path))
    proj = tmp_path / "p"
    deep = proj.joinpath(*[f"d{i}" for i in range(28)])
    deep.mkdir(parents=True)
    names = []
    for i in range(20):
        (deep / f"f{i}.py").write_text("x")
        names.append(str((deep / f"f{i}.py").relative_to(proj)))
    set_head(proj, SHA)
    pol = dict(cm_policy(), project_dir=str(proj), decisions=_evil_decisions() + [_d(999, "Late", ["**/f0.py"])])
    assert co_hook.matches(EVIL, "a/b") is False and co_hook._glob_parts(EVIL, "x", None) is not None
    t0 = time.monotonic()
    out = co_hook._code_map_note(pol, answer(" ".join(names)))
    elapsed = time.monotonic() - t0
    assert elapsed < 0.5, elapsed
    assert out.startswith(CURRENT)  # the late match was cut off by the bound: no error, no decision
    assert "D-999" not in out
    rec = [r for r in co_hook_spool(tmp_path) if r.get("nudge") == "code_map"]
    assert rec and rec[-1]["partial"] is True  # and the record says some were not checked


def co_hook_spool(tdir):
    path = tdir / "spool.jsonl"
    return [json.loads(ln) for ln in path.read_text().splitlines()] if path.exists() else []


def test_before_edit_note_spools_partial_when_cut_short(tmp_path, monkeypatch):
    monkeypatch.setenv("CO_TASK_DIR", str(tmp_path))
    pol = {"cwd": str(tmp_path), "decisions": [_d(1, "Billing", ["src/billing.py"])] + [
        _d(i, f"Old {i}", ["src/billing.py"]) for i in range(2, 6)]}
    payload = {"tool_name": "Edit", "session_id": "s1", "tool_input": {"file_path": "src/billing.py"}}
    found, partial = co_hook._covering(pol["decisions"], ["src/billing.py"], calls=2)
    assert [f[2] for f in found] == [1, 2] and partial is True
    orig = co_hook._covering

    def tight(decisions, rels):
        return orig(decisions, rels, calls=2)

    monkeypatch.setattr(co_hook, "_covering", tight)
    assert "D-001" in co_hook._decision_notes(pol, payload, "Edit")
    rec = [r for r in co_hook_spool(tmp_path) if r.get("nudge") == "decisions"]
    assert rec[-1]["ids"] == [1, 2] and rec[-1]["partial"] is True
    # not cut short: no partial key
    monkeypatch.setattr(co_hook, "_covering", orig)
    assert co_hook._decision_notes(pol, dict(payload, session_id="s2"), "Edit")
    assert "partial" not in [r for r in co_hook_spool(tmp_path) if r.get("nudge") == "decisions"][-1]


def test_code_map_note_has_no_partial_when_complete(crun):
    crun(answer("src/billing.py"), event="PostToolUse")
    assert "partial" not in nudges(crun)[-1]


def test_ok_index_without_a_commit_says_freshness_unknown(crun):
    """The policy sends sha None for an ``ok`` index with no commit to compare: the line is "freshness
    unknown" and the decisions are still shown."""
    crun.policy["code_map"]["sha"] = None
    (crun.tdir / "policy.json").write_text(json.dumps(crun.policy))
    out = ctx(crun(answer("src/billing.py"), event="PostToolUse"))
    assert out.startswith(UNKNOWN + "\n" + DEC) and "D-012" in out
    assert nudges(crun)[-1]["fresh"] is None
    assert co_hook._code_map_note({**crun.policy, "project_dir": str(crun.project)},
                                  answer("x", session="s9")).startswith(UNKNOWN)


def test_before_edit_note_is_bounded_on_a_deep_path(tmp_path, monkeypatch):
    monkeypatch.setenv("CO_TASK_DIR", str(tmp_path))
    deep = "/".join(f"d{i}" for i in range(200)) + "/f.py"
    pol = {"cwd": str(tmp_path), "decisions": _evil_decisions()}
    payload = {"tool_name": "Edit", "session_id": "s1", "tool_input": {"file_path": deep}}
    t0 = time.monotonic()
    assert co_hook._decision_notes(pol, payload, "Edit") is None
    assert time.monotonic() - t0 < 0.5


def test_matcher_call_budget_and_best_of_touches():
    calls = []
    real = co_hook.specificity

    def counting(t, rel, ci=None):
        calls.append(t)
        return real(t, rel, ci)

    decisions = [_d(i, "x", ["nope/*"] * 20) for i in range(1, 201)]
    try:
        co_hook.specificity = counting
        assert co_hook._covering(decisions, ["a.py", "b.py"]) == ([], True)
        assert len(calls) == co_hook.DECISIONS_MATCH_CALLS
        calls.clear()
        # an exact match ends the decision's scan; touches without a wildcard are tried first
        found, partial = co_hook._covering([_d(1, "x", ["*.py", "b.py", "a.py"])], ["a.py"])
        assert [(f[0], f[2]) for f in found] == [(0, 1)] and calls == ["b.py", "a.py"] and partial is False
        calls.clear()
        # a folder match: the remaining plain touches are tried (one may be exact), the globs are skipped
        found, _ = co_hook._covering([_d(1, "x", ["src/**", "src", "lib"])], ["src/a.py"])
        assert [f[0] for f in found] == [1] and calls == ["src", "lib"]
        calls.clear()
        # only globs: every glob touch is tried, the best (2) counts
        found, _ = co_hook._covering([_d(1, "x", ["*.md", "src/**"])], ["src/a.py"])
        assert [f[0] for f in found] == [2] and calls == ["*.md", "src/**"]
    finally:
        co_hook.specificity = real


def test_exact_touch_after_a_glob_ranks_exact_before_an_edit(tmp_path, monkeypatch):
    """The 4a ranking (the review's repro): a decision whose first touch is a glob and whose second is the
    exact file ranks as exact and is in the top 3, ahead of newer glob-only decisions."""
    monkeypatch.setenv("CO_TASK_DIR", str(tmp_path))
    decisions = [_d(5, "Five", ["src/*.py"]), _d(4, "Four", ["**/*.py"]), _d(3, "Three", ["src/**"]),
                 _d(2, "Two", ["*/billing.py"]), _d(1, "One", ["src/**", "src/billing.py"])]  # newest first
    found, partial = co_hook._covering(decisions, ["src/billing.py"])
    assert found[0][2] == 1 and found[0][0] == 0 and partial is False
    payload = {"tool_name": "Edit", "session_id": "s1", "tool_input": {"file_path": "src/billing.py"}}
    out = co_hook._decision_notes({"cwd": str(tmp_path), "decisions": decisions}, payload, "Edit")
    shown = [ln.split(" ")[0] for ln in out.splitlines()[1:] if ln.startswith("D-")]
    assert shown == ["D-001", "D-005", "D-004"]


def test_paths_deeper_than_the_cap_are_left_out(tmp_path):
    proj = tmp_path / "p"
    deep = proj.joinpath(*[f"d{i}" for i in range(31)])
    deep.mkdir(parents=True)
    (deep / "f.py").write_text("x")
    ok = proj.joinpath(*[f"d{i}" for i in range(29)]) / "g.py"
    ok.write_text("x")
    rels, _ = co_hook._map_paths(f"{(deep / 'f.py').relative_to(proj)} {ok.relative_to(proj)}",
                                 os.path.realpath(proj), [])
    assert rels == [str(ok.relative_to(proj))]


def test_absolute_tokens_elsewhere_never_touch_the_disk(tmp_path, monkeypatch):
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "a.py").write_text("x")
    root = os.path.realpath(proj)
    seen = []
    real = os.path.realpath

    def counting(p, *a, **k):
        seen.append(p)
        return real(p, *a, **k)

    monkeypatch.setattr(co_hook.os.path, "realpath", counting)
    text = "/net/host/a.py /Volumes/x/b.py /etc/passwd " + str(proj / "a.py")
    rels, old = co_hook._map_paths(text, root, [], [os.path.normpath(str(proj)), root])
    assert rels == ["a.py"] and old == []
    assert seen == [str(proj / "a.py")]


def _block(path, start, end):
    import re
    from pathlib import Path

    text = Path(path).read_text()
    m = re.search(rf"^{start}.*?^{end}\n", text, re.S | re.M)
    assert m, path
    return [ln for ln in m.group(0).splitlines() if not ln.startswith("def ")]


def test_head_reader_source_is_a_copy():
    from app.skillpacks import update

    app_src = _block(update.__file__, r"def _read_small\(", r"    return head if _HEX\.match\(head\) else None")
    hook_src = _block(co_hook.__file__, r"def _read_small\(", r"    return head if _SHA\.match\(head\) else None")
    assert [ln.replace("_SHA", "_HEX") for ln in hook_src] == app_src
