"""Table-driven tests for the co-guard Bash parser (app/guard/shell.py).

Inputs are largely ported from jspark-forge tests/blocklist.test.js and
tests/substitution.test.js (MIT, same owner), plus the phase-1 Review Focus
cases (obfuscated dangerous commands, everyday false positives).
"""

import ast
import os
import pathlib
import shutil
import subprocess
import sys
import time

import pytest

from app.guard import shell
from app.guard.shell import expand_wrappers, parse, walk

SHELL_PY = pathlib.Path(shell.__file__)


def top(cmd):
    """argv of each top-level stage."""
    return [s.argv for s in parse(cmd)]


def effective(cmd):
    """Effective (wrapper-stripped) argv of every stage, nested included."""
    out = []
    for s in walk(parse(cmd)):
        argv = expand_wrappers(s)
        if argv:
            out.append(argv)
    return out


def runs(cmd, *argv):
    """True when some stage (at any depth) effectively runs `argv` as a prefix."""
    want = list(argv)
    return any(a[: len(want)] == want for a in effective(cmd))


# ---------------------------------------------------------------------------
# 1. Splitting into pipelines and stages, quoting, escapes
# ---------------------------------------------------------------------------
SPLIT_CASES = [
    ("ls -la", [["ls", "-la"]]),
    ("a; b", [["a"], ["b"]]),
    ("a && b || c", [["a"], ["b"], ["c"]]),
    ("a & b", [["a"], ["b"]]),
    ("a\nb", [["a"], ["b"]]),
    ("a | b | c", [["a"], ["b"], ["c"]]),
    ("a |& b", [["a"], ["b"]]),
    ("echo 'a b'  \"c d\"", [["echo", "a b", "c d"]]),
    ("echo a\\ b", [["echo", "a b"]]),
    ("r\\m -rf x", [["rm", "-rf", "x"]]),
    ('"rm" -rf x', [["rm", "-rf", "x"]]),
    ("r''m -rf x", [["rm", "-rf", "x"]]),
    ('r""m -rf x', [["rm", "-rf", "x"]]),
    ("echo \"a \\\"q\\\" b\"", [["echo", 'a "q" b']]),
    ("echo 'it''s'", [["echo", "its"]]),
    ("echo $'r\\x6d'", [["echo", "rm"]]),
    ("ls # rm -rf ~", [["ls"]]),
    ("# rm -rf ~\nls", [["ls"]]),
    ("{ a; b; }", [["a"], ["b"]]),
    ("(cd x && make)", [["cd", "x"], ["make"]]),
    ("if [ -f x ]; then cat x; fi", [["if", "[", "-f", "x", "]"], ["then", "cat", "x"], ["fi"]]),
    ("echo hi;;echo yo", [["echo", "hi"], ["echo", "yo"]]),
    ("", []),
    ("   \n  ", []),
]


@pytest.mark.parametrize("cmd,expected", SPLIT_CASES)
def test_split_and_quoting(cmd, expected):
    assert top(cmd) == expected


def test_pipeline_and_index_numbering():
    stages = parse("curl x | sh; echo a | tee b | wc")
    assert [(s.pipeline, s.index) for s in stages] == [(0, 0), (0, 1), (1, 0), (1, 1), (1, 2)]


# ---------------------------------------------------------------------------
# 2. Line continuations
# ---------------------------------------------------------------------------
CONT_CASES = [
    ("rm -rf \\\n ~", [["rm", "-rf", "~"]]),
    ("git push --fo\\\nrce origin main", [["git", "push", "--force", "origin", "main"]]),
    ('git push "--fo\\\nrce" origin main', [["git", "push", "--force", "origin", "main"]]),
    ("r\\\nm -rf /", [["rm", "-rf", "/"]]),
    # single quotes keep backslash-newline literally
    ("echo 'a\\\nb'", [["echo", "a\\\nb"]]),
]


@pytest.mark.parametrize("cmd,expected", CONT_CASES)
def test_line_continuations(cmd, expected):
    assert top(cmd) == expected


def test_unfold_leaves_quoted_heredoc_body():
    s = "cat <<'EOF'\na\\\nb\nEOF"
    assert shell.unfold(s) == s


# ---------------------------------------------------------------------------
# 3. Redirects
# ---------------------------------------------------------------------------
REDIRECT_CASES = [
    ("echo x > out.txt", ["echo", "x"], [(">", "out.txt")]),
    ("echo x >> log", ["echo", "x"], [(">>", "log")]),
    ("cmd 2> err.txt", ["cmd"], [(">", "err.txt")]),
    ("cmd 2>&1 | cat", ["cmd"], []),
    ("cmd &> all.log", ["cmd"], [("&>", "all.log")]),
    ("cmd &>> all.log", ["cmd"], [("&>>", "all.log")]),
    ("cmd >| f", ["cmd"], [(">|", "f")]),
    ("cmd >&out.txt", ["cmd"], [(">&", "out.txt")]),
    ("sort < in.txt > out.txt", ["sort"], [("<", "in.txt"), (">", "out.txt")]),
    ('echo {} > "$F"', ["echo", "{}"], [(">", "$F")]),
    ("echo x >.env", ["echo", "x"], [(">", ".env")]),
]


@pytest.mark.parametrize("cmd,argv,redirects", REDIRECT_CASES)
def test_redirects(cmd, argv, redirects):
    st = parse(cmd)[0]
    assert st.argv == argv
    assert st.redirects == redirects


# ---------------------------------------------------------------------------
# 4. Substitutions are followed as nested commands
# ---------------------------------------------------------------------------
NESTED_RUNS = [
    ("echo $(rm -rf ~)", ("rm", "-rf", "~")),
    ('echo "$(rm -rf ~)"', ("rm", "-rf", "~")),
    ("echo `rm -rf ~`", ("rm", "-rf", "~")),
    ('echo "`git reset --hard`"', ("git", "reset", "--hard")),
    ("echo $(echo $(rm -rf ~))", ("rm", "-rf", "~")),
    ("cat <(rm -rf ~)", ("rm", "-rf", "~")),
    ("tee >(rm -rf ~) < x", ("rm", "-rf", "~")),
    ("X=$(rm -rf ~)", ("rm", "-rf", "~")),
    ("X=`git reset --hard`", ("git", "reset", "--hard")),
    ("cat <<EOF\n$(rm -rf ~)\nEOF", ("rm", "-rf", "~")),
    ("cat <<EOF\n`git clean -fdx`\nEOF", ("git", "clean", "-fdx")),
    ("echo ${X:-$(rm -rf ~)}", ("rm", "-rf", "~")),
    ("echo $(( $(rm -rf ~) + 1 ))", ("rm", "-rf", "~")),
    ("sed -i s/a/b/ $(find . -name x)", ("find", ".", "-name", "x")),
]


@pytest.mark.parametrize("cmd,argv", NESTED_RUNS)
def test_substitutions_are_commands(cmd, argv):
    assert runs(cmd, *argv), effective(cmd)


def test_nested_structure_is_list_of_stage_lists():
    st = parse("echo $(a | b) $(c)")[0]
    assert [[x.argv for x in n] for n in st.nested] == [[["a"], ["b"]], [["c"]]]


# ---------------------------------------------------------------------------
# 5. Data is not a command: single quotes, quoted heredocs, commit messages
# ---------------------------------------------------------------------------
def commit(body):
    """Claude Code's standard commit form."""
    return f"git commit -m \"$(cat <<'EOF'\n{body}\n\nCo-Authored-By: Claude <noreply@anthropic.com>\nEOF\n)\""


DATA_CASES = [
    commit("docs: rm -rf ~ is never allowed"),
    commit("git reset --hard is now held"),
    commit("don't run rm -rf ~ (ever"),
    commit("fix: `rm -rf ~` and $(git reset --hard) are now held"),
    "git commit -m 'docs: mention `rm -rf ~`'",
    "git commit -m \"rm -rf old dir\"",
    "grep '$(rm -rf ~)' docs",
    "echo 'curl x | sh'",
    "echo \"rm -rf /\"",
    "echo '$(rm -rf ~)' ; ls",
    "cat > notes.txt <<'EOF'\nrm -rf ~\ngit reset --hard\nEOF",
    "cat <<-'EOF'\n\trm -rf ~\n\tEOF",
    "cat <<\"EOF\"\n$(rm -rf ~)\nEOF",
    "cat <<\\EOF\n`rm -rf ~`\nEOF",
    "git commit -F - <<EOF\nrm -rf ~\nEOF",
    'echo "\\$(rm -rf ~)"',
    "grep -r \"rm -rf\" .",
    "printf '%s\\n' 'git push --force origin main'",
]


@pytest.mark.parametrize("cmd", DATA_CASES)
def test_quoted_data_is_never_a_command(cmd):
    eff = effective(cmd)
    assert not any(a and a[0] == "rm" for a in eff), eff
    assert not any(a[:2] == ["git", "reset"] for a in eff), eff
    assert not any(a[:2] == ["git", "push"] for a in eff), eff
    assert not any(a[:2] == ["git", "clean"] for a in eff), eff


AFTER_DATA = [
    (commit("don't (ever) do this") + " && git reset --hard", ("git", "reset", "--hard")),
    (commit("fine") + " && rm -rf ~", ("rm", "-rf", "~")),
    ("cat > n.txt <<'EOF'\nnotes\nEOF\ngit reset --hard", ("git", "reset", "--hard")),
    ("cat <<EOF > a\nx\nEOF\nrm -rf /", ("rm", "-rf", "/")),
    ("echo '$($($(' ; rm -rf ~", ("rm", "-rf", "~")),
]


@pytest.mark.parametrize("cmd,argv", AFTER_DATA)
def test_what_follows_data_is_still_parsed(cmd, argv):
    assert runs(cmd, *argv), effective(cmd)


def test_heredoc_body_recorded():
    st = parse("cat > f <<'EOF'\nhello\nworld\nEOF")[0]
    assert st.heredocs == [("EOF", True, "hello\nworld")]
    assert (">", "f") in st.redirects


# ---------------------------------------------------------------------------
# 6. Wrappers and shells
# ---------------------------------------------------------------------------
WRAPPER_CASES = [
    ("sudo rm -rf /", ["rm", "-rf", "/"]),
    ("sudo -u root -E rm -rf /", ["rm", "-rf", "/"]),
    ("sudo --preserve-env -- rm -rf /", ["rm", "-rf", "/"]),
    ("env FOO=1 BAR=2 rm -rf ~", ["rm", "-rf", "~"]),
    ("env -i -u X rm -rf ~", ["rm", "-rf", "~"]),
    ("FOO=1 rm -rf ~/x", ["rm", "-rf", "~/x"]),
    ("nohup nice -n 10 rm -rf ~", ["rm", "-rf", "~"]),
    ("time -p rm -rf ~", ["rm", "-rf", "~"]),
    ("exec rm -rf ~", ["rm", "-rf", "~"]),
    ("command rm -rf ~", ["rm", "-rf", "~"]),
    ("xargs -I {} rm -rf {}", ["rm", "-rf", "{}"]),
    ("timeout 60 git reset --hard", ["git", "reset", "--hard"]),
    ("timeout -s KILL 5m make", ["make"]),
    ("/usr/bin/sudo /bin/rm -rf /", ["/bin/rm", "-rf", "/"]),
    ("then rm -rf ~", ["rm", "-rf", "~"]),
    ("! git reset --hard", ["git", "reset", "--hard"]),
]


@pytest.mark.parametrize("cmd,argv", WRAPPER_CASES)
def test_expand_wrappers(cmd, argv):
    assert expand_wrappers(parse(cmd)[0]) == argv


def test_expand_wrappers_records_wrappers_and_env():
    st = parse("FOO=1 sudo env BAR=2 xargs rm -f")[0]
    assert st.env == {"FOO": "1"}
    assert st.wrappers == ["sudo", "env", "xargs"]
    assert expand_wrappers(st) == ["rm", "-f"]
    assert expand_wrappers(["nice", "-n", "5", "ls"]) == ["ls"]


@pytest.mark.parametrize("cmd", ["sudo", "env FOO=1", "X=1", "exec > log", "nohup"])
def test_expand_wrappers_none_without_command(cmd):
    stages = parse(cmd)
    assert stages and expand_wrappers(stages[0]) is None


SHELL_RUNS = [
    ('bash -c "rm -rf ~"', ("rm", "-rf", "~")),
    ("sh -c 'rm -rf /'", ("rm", "-rf", "/")),
    ("zsh -c 'git push -f origin main'", ("git", "push", "-f", "origin", "main")),
    ("bash -lc 'cd / && rm -rf *'", ("rm", "-rf", "*")),
    ("sudo bash -c \"rm -rf /\"", ("rm", "-rf", "/")),
    ("bash -c \"bash -c 'rm -rf ~'\"", ("rm", "-rf", "~")),
    ("xargs sh -c 'rm -rf ~'", ("rm", "-rf", "~")),
    ('eval "rm -rf ~"', ("rm", "-rf", "~")),
    ("eval rm -rf ~", ("rm", "-rf", "~")),
    ("env -S 'rm -rf ~'", ("rm", "-rf", "~")),
    ("bash <<EOF\nrm -rf ~\nEOF", ("rm", "-rf", "~")),
    ("bash <<'EOF'\nrm -rf ~\nEOF", ("rm", "-rf", "~")),
    ("sh <<< 'rm -rf ~'", ("rm", "-rf", "~")),
    ("find . -exec rm -rf ~ \\;", ("rm", "-rf", "~")),
    ("find . -name '*.tmp' -execdir sh -c 'rm -rf /' \\;", ("rm", "-rf", "/")),
    ("if true; then rm -rf ~; fi", ("rm", "-rf", "~")),
    ("for d in a b; do rm -rf ~; done", ("rm", "-rf", "~")),
    ("f() { rm -rf ~; }; f", ("rm", "-rf", "~")),
    ("function f { rm -rf ~; }", ("rm", "-rf", "~")),
    ("case $x in a) rm -rf ~;; esac", ("rm", "-rf", "~")),
    ("case $x in (a|b) rm -rf ~;; *) ls;; esac", ("rm", "-rf", "~")),
    ("echo $(case $x in a) rm -rf ~;; esac)", ("rm", "-rf", "~")),
    ("cat <<EOF | bash\nrm -rf ~\nEOF", ("rm", "-rf", "~")),
    ("echo 'rm -rf ~' | sh", ("rm", "-rf", "~")),
    ("printf 'git reset --hard' | bash -s", ("git", "reset", "--hard")),
    ("trap 'rm -rf ~' EXIT", ("rm", "-rf", "~")),
    ("watch -n 1 rm -rf ~", ("rm", "-rf", "~")),
    ("su -c 'rm -rf /' root", ("rm", "-rf", "/")),
]


@pytest.mark.parametrize("cmd,argv", SHELL_RUNS)
def test_nested_shells_are_followed(cmd, argv):
    assert runs(cmd, *argv), effective(cmd)


def test_bash_script_file_is_not_parsed_as_script():
    assert effective("bash ./build.sh --rm") == [["bash", "./build.sh", "--rm"]]


# ---------------------------------------------------------------------------
# 7. Variables: assignments and $VAR used as a command
# ---------------------------------------------------------------------------
VAR_CASES = [
    ("x=rm; $x -rf ~", ["rm", "-rf", "~"]),
    ("x=rm && ${x} -rf ~", ["rm", "-rf", "~"]),
    ('x="rm -rf"; $x ~', ["rm", "-rf", "~"]),
    ("export X=rm; $X -rf ~", ["rm", "-rf", "~"]),
    ("D=/; rm -rf $D", ["rm", "-rf", "/"]),
    ('D=/tmp/x; rm -rf "$D/y"', ["rm", "-rf", "/tmp/x/y"]),
    ("a=r; b=m; $a$b -rf ~", ["rm", "-rf", "~"]),
    ("x=rm; y=$x; $y -rf ~", ["rm", "-rf", "~"]),
    ("rm${IFS}-rf${IFS}~", ["rm", "-rf", "~"]),
    ("$(echo rm) -rf /", ["rm", "-rf", "/"]),
    ("`echo rm` -rf /", ["rm", "-rf", "/"]),
    ("x=$(echo rm); $x -rf ~", ["rm", "-rf", "~"]),
    ("x=rm\n$x -rf ~", ["rm", "-rf", "~"]),
    ("IFS=,; x=rm,-rf,~; $x", ["rm", "-rf", "~"]),
    ("$'\\162\\155' -rf ~", ["rm", "-rf", "~"]),
]


@pytest.mark.parametrize("cmd,argv", VAR_CASES)
def test_variables_resolve(cmd, argv):
    stages = [s for s in parse(cmd) if s.argv]
    assert stages[-1].argv == argv
    assert not stages[-1].uncertain


def test_single_quoted_var_is_literal():
    st = parse("x=rm; echo '$x'")[1]
    assert st.argv == ["echo", "$x"]


def test_unknown_vars_are_kept_literal():
    st = parse('rm -rf "$HOME" $BUILD_DIR ~')[0]
    assert st.argv == ["rm", "-rf", "$HOME", "$BUILD_DIR", "~"]
    assert st.dynamic == [False, False, True, True, False]
    assert not st.uncertain


def test_prefix_assignment_does_not_leak_or_apply():
    stages = parse("x=rm echo hi; $x -rf ~")
    assert stages[0].env == {"x": "rm"}
    assert stages[1].argv[0] == "$x" and stages[1].uncertain


def test_unset_forgets_value():
    st = parse("x=rm; unset x; $x -rf ~")[2]
    assert st.uncertain


# ---------------------------------------------------------------------------
# 8. Uncertainty flag
# ---------------------------------------------------------------------------
UNCERTAIN = [
    "$CMD -rf ~",
    "${CMD} -rf ~",
    "$(cat cmd.txt) -rf ~",
    "`cat cmd.txt` -rf ~",
    "x=$(cat cmd.txt); $x -rf ~",
    "sudo $X -rf /",
    "/bin/r? -rf /",
    "{rm,-rf,~}",
    "echo 'unterminated",
    'echo "unterminated',
    "echo $(rm -rf ~",
    "echo `rm -rf ~",
    "bash -c \"$SCRIPT\"",
    "$1 -rf ~",
]


@pytest.mark.parametrize("cmd", UNCERTAIN)
def test_uncertain(cmd):
    res = parse(cmd)
    assert res.uncertain, [(s.argv, s.reasons) for s in walk(res)]


CERTAIN = [
    "ls -la",
    "rm -rf ./build",
    "git push --force origin feature/x",
    "echo $HOME",
    "[ -f x ] && cat x",
    "[[ -n $X ]] && echo yes",
    "rm -rf build/*",
    "for i in 1 2; do echo $i; done",
    "(( i++ ))",
    "echo $((1 << 2))\nls",
    commit("feat: x"),
    "arr=(a b c); echo ${arr[0]}",
    "cat <<EOF\nhello $USER\nEOF",
    "case $1 in start) run;; esac",
]


def test_case_patterns_are_not_commands():
    assert ["rm"] not in [a[:1] for a in effective("case $x in rm) echo hi;; esac")]
    assert runs("case $x in rm) echo hi;; esac", "echo", "hi")


def test_heredoc_to_non_shell_pipeline_is_data():
    assert not runs("cat <<'EOF' | grep x\nrm -rf ~\nEOF", "rm")


@pytest.mark.parametrize("cmd", CERTAIN)
def test_certain(cmd):
    res = parse(cmd)
    assert not res.uncertain, [(s.argv, s.reasons) for s in walk(res)]
    assert not res.too_complex


def test_arithmetic_shift_is_not_a_heredoc():
    assert runs("echo $((1<<2))\nrm -rf ~", "rm", "-rf", "~")


def test_unterminated_quote_still_returns_best_effort_stages():
    res = parse("rm -rf ~ 'oops")
    assert res[0].argv == ["rm", "-rf", "~", "oops"]
    assert res[0].uncertain and res.uncertain


# ---------------------------------------------------------------------------
# 9. Caps: nesting depth and number of command texts
# ---------------------------------------------------------------------------
def test_deep_nesting_is_too_complex_and_fast():
    t = time.monotonic()
    res = parse("echo " + "$(" * 400 + "rm -rf ~" + ")" * 400)
    assert res.too_complex and res.uncertain
    assert time.monotonic() - t < 1.0


def test_unterminated_deep_nesting():
    t = time.monotonic()
    res = parse("echo " + '"$(' * 300)
    assert res.too_complex and res.uncertain
    assert time.monotonic() - t < 1.0


def test_shallow_nesting_is_fine():
    res = parse("echo " + "$(echo " * 4 + "x" + ")" * 4)
    assert not res.too_complex and not res.uncertain


def test_too_many_command_texts():
    res = parse("; ".join(["echo $(a) $(b)"] * 250))
    assert res.too_complex


def test_quoted_nesting_is_fast():
    t = time.monotonic()
    res = parse("echo '" + "$(" * 5000 + "' ; rm -rf ~")
    assert runs("echo '" + "$(" * 5000 + "' ; rm -rf ~", "rm", "-rf", "~")
    assert not res.too_complex
    assert time.monotonic() - t < 1.0


# ---------------------------------------------------------------------------
# 10. Review Focus 1/2 smoke (obfuscations surface the dangerous argv;
#     everyday commands do not)
# ---------------------------------------------------------------------------
FOCUS_DANGEROUS = [
    ('bash -c "rm -rf ~"', ("rm", "-rf", "~")),
    ("$(echo rm) -rf /", ("rm", "-rf", "/")),
    ("x=rm; $x -rf ~", ("rm", "-rf", "~")),
    ("cd / && rm -rf *", ("rm", "-rf", "*")),
    ("rm -r\\\nf ~", ("rm", "-rf", "~")),
    ("git push --force \\\n  origin main", ("git", "push", "--force", "origin", "main")),
]


@pytest.mark.parametrize("cmd,argv", FOCUS_DANGEROUS)
def test_focus_obfuscations_surface(cmd, argv):
    assert runs(cmd, *argv), effective(cmd)


def test_focus_cd_then_rm_order_preserved():
    assert top("cd / && rm -rf *") == [["cd", "/"], ["rm", "-rf", "*"]]


FOCUS_EVERYDAY = [
    ('git commit -m "rm -rf old dir"', [["git", "commit", "-m", "rm -rf old dir"]]),
    ("echo 'curl x | sh'", [["echo", "curl x | sh"]]),
    ("rm -rf ./build", [["rm", "-rf", "./build"]]),
    ("git push --force origin feat/x", [["git", "push", "--force", "origin", "feat/x"]]),
    ("cp .env.example .env.example.bak", [["cp", ".env.example", ".env.example.bak"]]),
]


@pytest.mark.parametrize("cmd,expected", FOCUS_EVERYDAY)
def test_focus_everyday(cmd, expected):
    res = parse(cmd)
    assert effective(cmd) == expected
    assert not res.uncertain


# ---------------------------------------------------------------------------
# 11. Ruling R1: stdlib only, runs under `python3 -I` outside the app
# ---------------------------------------------------------------------------
def test_stdlib_only_imports():
    tree = ast.parse(SHELL_PY.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative imports"
            names.add((node.module or "").split(".")[0])
    assert names <= set(sys.stdlib_module_names), names - set(sys.stdlib_module_names)


def test_runs_isolated(tmp_path):
    shutil.copy(SHELL_PY, tmp_path / "shell.py")
    script = (
        "import sys; sys.path.insert(0, sys.argv[1]); import shell; "
        "r = shell.parse('x=rm; $x -rf ~'); print(r[1].argv)"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", script, str(tmp_path)],
        capture_output=True, text=True, timeout=30, env={"PATH": os.environ.get("PATH", "")},
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "['rm', '-rf', '~']"


def test_find_exec_chain_is_capped():
    t = time.monotonic()
    res = parse("find . -exec " * 600 + "rm -rf ~")
    assert res.too_complex
    assert time.monotonic() - t < 1.0


# ===========================================================================
# Fix round 1 (task review): regression tests with the reviewer's exact inputs
# ===========================================================================
from app.guard.shell import all_words  # noqa: E402


def seen_or_uncertain(cmd):
    res = parse(cmd)
    return runs(cmd, "rm") or res.uncertain


# 1. Variable resolution must be sound: never a wrong value with confidence.
UNSOUND_VARS = [
    "x=rm; (x=ls); $x -rf ~",
    "x=rm; f() { x=ls; }; $x -rf ~",
    "x=rm; export x=ls & $x -rf ~",
    "x=rm; x=ls | true; $x -rf ~",
    "x=rm; f(){ local x=ls; }; f; $x -rf ~",
    "x=ls; if true; then x=rm; fi; $x -rf ~",
    "x=ls; while x=rm; do break; done; $x -rf ~",
    "x=ls; for x in rm; do $x -rf ~; done",
    "x=ls; printf -v x rm; $x -rf ~",
    "x=ls; : ${x:=rm}; $x -rf ~",
    # more from the reviewer's harness
    "x=ls; true && x=rm; $x -rf ~",
    "x=ls; false || x=rm; $x -rf ~",
    "x=ls; [ -e f ] && x=rm; $x -rf ~",
    "x=ls; case a in a) x=rm;; esac; $x -rf ~",
    "x=ls; { x=rm; }; $x -rf ~",
    "x=ls; echo $(x=rm); $x -rf ~",
    "x=ls; read x; $x -rf ~",
    "x=ls; getopts a x; $x -rf ~",
    "x=ls; mapfile x <<< rm; $x -rf ~",
    "x=ls; declare -n y=x; y=rm; $x -rf ~",
    "x=ls; eval x=rm; $x -rf ~",
    "x=ls; source f; $x -rf ~",
    "x=ls; x=rm & $x -rf ~",
    "x=ls; echo | x=rm; $x -rf ~",
    "f(){ x=rm; }; x=ls; f; $x -rf ~",
    "if true; then x=rm; else x=ls; fi; $x -rf ~",
]


@pytest.mark.parametrize("cmd", UNSOUND_VARS)
def test_variable_resolution_is_sound(cmd):
    assert seen_or_uncertain(cmd), [(s.argv, s.reasons) for s in walk(parse(cmd))]
    # and never resolved to `ls` with confidence
    last = [s for s in parse(cmd) if s.argv][-1]
    assert not (last.argv[:1] == ["ls"] and not last.uncertain)


def test_sound_resolution_still_resolves_straight_line():
    assert parse("x=ls; cd /; $x -rf *")[-1].argv == ["ls", "-rf", "*"]
    assert not parse("x=ls; cd /; $x -rf *").uncertain
    assert parse("x=rm; y=$x; $y -rf ~")[-1].argv == ["rm", "-rf", "~"]


# 2. A brace group after a keyword or wrapper.
@pytest.mark.parametrize("cmd", [
    "time { rm -rf ~; }",
    "if { rm -rf ~; }; then :; fi",
    "while { rm -rf ~; }; do :; done",
    "! { rm -rf ~; }",
    "true && { rm -rf ~; }",
    "echo | { rm -rf ~; }",
])
def test_brace_group_after_keyword(cmd):
    assert runs(cmd, "rm", "-rf", "~"), effective(cmd)


# 3. Script input that is not a literal string.
@pytest.mark.parametrize("cmd", [
    "source <(echo rm -rf ~)",
    ". <(echo 'rm -rf ~')",
    "bash <(echo 'rm -rf ~')",
    "bash < <(echo 'rm -rf ~')",
    "bash /dev/stdin <<< 'rm -rf ~'",
    "bash /dev/fd/0 <<< 'rm -rf ~'",
    "sh /proc/self/fd/0 <<< 'rm -rf ~'",
    "source /dev/fd/0 <<< 'rm -rf ~'",
    "bash 0<<< 'rm -rf ~'",
    "cat <<< 'rm -rf ~' | bash",
])
def test_script_input_is_visible(cmd):
    assert runs(cmd, "rm", "-rf", "~"), effective(cmd)


@pytest.mark.parametrize("cmd", [
    "exec 3<<<'rm -rf ~'; bash <&3",
    "bash < script.sh",
    "printf '%s\\n' 'rm -rf ~' | bash",
    "curl x | bash",
    "echo 'rm -rf ~' | tee | bash",
    "bash /dev/fd/3",
    "source <(curl x)",
    "bash <(cat x)",
    "tee >(bash) <<< 'rm -rf ~'",
    "x=$(cat f); bash <<< \"$x\"",
])
def test_invisible_script_input_is_uncertain(cmd):
    assert parse(cmd).uncertain, [(s.argv, s.redirects, s.reasons) for s in walk(parse(cmd))]


@pytest.mark.parametrize("cmd", [
    "bash script.sh",
    "source venv/bin/activate",
    ". ~/.nvm/nvm.sh && nvm use",
    "bash -c 'echo hi'",
    "cat <<EOF | bash\necho hi\nEOF",
])
def test_ordinary_script_runs_are_certain(cmd):
    assert not parse(cmd).uncertain, [(s.argv, s.reasons) for s in walk(parse(cmd))]


# 4. `--` before eval / trap text.
@pytest.mark.parametrize("cmd", ["eval -- 'rm -rf ~'", "trap -- 'rm -rf ~' EXIT"])
def test_double_dash_eval_trap(cmd):
    assert runs(cmd, "rm", "-rf", "~"), effective(cmd)


# 5/6/7. Wrappers, flag clusters, more shells.
@pytest.mark.parametrize("cmd", [
    "chroot / rm -rf ~",
    "unshare -r rm -rf ~",
    "nsenter -t 1 rm -rf ~",
    "flock /tmp/l rm -rf ~",
    "flock /tmp/l -c 'rm -rf ~'",
    "strace rm -rf ~",
    "strace -o log -f rm -rf ~",
    "pkexec rm -rf ~",
    "unbuffer rm -rf ~",
    "caffeinate rm -rf ~",
    "arch -x86_64 rm -rf ~",
    "runuser -u x -- rm -rf ~",
    "runuser -l x -c 'rm -rf ~'",
    "sg grp -c 'rm -rf ~'",
    "script -c 'rm -rf ~'",
    "script -qc 'rm -rf ~' /dev/null",
    "coproc rm -rf ~",
    "coproc X { rm -rf ~; }",
    "timeout 5s -- rm -rf ~",
    "sudo -iu root rm -rf ~",
    "env -iS 'rm -rf ~'",
    "su -c'rm -rf ~'",
    "su --command='rm -rf ~'",
    "sudo -s -- 'rm -rf ~'",
    "xargs -0I{} rm -rf {}",
    "nice -n5 -- rm -rf ~",
    "tcsh -c 'rm -rf ~'",
    "csh -c 'rm -rf ~'",
    "bash5 -c 'rm -rf ~'",
])
def test_more_wrappers(cmd):
    assert runs(cmd, "rm", "-rf"), effective(cmd)


def test_parallel_wrapper():
    assert runs("echo ~ | parallel rm -rf", "rm", "-rf")
    assert parse("echo ~ | parallel rm -rf")[1].wrappers == ["parallel"]


def test_all_words_reaches_every_depth():
    words = all_words(parse("echo $(bash -c 'git reset --hard') | cat"))
    assert {"echo", "bash", "git", "reset", "--hard", "cat"} <= set(words)


# 8. Aliases.
def test_alias_value_is_parsed_and_use_is_uncertain():
    cmd = "alias x='rm -rf ~'\nx"
    assert runs(cmd, "rm", "-rf", "~")
    assert parse(cmd)[-1].uncertain


# 9. Git aliases.
@pytest.mark.parametrize("cmd,argv", [
    ("git -c alias.x='!rm -rf ~' x", ("rm", "-rf", "~")),
    ("git config alias.x '!rm -rf ~'; git x", ("rm", "-rf", "~")),
    ("git config --global alias.x '!rm -rf ~'", ("rm", "-rf", "~")),
    ("git -c alias.p='push --force origin main' p", ("git", "push", "--force", "origin", "main")),
])
def test_git_aliases(cmd, argv):
    assert runs(cmd, *argv), effective(cmd)


# 10. Minor: mark uncertain.
@pytest.mark.parametrize("cmd", [
    "echo ${x@P}",
    "x='$(rm -rf ~)'; echo ${x@P}",
    "x='a[$(rm -rf ~)]'; echo $((x))",
    "printf -v 'a[$(rm -rf ~)]' x",
    "read 'a[$(rm -rf ~)]' <<< x",
    "test -v 'a[$(rm -rf ~)]'",
    "PS4='$(rm -rf ~)' bash -xc :",
    "PROMPT_COMMAND='rm -rf ~' bash -i",
    "BASH_ENV=<(echo rm -rf ~) bash -c :",
    "ENV=x sh -c :",
])
def test_minor_injections_uncertain(cmd):
    assert parse(cmd).uncertain, [(s.argv, s.reasons) for s in walk(parse(cmd))]


# ===========================================================================
# Fix round 2 (re-review): regression tests with the reviewer's exact inputs
# ===========================================================================
# A. Writers behind builtin/command (and other wrappers); F. fd/wait/coproc writers.
@pytest.mark.parametrize("cmd", [
    "x=ls; builtin printf -v x rm; $x -rf ~",
    "x=ls; command printf -v x rm; $x -rf ~",
    "x=ls; command export x=rm; $x -rf ~",
    "x=ls; builtin read x <<< rm; $x -rf ~",
    "x=ls; builtin declare x=rm; $x -rf ~",
    "x=ls; sudo printf -v x rm; $x -rf ~",
    "x=ls; exec {x}>/dev/null; $x -rf ~",
    "x=ls; wait -p x; $x -rf ~",
    "x=ls; coproc x { :; }; $x -rf ~",
])
def test_writers_behind_wrappers(cmd):
    assert seen_or_uncertain(cmd), [(s.argv, s.reasons) for s in walk(parse(cmd))]
    last = [s for s in parse(cmd) if s.argv][-1]
    assert not (last.argv[:1] == ["ls"] and not last.uncertain)


# B. Stdin paths are normalised; other /dev, /proc script paths are uncertain.
@pytest.mark.parametrize("cmd", [
    "bash /dev/./stdin <<< 'rm -rf ~'",
    "bash //dev/stdin <<< 'rm -rf ~'",
    "bash /proc/thread-self/fd/0 <<< 'rm -rf ~'",
    "bash /dev/fd/00 <<< 'rm -rf ~'",
    "echo 'rm -rf ~' | bash /dev/./stdin",
])
def test_stdin_paths_normalised(cmd):
    assert runs(cmd, "rm", "-rf", "~"), effective(cmd)


@pytest.mark.parametrize("cmd", ["bash /dev/fd/3", "source /proc/1/environ", "bash /dev/tcp/x/1"])
def test_dev_proc_script_paths_uncertain(cmd):
    assert parse(cmd).uncertain


# C. A shell reading inherited stdin is uncertain.
@pytest.mark.parametrize("cmd", [
    "exec 0<<< 'rm -rf ~'; bash",
    "exec < <(echo rm -rf ~); bash",
    "{ bash; } <<< 'rm -rf ~'",
    "if true; then bash; fi <<< 'rm -rf ~'",
    "f(){ bash; }; f <<< 'rm -rf ~'",
    "bash 2<<< 'x'",
    "source /dev/stdin",
])
def test_inherited_stdin_shell_uncertain(cmd):
    assert parse(cmd).uncertain, [(s.argv, s.redirects, s.reasons) for s in walk(parse(cmd))]


def test_subshell_herestring_reaches_the_shell():
    assert runs("( bash ) <<< 'rm -rf ~'", "rm", "-rf", "~")


# D/E. xargs/parallel building a -c script; parallel command lines.
@pytest.mark.parametrize("cmd", [
    "xargs bash -c <<< 'rm -rf ~'",
    "echo 'rm -rf ~' | xargs bash -c",
    "xargs -I% bash -c % <<< 'rm -rf ~'",
    "echo 'rm -rf ~' | xargs -I{} sh -c '{}'",
    "xargs -0 bash -c <<< 'rm -rf ~'",
])
def test_xargs_built_script_uncertain(cmd):
    assert parse(cmd).uncertain


def test_xargs_literal_script_is_certain():
    assert not parse("ls | xargs -I{} sh -c 'echo hi'").uncertain


def test_parallel_command_line_word():
    assert runs("parallel 'rm -rf {}' ::: ~", "rm", "-rf", "{}")


# G. git settings and environment variables that run commands.
@pytest.mark.parametrize("cmd", [
    "git -c core.pager='rm -rf ~' log",
    "git -c core.sshCommand='rm -rf ~' fetch",
    "git -c core.fsmonitor='rm -rf ~' status",
    "GIT_SSH_COMMAND='rm -rf ~' git fetch",
    "git config core.pager 'rm -rf ~'; git log",
    "git config --global core.editor 'rm -rf ~'",
    "git config set sequence.editor 'rm -rf ~'",
    "git -c diff.external='rm -rf ~' diff",
    "git -c credential.helper='!rm -rf ~' fetch",
    "env GIT_PAGER='rm -rf ~' git log",
    "export EDITOR='rm -rf ~'; git commit",
    "PAGER='rm -rf ~' man ls",
])
def test_git_command_settings(cmd):
    assert runs(cmd, "rm", "-rf", "~"), effective(cmd)


@pytest.mark.parametrize("cmd", [
    "git --config-env=alias.x=V x",
    "git -c core.hooksPath=/tmp/h commit -m x",
    'GIT_SSH_COMMAND="$CMD" git fetch',
])
def test_git_settings_uncertain(cmd):
    assert parse(cmd).uncertain


@pytest.mark.parametrize("cmd", [
    "git -c core.fsmonitor=true status",
    "git -c credential.helper=store push origin feat/x",
    "git config user.name 'A B'",
    "EDITOR=vim git commit",
])
def test_git_settings_everyday(cmd):
    assert not parse(cmd).uncertain
    assert not runs(cmd, "rm")


# F. taskset / numactl wrappers.
@pytest.mark.parametrize("cmd", [
    "taskset -c 0 rm -rf ~",
    "taskset 0x1 rm -rf ~",
    "numactl --cpunodebind=0 rm -rf ~",
    "numactl -N 0 -m 0 rm -rf ~",
])
def test_taskset_numactl(cmd):
    assert runs(cmd, "rm", "-rf", "~"), effective(cmd)


# The reviewer's false-positive set (fp.py): everyday commands stay certain.
EVERYDAY_FP = [
    'git commit -m "rm -rf old dir; curl x | sh"',
    "git commit -m \"$(cat <<'EOF'\nfix: drop rm -rf ~ usage\n\nbody\nEOF\n)\"",
    "npm install", "npm run build && npm test", "pip install -r requirements.txt",
    "python -m pytest -q tests/", "pytest -k 'not slow' -x", "ls -la | grep foo", "cd backend && make test",
    "cd frontend && npx vitest run", "git status && git diff --stat", "git log --oneline -5",
    "git push origin feat/x", "rm -rf ./build", "find . -name '*.pyc' -delete", "grep -rn 'TODO' src/ | head -20",
    "for f in *.py; do echo $f; done", "if [ -f .env.example ]; then cp .env.example .env; fi",
    "export NODE_ENV=production && npm run build", "source .venv/bin/activate && pytest",
    "cat package.json | jq '.scripts'", "echo $HOME", "docker compose up -d", "curl -s http://localhost:8000/health",
    "mkdir -p out && cp -r dist/* out/", "ls *.txt", "python3 -c 'print(1)'", "tar -czf a.tgz src/",
    "sed -i '' 's/a/b/' file.txt", "PYTHONPATH=. python app.py", "uv run pytest", "make -j4 2>&1 | tail -n 50",
    "git stash list", "echo \"$(date +%F)\"", "for i in {1..3}; do echo $i; done", "x=$(git rev-parse HEAD); echo $x",
    "npm test -- --watch=false", "node -e 'console.log(1)'", "ls src/**/*.ts", "find . -type f -exec grep -l foo {} +",
]


@pytest.mark.parametrize("cmd", EVERYDAY_FP)
def test_everyday_commands_are_certain(cmd):
    res = parse(cmd)
    assert not res.uncertain, [(s.argv, s.reasons) for s in walk(res)]


# ===========================================================================
# Fix round 3 (fail-closed rules): regression tests with the reviewer's exact inputs
# ===========================================================================
# 1. A script FILE run while the same stage has stdin input (or a pipe into it).
@pytest.mark.parametrize("cmd", [
    "bash ../../../../../../dev/stdin <<< 'rm -rf ~'",
    "source ../../../../../../../dev/stdin <<< 'rm -rf ~'",
    "bash ~/../../dev/stdin <<< 'rm -rf ~'",
    "cd /dev && bash stdin <<< 'rm -rf ~'",
    "cd /dev; bash ./stdin <<< 'rm -rf ~'",
    "bash ./../../../../dev/stdin <<< 'rm -rf ~'",
    "echo 'rm -rf ~' | bash ../../dev/stdin",
    "bash x.sh < input.txt",
])
def test_script_file_with_stdin_is_uncertain(cmd):
    assert seen_or_uncertain(cmd) and parse(cmd).uncertain, [(s.argv, s.reasons) for s in walk(parse(cmd))]


# 2. xargs/parallel placeholder in the command word.
@pytest.mark.parametrize("cmd", [
    "echo rm | xargs -I{} {} -rf ~",
    "echo rm | xargs -I% %",
    "echo rm | xargs --replace=Q Q -rf ~",
    "echo 'rm -rf ~' | xargs eval",
    "echo 'rm -rf ~' | xargs sh",
])
def test_xargs_input_in_command_word(cmd):
    assert parse(cmd).uncertain


# 3. parallel ::: command lines.
def test_parallel_triple_colon_scripts():
    assert runs("parallel ::: 'rm -rf ~'", "rm", "-rf", "~")


@pytest.mark.parametrize("cmd", ["parallel sh -c ::: 'rm -rf ~'", "parallel eval ::: 'rm -rf ~'",
                                 "parallel :::: cmds.txt"])
def test_parallel_script_from_args_uncertain(cmd):
    assert parse(cmd).uncertain


# 4. git, fail closed.
@pytest.mark.parametrize("cmd", [
    "git -c include.path=/tmp/x log",
    "git -c includeIf.gitdir:~/x.path=/tmp/x log",
    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.pager GIT_CONFIG_VALUE_0='rm -rf ~' git log",
    "GIT_CONFIG_GLOBAL=/tmp/cfg git log",
    "GIT_EXEC_PATH=/tmp git log",
    "git -c uploadpack.packObjectsHook='rm -rf ~' fetch",
    "git -c core.gitProxy='rm -rf ~' fetch",
    "git -c remote.origin.uploadpack='rm -rf ~' fetch",
    "git -c remote.origin.receivepack='rm -rf ~' push",
    "git -c protocol.ext.allow=always fetch 'ext::sh -c rm% -rf% ~'",
    "git -c core.alternateRefsCommand='rm -rf ~' fetch",
    "git -c mergetool.x.cmd='rm -rf ~' mergetool -t x",
    "git -c difftool.x.cmd='rm -rf ~' difftool -t x",
    "git -c foo.bar='rm -rf ~' log",
    "git config weird.key /tmp/x",
])
def test_git_fail_closed(cmd):
    assert parse(cmd).uncertain, [(s.argv, s.reasons) for s in walk(parse(cmd))]


@pytest.mark.parametrize("cmd", [
    "git -c uploadpack.packObjectsHook='rm -rf ~' fetch",
    "git -c core.gitProxy='rm -rf ~' fetch",
    "git -c mergetool.x.cmd='rm -rf ~' mergetool -t x",
    "git -c core.pager='!rm -rf ~' log",
])
def test_git_command_values_nested(cmd):
    assert runs(cmd, "rm", "-rf", "~"), effective(cmd)


@pytest.mark.parametrize("cmd", [
    "git -c user.name=x commit -m y",
    "git -c color.ui=always log",
    "git config credential.helper store",
    "git -c diff.external=difft diff",
    "git -c core.pager=less log",
    "git config core.editor 'code --wait'",
    "git config user.name 'A B'",
    "git config remote.origin.url https://github.com/o/r.git",
    "git -c core.excludesFile=~/.gitignore status",
])
def test_git_everyday_settings_certain(cmd):
    assert not parse(cmd).uncertain, [(s.argv, s.reasons) for s in walk(parse(cmd))]


# 5. Bare sudo -s / su / sudo su with no visible input.
@pytest.mark.parametrize("cmd", ["sudo -s", "su", "sudo su", "sudo -i", "su - root"])
def test_bare_privileged_shell_uncertain(cmd):
    assert parse(cmd).uncertain


@pytest.mark.parametrize("cmd", ["sudo -s <<< 'rm -rf ~'", "su <<< 'rm -rf ~'", "echo 'rm -rf ~' | sudo su"])
def test_privileged_shell_visible_input(cmd):
    assert runs(cmd, "rm", "-rf", "~"), effective(cmd)


@pytest.mark.parametrize("cmd", ["sudo -v", "sudo -l", "sudo apt-get update", "su -c 'whoami' root"])
def test_sudo_su_everyday_certain(cmd):
    assert not parse(cmd).uncertain


# Extra fail-closed: a heredoc body after a dangling operator feeds a later stage; uncertain.
def test_heredoc_after_dangling_operator():
    assert parse("cat <<'E' |\nbash\nrm -rf ~\nE").uncertain
    assert not parse("cat <<EOF | tr a b\nx\nEOF").uncertain


# ===========================================================================
# Fix round 4: narrower git data-key allowlist; dangling-heredoc wording
# ===========================================================================
@pytest.mark.parametrize("cmd", [
    "git -c submodule.x.update='!rm -rf ~' submodule update",
    "git config submodule.x.update '!rm -rf ~'",
])
def test_git_submodule_update_command(cmd):
    assert runs(cmd, "rm", "-rf", "~"), effective(cmd)


@pytest.mark.parametrize("cmd", [
    "git -c lfs.customtransfer.x.path=sh -c lfs.customtransfer.x.args='-c \"rm -rf ~\"' lfs fetch",
    "git -c init.templateDir=/tmp/t clone x",
    "git -c lfs.extension.x.clean='rm -rf ~' add .",
    "git -c submodule.x.foo='a b' status",
])
def test_git_round4_uncertain(cmd):
    assert parse(cmd).uncertain, [(s.argv, s.reasons) for s in walk(parse(cmd))]


@pytest.mark.parametrize("cmd", [
    "git -c submodule.recurse=true pull",
    "git config init.defaultBranch main",
    "git -c http.proxy=http://p:8080 fetch",
    "git -c submodule.x.update=rebase submodule update",
    "git config submodule.lib.url https://github.com/o/lib.git",
])
def test_git_round4_certain(cmd):
    assert not parse(cmd).uncertain, [(s.argv, s.reasons) for s in walk(parse(cmd))]


def test_dangling_heredoc_reason_wording():
    reasons = [r for s in walk(parse("cat <<'E' |\nbash\nrm -rf ~\nE")) for r in s.reasons]
    reasons += parse("cat <<'E' |\nbash\nrm -rf ~\nE").errors
    assert "heredoc body feeds a later pipeline stage; treated as uncertain" in reasons
    assert not any("syntax error" in r for r in reasons)


# ===========================================================================
# Task 2 review, fix round 1: parser changes (C6 transformed stdin, I8 wrappers)
# ===========================================================================
from app.guard.shell import shell_mode, shell_name  # noqa: E402


@pytest.mark.parametrize("cmd", [
    "base64 -d <<< cm0gLXJmIH4K | bash",
    "xxd -r -p <<< 726d202d7266207e | sh",
    'sed s/X/rm/ <<< "X -rf ~" | bash',
    'tr a-z a-z <<< "rm -rf ~" | sh',
    'rev <<< "~ fr- mr" | bash',
    "sed s/X/rm/ <<EOF | bash\nX -rf ~\nEOF",
    "cat <<EOF | sed s/X/rm/ | bash\nX -rf ~\nEOF",
])
def test_transformed_stdin_into_shell_is_uncertain(cmd):
    assert parse(cmd).uncertain


@pytest.mark.parametrize("cmd", [
    "cat <<< 'rm -rf ~' | bash",
    "cat - <<< 'rm -rf ~' | bash",
    "cat <<EOF | bash\nrm -rf ~\nEOF",
])
def test_cat_passes_stdin_through_to_shell(cmd):
    assert runs(cmd, "rm", "-rf", "~")


@pytest.mark.parametrize("cmd", [
    "at now <<< 'rm -rf ~'",
    "echo 'rm -rf ~' | at now",
    "at now + 1 minute <<EOF\nrm -rf ~\nEOF",
    "batch <<< 'rm -rf ~'",
    "tmux new -d 'rm -rf ~'",
    "tmux new-session -d -s x 'rm -rf ~'",
    "tmux split-window -h 'rm -rf ~'",
    "tmux run-shell 'rm -rf ~'",
    "screen -dm sh -c 'rm -rf ~'",
    "screen -dmS name rm -rf ~",
    "systemd-run rm -rf ~",
    "systemd-run --user -p X=1 --unit u rm -rf ~",
])
def test_scheduler_and_terminal_wrappers_are_followed(cmd):
    assert runs(cmd, "rm", "-rf", "~")


@pytest.mark.parametrize("cmd", ["at -f job.sh now", "cat f | at now", "tmux send-keys 'rm -rf ~' Enter",
                                 "at now"])
def test_unknown_at_jobs_and_send_keys_are_uncertain(cmd):
    assert parse(cmd).uncertain


@pytest.mark.parametrize("cmd", ["at -l", "atq", "tmux ls", "tmux attach -t x", "screen -ls"])
def test_at_and_tmux_listing_is_certain(cmd):
    assert not parse(cmd).uncertain


def test_shell_helpers_are_public():
    assert shell_name("bash5") == "bash" and shell_name("python") is None
    assert shell_mode(["-c", "ls"]) == ("c", "ls")
    assert shell_mode([]) == ("stdin", None)
