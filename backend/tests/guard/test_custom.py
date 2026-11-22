"""co-guard custom rules (custom.py): slugs and ids, reasons, pattern checks,
the repo rules file parser (forge ``scripts/lib/rules.js`` syntax, add-only)
and budgeted matching (one shared SIGALRM budget per call)."""

import ast
import pathlib
import signal
import sys
import threading
import time

import pytest
from conftest import time_limit

from app.guard import custom
from app.guard.custom import (ONLY_RULES, REGEX_BUDGET_MS, clean_reason, match_rules, parse_rules_md, rule_id,
                              slugify, unique_slug, validate_pattern)

SLOW = "(a+)+$"
SLOW_TEXT = "a" * 40 + "b"


# ---------------------------------------------------------------------------
# slugs and ids
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,slug", [
    ("terraform destroy", "terraform-destroy"),
    ("  Drop DATABASE production!! ", "drop-database-production"),
    ("re:kubectl\\s+delete\\s+(ns|namespace)", "re-kubectl-s-delete-s-ns-namespace"),
    ("rm -rf /", "rm-rf"),
    ("---", "rule"),
    ("", "rule"),
    ("日本語", "rule"),
    ("café_crème", "caf-cr-me"),
    ("x" * 60, "x" * 40),
    ("a" * 39 + " b", "a" * 39),  # cut at 40, then trimmed again
    ("UPPER lower 123", "upper-lower-123"),
])
def test_slugify(text, slug):
    assert slugify(text) == slug
    assert len(slugify(text)) <= 40


def test_unique_slug_collisions():
    assert unique_slug("terraform destroy", set()) == "terraform-destroy"
    taken = {"terraform-destroy"}
    assert unique_slug("Terraform  Destroy", taken) == "terraform-destroy-2"
    taken |= {"terraform-destroy-2", "terraform-destroy-3"}
    assert unique_slug("terraform destroy", taken) == "terraform-destroy-4"
    long = "y" * 40
    got = unique_slug(long, {long})
    assert got == "y" * 38 + "-2" and len(got) <= 40
    assert unique_slug("", {"rule"}) == "rule-2"


def test_rule_id_prefixes():
    assert rule_id("org", "terraform-destroy") == "org.terraform-destroy"
    assert rule_id("project", "x") == "project.x"
    assert rule_id("repo", "rule-2") == "repo.rule-2"
    for bad_scope in ("user", "ORG", "", "org."):
        with pytest.raises(ValueError):
            rule_id(bad_scope, "x")
    for bad_slug in ("", "A", "a.b", "-x", "x" * 41, "x y"):
        with pytest.raises(ValueError):
            rule_id("org", bad_slug)


def test_builtin_ids_never_contain_a_dot():
    from app.guard.rules import DISABLEABLE, RULES
    assert all("." not in r for r in RULES)
    assert DISABLEABLE <= set(RULES)


# ---------------------------------------------------------------------------
# reasons
# ---------------------------------------------------------------------------
def test_clean_reason_strips_bidi_zero_width_and_controls():
    raw = "dele​te‮ the⁦ world﻿⁠‏‪ now\x00\x1b[31m\x7f\x85"
    assert clean_reason(raw) == "delete the world now [31m"
    assert clean_reason("line one\nline two\ttab") == "line one line two tab"
    assert clean_reason("  padded  ") == "padded"
    assert clean_reason(None) == ""
    assert clean_reason(42) == "42"


def test_clean_reason_cuts_to_200_after_stripping():
    assert clean_reason("​" * 300 + "x" * 199) == "x" * 199
    assert len(clean_reason("y" * 500)) == 200


# ---------------------------------------------------------------------------
# patterns
# ---------------------------------------------------------------------------
def test_validate_pattern():
    assert validate_pattern("text", "terraform destroy") is None
    assert validate_pattern("regex", r"kubectl\s+delete") is None
    assert validate_pattern("text", "x" * 500) is None
    assert validate_pattern("regex", "x" * 500) is None
    assert "500" in validate_pattern("text", "x" * 501)
    assert "500" in validate_pattern("regex", "x" * 501)
    assert "regular expression" in validate_pattern("regex", "(unclosed")
    assert "regular expression" in validate_pattern("regex", "*bad")
    assert validate_pattern("text", "(unclosed") is None  # text is never compiled
    for empty in ("", "   ", None, 5):
        assert validate_pattern("text", empty) is not None
        assert validate_pattern("regex", empty) is not None
    assert validate_pattern("glob", "x") is not None


# ---------------------------------------------------------------------------
# the repo rules file
# ---------------------------------------------------------------------------
GOOD = """# Rules for this project

Lines under "Confirm" need approval.
Example: - `terraform destroy` : destroys cloud infrastructure

## Confirm
- `terraform destroy` : destroys cloud infrastructure
* `kubectl delete` :
- `helm uninstall`
- `re:kubectl\\s+delete\\s+(ns|namespace)` : deletes a whole namespace

## Never
  - `drop database production` : production database ‮
- `re:^git push .*--mirror` : mirrors
"""


def test_parse_good_lines():
    out = parse_rules_md(GOOD)
    assert out["errors"] == []
    assert out["confirm"] == [
        {"match": "text", "pattern": "terraform destroy", "reason": "destroys cloud infrastructure", "line": 7},
        {"match": "text", "pattern": "kubectl delete", "reason": "", "line": 8},
        {"match": "text", "pattern": "helm uninstall", "reason": "", "line": 9},
        {"match": "regex", "pattern": r"kubectl\s+delete\s+(ns|namespace)", "reason": "deletes a whole namespace",
         "line": 10},
    ]
    assert out["never"] == [
        {"match": "text", "pattern": "drop database production", "reason": "production database", "line": 13},
        {"match": "regex", "pattern": "^git push .*--mirror", "reason": "mirrors", "line": 14},
    ]


def test_parse_crlf_and_heading_case():
    out = parse_rules_md("## CONFIRM\r\n- `a b` : r\r\n##   never  \r\n- `c`\r\n")
    assert [r["pattern"] for r in out["confirm"]] == ["a b"]
    assert [(r["pattern"], r["line"]) for r in out["never"]] == [("c", 4)]


def test_parse_malformed_lines_with_line_numbers():
    text = "\n".join([
        "## Confirm",               # 1
        "- terraform destroy",      # 2 no backticks
        "- `` : empty",             # 3
        "- `re:(unclosed` : bad",   # 4
        "- `" + "x" * 501 + "`",    # 5 too long
        "- `ok` : fine",            # 6
        "- `re:` : empty regex",    # 7
        "- `a` trailing junk",      # 8
        "plain prose is skipped",   # 9
    ])
    out = parse_rules_md(text)
    assert [r["pattern"] for r in out["confirm"]] == ["ok"]
    lines = [e["line"] for e in out["errors"]]
    assert lines == [2, 3, 4, 5, 7, 8]
    msgs = {e["line"]: e["message"] for e in out["errors"]}
    assert "expected" in msgs[2]
    assert "regular expression" in msgs[4]
    assert "500" in msgs[5]
    assert all(set(e) == {"line", "message"} for e in out["errors"])


def test_parse_reports_other_sections_and_relaxing_lines():
    text = "\n".join([
        "- `before any heading`",             # 1
        "## Settings",                        # 2
        "- notes: off",                       # 3
        "- disable: git-reset-hard",          # 4
        "- database dev: host:*.prod",        # 5
        "## Sources",                         # 6
        "- readonly: /etc : system",          # 7
        "## Other",                           # 8
        "* `terraform destroy` : looks real", # 9
        "## Confirm",                         # 10
        "- disable: rm-rf-outside",           # 11
        "- database dev: prod-db",            # 12
        "- `fine` : ok",                      # 13
        "### Subheading stays in Confirm",    # 14
        "- `also fine`",                      # 15
    ])
    out = parse_rules_md(text)
    assert [r["pattern"] for r in out["confirm"]] == ["fine", "also fine"]
    assert out["never"] == []
    msgs = {e["line"]: e["message"] for e in out["errors"]}
    assert sorted(msgs) == [1, 3, 4, 5, 7, 9, 11, 12]
    for n in (1, 3, 7, 9):
        assert msgs[n] == ONLY_RULES
    assert ONLY_RULES == "a repo rules file can only add Confirm/Never rules"
    for n in (4, 11):
        assert "switch off" in msgs[n]
    for n in (5, 12):
        assert "dev" in msgs[n]


def test_parse_caps_at_200_rules_and_reports_the_rest():
    lines = ["## Confirm"] + [f"- `rule {i}` : r" for i in range(150)]
    lines += ["## Never"] + [f"- `never {i}`" for i in range(60)]
    out = parse_rules_md("\n".join(lines))
    assert len(out["confirm"]) == 150 and len(out["never"]) == 50
    assert len(out["errors"]) == 10
    assert all("200" in e["message"] for e in out["errors"])
    assert out["errors"][0]["line"] == 1 + 150 + 1 + 51


def test_parse_rejects_files_over_64_kb():
    head = "## Confirm\n- `ok`\n"
    exact = head + "#" * (65536 - len(head.encode()))
    assert len(exact.encode()) == 65536
    assert [r["pattern"] for r in parse_rules_md(exact)["confirm"]] == ["ok"]
    out = parse_rules_md(exact + "#")
    assert out["confirm"] == [] and out["never"] == []
    assert len(out["errors"]) == 1 and "65536" in out["errors"][0]["message"]
    multibyte = head + "é" * 32768  # 2 bytes each: over the byte limit, under in chars
    assert parse_rules_md(multibyte)["confirm"] == []


def test_parse_empty_and_non_text():
    for text in ("", None):
        assert parse_rules_md(text) == {"confirm": [], "never": [], "errors": []}


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------
def R(rid, pattern, match="text", kind="confirm"):
    return {"id": rid, "kind": kind, "match": match, "pattern": pattern, "reason": ""}


def test_text_rules_are_case_insensitive_substrings():
    rules = [R("org.tf", "Terraform DESTROY"), R("org.other", "kubectl")]
    got = match_rules("cd infra && terraform destroy -auto-approve", rules, REGEX_BUDGET_MS)
    assert got == [(rules[0], "match")]
    assert match_rules("TERRAFORM destroy", rules, 250) == [(rules[0], "match")]
    assert match_rules("terraform plan", rules, 250) == []


def test_regex_rules_are_case_insensitive():
    rules = [R("org.ns", r"kubectl\s+delete\s+(ns|namespace)", "regex")]
    assert match_rules("KUBECTL   DELETE NS prod", rules, 250) == [(rules[0], "match")]
    assert match_rules("kubectl get ns", rules, 250) == []


def test_never_rules_are_checked_first():
    c, n = R("org.c", "x"), R("org.n", "x", kind="never")
    assert match_rules("x", [c, n], 250) == [(n, "match"), (c, "match")]


def test_budget_timeout_counts_as_timeout_and_is_shared():
    slow = R("repo.slow", SLOW, "regex", kind="never")
    later = R("repo.later", "b$", "regex")
    text_rule = R("repo.txt", "aaa")
    with time_limit(3.0):  # load-tolerant bound: wall or CPU time
        got = match_rules(SLOW_TEXT, [slow, later, text_rule], 100)
    verdicts = {r["id"]: v for r, v in got}
    assert verdicts["repo.slow"] == "timeout"
    assert verdicts["repo.later"] == "timeout"  # the budget is shared: nothing left for it
    assert verdicts["repo.txt"] == "match"  # text rules never time out


def test_zero_budget_times_out_every_regex():
    rules = [R("org.r", "a", "regex"), R("org.t", "a")]
    assert match_rules("a", rules, 0) == [(rules[0], "timeout"), (rules[1], "match")]


def test_no_regexes_never_touch_the_signal():
    def sentinel(*_a):
        pass

    old = signal.signal(signal.SIGALRM, sentinel)
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(custom.signal, "setitimer", lambda *a: pytest.fail("setitimer called"))
            assert match_rules("abc", [R("org.t", "b")], 250) == [({"id": "org.t", "kind": "confirm",
                                                                   "match": "text", "pattern": "b",
                                                                   "reason": ""}, "match")]
    finally:
        signal.signal(signal.SIGALRM, old)


def test_previous_handler_and_timer_are_restored():
    fired = []

    def sentinel(*_a):
        fired.append(1)

    old = signal.signal(signal.SIGALRM, sentinel)
    try:
        signal.setitimer(signal.ITIMER_REAL, 30.0, 0.0)
        match_rules(SLOW_TEXT, [R("repo.slow", SLOW, "regex")], 50)
        assert signal.getsignal(signal.SIGALRM) is sentinel
        left, interval = signal.getitimer(signal.ITIMER_REAL)
        assert 29.0 < left <= 30.0 and interval == 0.0
        match_rules("x", [R("repo.fast", "x", "regex")], 250)
        assert signal.getsignal(signal.SIGALRM) is sentinel
        assert 29.0 < signal.getitimer(signal.ITIMER_REAL)[0] <= 30.0
        assert fired == []
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def test_no_previous_timer_leaves_none_running():
    old = signal.getsignal(signal.SIGALRM)
    match_rules(SLOW_TEXT, [R("repo.slow", SLOW, "regex")], 30)
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    assert signal.getsignal(signal.SIGALRM) == old
    match_rules("x", [R("repo.fast", "x", "regex")], 250)
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    assert signal.getsignal(signal.SIGALRM) == old


def test_an_earlier_timer_due_inside_the_budget_still_fires():
    fired = []
    old = signal.signal(signal.SIGALRM, lambda *_a: fired.append(1))
    try:
        signal.setitimer(signal.ITIMER_REAL, 0.02, 0.0)
        got = match_rules(SLOW_TEXT, [R("repo.slow", SLOW, "regex")], 250)
        assert got[0][1] == "timeout"  # cut short by the earlier timer: fail safe
        deadline = time.monotonic() + 1
        while not fired and time.monotonic() < deadline:
            time.sleep(0.01)
        assert fired == [1]
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def test_off_the_main_thread_every_regex_times_out():
    rules = [R("org.r", "abc", "regex", kind="never"), R("org.t", "abc")]
    out = []
    t = threading.Thread(target=lambda: out.append(match_rules("abc", rules, 250)))
    t.start()
    t.join(5)
    assert out == [[(rules[0], "timeout"), (rules[1], "match")]]


def test_regexes_compile_once_per_call(monkeypatch):
    calls = []
    real = custom.re.compile

    def counting(p, flags=0):
        calls.append(p)
        return real(p, flags)

    monkeypatch.setattr(custom.re, "compile", counting)
    rules = [R("org.a", "a+", "regex"), R("org.b", "b+", "regex")]
    match_rules("ab", rules, 250)
    assert calls.count("a+") == 1 and calls.count("b+") == 1


# ---------------------------------------------------------------------------
# policy entries
# ---------------------------------------------------------------------------
def test_policy_rules_accepts_good_entries():
    entries = [{"id": "org.tf", "kind": "never", "match": "text", "pattern": "terraform destroy", "reason": "x"},
               {"id": "repo.re-2", "kind": "confirm", "match": "regex", "pattern": "a+", "reason": ""}]
    assert custom.policy_rules(entries) == entries
    assert custom.policy_rules([]) == []


@pytest.mark.parametrize("value", [
    "x", {"id": "org.x"}, 5,
    [5],
    [{"id": "x", "kind": "never", "match": "text", "pattern": "a", "reason": ""}],        # no scope
    [{"id": "rm-rf-outside", "kind": "never", "match": "text", "pattern": "a", "reason": ""}],
    [{"id": "user.x", "kind": "never", "match": "text", "pattern": "a", "reason": ""}],
    [{"id": "org.x", "kind": "maybe", "match": "text", "pattern": "a", "reason": ""}],
    [{"id": "org.x", "kind": "never", "match": "glob", "pattern": "a", "reason": ""}],
    [{"id": "org.x", "kind": "never", "match": "regex", "pattern": "(", "reason": ""}],
    [{"id": "org.x", "kind": "never", "match": "text", "pattern": "", "reason": ""}],
    [{"id": "org.x", "kind": "never", "match": "text", "pattern": "a" * 501, "reason": ""}],
    [{"id": "org.x", "kind": "never", "match": "text", "pattern": "a", "reason": 5}],
    [{"id": "org.x", "kind": "never", "match": "text", "pattern": "a"}],
])
def test_policy_rules_rejects_malformed(value):
    with pytest.raises(ValueError):
        custom.policy_rules(value)


# ---------------------------------------------------------------------------
# R1: stdlib only
# ---------------------------------------------------------------------------
def test_custom_is_stdlib_only():
    src = pathlib.Path(custom.__file__).read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative imports"
            names = [node.module]
        else:
            continue
        for n in names:
            assert n.split(".")[0] in sys.stdlib_module_names or n == "__future__", n


# ===========================================================================
# Fix round 1 (review I1-I3, M2, M3; Rulings P11, P12)
# ===========================================================================
def test_p11_org_rules_run_before_repo_rules_whatever_the_list_order():
    slow = R("repo.slow", SLOW, "regex", kind="never")
    org = R("org.drop", r"drop\s+table", "regex", kind="never")
    got = match_rules("psql -c 'drop table x' " + SLOW_TEXT, [slow, org], 250)
    assert got == [(org, "match"), (slow, "timeout")]


def test_p11_each_source_has_its_own_budget():
    org_slow = R("org.slow", SLOW, "regex", kind="never")
    proj = R("project.b", "b$", "regex")
    repo_fast = R("repo.fast", "b$", "regex")
    repo_slow = R("repo.slow", SLOW, "regex")
    with time_limit(3.0):  # load-tolerant bound: wall or CPU time
        got = {r["id"]: v for r, v in match_rules(SLOW_TEXT, [repo_fast, repo_slow, org_slow, proj], 60)}
    # org + project share one budget (spent by org.slow); the repo budget is separate
    assert got == {"org.slow": "timeout", "project.b": "timeout", "repo.fast": "match", "repo.slow": "timeout"}


def test_p12_a_rule_hits_when_any_text_matches():
    rules = [R("org.t", "git push"), R("org.r", r"^git\s+push$", "regex")]
    got = match_rules(['"git" push', "git push"], rules, 250)
    assert [(r["id"], v) for r, v in got] == [("org.t", "match"), ("org.r", "match")]
    assert match_rules(["git pull", "ls"], rules, 250) == []


def test_values_text_is_forge_mcp_text():
    v = {"b": ["x", {"c": 1, "d": None}], "a": True, "e": "multi\nline"}
    assert custom.values_text(v) == "x\n1\ntrue\nmulti\nline"
    assert custom.values_text({"s": "delete\n  from\tusers"}, collapse=True) == "delete from users"
    deep = cur = {}
    for _ in range(150):
        cur["k"] = {}
        cur = cur["k"]
    cur["v"] = "hidden"
    assert "hidden" not in custom.values_text(deep)  # past 100 levels: not walked (the JSON form still has it)


def test_i3_sigalrm_blocked_in_the_mask_is_unblocked_then_restored():
    old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, [signal.SIGALRM])
    try:
        with time_limit(3.0):  # load-tolerant bound: wall or CPU time
            got = match_rules(SLOW_TEXT, [R("repo.slow", SLOW, "regex", kind="never")], 50)
        assert got[0][1] == "timeout"
        assert signal.SIGALRM in signal.pthread_sigmask(signal.SIG_BLOCK, [])  # still blocked afterwards
        assert signal.SIGALRM not in signal.sigpending()
        assert match_rules("abc", [R("org.r", "b", "regex")], 250)[0][1] == "match"
        assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)


def test_i3_a_pending_blocked_alarm_is_left_alone_and_every_regex_times_out():
    import threading
    fired = []
    old_handler = signal.signal(signal.SIGALRM, lambda *_a: fired.append(1))
    old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, [signal.SIGALRM])
    try:
        # thread-directed: with other threads alive (a live server from an earlier
        # test), a process-directed signal goes to a thread that does not block it
        signal.pthread_kill(threading.main_thread().ident, signal.SIGALRM)
        assert signal.SIGALRM in signal.sigpending()
        got = match_rules("abc", [R("org.r", "b", "regex"), R("org.t", "b")], 250)
        assert [v for _r, v in got] == ["timeout", "match"]
        assert signal.SIGALRM in signal.sigpending() and fired == []
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
        signal.signal(signal.SIGALRM, old_handler)
    assert fired == [1]  # the owner's alarm reached the owner's handler


@pytest.mark.parametrize("ch", ["؜", "­", "᠎", "͏", "⁦", "⁯", "\U000e0001"])
def test_m2_clean_reason_strips_format_characters(ch):
    assert clean_reason(f"de{ch}lete{ch} it") == "delete it"


@pytest.mark.parametrize("ch", ["​", "‍", "‮", "⁦", "﻿", "­", "؜", "͏",
                                "᠎"])
def test_m3_patterns_with_invisible_characters_are_rejected(ch):
    for match in ("text", "regex"):
        assert "invisible" in validate_pattern(match, f"git{ch} push")
    out = parse_rules_md(f"## Never\n- `git{ch} push` : x\n")
    assert out["never"] == [] and out["errors"][0]["line"] == 2


def test_m3_bom_and_cr_only_line_endings():
    out = parse_rules_md("﻿## Never\n- `rm -rf`\n")
    assert [r["pattern"] for r in out["never"]] == ["rm -rf"]
    out = parse_rules_md("## Never\r- `x` : y\r## Confirm\r- `z`\r")
    assert [(r["pattern"], r["line"]) for r in out["never"]] == [("x", 2)]
    assert [(r["pattern"], r["line"]) for r in out["confirm"]] == [("z", 4)]
