"""A small, conservative Bash command-line parser for the co-guard.

Python port of jspark-forge ``scripts/lib/shell.js`` (MIT, same owner), Bash
dialect only. Pure parsing: nothing is executed, evaluated or spawned.

RULING R1: this module uses only the Python standard library and imports
nothing from the app. It is copied into the co-guard plugin and run with
``python3 -I``.

API
---
``parse(command) -> ParseResult``
    A ``list`` of :class:`Stage` in source order. A command line is split into
    pipelines on ``;``, ``&&``, ``||``, ``&`` and newlines, and pipelines into
    stages on ``|`` / ``|&``. ``Stage.pipeline`` / ``Stage.index`` give each
    stage's pipeline number and its position inside the pipeline.

``Stage``
    ``argv``       words with quotes removed; leading ``VAR=value`` prefix
                   assignments are moved to ``env``; brace-group ``{``/``}``
                   words are dropped.
    ``redirects``  ``[(op, target)]`` with op one of ``> >> >| >& &> &>> < <>
                   << <<<`` (fd numbers dropped; ``2>&1``-style descriptor
                   duplication is not a redirect). For ``<<`` the target is
                   the delimiter, for ``<<<`` the here-string word.
    ``nested``     ``list[ParseResult]``: command texts this stage runs -
                   ``$( )``, backticks, ``<( )``, ``>( )``, ``$(( ))``,
                   substitutions in unquoted heredoc bodies and in ``${...}``,
                   the script of ``sh|bash|zsh|... -c``, ``eval`` arguments,
                   ``env -S``, ``find -exec ... ;``, ``trap 'cmd'``,
                   ``watch cmd``, ``su|runuser|sg|script -c cmd``,
                   ``flock F -c cmd``, ``sudo -s|-i ARGS``, ``alias n='cmd'``
                   values, git settings that run commands (``-c KEY=V`` /
                   ``git config [set] KEY V`` for ``alias.*``, ``core.pager``,
                   ``core.editor``, ``core.sshCommand``, ``core.fsmonitor``,
                   ``sequence.editor``, ``diff.external``, ``credential.helper``
                   ``!cmd`` ...), command-valued variables (``GIT_SSH_COMMAND``,
                   ``GIT_PAGER``, ``EDITOR``, ``PAGER`` ... as prefix, ``env`` or
                   ``export``), a ``parallel`` command word with spaces, and a
                   shell's script when it is visible: a
                   heredoc or here-string (``bash <<EOF``, ``bash 0<<< ..``,
                   ``bash /dev/stdin <<< ..``), a heredoc or here-string piped
                   into a later shell stage (``cat <<EOF | bash``), literal
                   ``echo``/``printf`` output piped into one (``echo 'cmd' |
                   sh``), or ``<(echo ...)`` as the script file or ``<``
                   source (``source <(echo ..)``, ``bash < <(echo ..)``).
                   ``case`` patterns end at ``)``, so the commands after them
                   are separate stages; brace groups after keywords and
                   wrappers (``time { ..; }``, ``! { ..; }``) are unwrapped.
    ``dynamic``    one bool per argv word: True when the word still holds an
                   unresolved ``$VAR``/substitution or an unquoted glob/brace.
    ``env``        prefix assignments ``{name: value-or-None}``.
    ``wrappers``   wrapper commands stripped by :func:`expand_wrappers`.
    ``heredocs``   ``[(delimiter, quoted, body)]``.
    ``uncertain`` / ``reasons``   see below.

``expand_wrappers(stage_or_argv) -> list[str] | None``
    The argv of the command that really runs, with prefix assignments and
    wrappers (``sudo``, ``env``, ``nohup``, ``nice``, ``time``, ``exec``,
    ``command``, ``xargs``, ``timeout <dur>``, ``doas``, ``busybox`` ...) and
    shell keywords (``if then do ! ...``) stripped, or ``None`` if no command
    remains. Shell ``-c`` scripts are not flattened here: they are parsed
    into ``Stage.nested`` (use :func:`walk`).

``walk(stages)``  yields every stage, nested ones included, depth first.

``all_words(stages)``  every argv word at any depth (defence in depth).

Data vs. command
----------------
Single-quoted text, quoted heredoc bodies (``<<'EOF'``), ``\\$`` escapes and
here-strings fed to non-shells are data: they are never parsed as commands
(so a commit message is not a command). The one exception is input fed to a
shell's stdin (``bash <<'EOF' ... EOF``), which the shell executes.

Variables
---------
Resolution is sound or absent, never a wrong value with confidence. ``parse``
makes repeated passes: a name is resolved only if it has exactly one writer
anywhere in the text (at any depth) and that writer is a top-level,
unconditional, straight-line assignment (``x=v`` or ``export|readonly x=v``
on its own, after nothing or ``;``/newline, not in a pipeline, a subshell,
a compound command or function body, or a background job). Writers include
assignments anywhere, prefix assignments, ``for``/``select NAME``,
``read``/``mapfile``/``getopts``, ``printf -v``, ``declare``/``local``,
``unset``, ``${x:=v}``, arithmetic and ``let`` assignments. ``eval`` of
non-literal text, ``source``/``.`` of a file and ``declare -n`` disable
resolution entirely. A resolved ``$NAME`` / ``${NAME}`` outside single
quotes is replaced by its value (word-split when unquoted, so
``x="rm -rf"; $x ~`` gives ``rm -rf ~``); an unresolved one stays literal and
dynamic, which makes a command word uncertain. ``$(echo WORDS)`` / ``$(printf
WORD)`` substitutions with literal words are resolved to those words.
Unquoted ``$IFS``/``${IFS}`` acts as a word separator. Unknown variables
(including ``$HOME``) are kept literally in argv and flagged in ``dynamic``.

UNCERTAINTY FLAG
----------------
The parser never gives up: it always returns its best-effort stages. When it
cannot be confident what runs, it sets ``Stage.uncertain = True`` with a
human-readable entry in ``Stage.reasons``:

* the command word (after wrappers) is not a literal: unresolved ``$VAR``,
  ``$1``, an unresolvable ``$( )``/backtick, an unquoted glob or brace
  expansion (``/bin/r?``, ``{rm,-rf,~}``);
* a shell or ``source`` reads a script we cannot see: ``< file``, ``<&N``,
  any ``/dev`` or ``/proc`` path other than stdin (paths are normalised:
  ``/dev/./stdin`` is stdin), a non-literal ``<( )``, a pipe from something
  other than a literal ``echo``/``printf``/heredoc/here-string
  (``curl x | bash``), or inherited stdin - a stdin-mode shell needs its
  input visible at its own stage (``exec 0<<<..; bash``, ``{ bash; } <<<..``
  and a bare ``bash`` are uncertain);
* a script FILE runs while its stage has stdin input or a pipe into it
  (``bash ../../dev/stdin <<< ..``: the file may be stdin);
* ``xargs``/``parallel`` builds a ``sh -c`` script, the command word, or an
  ``eval``/``source`` from its input (no script, a ``{}``/``-I``
  placeholder, ``:::``), or ``parallel :::: file``;
* git, fail closed: ``--config-env``, ``GIT_CONFIG_*``, ``GIT_EXEC_PATH``,
  ``include*``, ``core.hooksPath``, ``core.gitProxy``, ``remote.*.uploadpack|
  receivepack``, ``uploadpack.packObjectsHook``, ``core.alternateRefsCommand``,
  ``mergetool|difftool.*.cmd``, any ``ext::`` argument, and an unrecognised
  key (not a known command or data key) whose value has whitespace or ``/``;
* a privileged shell with no visible input (``sudo -s``, ``su``, ``sudo su``),
  a heredoc body after a dangling ``|``/``&&``/``||``, or a command-valued
  variable that is not a literal;
* a shell hook variable is set (``PS4``, ``PROMPT_COMMAND``, ``BASH_ENV``,
  ``ENV`` ...), ``${x@P}``, an array subscript holding ``$( )`` in a name
  argument (``read 'a[$(..)]'``) or reached through arithmetic, or the
  command word is an alias defined earlier in the line;
* lexical problems while building the stage: unterminated single/double
  quote, ``$(``, backtick, ``${`` or ``$'``.

``ParseResult.uncertain`` is True if any stage at any depth is uncertain, any
lexical error occurred, or the line is too complex. ``ParseResult.too_complex``
is True when nesting goes past ``MAX_NEST`` (8) levels or more than
``MAX_CALLS`` (400) command texts would be parsed; deeper text is then not
followed. Rules engines must treat ``too_complex`` as a deny, and uncertainty
around a dangerous verb as a hit rather than an allow.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field

__all__ = [
    "MAX_CALLS",
    "MAX_NEST",
    "ParseResult",
    "Stage",
    "all_words",
    "command_name",
    "expand_wrappers",
    "parse",
    "unfold",
    "walk",
]

MAX_NEST = 8
MAX_CALLS = 400

_ASSIGN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(\+?)=(.*)\Z", re.S)
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_WS = " \t\r\f\v"
_DELIM_STOP = " \t\n\r\f\v;|&<>()"

SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "ash", "mksh", "fish", "rbash", "tcsh", "csh"})

# Wrappers and shell keywords that run the command after them.
_PREFIX = frozenset({
    "sudo", "doas", "env", "command", "builtin", "nohup", "time", "exec", "xargs", "nice",
    "ionice", "timeout", "stdbuf", "busybox", "winpty", "setsid", "chronic", "chroot", "unshare",
    "nsenter", "flock", "strace", "pkexec", "unbuffer", "caffeinate", "arch", "parallel", "runuser",
    "coproc", "taskset", "numactl", "if", "then", "else", "elif", "do", "while", "until", "!",
})
_WITH_VALUE = {
    "sudo": {"-u", "-g", "-h", "-p", "-C", "-U", "-r", "-t", "-D", "-T", "--user", "--group",
             "--host", "--prompt", "--chdir", "--close-from", "--other-user", "--role", "--type"},
    "doas": {"-u", "-C"},
    "env": {"-u", "--unset", "-C", "--chdir"},
    "exec": {"-a"},
    "xargs": {"-I", "-n", "-P", "-L", "-d", "-s", "-E", "-a", "--arg-file", "--delimiter",
              "--max-args", "--max-procs", "--max-lines", "--max-chars", "--eof"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p", "--class", "--classdata"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "stdbuf": {"-i", "-o", "-e"},
    "chroot": {"--userspec", "--groups"},
    "unshare": {"-S", "-G", "--setuid", "--setgid", "-R", "--root", "-w", "--wd"},
    "nsenter": {"-t", "--target", "-S", "-G", "--setuid", "--setgid"},
    "flock": {"-w", "--wait", "--timeout", "-E", "--conflict-exit-code"},
    "strace": {"-o", "-e", "-p", "-s", "-u", "-E", "-a", "-b", "-I", "-O", "-P", "-S", "-X"},
    "pkexec": {"-u", "--user"},
    "caffeinate": {"-t", "-w"},
    "arch": {"-arch", "-e"},
    "parallel": {"-j", "--jobs", "-a", "--arg-file", "-n", "-N", "-S", "--sshlogin", "-d",
                 "--delimiter", "-I", "--colsep", "--joblog", "--results", "-P", "--max-procs",
                 "--timeout", "--delay"},
    "runuser": {"-u", "-g", "-G", "--user", "--group", "--supp-group"},
    "taskset": {"-c", "--cpu-list"},
    "numactl": {"-N", "-m", "-C", "-i", "-p", "--cpunodebind", "--membind", "--physcpubind",
                "--interleave", "--preferred"},
}
_NEEDS_ARG = frozenset({"timeout", "chroot", "flock"})  # a duration / root dir / lock file comes first
_DECLARE = frozenset({"export", "declare", "typeset", "local", "readonly"})
_MISSING = object()
_STDIN_PATH = re.compile(r"/dev/stdin|/dev/fd/0+|/proc/(self|thread-self|\d+)(/task/\d+)?/fd/0+")
# Variables whose value some command runs as a shell command.
_CMD_ENV_VARS = frozenset({"GIT_SSH_COMMAND", "GIT_SSH", "GIT_PAGER", "GIT_EDITOR", "GIT_SEQUENCE_EDITOR",
                           "GIT_EXTERNAL_DIFF", "GIT_ASKPASS", "GIT_PROXY_COMMAND", "PAGER", "EDITOR",
                           "VISUAL", "SSH_ASKPASS", "MANPAGER", "LESSOPEN"})
# git config keys whose value is run as a command.
_GIT_CMD_KEYS = re.compile(
    r"(core\.(pager|editor|sshcommand|fsmonitor|askpass)|sequence\.editor|diff\.external|pager\..+"
    r"|diff\..+\.(textconv|command)|merge\..+\.driver|filter\..+\.(clean|smudge|process)"
    r"|gpg(\..+)?\.program|credential(\..+)?\.helper|alias\..+)")
# git keys that are always uncertain (fail closed); the value is also nested when it is a command.
_GIT_RISKY_KEYS = re.compile(
    r"(include\..+|includeif\..+|remote\..+\.(uploadpack|receivepack)|core\.gitproxy"
    r"|uploadpack\.packobjectshook|core\.alternaterefscommand|(mergetool|difftool)\..+\.cmd|core\.hookspath)")
_GIT_RISKY_NO_NEST = re.compile(r"(include\..+|includeif\..+|core\.hookspath)")
# git keys known to hold plain data (names, colours, urls, paths): never uncertain.
_GIT_DATA_KEYS = re.compile(
    r"((user|author|committer|color|i18n|init|branch|push|pull|fetch|rebase|advice|log|format|status|help"
    r"|safe|http|https|column|gc|pack|index|maintenance|feature|submodule|lfs)\..+"
    r"|remote\.[^.]+\.(url|pushurl|fetch|push|tagopt|prune|mirror)"
    r"|merge\.(ff|conflictstyle|log|renamelimit)|commit\.(template|gpgsign|verbose|cleanup)|tag\.(gpgsign|sort)"
    r"|core\.(autocrlf|filemode|excludesfile|attributesfile|ignorecase|quotepath|whitespace|eol|longpaths"
    r"|symlinks|bare|logallrefupdates|compression|precomposeunicode|safecrlf|abbrev|untrackedcache))")
# Shell variables that make a shell run code from their value.
_SHELL_HOOK_VARS = frozenset({"PS0", "PS1", "PS4", "PROMPT_COMMAND", "BASH_ENV", "ENV", "SHELLOPTS",
                              "BASHOPTS", "BASH_FUNC", "ZDOTDIR"})
# Builtins whose arguments name variables (an array subscript there is arithmetic: $( ) runs).
_NAME_ARG_CMDS = frozenset({"read", "mapfile", "readarray", "printf", "test", "[", "[[", "declare",
                            "typeset", "local", "export", "readonly", "unset", "let", "getopts"})
_COMPOUND_OPEN = frozenset({"if", "while", "until", "for", "select", "case", "{"})
_COMPOUND_CLOSE = frozenset({"fi", "done", "esac", "}"})
_ARITH_WRITE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:\[[^\]]*\])?\s*(?:(?:<<|>>|[-+*/%&|^])?=(?!=)|\+\+|--)"
    r"|(?:\+\+|--)\s*([A-Za-z_][A-Za-z0-9_]*)")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(eq=False)
class Stage:
    argv: list = field(default_factory=list)
    redirects: list = field(default_factory=list)
    nested: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    dynamic: list = field(default_factory=list)
    wrappers: list = field(default_factory=list)
    heredocs: list = field(default_factory=list)
    stdin: bool = False
    uncertain: bool = False
    reasons: list = field(default_factory=list)
    pipeline: int = 0
    index: int = 0

    def mark(self, reason):
        self.uncertain = True
        if reason not in self.reasons:
            self.reasons.append(reason)

    def __repr__(self):
        return (f"Stage(argv={self.argv!r}, redirects={self.redirects!r}, "
                f"nested={len(self.nested)}, uncertain={self.uncertain})")


class _Ctx:
    """Per-pass parse state. `safe` holds the names that may be resolved in
    this pass (exactly one writer, top-level and straight-line, found by the
    previous pass); `written` every name with a writer anywhere (None in the
    first pass)."""

    __slots__ = ("depth", "calls", "overflow", "safe", "written", "writes", "poison", "aliases")

    def __init__(self, safe=frozenset(), written=None):
        self.depth = 0
        self.calls = 0
        self.overflow = False
        self.safe = safe
        self.written = written
        self.writes = {}  # name -> [is_safe_writer, ...]
        self.poison = False  # eval/source of unknown text: no name can be trusted
        self.aliases = set()

    def write(self, name, safe=False):
        self.writes.setdefault(name, []).append(bool(safe))

    def safe_names(self):
        if self.poison:
            return set()
        return {n for n, w in self.writes.items() if w == [True]}

    def is_written(self, name):
        return self.written is not None and name in self.written


class ParseResult(list):
    """A list of Stage plus whole-result flags (see module docstring)."""

    def __init__(self, ctx=None):
        super().__init__()
        self._ctx = ctx or _Ctx()
        self.errors = []

    @property
    def too_complex(self):
        return self._ctx.overflow

    @property
    def uncertain(self):
        if self.errors or self._ctx.overflow:
            return True
        return any(s.uncertain for s in walk(self))


def walk(stages):
    """Every stage, nested ones included, depth first in source order."""
    for st in stages or ():
        yield st
        for sub in st.nested:
            yield from walk(sub)


def all_words(stages):
    """Every argv word of every stage at any depth (defence in depth: lets a
    rule notice a dangerous verb wherever it appears)."""
    return [w for st in walk(stages) for w in st.argv]


def command_name(word):
    """Basename of a command word, lower-cased, without .exe."""
    base = re.split(r"[\\/]", str(word))[-1].lower()
    return base[:-4] if base.endswith(".exe") else base


def _strip(argv):
    """(index of the effective command word or None, wrappers, scripts): the
    scripts are command texts a wrapper runs itself (`env -S`, `flock -c`,
    `sudo -s|-i ARGS`)."""
    i, n = 0, len(argv)
    wrappers, scripts = [], []
    while i < n:
        w = argv[i]
        if _ASSIGN.match(w):
            i += 1
            continue
        base = command_name(w)
        if base not in _PREFIX:
            break
        if base == "runuser" and not any(a in ("-u", "--user") or a.startswith("--user=")
                                         for a in argv[i + 1:]):
            break  # runuser [-l] USER -c CMD: like su, handled in _finish
        wrappers.append(base)
        i += 1
        opt_start = i
        vals = _WITH_VALUE.get(base, ())
        sudo_shell = False
        while i < n and (argv[i].startswith("-") or (base == "env" and _ASSIGN.match(argv[i]))):
            f = argv[i]
            if f == "--":
                i += 1
                break
            if f.startswith("--") or base == "arch" or not f.startswith("-") or len(f) == 1:
                if base == "env" and f.startswith("--split-string"):
                    if "=" in f:
                        scripts.append(f.split("=", 1)[1])
                        i += 1
                    else:
                        scripts.append(argv[i + 1] if i + 1 < n else "")
                        i += 2
                    continue
                if base == "sudo" and f in ("--shell", "--login"):
                    sudo_shell = True
                i += 2 if ("=" not in f and f in vals) else 1
                continue
            # a short-option cluster: -iu root, -0I{}, -n5, -iS 'cmd'
            letters, take_next, j = f[1:], False, 0
            while j < len(letters):
                ch = letters[j]
                rest = letters[j + 1:]
                if base == "env" and ch == "S":
                    if rest:
                        scripts.append(rest)
                    else:
                        scripts.append(argv[i + 1] if i + 1 < n else "")
                        take_next = True
                    break
                if base == "sudo" and ch in "si":
                    sudo_shell = True
                if "-" + ch in vals:
                    take_next = not rest
                    break
                j += 1
            i += 2 if take_next else 1
        if base in _NEEDS_ARG and i < n:
            i += 1
            if i < n and argv[i] == "--":
                i += 1
            if base == "flock" and i < n and argv[i] in ("-c", "--command"):
                scripts.append(argv[i + 1] if i + 1 < n else "")
                i = n
        if base == "taskset" and i < n and not any(
                o in ("-c", "--cpu-list") or o.startswith("--cpu-list=")
                or (o.startswith("-") and not o.startswith("--") and "c" in o) for o in argv[opt_start:i]):
            i += 1  # taskset MASK cmd
        if base == "coproc" and i + 1 < n and argv[i + 1] == "{":
            i += 1  # coproc NAME { ...; }
        if sudo_shell and i < n:
            scripts.append(" ".join(argv[i:]))  # sudo -s/-i joins its args into a shell command
    return (i if i < n else None), wrappers, scripts


def _opt_value(args, letter, longs=("--command",)):
    """Value of -c X / -cX / -qc X / --command X / --command=X, or None."""
    for j, a in enumerate(args):
        if a == "--":
            break
        for long_ in longs:
            if a == long_:
                return args[j + 1] if j + 1 < len(args) else None
            if a.startswith(long_ + "="):
                return a[len(long_) + 1:]
        if a.startswith("-") and not a.startswith("--") and letter in a[1:]:
            rest = a[a.index(letter, 1) + 1:]
            if rest:
                return rest
            return args[j + 1] if j + 1 < len(args) else None
    return None


def expand_wrappers(stage):
    """argv of the command that really runs (see module docstring), or None."""
    argv = stage.argv if isinstance(stage, Stage) else list(stage)
    idx, _, _ = _strip(argv)
    return None if idx is None else argv[idx:]


# ---------------------------------------------------------------------------
# Line continuations
# ---------------------------------------------------------------------------
def unfold(s):
    """Remove backslash-newline as bash does before word splitting: outside
    quotes and inside double quotes, not inside single quotes, comments or
    quoted heredoc bodies. Text without one is returned unchanged."""
    if "\\\n" not in s:
        return s
    n = len(s)
    out = []
    last = ""
    stack = []  # 'dq', 'bt' or [depth] for a $( )
    heredocs = []

    def put(t):
        nonlocal last
        if t:
            out.append(t)
            last = t[-1]

    def cont(k):
        return 2 if k + 1 < n and s[k] == "\\" and s[k + 1] == "\n" else 0

    i = 0
    while i < n:
        if cont(i):
            i += 2
            continue
        c = s[i]
        top = stack[-1] if stack else None
        if c == "\\":
            put(s[i:i + 2])
            i += 2
            continue
        if c == "$" and s[i + 1:i + 2] == "(":
            stack.append([1])
            put("$(")
            i += 2
            continue
        if c == "`":
            if top == "bt":
                stack.pop()
            else:
                stack.append("bt")
            put(c)
            i += 1
            continue
        if top == "dq":
            if c == '"':
                stack.pop()
            put(c)
            i += 1
            continue
        if c == '"':
            stack.append("dq")
            put(c)
            i += 1
            continue
        if c == "'":
            e = s.find("'", i + 1)
            end = n if e < 0 else e + 1
            put(s[i:end])
            i = end
            continue
        if c == "#" and (not last or last in " \t\n\r;&|()<>"):
            e = s.find("\n", i)
            end = n if e < 0 else e
            put(s[i:end])
            i = end
            continue
        if isinstance(top, list):
            if c == "(":
                top[0] += 1
            elif c == ")":
                top[0] -= 1
                if top[0] == 0:
                    stack.pop()
        if c == "<" and s[i + 1:i + 2] == "<":
            if s[i + 2:i + 3] == "<":
                put("<<<")
                i += 3
                continue
            put("<<")
            i += 2
            strip = s[i:i + 1] == "-"
            if strip:
                put("-")
                i += 1
            while i < n and s[i] in " \t":
                put(s[i])
                i += 1
            delim, quoted = "", False
            while i < n:
                if cont(i):
                    i += 2
                    continue
                if s[i] in _DELIM_STOP:
                    break
                if s[i] in "'\"":
                    quoted = True
                    e = s.find(s[i], i + 1)
                    end = n if e < 0 else e + 1
                    delim += s[i + 1:n if e < 0 else e]
                    put(s[i:end])
                    i = end
                    continue
                if s[i] == "\\":
                    quoted = True
                    delim += s[i + 1:i + 2]
                    put(s[i:i + 2])
                    i += 2
                    continue
                delim += s[i]
                put(s[i])
                i += 1
            if delim:
                heredocs.append((delim, strip, quoted))
            continue
        put(c)
        i += 1
        if c == "\n" and heredocs:
            for delim, strip, quoted in heredocs:
                while i < n:
                    parts = []
                    j = i
                    while j < n and s[j] != "\n":
                        if not quoted and s[j] == "\\":
                            if cont(j):
                                j += 2
                                continue
                            parts.append(s[j:j + 2])
                            j += 2
                            continue
                        parts.append(s[j])
                        j += 1
                    line = "".join(parts)
                    cmp = line.lstrip("\t") if strip else line
                    if cmp.endswith("\r"):
                        cmp = cmp[:-1]
                    if isinstance(top, list) and cmp == delim + ")":
                        put(line[:line.rfind(")")])
                        i = s.rfind(")", 0, j + 1)
                        break
                    nl = j < n
                    put(line + "\n" if nl else line)
                    i = j + 1 if nl else j
                    if cmp == delim:
                        break
            heredocs = []
    return "".join(out)


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------
def _capture_balanced(s, i, open_, close):
    """s[i] is the opening char; index after the matching close (or len(s))."""
    depth = 0
    j, n = i, len(s)
    while j < n:
        c = s[j]
        if c == "'":
            k = s.find("'", j + 1)
            j = n if k < 0 else k + 1
            continue
        if c == open_:
            depth += 1
        elif c == close:
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return n


def _budget_ok(ctx):
    if ctx.depth >= MAX_NEST or ctx.calls >= MAX_CALLS:
        ctx.overflow = True
        return False
    return True


def _parse_script(text, ctx, vars_):
    """Parse a command text (a -c script, eval args, a backtick body ...)."""
    if not _budget_ok(ctx):
        return None
    ctx.depth += 1
    try:
        res, _, _ = _lex(unfold(text), 0, False, ctx, dict(vars_))
    finally:
        ctx.depth -= 1
    return res


def _sub_at(s, i, ctx, vars_):
    """A substitution whose '(' is s[i]: (ParseResult | None, end, found)."""
    if not _budget_ok(ctx):
        e = _capture_balanced(s, i, "(", ")")
        return None, e, s[e - 1:e] == ")" and e > i + 1
    ctx.depth += 1
    try:
        res, end, found = _lex(s, i + 1, True, ctx, dict(vars_))
    finally:
        ctx.depth -= 1
    return res, end, found


def _body_subs(body, ctx, vars_):
    """Command texts in $( ) and backticks of an expanding text (heredoc body)."""
    out = []
    i, n = 0, len(body)
    while i < n:
        c = body[i]
        if c == "\\":
            i += 2
            continue
        if c == "$" and body[i + 1:i + 2] == "(":
            res, end, _ = _sub_at(body, i + 1, ctx, vars_)
            if res is not None:
                out.append(res)
            i = max(end, i + 2)
            continue
        if c == "`":
            k = body.find("`", i + 1)
            if k < 0:
                break
            res = _parse_script(body[i + 1:k], ctx, vars_)
            if res is not None:
                out.append(res)
            i = k + 1
            continue
        i += 1
    return out


_ANSI = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v",
         "e": "\x1b", "E": "\x1b", "\\": "\\", "'": "'", '"': '"', "?": "?"}


def _ansi_c(s, i):
    """Decode $'...' starting after the opening quote: (text, end, terminated)."""
    out = []
    n = len(s)
    while i < n:
        c = s[i]
        if c == "'":
            return "".join(out), i + 1, True
        if c == "\\" and i + 1 < n:
            d = s[i + 1]
            if d in _ANSI:
                out.append(_ANSI[d])
                i += 2
                continue
            if d == "x":
                m = re.match(r"[0-9A-Fa-f]{1,2}", s[i + 2:i + 4])
                if m:
                    out.append(chr(int(m.group(), 16)))
                    i += 2 + len(m.group())
                    continue
            if d in "uU":
                m = re.match(r"[0-9A-Fa-f]{1,%d}" % (4 if d == "u" else 8), s[i + 2:i + 10])
                if m:
                    try:
                        out.append(chr(int(m.group(), 16)))
                    except ValueError:
                        pass
                    i += 2 + len(m.group())
                    continue
            m = re.match(r"[0-7]{1,3}", s[i + 1:i + 4])
            if m:
                out.append(chr(int(m.group(), 8) & 0xFF))
                i += 1 + len(m.group())
                continue
            out.append(c + d)
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out), n, False


def _echo_value(res):
    """The output of `echo WORDS` / `printf WORD` with literal words, else None."""
    if res is None or len(res) != 1 or res.errors:
        return None
    st = res[0]
    if st.uncertain or st.nested or st.redirects or st.env or not st.argv or any(st.dynamic):
        return None
    name, args = command_name(st.argv[0]), st.argv[1:]
    if name == "echo":
        flags = ""
        while args and re.fullmatch(r"-[neE]+", args[0]):
            flags += args[0]
            args = args[1:]
        if "e" in flags and any("\\" in a for a in args):
            return None
        return " ".join(args)
    if name == "printf":
        if len(args) == 1 and "%" not in args[0] and "\\" not in args[0]:
            return args[0]
        if len(args) == 2 and args[0] == "%s" and "\\" not in args[1]:
            return args[1]
    return None


def _single(stage, ctx):
    res = ParseResult(ctx)
    res.append(stage)
    return res


class _Heredoc:
    __slots__ = ("delim", "quoted", "strip", "stage", "pipeline")

    def __init__(self, delim, quoted, strip, stage, pipeline):
        self.delim, self.quoted, self.strip, self.stage = delim, quoted, strip, stage
        self.pipeline = pipeline  # the stage's pipeline list (a later `| bash` runs the body)


class _Level:
    """One lexing level (top level or the inside of a substitution)."""

    def __init__(self, s, ctx, vars_):
        self.s = s
        self.ctx = ctx
        self.vars = vars_
        self.result = ParseResult(ctx)
        self.pipeline_no = 0
        self.pipeline = []
        self.stage = self._new_stage()
        self.pending = None
        self.parens = 0
        self.heredocs = []
        self.case_depth = 0
        self.compound = 0  # open if/while/for/case/{ ... (assignments there are not straight-line)
        self.prev_sep = None  # the operator before the current pipeline
        self.pending_writes = []  # candidate safe writers of the current pipeline
        self.redir_fd = None  # fd digits before the pending redirect
        self.dangling = False  # just after | && || (a heredoc body here is a syntax error)
        self._reset_word()

    @staticmethod
    def _new_stage():
        st = Stage()
        st._words = []
        st._procsub = {}  # word text -> ParseResult of a <( ) / >( ) word
        st._rdyn = set()  # redirect targets that are not literal
        st._fdx = set()  # indexes of input redirects on an fd other than 0 (3<<< x is not stdin)
        return st

    def _reset_word(self):
        self.buf = []
        self.unq = []
        self.started = False
        self.dyn = False

    # -- words ---------------------------------------------------------------
    def add(self, text, quoted=False, dyn=False):
        self.dangling = False
        self.buf.append(text)
        if not quoted:
            self.unq.append(text)
        self.started = True
        if dyn:
            self.dyn = True

    def in_assignment(self):
        """The word being built is the value of a prefix assignment."""
        if not _ASSIGN.match("".join(self.buf)):
            return False
        return all(_ASSIGN.match(w) for w, _ in self.stage._words)

    def add_value(self, value, quoted):
        """Insert an expansion's value, word-split when unquoted."""
        if quoted or self.in_assignment():
            self.add(value, quoted=True)
            self.started = True
            return
        if self.ctx.is_written("IFS") and "IFS" not in self.vars:
            self.dyn = True  # word splitting with an unknown IFS
        if value[:1] in " \t\n":
            self.end_word()
        ifs = self.vars.get("IFS", _MISSING)
        if isinstance(ifs, str) and ifs:
            parts = [p for p in re.split("[" + re.escape(ifs) + "]+", value) if p]
        else:
            parts = value.split()
        for k, p in enumerate(parts):
            if k:
                self.end_word()
            self.add(p)
        if value[-1:] in (" ", "\t", "\n"):
            self.end_word()

    def end_word(self):
        if not self.started:
            return
        text = "".join(self.buf)
        unq = "".join(self.unq)
        dyn = self.dyn
        if text not in ("[", "[[") and any(ch in unq for ch in "*?["):
            dyn = True
        if "{" in unq and "}" in unq and ("," in unq or ".." in unq):
            dyn = True
        if self.pending:
            self.stage.redirects.append((self.pending, text))
            self._note_fd()
            if dyn:
                self.stage._rdyn.add(text)
            self.pending = None
        else:
            words = self.stage._words
            if not dyn:
                if not words:
                    if text == "case":
                        self.case_depth += 1
                    elif text == "esac" and self.case_depth:
                        self.case_depth -= 1
                cmdpos = all(_ASSIGN.match(w) or command_name(w) in _PREFIX for w, _ in words)
                if cmdpos and text in _COMPOUND_OPEN:
                    self.compound += 1
                elif cmdpos and text in _COMPOUND_CLOSE and self.compound:
                    self.compound -= 1
                elif text == "{" and len(words) == 2 and words[0][0] == "function":
                    self.compound += 1
            words.append((text, dyn))
        self._reset_word()

    def _note_fd(self):
        if self.redir_fd is not None and self.redir_fd.strip("0"):
            self.stage._fdx.add(len(self.stage.redirects) - 1)
        self.redir_fd = None

    def take_fd(self):
        """Consume a pending fd number word (2>file, 0<<<x); True if there was one."""
        text = "".join(self.buf)
        if self.started and text.isdigit() and not self.dyn:
            self.redir_fd = text
            self._reset_word()
            return True
        self.redir_fd = None
        return False

    def error(self, msg):
        self.stage.mark(msg)
        if msg not in self.result.errors:
            self.result.errors.append(msg)

    # -- stages ----------------------------------------------------------------
    def end_stage(self):
        self.end_word()
        self.pending = None
        st = self.stage
        if st._words or st.redirects or st.nested or st.heredocs or st.uncertain or any(
                h.stage is st for h in self.heredocs):
            pos_ok = (self.ctx.depth == 0 and self.parens == 0 and self.compound == 0
                      and self.prev_sep in (None, ";") and not self.pipeline)
            for name, val, eligible in _finish(st, self.ctx, self.vars):
                if eligible and pos_ok:
                    if name in self.ctx.safe:
                        self.vars[name] = val
                    else:
                        self.vars.pop(name, None)
                    self.pending_writes.append(name)
                else:
                    self.vars.pop(name, None)
                    self.ctx.write(name, False)
            if not (st.argv or st.env or st.redirects or st.nested or st.heredocs or st.uncertain
                    or any(h.stage is st for h in self.heredocs)):
                self.stage = self._new_stage()
                return  # only brace-group words
            st.pipeline = self.pipeline_no
            st.index = len(self.pipeline)
            self.pipeline.append(st)
            self.result.append(st)
        self.stage = self._new_stage()

    def end_pipeline(self, sep=None):
        self.end_stage()
        for name in self.pending_writes:  # straight-line only if alone and not backgrounded
            safe = len(self.pipeline) == 1 and sep != "&"
            self.ctx.write(name, safe)
            if not safe:
                self.vars.pop(name, None)
        self.pending_writes = []
        for k, sh in enumerate(self.pipeline):
            if k and _script_file(sh):  # ... | bash x.sh: the file may itself be stdin (../dev/stdin)
                sh.mark("a script file runs with a pipe into it")
            if not _stdin_shell(sh) or any(op in ("<<", "<<<", "<&", "<>") or (op == "<" and not _is_stdin_path(t))
                                           for j, (op, t) in enumerate(sh.redirects)
                                           if j not in sh.__dict__.get("_fdx", ())):
                continue
            if k == 0:  # inherited stdin (exec 0<..., { bash; } <<<..., >(bash)): not visible here
                sh.mark("shell reads a script from stdin that is not visible")
                continue
            prev = self.pipeline[k - 1]
            val = _echo_value(_single(prev, self.ctx))
            texts = [val] if val else [t for op, t in prev.redirects if op == "<<<"]
            if texts:  # echo 'cmd' | bash, cat <<< 'cmd' | bash: the shell runs the text
                for text in texts:
                    res = _parse_script(text, self.ctx, self.vars)
                    if res is not None:
                        sh.nested.append(res)
            elif not any(op == "<<" for op, _ in prev.redirects):  # heredoc bodies: _attach_body
                sh.mark("shell reads a script from a pipe that is not a literal")
        if self.pipeline:
            self.pipeline_no += 1
        self.pipeline = []
        self.prev_sep = sep

    # -- constructs ------------------------------------------------------------
    def subst(self, i_paren, from_, quoted, raw=False):
        res, end, found = _sub_at(self.s, i_paren, self.ctx, self.vars)
        if res is not None:
            self.stage.nested.append(res)
            if raw:
                self.stage._procsub[self.s[from_:end]] = res
        if not found:
            self.error("unterminated $(")
        val = None if raw or not found else _echo_value(res)
        if val is not None:
            self.add_value(val, quoted)
        else:
            self.add(self.s[from_:end], quoted=quoted, dyn=True)
        return end

    def arith(self, i, from_, quoted, as_word=True):
        """$(( )) / (( )): its text is followed as commands (bash falls back
        to a subshell when it is not arithmetic); s[i] is the first '('."""
        s = self.s
        e = _capture_balanced(s, i, "(", ")")
        closed = s[e - 2:e] == "))" and e - i >= 4
        inner = s[i + 2:e - 2] if closed else s[i + 2:e]
        if not closed:
            self.error("unterminated ((")
        res = _parse_script(inner, self.ctx, self.vars)
        if res is not None:
            self.stage.nested.append(res)
        _arith_scan(inner, self.ctx, self.vars, self.stage)
        if as_word:
            self.add(s[from_:e], quoted=quoted, dyn=True)
        return e

    def tick(self, i, quoted):
        s, n = self.s, len(self.s)
        j = i + 1
        body = []
        while j < n and s[j] != "`":
            if s[j] == "\\" and j + 1 < n and s[j + 1] in "`\\$":
                body.append(s[j + 1])
                j += 2
                continue
            body.append(s[j])
            j += 1
        terminated = j < n
        end = j + 1 if terminated else n
        res = _parse_script("".join(body), self.ctx, self.vars)
        if res is not None:
            self.stage.nested.append(res)
        if not terminated:
            self.error("unterminated backtick")
        val = _echo_value(res) if terminated else None
        if val is not None:
            self.add_value(val, quoted)
        else:
            self.add(s[i:end], quoted=quoted, dyn=True)
        return end

    def var(self, name, literal, quoted):
        if name == "IFS" and not quoted and "IFS" not in self.vars:
            if self.ctx.is_written("IFS"):
                self.add(literal, dyn=True)
            else:
                self.end_word()  # default IFS: a word separator
            return
        val = self.vars.get(name, _MISSING)
        if val is _MISSING or val is None:
            self.add(literal, quoted=quoted, dyn=True)
        else:
            self.add_value(val, quoted)

    def dollar(self, i, quoted):
        s, n = self.s, len(self.s)
        nx = s[i + 1:i + 2]
        if nx == "(":
            if s[i + 2:i + 3] == "(":
                return self.arith(i + 1, i, quoted)
            return self.subst(i + 1, i, quoted)
        if nx == "{":
            depth, k = 0, i + 1
            while k < n:
                if s[k] == "{":
                    depth += 1
                elif s[k] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                k += 1
            if k >= n:
                self.error("unterminated ${")
                self.add(s[i:], quoted=quoted, dyn=True)
                return n
            inner = s[i + 2:k]
            m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)(?:\[[^\]]*\])?:?=", inner)
            if m:
                self.ctx.write(m.group(1), False)  # ${x:=v} / ${x=v} assigns
                self.vars.pop(m.group(1), None)
            if inner.endswith("@P"):
                self.stage.mark("${...@P} prompt expansion can run commands")
            if _NAME.fullmatch(inner):
                self.var(inner, s[i:k + 1], quoted)
            else:
                if "$(" in inner or "`" in inner:
                    self.stage.nested.extend(_body_subs(inner, self.ctx, self.vars))
                self.add(s[i:k + 1], quoted=quoted, dyn=True)
            return k + 1
        if not quoted and nx == "'":
            text, end, ok = _ansi_c(s, i + 2)
            self.add(text, quoted=True)
            if not ok:
                self.error("unterminated $'")
            return end
        if not quoted and nx == '"':
            return i + 1  # $"..." locale string: a double-quoted string
        m = _NAME.match(s, i + 1)
        if m:
            self.var(m.group(), s[i:m.end()], quoted)
            return m.end()
        if nx and nx in "0123456789@*#?$!-":
            self.add(s[i:i + 2], quoted=quoted, dyn=True)
            return i + 2
        self.add("$", quoted=quoted)
        return i + 1

    def dquote(self, i):
        s, n = self.s, len(self.s)
        self.started = True
        while i < n and s[i] != '"':
            c = s[i]
            if c == "\\" and i + 1 < n and s[i + 1] in '$`"\\\n':
                if s[i + 1] != "\n":
                    self.add(s[i + 1], quoted=True)
                i += 2
                continue
            if c == "$":
                i = self.dollar(i, quoted=True)
                continue
            if c == "`":
                i = self.tick(i, quoted=True)
                continue
            self.add(c, quoted=True)
            i += 1
        if i >= n:
            self.error("unterminated double quote")
            return n
        return i + 1

    def heredoc_op(self, i):
        """At '<<' (not '<<<'): read the delimiter word, register the body."""
        s, n = self.s, len(self.s)
        i += 2
        strip = s[i:i + 1] == "-"
        if strip:
            i += 1
        while i < n and s[i] in " \t":
            i += 1
        delim, quoted = "", False
        while i < n and s[i] not in _DELIM_STOP:
            c = s[i]
            if c in "'\"":
                quoted = True
                k = s.find(c, i + 1)
                delim += s[i + 1:n if k < 0 else k]
                i = n if k < 0 else k + 1
                continue
            if c == "\\":
                quoted = True
                delim += s[i + 1:i + 2]
                i += 2
                continue
            delim += c
            i += 1
        self.stage.stdin = True
        if delim:
            self.stage.redirects.append(("<<", delim))
            self._note_fd()
            self.heredocs.append(_Heredoc(delim, quoted, strip, self.stage, self.pipeline))
        return i

    def read_bodies(self, i, nested):
        """Heredoc bodies start on the next line and end at the delimiter line."""
        s, n = self.s, len(self.s)
        for h in self.heredocs:
            body = []
            while i < n:
                k = s.find("\n", i)
                line = s[i:n if k < 0 else k]
                if line.endswith("\r"):
                    line = line[:-1]
                cmp = line.lstrip("\t") if h.strip else line
                if nested and cmp == h.delim + ")":
                    i += line.rfind(")")  # "EOF)" also closes the $(
                    break
                i = n if k < 0 else k + 1
                if cmp == h.delim:
                    break
                body.append(line)
            _attach_body(h, "\n".join(body), self.ctx, self.vars)
        self.heredocs = []
        return i


def _attach_body(h, body, ctx, vars_):
    st = h.stage
    st.heredocs.append((h.delim, h.quoted, body))
    if _stdin_shell(st) or any(_stdin_shell(x) for x in h.pipeline if x is not st and x.index > st.index):
        res = _parse_script(body, ctx, vars_)  # the shell runs its stdin
        if res is not None:
            st.nested.append(res)
    elif not h.quoted:
        st.nested.extend(_body_subs(body, ctx, vars_))


def _shell_name(name):
    """bash5, zsh-5.9, sh.exe -> the shell, or None."""
    base = re.sub(r"[-.]?[0-9][0-9.]*\Z", "", name)
    return base if base in SHELLS else None


def _shell_mode(args):
    """('c', script|None) | ('stdin', None) | ('file', path) for a shell's args."""
    i, c_flag, s_flag = 0, False, False
    while i < len(args):
        a = args[i]
        if a in ("--", "-"):
            i += 1
            break
        if a.startswith("--"):
            i += 2 if a in ("--rcfile", "--init-file") else 1
            continue
        if a[:1] in ("-", "+") and len(a) > 1:
            flags = a[1:]
            if a[0] == "-" and "c" in flags:
                c_flag = True
            if a[0] == "-" and "s" in flags:
                s_flag = True
            i += 1
            if "o" in flags or "O" in flags:
                i += 1
            continue
        break
    rest = args[i:]
    if c_flag:
        return "c", (rest[0] if rest else None)
    if s_flag or not rest or _is_stdin_path(rest[0]):
        return "stdin", None
    return "file", rest[0]


def _norm_path(path):
    if not path.startswith("/"):
        return path
    return re.sub(r"^/+", "/", posixpath.normpath(path))


def _is_stdin_path(path):
    return path == "-" or bool(_STDIN_PATH.fullmatch(_norm_path(path)))


def _is_dev_path(path):
    return _norm_path(path).startswith(("/dev/", "/proc/"))


def _source_args(args):
    return args[1:] if args[:1] == ["--"] else args


def _sudo_shell(argv):
    """sudo -s / -i / --shell / --login with no command: an interactive shell."""
    return any(t in ("--shell", "--login") or (t.startswith("-") and not t.startswith("--")
                                                 and ("s" in t[1:] or "i" in t[1:])) for t in argv)


def _stdin_shell(st):
    idx, wrappers, _ = _strip(st.argv)
    if idx is None:
        return "sudo" in wrappers and _sudo_shell(st.argv)
    name, args = command_name(st.argv[idx]), st.argv[idx + 1:]
    if _shell_name(name):
        return _shell_mode(args)[0] == "stdin"
    if name in ("su", "sg") or (name == "runuser" and "runuser" not in wrappers):
        return _opt_value(args, "c") is None  # su [-] [user]: a shell on stdin
    if name in ("source", "."):
        args = _source_args(args)
        return bool(args) and _is_stdin_path(args[0])
    return False


def _fd0_inputs(st):
    fdx = st.__dict__.get("_fdx", ())
    return [(op, t) for j, (op, t) in enumerate(st.redirects)
            if j not in fdx and op in ("<", "<<", "<<<", "<&", "<>")]


def _script_file(st):
    """The stage runs a script FILE (bash x.sh, source f), not stdin, -c or <( )."""
    idx, _, _ = _strip(st.argv)
    if idx is None:
        return False
    name, args = command_name(st.argv[idx]), st.argv[idx + 1:]
    procsub = st.__dict__.get("_procsub", {})
    if _shell_name(name):
        mode, path = _shell_mode(args)
    elif name in ("source", "."):
        sargs = _source_args(args)
        if not sargs or _is_stdin_path(sargs[0]):
            return False
        mode, path = "file", sargs[0]
    else:
        return False
    return mode == "file" and path not in procsub and not _is_dev_path(path)


def _script_from_path(st, path, scripts, what):
    """A script file argument / `<` target: <( echo ... ) is visible, fd paths are not."""
    procsub = st.__dict__.get("_procsub", {})
    if path in procsub:
        val = _echo_value(procsub[path])
        if val:
            scripts.append(val)
        else:
            st.mark(f"{what} reads a script from a process substitution that is not a literal")
        return True
    if _is_dev_path(path) and not _is_stdin_path(path):
        st.mark(f"{what} reads a script from a device or /proc path: {path!r}")
        return True
    return False


def _stdin_inputs(st, scripts, what):
    """Scripts a stdin-reading shell gets from its own redirects."""
    rdyn = st.__dict__.get("_rdyn", set())
    fdx = st.__dict__.get("_fdx", ())
    for j, (op, t) in enumerate(st.redirects):
        if j in fdx:
            continue
        if op == "<<<":
            scripts.append(t)
            if t in rdyn:
                st.mark(f"{what} runs a here-string that is not a literal")
        elif op == "<":
            if _is_stdin_path(t):
                continue
            if not _script_from_path(st, t, scripts, what):
                st.mark(f"{what} reads its script from a file: {t!r}")
        elif op in ("<&", "<>"):
            st.mark(f"{what} reads its script from a file descriptor")


def _arith_scan(text, ctx, vars_, st):
    """Arithmetic text: record assigned names; a variable whose value holds an
    array subscript with a substitution runs it (x='a[$(cmd)]'; $((x)))."""
    for m in _ARITH_WRITE.finditer(text):
        name = m.group(1) or m.group(2)
        ctx.write(name, False)
        vars_.pop(name, None)
    for name in _IDENT.findall(text):
        val = vars_.get(name)
        if isinstance(val, str) and "[" in val and ("$(" in val or "`" in val):
            st.mark("arithmetic on a value with a command in an array subscript")


def _finish(st, ctx, vars_):
    """Turn the collected words into argv/env and follow what the stage runs.
    Returns the variable writes [(name, value|None, eligible_as_safe)]."""
    words = st.__dict__.pop("_words", [])
    writes = []
    if len(words) >= 2 and words[0][0] == "function":  # function f { ...: the body runs
        words = words[2:]
    while words and words[0][0] == "{":
        words.pop(0)
    while words and words[-1][0] == "}" and not words[-1][1]:
        words.pop()
    while True:  # time { ...; }, if { ...; }, ! { ...; }: a brace group after keywords/wrappers
        bi, _, _ = _strip([w for w, _ in words])
        if bi is None or words[bi][0] != "{" or words[bi][1]:
            break
        if bi >= 2 and command_name(words[bi - 2][0]) == "coproc":
            writes.append((words[bi - 1][0], None, False))  # coproc NAME sets NAME (an array)
            del words[bi - 1:bi + 1]  # coproc NAME { ...; }: drop NAME too
        else:
            del words[bi]
    k = 0
    while k < len(words):
        m = _ASSIGN.match(words[k][0])
        if not m:
            break
        st.env[m.group(1)] = None if (words[k][1] or m.group(2)) else m.group(3)
        k += 1
    st.argv = [w for w, _ in words[k:]]
    st.dynamic = [d for _, d in words[k:]]
    argv = st.argv

    # Variable writers (see parse()).
    assigns = list(st.env.items())  # (name, value|None) of every assignment in this stage
    for name, val in st.env.items():
        writes.append((name, val, not argv))  # a prefix assignment on a command is not a shell write
    idx, wrappers, scripts = _strip(argv)
    st.wrappers = wrappers
    for w, d in zip(argv[:len(argv) if idx is None else idx], st.dynamic):  # then x=rm, env X=1 ...
        m = _ASSIGN.match(w)
        if m:
            writes.append((m.group(1), None, False))
            assigns.append((m.group(1), None if (d or m.group(2)) else m.group(3)))
    if idx is not None:
        # The writer is the command after wrappers (builtin printf -v x, command export x=..);
        # a write behind any wrapper is never straight-line.
        head, rest, rdyn = command_name(argv[idx]), argv[idx + 1:], st.dynamic[idx + 1:]
        flags = [w for w in rest if w.startswith(("-", "+"))]
        if head in _DECLARE:
            if any("n" in f for f in flags):
                ctx.poison = True  # declare -n: a name reference writes another name
            for w, d in zip(rest, rdyn):
                m = _ASSIGN.match(w)
                if m:
                    val = None if (d or m.group(2)) else m.group(3)
                    writes.append((m.group(1), val,
                                   head in ("export", "readonly") and not flags and not wrappers))
                    assigns.append((m.group(1), val))
                elif _NAME.fullmatch(w) and head not in ("export", "readonly"):
                    writes.append((w, None, False))
        elif head in ("unset", "read", "mapfile", "readarray"):
            names = [w for w in rest if _NAME.fullmatch(w)]
            if head in ("mapfile", "readarray"):
                names.append("MAPFILE")
            if head == "read":
                names.append("REPLY")
            writes.extend((w, None, False) for w in names)
        elif head == "getopts":
            writes.extend((w, None, False) for w in rest[1:2] + ["OPTARG", "OPTIND"])
        elif head in ("printf", "wait"):
            for j, w in enumerate(rest):  # printf -v NAME / wait -p NAME
                opt = "-v" if head == "printf" else "-p"
                if w == opt and j + 1 < len(rest):
                    writes.append((rest[j + 1].split("[")[0], None, False))
                elif w.startswith(opt) and len(w) > 2:
                    writes.append((w[2:].split("[")[0], None, False))
        elif head in ("for", "select") and rest:
            writes.append((rest[0], None, False))
        elif head == "let":
            _arith_scan(" ".join(rest), ctx, vars_, st)
        if head in _NAME_ARG_CMDS and any("[" in w and ("$(" in w or "`" in w) for w in rest):
            st.mark("an array subscript in a variable name runs a command")
    for name, val in assigns:  # GIT_SSH_COMMAND=..., EDITOR=...: some program runs the value
        if name.startswith("GIT_CONFIG") or name == "GIT_EXEC_PATH":
            st.mark(f"{name} changes the git configuration or programs")
        if name in _CMD_ENV_VARS:
            if val is None:
                st.mark(f"{name} is set to a value that is not a literal")
            elif val:
                scripts.append(val)
    if idx is None:
        if "sudo" in wrappers and _sudo_shell(argv):
            _stdin_inputs(st, scripts, "sudo")  # sudo -s <<< 'cmd'
        _nest(st, scripts, ctx, vars_)
        return writes
    if st.dynamic[idx]:
        st.mark(f"command word is not a literal: {argv[idx]!r}")
    name, args = command_name(argv[idx]), argv[idx + 1:]
    if name in ctx.aliases:
        st.mark(f"command word is an alias: {argv[idx]!r}")
    if _shell_name(name):
        hooks = set(st.env) | {m.group(1) for w in argv[:idx] for m in [_ASSIGN.match(w)] if m}
        hooks |= set(ctx.writes)
        if hooks & _SHELL_HOOK_VARS:
            st.mark("a shell hook variable (PS4, PROMPT_COMMAND, BASH_ENV, ENV ...) is set")
        mode, script = _shell_mode(args)
        if {"xargs", "parallel"} & set(wrappers):
            holders = _placeholders(argv[:idx])
            if (mode != "c" or script is None or script.startswith(":::")
                    or any(h in script for h in holders)):
                st.mark("xargs/parallel builds the shell script from its input")
        if mode == "c" and script is not None:
            scripts.append(script)
        elif mode == "file":
            _script_from_path(st, script, scripts, name)
        elif mode == "stdin":
            _stdin_inputs(st, scripts, name)
    elif name == "eval":
        eargs = args[1:] if args[:1] == ["--"] else args
        if any(st.dynamic[idx + 1:]):
            ctx.poison = True  # eval of text we cannot see may assign anything
        scripts.append(" ".join(eargs))
    elif name == "trap":
        targs = args[1:] if args[:1] == ["--"] else args
        if targs and targs[0] not in ("-p", "-l", "-P"):
            scripts.append(targs[0])
    elif name == "watch":
        j = 0
        while j < len(args) and args[j].startswith("-"):
            j += 2 if args[j] in ("-n", "--interval", "-q", "--equexit") else 1
        if args[j:]:
            scripts.append(" ".join(args[j:]))
    elif name in ("su", "runuser", "sg", "script"):
        val = _opt_value(args, "c")
        if val is not None:
            scripts.append(val)
        elif name != "script":
            _stdin_inputs(st, scripts, name)  # su <<< 'cmd'
    elif name in ("source", "."):
        sargs = _source_args(args)
        if sargs and _is_stdin_path(sargs[0]):
            _stdin_inputs(st, scripts, name)
        elif sargs and _script_from_path(st, sargs[0], scripts, name):
            pass
        else:
            ctx.poison = True  # a sourced file may assign anything
    elif name == "alias":
        for w in args:
            m = re.match(r"([^=\s]+)=(.*)\Z", w, re.S)
            if m:
                ctx.aliases.add(m.group(1))
                scripts.append(m.group(2))
    elif name == "git":
        scripts.extend(_git_scripts(args, st))
    elif name == "find":
        for w in _find_exec(st, idx, ctx, vars_):
            ctx.write(w[0], False)
    if {"xargs", "parallel"} & set(wrappers):
        holders = _placeholders(argv[:idx])
        if any(h in argv[idx] for h in holders):
            st.mark("xargs/parallel puts its input in the command word")
        if name in ("eval", "source", "."):
            st.mark("xargs/parallel feeds its input to eval/source")
    if "parallel" in wrappers:
        seps = (":::", "::::", ":::+", "::::+")
        if argv[idx] in seps:  # parallel ::: 'cmd' ...: each argument is a command line
            op = argv[idx]
            for w in argv[idx + 1:]:
                if w in seps:
                    op = w
                    continue
                if op.startswith("::::"):
                    st.mark("parallel reads command lines from a file")
                else:
                    scripts.append(w)
        elif any(ch.isspace() for ch in argv[idx]):
            end = next((j for j in range(idx, len(argv)) if argv[j] in seps), len(argv))
            scripts.append(" ".join(argv[idx:end]))  # parallel 'rm -rf {}' ::: x: the word is a command line
    if _script_file(st) and _fd0_inputs(st):
        st.mark("a script file runs while stdin is redirected (the file may be stdin)")
    _nest(st, scripts, ctx, vars_)
    return writes


def _nest(st, scripts, ctx, vars_):
    for text in scripts:
        res = _parse_script(text, ctx, vars_)
        if res is not None:
            st.nested.append(res)


def _placeholders(prefix):
    """Replacement strings xargs/parallel substitute into the command."""
    out = {"{}", "{.}", "{/}", "{//}", "{/.}", "{#}", "{%}"}
    for j, w in enumerate(prefix):
        if w == "-I" and j + 1 < len(prefix):
            out.add(prefix[j + 1])
        elif w.startswith("-I") and len(w) > 2:
            out.add(w[2:])
        elif w.startswith("--replace="):
            out.add(w.split("=", 1)[1])
        elif w.startswith("-i") and len(w) > 2:
            out.add(w[2:])
    out.discard("")
    return out


def _git_config_pairs(rest):
    """KEY VALUE of `git config [opts] [set] KEY VALUE`."""
    pos, j = [], 0
    while j < len(rest):
        w = rest[j]
        if w in ("-f", "--file", "--blob", "--type", "--default", "--comment", "--value"):
            j += 2
            continue
        if not w.startswith("-"):
            pos.append(w)
        j += 1
    if pos and pos[0] in ("set", "add"):
        pos = pos[1:]
    return [(pos[0], pos[1])] if len(pos) >= 2 else []


def _git_scripts(args, st):
    """Commands git runs from settings: -c KEY=VALUE, git config KEY VALUE."""
    pairs = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-c", "--config") and i + 1 < len(args):
            kv, i = args[i + 1], i + 2
        elif a.startswith("-c") and len(a) > 2:
            kv, i = a[2:], i + 1
        elif a.startswith("--config="):
            kv, i = a[len("--config="):], i + 1
        elif a == "--config-env" or a.startswith("--config-env="):
            st.mark("git --config-env takes a setting from the environment")
            i += 2 if a == "--config-env" else 1
            continue
        elif a in ("-C", "--git-dir", "--work-tree", "--namespace", "--exec-path"):
            i += 2
            continue
        elif a.startswith("-"):
            i += 1
            continue
        else:
            if a == "config":
                pairs.extend(_git_config_pairs(args[i + 1:]))
            break
        if "=" in kv:
            pairs.append(tuple(kv.split("=", 1)))
    scripts = []
    if any("ext::" in a for a in args):
        st.mark("git ext:: transport runs a command")
    for key, val in pairs:
        k = key.lower()
        if val and "ext::" in val:
            st.mark("git ext:: transport runs a command")
        if _GIT_RISKY_KEYS.fullmatch(k):
            st.mark(f"git setting {key} runs or loads something else")
            if val and not _GIT_RISKY_NO_NEST.fullmatch(k):
                scripts.append(val[1:] if val.startswith("!") else val)
            continue
        if not _GIT_CMD_KEYS.fullmatch(k):
            if val and not _GIT_DATA_KEYS.fullmatch(k) and any(ch.isspace() or ch == "/" for ch in val):
                st.mark(f"unrecognised git setting {key} with a command-like value")
            continue
        if not val:
            continue
        if k.startswith("alias."):
            scripts.append(val[1:] if val.startswith("!") else "git " + val)
        elif k.startswith("credential"):
            if val.startswith("!"):
                scripts.append(val[1:])
            elif "/" in val or " " in val:
                scripts.append(val)
        elif k == "core.fsmonitor":
            if val.lower() not in ("true", "false", "yes", "no", "on", "off", "0", "1"):
                scripts.append(val)
        else:
            scripts.append(val[1:] if val.startswith("!") else val)
    return scripts


def _find_exec(st, idx, ctx, vars_):
    argv, dyn = st.argv, st.dynamic
    writes = []
    j = idx + 1
    while j < len(argv):
        if argv[j] in ("-exec", "-execdir", "-ok", "-okdir"):
            k = j + 1
            while k < len(argv) and argv[k] not in (";", "+"):
                k += 1
            if k > j + 1:
                if not _budget_ok(ctx):
                    return writes
                ctx.calls += 1
                ctx.depth += 1
                sub = Stage()
                sub._words = list(zip(argv[j + 1:k], dyn[j + 1:k]))
                try:
                    writes.extend(_finish(sub, ctx, dict(vars_)))
                finally:
                    ctx.depth -= 1
                res = ParseResult(ctx)
                res.append(sub)
                st.nested.append(res)
            j = k
        j += 1
    return writes


def _lex(s, start, nested, ctx, vars_):
    """Lex s from start. Returns (ParseResult, end, found_closing_paren)."""
    ctx.calls += 1
    lv = _Level(s, ctx, vars_)
    i, n = start, len(s)
    while i < n:
        c = s[i]
        nx = s[i + 1:i + 2]
        if c == "'":
            k = s.find("'", i + 1)
            if k < 0:
                lv.add(s[i + 1:], quoted=True)
                lv.error("unterminated single quote")
                i = n
            else:
                lv.add(s[i + 1:k], quoted=True)
                i = k + 1
            continue
        if c == '"':
            i = lv.dquote(i + 1)
            continue
        if c == "\\":
            if nx == "\n":
                i += 2
                continue
            lv.add(nx, quoted=True)
            i += 2
            continue
        if c == "$":
            i = lv.dollar(i, quoted=False)
            continue
        if c == "`":
            i = lv.tick(i, quoted=False)
            continue
        if c in "<>" and nx == "(":
            lv.end_word()
            i = lv.subst(i + 1, i, quoted=False, raw=True)
            lv.end_word()
            continue
        if c == "<" and nx == "<":
            if not lv.take_fd():  # 0<<< / 3<<: an fd number
                lv.end_word()
            if s[i + 2:i + 3] == "<":
                lv.stage.stdin = True
                lv.pending = "<<<"
                i += 3
            else:
                i = lv.heredoc_op(i)
            continue
        if c == "#" and not lv.started:
            k = s.find("\n", i)
            i = n if k < 0 else k
            continue
        if c == "\n" and lv.heredocs:
            if lv.dangling:
                lv.error("heredoc body after a dangling | && || (bash syntax error)")
            lv.end_pipeline(";")
            i = lv.read_bodies(i + 1, nested)
            continue
        if c in "\n;":
            lv.end_pipeline(";")
            i += 1
            continue
        if c == "&":
            if nx == "&":
                lv.end_pipeline("&&")
                lv.dangling = True
                i += 2
            elif nx == ">":
                lv.end_word()
                if s[i + 2:i + 3] == ">":
                    lv.pending = "&>>"
                    i += 3
                else:
                    lv.pending = "&>"
                    i += 2
            else:
                lv.end_pipeline("&")
                i += 1
            continue
        if c == "|":
            if nx == "|":
                lv.end_pipeline("||")
                i += 2
            else:
                lv.end_stage()
                i += 2 if nx == "&" else 1
            lv.dangling = True
            continue
        if c in "<>":
            fdvar = re.fullmatch(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", "".join(lv.buf)) if lv.started else None
            if fdvar and not lv.dyn:
                ctx.write(fdvar.group(1), False)  # exec {x}>file assigns an fd number to x
                lv.vars.pop(fdvar.group(1), None)
                lv._reset_word()
            elif not lv.take_fd():  # an fd number: 2>file
                lv.end_word()
            if nx == "&":  # descriptor duplication (2>&1, <&0, >&-) or >&file
                j = i + 2
                if j < n and (s[j].isdigit() or s[j] == "-"):
                    while j < n and (s[j].isdigit() or s[j] == "-"):
                        j += 1
                    if c == "<":
                        lv.stage.stdin = True
                        lv.stage.redirects.append(("<&", s[i + 2:j]))
                    i = j
                    continue
                if c == "<":
                    i = j
                    continue
                lv.pending = ">&"
                i = j
                continue
            if c == ">":
                if nx == ">":
                    lv.pending, i = ">>", i + 2
                elif nx == "|":
                    lv.pending, i = ">|", i + 2
                else:
                    lv.pending, i = ">", i + 1
            else:
                lv.stage.stdin = True
                if nx == ">":
                    lv.pending, i = "<>", i + 2
                else:
                    lv.pending, i = "<", i + 1
            continue
        if c == ")" and lv.case_depth and lv.parens == 0:  # case pattern) commands
            lv.end_stage()
            i += 1
            continue
        if c == "(" and lv.case_depth and not lv.started:
            i += 1  # "(pattern)" opener; a subshell here still splits on ; and )
            continue
        if c == ")" and nested and lv.parens == 0:
            lv.end_pipeline(")")
            return lv.result, i + 1, True
        if c == "(":
            if lv.started and lv.in_assignment():  # arr=(a b): data, but follow $( )
                e = _capture_balanced(s, i, "(", ")")
                lv.stage.nested.extend(_body_subs(s[i + 1:e - 1], ctx, lv.vars))
                lv.add(s[i:e], dyn=True)
                i = e
                continue
            if nx == ")":  # f() { ...; }: the name is its own stage, the body follows
                lv.end_stage()
                i += 2
                continue
            if nx == "(" and not lv.started:
                i = lv.arith(i, i, quoted=False, as_word=False)
                continue
            lv.parens += 1
            lv.end_word()
            i += 1
            continue
        if c == ")":
            if lv.parens:
                lv.parens -= 1
            lv.end_word()
            i += 1
            continue
        if c in _WS:
            lv.end_word()
            i += 1
            continue
        lv.add(c)
        i += 1
    if lv.pending:
        lv.end_word()
    lv.end_pipeline()
    return lv.result, n, False


def _pass(s, safe, written):
    ctx = _Ctx(safe, written)
    res, _, _ = _lex(s, 0, False, ctx, {})
    return res, ctx


def parse(command):
    """Parse a Bash command line into stages (see module docstring).

    Variables are resolved soundly by repeated passes: the first pass resolves
    nothing and records every writer of every name; a name is then resolved
    only if it has exactly one writer, that writer is a top-level,
    unconditional, straight-line assignment, and nothing poisons the table
    (eval/source of unknown text, declare -n). Passes repeat (the safe set
    only shrinks) until the set is stable."""
    s = unfold(str(command or ""))
    res, ctx = _pass(s, frozenset(), None)
    if not ctx.writes and not ctx.poison:
        return res
    safe, written = ctx.safe_names(), set(ctx.writes)
    for _ in range(6):
        res, ctx = _pass(s, frozenset(safe), frozenset(written))
        new_safe = ctx.safe_names() & safe
        new_written = written | set(ctx.writes)
        if new_safe == safe and new_written == written:
            return res
        safe, written = new_safe, new_written
    res, _ = _pass(s, frozenset(), frozenset(written))
    return res
