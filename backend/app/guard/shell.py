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
                   ``watch cmd``, ``su -c cmd``, and text fed to a shell's
                   stdin: a heredoc or here-string (``bash <<EOF``), a heredoc
                   piped into a later shell stage (``cat <<EOF | bash``), or
                   literal ``echo``/``printf`` output piped into one
                   (``echo 'cmd' | sh``). ``case`` patterns end at ``)``, so
                   the commands after them are separate stages.
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

Data vs. command
----------------
Single-quoted text, quoted heredoc bodies (``<<'EOF'``), ``\\$`` escapes and
here-strings fed to non-shells are data: they are never parsed as commands
(so a commit message is not a command). The one exception is input fed to a
shell's stdin (``bash <<'EOF' ... EOF``), which the shell executes.

Variables
---------
``NAME=value`` assignment-only stages and ``export|declare|local|readonly|
typeset NAME=value`` record values; later ``$NAME`` / ``${NAME}`` outside
single quotes are replaced by them (word-split when unquoted, so
``x="rm -rf"; $x ~`` gives ``rm -rf ~``). ``$(echo WORDS)`` / ``$(printf
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

import re
from dataclasses import dataclass, field

__all__ = [
    "MAX_CALLS",
    "MAX_NEST",
    "ParseResult",
    "Stage",
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

SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "ash", "mksh", "fish", "rbash"})

# Wrappers and shell keywords that run the command after them.
_PREFIX = frozenset({
    "sudo", "doas", "env", "command", "builtin", "nohup", "time", "exec", "xargs", "nice",
    "ionice", "timeout", "stdbuf", "busybox", "winpty", "setsid", "chronic",
    "if", "then", "else", "elif", "do", "while", "until", "!",
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
}
_NEEDS_ARG = frozenset({"timeout"})  # its first plain word is a duration
_DECLARE = frozenset({"export", "declare", "typeset", "local", "readonly"})
_MISSING = object()


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
    __slots__ = ("depth", "calls", "overflow")

    def __init__(self):
        self.depth = 0
        self.calls = 0
        self.overflow = False


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


def command_name(word):
    """Basename of a command word, lower-cased, without .exe."""
    base = re.split(r"[\\/]", str(word))[-1].lower()
    return base[:-4] if base.endswith(".exe") else base


def _strip(argv):
    """Index of the effective command word, wrappers stripped, `env -S` strings."""
    i, n = 0, len(argv)
    wrappers, split = [], []
    while i < n:
        w = argv[i]
        if _ASSIGN.match(w):
            i += 1
            continue
        base = command_name(w)
        if base not in _PREFIX:
            break
        wrappers.append(base)
        i += 1
        while i < n and (argv[i].startswith("-") or (base == "env" and _ASSIGN.match(argv[i]))):
            f = argv[i]
            if f == "--":
                i += 1
                break
            if base == "env" and f in ("-S", "--split-string"):
                split.append(argv[i + 1] if i + 1 < n else "")
                i += 2
                continue
            if base == "env" and (f.startswith("--split-string=") or (f.startswith("-S") and len(f) > 2)):
                split.append(f.split("=", 1)[1] if f.startswith("--") else f[2:])
                i += 1
                continue
            i += 2 if f in _WITH_VALUE.get(base, ()) else 1
        if base in _NEEDS_ARG and i < n:
            i += 1
    return (i if i < n else None), wrappers, split


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
        self._reset_word()

    @staticmethod
    def _new_stage():
        st = Stage()
        st._words = []
        return st

    def _reset_word(self):
        self.buf = []
        self.unq = []
        self.started = False
        self.dyn = False

    # -- words ---------------------------------------------------------------
    def add(self, text, quoted=False, dyn=False):
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
            self.pending = None
        else:
            if not self.stage._words and not dyn:
                if text == "case":
                    self.case_depth += 1
                elif text == "esac" and self.case_depth:
                    self.case_depth -= 1
            self.stage._words.append((text, dyn))
        self._reset_word()

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
            _finish(st, self.ctx, self.vars)
            if not (st.argv or st.env or st.redirects or st.nested or st.heredocs or st.uncertain
                    or any(h.stage is st for h in self.heredocs)):
                self.stage = self._new_stage()
                return  # only brace-group words
            st.pipeline = self.pipeline_no
            st.index = len(self.pipeline)
            self.pipeline.append(st)
            self.result.append(st)
        self.stage = self._new_stage()

    def end_pipeline(self):
        self.end_stage()
        for k in range(1, len(self.pipeline)):  # echo 'cmd' | bash: the shell runs the text
            sh = self.pipeline[k]
            if _stdin_shell(sh):
                val = _echo_value(_single(self.pipeline[k - 1], self.ctx))
                if val:
                    res = _parse_script(val, self.ctx, self.vars)
                    if res is not None:
                        sh.nested.append(res)
        if self.pipeline:
            self.pipeline_no += 1
        self.pipeline = []

    # -- constructs ------------------------------------------------------------
    def subst(self, i_paren, from_, quoted, raw=False):
        res, end, found = _sub_at(self.s, i_paren, self.ctx, self.vars)
        if res is not None:
            self.stage.nested.append(res)
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
            self.end_word()
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
    if s_flag or not rest:
        return "stdin", None
    return "file", rest[0]


def _stdin_shell(st):
    idx, _, _ = _strip(st.argv)
    if idx is None:
        return False
    name, args = command_name(st.argv[idx]), st.argv[idx + 1:]
    if name in SHELLS:
        return _shell_mode(args)[0] == "stdin"
    if name in ("source", "."):
        return bool(args) and args[0] in ("/dev/stdin", "-")
    return False


def _finish(st, ctx, vars_):
    """Turn the collected words into argv/env and follow what the stage runs."""
    words = st.__dict__.pop("_words", [])
    if len(words) >= 2 and words[0][0] == "function":  # function f { ...: the body runs
        words = words[2:]
    while words and words[0][0] == "{":
        words.pop(0)
    while words and words[-1][0] == "}":
        words.pop()
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

    # Variable bookkeeping for later stages.
    if not argv:
        vars_.update(st.env)
    else:
        head = command_name(argv[0])
        if head in _DECLARE:
            for w, d in zip(argv[1:], st.dynamic[1:]):
                m = _ASSIGN.match(w)
                if m:
                    vars_[m.group(1)] = None if (d or m.group(2)) else m.group(3)
        elif head == "unset":
            for w in argv[1:]:
                vars_.pop(w, None)
        elif head in ("read", "mapfile", "readarray"):
            for w in argv[1:]:
                if _NAME.fullmatch(w):
                    vars_[w] = None

    idx, wrappers, split = _strip(argv)
    st.wrappers = wrappers
    for text in split:
        res = _parse_script(text, ctx, vars_)
        if res is not None:
            st.nested.append(res)
    if idx is None:
        return
    if st.dynamic[idx]:
        st.mark(f"command word is not a literal: {argv[idx]!r}")
    name, args = command_name(argv[idx]), argv[idx + 1:]
    scripts = []
    if name in SHELLS:
        mode, script = _shell_mode(args)
        if mode == "c" and script is not None:
            scripts.append(script)
        elif mode == "stdin":
            scripts.extend(t for op, t in st.redirects if op == "<<<")
    elif name == "eval":
        scripts.append(" ".join(args))
    elif name == "trap" and args and not args[0].startswith("-"):
        scripts.append(args[0])
    elif name == "watch":
        j = 0
        while j < len(args) and args[j].startswith("-"):
            j += 2 if args[j] in ("-n", "--interval", "-q", "--equexit") else 1
        if args[j:]:
            scripts.append(" ".join(args[j:]))
    elif name in ("su", "runuser"):
        for j, a in enumerate(args[:-1]):
            if a in ("-c", "--command"):
                scripts.append(args[j + 1])
    elif name in ("source", ".") and args and args[0] in ("/dev/stdin", "-"):
        scripts.extend(t for op, t in st.redirects if op == "<<<")
    elif name == "find":
        _find_exec(st, idx, ctx, vars_)
    for text in scripts:
        res = _parse_script(text, ctx, vars_)
        if res is not None:
            st.nested.append(res)


def _find_exec(st, idx, ctx, vars_):
    argv, dyn = st.argv, st.dynamic
    j = idx + 1
    while j < len(argv):
        if argv[j] in ("-exec", "-execdir", "-ok", "-okdir"):
            k = j + 1
            while k < len(argv) and argv[k] not in (";", "+"):
                k += 1
            if k > j + 1:
                if not _budget_ok(ctx):
                    return
                ctx.calls += 1
                ctx.depth += 1
                sub = Stage()
                sub._words = list(zip(argv[j + 1:k], dyn[j + 1:k]))
                try:
                    _finish(sub, ctx, dict(vars_))
                finally:
                    ctx.depth -= 1
                res = ParseResult(ctx)
                res.append(sub)
                st.nested.append(res)
            j = k
        j += 1


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
            lv.end_pipeline()
            i = lv.read_bodies(i + 1, nested)
            continue
        if c in "\n;":
            lv.end_pipeline()
            i += 1
            continue
        if c == "&":
            if nx == "&":
                lv.end_pipeline()
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
                lv.end_pipeline()
                i += 1
            continue
        if c == "|":
            if nx == "|":
                lv.end_pipeline()
                i += 2
            else:
                lv.end_stage()
                i += 2 if nx == "&" else 1
            continue
        if c in "<>":
            if lv.started and "".join(lv.buf).isdigit() and not lv.dyn:
                lv._reset_word()  # an fd number: 2>file
            else:
                lv.end_word()
            if nx == "&":  # descriptor duplication (2>&1, <&0, >&-) or >&file
                j = i + 2
                if j < n and (s[j].isdigit() or s[j] == "-"):
                    while j < n and (s[j].isdigit() or s[j] == "-"):
                        j += 1
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
            lv.end_pipeline()
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


def parse(command):
    """Parse a Bash command line into stages (see module docstring)."""
    ctx = _Ctx()
    s = unfold(str(command or ""))
    res, _, _ = _lex(s, 0, False, ctx, {})
    return res
