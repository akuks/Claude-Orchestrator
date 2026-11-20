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
