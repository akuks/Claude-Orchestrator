"""co-guard rules engine: decides whether one Claude Code tool call may run.

Python port of jspark-forge ``scripts/lib/blocklist.js`` and
``rules/default-blocklist.json`` (MIT, same owner): the built-in block list
(inventory F28-F31, F34-F36, F38, F39), plus MCP tool policies. Bash is
parsed with :mod:`shell` (the co-guard parser); nothing is ever executed.

RULING R1: this module uses only the Python standard library and the sibling
``shell.py``. It is copied next to ``shell.py`` into the co-guard plugin and
must work both as ``app.guard.rules`` and as a standalone file run with
``python3 -I`` (``python3 -I rules.py < request.json`` prints a verdict).

API
---
``evaluate(tool_name, tool_input, policy, cwd) -> Verdict``

``Verdict{decision, hits}``  ``decision`` is ``"deny"`` if any hit is hard
(``hard`` = never approvable) or is ``mcp-block``; else ``"approve"`` (a person
must approve it live) if there is any hit; else ``"allow"``. Hits are listed
hard first, then confirmable, de-duplicated by rule id.

``Hit{rule_id, reason, hard, summary}``  ``reason`` is a plain sentence for
people and the agent; ``summary`` is the command, file path or MCP call
(shortened) the hit is about.

Callers must fail closed: an exception from ``evaluate`` means deny.

Policy (a dict, as written by the worker into ``policy.json``)::

    {
      "project_dir": "/abs/workspace",     # the task's project (cwd default)
      "home": "/Users/me",                 # the server user's home
      "guard_enabled": true,               # false: built-in rules off, except
                                           #   self-protect, unparseable, MCP
      "mcp_tools": {"mcp__srv__tool": "auto_approve"|"require_approval"|"block"},
                                           #   keys may be fnmatch patterns
      "protected_paths": ["/abs/co-guard", "/abs/_tasks/T1", ...],
                                           #   dirs/files (fnmatch patterns ok)
      "claude_config_dir": "/abs/.claude"  # its settings*.json are protected
    }

Rule ids
--------
HARD (never approvable): ``rm-rf-outside`` when a target is home, ``/`` or an
ancestor of home (``~``, ``~/``, ``~/*``, ``$HOME``, ``/``, ``/*``, the home
path); ``self-protect`` (writes, deletes, moves or chmods of a protected
path or a folder holding one, and running ``co_hook.py`` by hand - so with
the guard off ``rm -rf ~`` is still denied when home holds the Claude config
dir); ``unparseable`` (the parser gave up: ``too_complex``, Ruling R3; a
malformed ``tool_input``; write-then-run/ssh re-checks deeper than 6 levels
or more than 64 per call); ``mcp-block`` (marked hard: never approvable).

CONFIRMABLE (a person may approve once): ``rm-rf-outside`` (recursive delete
outside the project, of the project itself or its ``.git``, or of a target we
cannot resolve), ``git-force-push-main``, ``git-reset-hard``,
``git-clean-fdx``, ``git-stash-drop``, ``secret-files``, ``chmod-wide``,
``pipe-to-shell``, ``git-internals`` (writes to ``.git/config``,
``.git/hooks/*``, ``.git/info/attributes``, ``.gitmodules`` with an
``update = !cmd`` key), ``uncertain-command``, ``mcp-require-approval``.

Bash: every stage is checked, nested ones (``$( )``, ``sh -c``, ``eval``,
``find -exec``, heredocs fed to shells ...) included, after wrappers
(``sudo``, ``env``, ``xargs`` ...) are stripped. Relative paths resolve
against ``cwd`` and against literal ``cd``/``pushd`` earlier in the same
command line (a ``cd`` is assumed to take effect; subshells are flattened by
the parser, so a ``cd`` inside ``( )`` is also followed - this errs towards
asking). Extra checks:

* ``uncertain-command``: a stage the parser marked uncertain, when its
  pipeline (with everything nested in it) or the stages that run it
  (``bash -c "$x"``, ``eval "$x"``) mention a dangerous verb (rm, git, curl,
  a shell, eval, dd, mkfs, chmod, sudo, xargs ...) or, when the command word
  itself is unknown, dangerous arguments (``-rf``, ``--force``, ``~``, ``/``).
  Uncertain stages with only harmless words (``"$EDITOR" f``) are allowed.
  Interpreter one-liners that run shell or delete files (``python -c``,
  ``perl -e``, ``ruby -e``, ``node -e``, ``php -r``, ``awk 'system(..)'``,
  ``vim -c '!..'``) are also ``uncertain-command``.
* ``ssh [opts] host CMD``: CMD is parsed and checked as a command on a machine
  where nothing is "the project"; ``-o ProxyCommand=``/``LocalCommand=``
  values are checked as local commands.
* ``secret-files`` covers redirects, ``tee``, ``sed -i``/``perl -i`` and
  ``truncate`` (forge: redirects and tee); ``cp``/``mv`` destinations are not
  covered, so ``cp .env.example .env`` stays free.
* ``find ROOT ... -delete`` / ``-exec rm ...``: ROOT is a delete target (with
  a name/path/time filter, only things strictly inside ROOT).
* write-then-run in one command line (``echo .. > x.sh; bash x.sh``,
  ``cat > x <<EOF .. EOF; sh x``, ``curl -o x u && ./x``): the written text
  is checked when it is literal, a download is ``pipe-to-shell``, anything
  else is ``uncertain-command``.
"""

from __future__ import annotations

import fnmatch
import importlib
import importlib.util
import json
import os
import posixpath
import re
import sys
from dataclasses import asdict, dataclass, field

if __package__:
    _sh = importlib.import_module(__package__ + ".shell")
else:  # standalone copy next to shell.py (co-guard plugin, python3 -I)
    _sh = sys.modules.get("shell")
    if _sh is None:
        _spec = importlib.util.spec_from_file_location(
            "shell", os.path.join(os.path.dirname(os.path.abspath(__file__)), "shell.py"))
        _sh = importlib.util.module_from_spec(_spec)
        sys.modules["shell"] = _sh
        _spec.loader.exec_module(_sh)

__all__ = ["RULES", "Hit", "Verdict", "evaluate", "is_secret_name"]

# ---------------------------------------------------------------------------
# Rule table (plain reasons ported from forge default-blocklist.json)
# ---------------------------------------------------------------------------
RULES = {
    "rm-rf-outside": (
        "this deletes a folder outside the project, or the project itself or its history",
        "this would delete your home folder or the whole disk"),
    "git-force-push-main": ("this force-pushes to main or master and can erase commits on the shared branch", None),
    "git-reset-hard": ("this throws away your uncommitted work", None),
    "git-clean-fdx": ("this deletes every untracked and ignored file, including local settings and .env files", None),
    "git-stash-drop": ("this permanently deletes saved stashes", None),
    "secret-files": ("this changes a secrets or key file (.env, keys, credentials)", None),
    "chmod-wide": ("this opens file permissions to everyone or changes a whole folder tree", None),
    "pipe-to-shell": ("this runs a script straight from the internet without showing it first", None),
    "git-internals": ("this changes git's own config or hooks, which can make git run commands", None),
    "uncertain-command": ("co-guard could not determine what this command runs", None),
    "self-protect": (
        None,
        "this changes or runs co-guard's own files (plugin, policy, spool or Claude settings), "
        "which only the orchestrator does"),
    "unparseable": (None, "this command is too complex for co-guard to check; split it into simpler commands"),
    "mcp-require-approval": ("this MCP tool needs a person to approve each call", None),
    "mcp-block": (None, "this MCP tool is blocked for this task"),
}
_ALWAYS_ON = frozenset({"self-protect", "unparseable", "mcp-block", "mcp-require-approval"})

_SUMMARY_MAX = 300
_MAX_DEPTH = 6  # write-then-run / ssh re-evaluation depth
_MAX_EVALS = 64  # write-then-run / ssh re-evaluations per tool call

SHELLS = _sh.SHELLS
_FETCH = frozenset({"curl", "wget"})
_INTERP_STDIN = re.compile(r"(python|pypy)[\d.]*|perl|ruby|node|nodejs|php|bun|deno")
_PY = re.compile(r"(python|pypy)[\d.]*")
_TEMPLATE_WORDS = frozenset({"example", "sample", "template", "dist"})
_READERS = frozenset({"cat", "less", "more", "head", "tail", "grep", "egrep", "fgrep", "rg", "ag", "wc", "file",
                      "stat", "ls", "diff", "cmp", "sha256sum", "sha1sum", "md5sum", "md5", "shasum", "bat",
                      "strings", "od", "xxd", "hexdump", "test", "[", "[[", "echo", "printf", "realpath",
                      "readlink", "dirname", "basename", "find", "tree"})
_DANGEROUS_VERBS = frozenset({"rm", "rmdir", "unlink", "shred", "dd", "chmod", "chown", "chgrp", "git", "curl",
                              "wget", "eval", "source", ".", "exec", "truncate", "mv", "sudo", "doas", "su",
                              "xargs", "parallel", "ssh", "scp", "rsync", "kill", "pkill", "killall", "crontab",
                              "launchctl", "systemctl"}) | SHELLS
_DANGEROUS_ARGS = re.compile(r"-[a-zA-Z]*([rR][a-zA-Z]*f|f[a-zA-Z]*[rR])[a-zA-Z]*|--recursive|--force.*|--hard|"
                             r"--no-preserve-root|~|~/.*|/|/\*|\$\{?HOME\}?(/.*)?")
_FIND_DELETE = frozenset({"-delete", "-exec", "-execdir", "-ok", "-okdir"})
_FIND_NARROW = frozenset({"-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename", "-regex", "-iregex",
                          "-newer", "-anewer", "-cnewer", "-mtime", "-mmin", "-atime", "-amin", "-ctime", "-cmin",
                          "-size", "-empty", "-user", "-group", "-perm", "-links", "-inum", "-samefile", "-lname",
                          "-ilname", "-newermt", "-uid", "-gid"})
_DELETERS = frozenset({"rm", "rmdir", "unlink", "shred"})
_WRITE_OPS = frozenset({">", ">>", ">|", "&>", "&>>", "<>", ">&"})
_HOME_VAR = re.compile(r"^\$(\{HOME\}|HOME)(?=/|$)")
_PWD_VAR = re.compile(r"^(\$\{PWD\}|\$PWD|\$\(pwd\)|`pwd`)(?=/|$)")
_GLOB = re.compile(r"[*?\[]")
_FETCH_SUB = re.compile(r"(<\(|\$\(|`)\s*(sudo\s+)?(curl|wget)\b")

# Interpreter one-liners that run shell commands or delete files (carried from Task 1).
_CODE_PATTERNS = {
    "python": re.compile(r"\bos\s*\.\s*(system|popen|exec\w*|spawn\w*|remove|unlink|rmdir|removedirs|posix_spawn\w*)"
                         r"\b|\bsubprocess\b|\bshutil\s*\.\s*(rmtree|move)\b|\bpty\s*\.\s*spawn|\b__import__\b|"
                         r"\.\s*(unlink|rmdir)\s*\(|\bcommands\s*\.\s*getoutput"),
    "perl": re.compile(r"\bsystem\b|\bexec\b|`|\bqx\b|\bunlink\b|\brmtree\b|\bremove_tree\b|\bopen\s*\(?[^;]*\|"),
    "ruby": re.compile(r"\bsystem\b|\bexec\b|`|%x|\bspawn\b|\bFileUtils\b|\bFile\s*\.\s*(delete|unlink)|"
                       r"\bIO\s*\.\s*popen|\bOpen3\b|\bDir\s*\.\s*(rmdir|delete|unlink)"),
    "node": re.compile(r"child_process|\bexecSync\b|\bexecFileSync\b|\bspawn(Sync)?\b|\brmSync\b|\bunlinkSync\b|"
                       r"\brmdirSync\b|\bfs\s*\.\s*(rm|unlink|rmdir)\b|\bDeno\s*\.\s*(run|Command|remove)|"
                       r"\bBun\s*\.\s*(spawn|\$)"),
    "php": re.compile(r"\b(system|exec|shell_exec|passthru|popen|proc_open|pcntl_exec|unlink|rmdir)\s*\(|`"),
    "awk": re.compile(r"\bsystem\s*\(|\|\s*\"|\"\s*\|\s*getline|\|&|\|\s*[A-Za-z_]\w*\s*$|\|\s*cmd\b"),
    "vim": re.compile(r"(^|[|:\s%])(\d+(,\d+)?|%|\.)?\s*(r|read|w|write)?\s*!(?!\s*$)|\bsystem\s*\(|"
                      r"\bterm(inal)?\b|\bsystemlist\s*\("),
}
_NODE = frozenset({"node", "nodejs", "bun"})
_AWK = frozenset({"awk", "gawk", "mawk", "nawk"})
_VIM = frozenset({"vim", "vi", "nvim", "ex", "view", "gvim", "vimdiff", "rvim"})
_GITMODULES_EXEC = re.compile(r"(?im)^\s*update\s*=\s*!")
# forge WRITES_TEXT: inline code that may change a file
_WRITES_TEXT = re.compile(r"write|append|copy|rename|move|unlink|remove|\brm|symlink|truncate|chmod|mkdir|"
                          r"\bos\.replace\b|open\s{0,5}\([^)]{0,300}?['\"](?=[rbt]{0,3}[wax+])[rwaxbt+]{1,4}['\"]|"
                          r"system|exec|spawn|popen|subprocess", re.I)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
@dataclass
class Hit:
    rule_id: str
    reason: str
    hard: bool
    summary: str

    def to_dict(self):
        return asdict(self)


@dataclass
class Verdict:
    decision: str  # "allow" | "deny" | "approve"
    hits: list = field(default_factory=list)

    def to_dict(self):
        return {"decision": self.decision, "hits": [h.to_dict() for h in self.hits]}


def _short(text):
    text = " ".join(str(text).split())
    return text if len(text) <= _SUMMARY_MAX else text[: _SUMMARY_MAX - 1] + "…"


def _make_hit(rule_id, hard, summary):
    plain, hard_plain = RULES[rule_id]
    reason = (hard_plain or plain) if hard else (plain or hard_plain)
    return Hit(rule_id, reason, bool(hard), _short(summary))


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def _real(path):
    """Absolute normalised path with symlinks resolved in its parent folders;
    the last component is kept as is (rm removes a link, not its target)."""
    p = posixpath.normpath(path)
    if p.startswith("//"):
        p = "/" + p.lstrip("/")
    if p == "/":
        return "/"
    head, tail = posixpath.split(p)
    return posixpath.join(os.path.realpath(head), tail)


def _inside(path, folder):
    """path is strictly inside folder."""
    if folder == "/":
        return path != "/"
    return path.startswith(folder + "/")


def _within(path, folder):
    return path == folder or _inside(path, folder)


def is_secret_name(path):
    """forge isSecretName: .env*, *.pem, *.key, id_rsa*, *credentials*, *secret*,
    except template words (example, sample, template, dist)."""
    base = re.split(r"[\\/]", str(path))[-1].lower()
    if not base:
        return False
    if any(part in _TEMPLATE_WORDS for part in base.split(".")):
        return False
    return (base.startswith(".env") or base.endswith(".pem") or base.endswith(".key") or base.startswith("id_rsa")
            or "credentials" in base or "secret" in base)


def _git_internal_kind(path):
    """'config' | 'hooks' | 'attributes' | 'gitmodules' | None for an absolute path."""
    parts = path.split("/")
    if parts and parts[-1] == ".gitmodules":
        return "gitmodules"
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == ".git":
            rest = parts[i + 1:]
            if rest == ["config"]:
                return "config"
            if rest[:1] == ["hooks"] and len(rest) >= 2:
                return "hooks"
            if rest == ["info", "attributes"]:
                return "attributes"
            return None
    return None


def _brace_expand(word):
    """One level of {a,b} expansion, or None when it is not that simple."""
    m = re.fullmatch(r"([^{}]*)\{([^{}]*,[^{}]*)\}([^{}]*)", word)
    if not m:
        return None
    return [m.group(1) + alt + m.group(3) for alt in m.group(2).split(",")]


class _Ctx:
    """Normalised policy plus per-evaluation state."""

    def __init__(self, policy, cwd):
        policy = policy if isinstance(policy, dict) else {}
        home = policy.get("home") or os.path.expanduser("~")
        self.home = _real(os.path.abspath(str(home)))
        project = policy.get("project_dir") or cwd or os.getcwd()
        self.project = _real(os.path.abspath(str(project)))
        self.guard = policy.get("guard_enabled", True) is not False
        self.mcp_tools = policy.get("mcp_tools") or {}
        prot = [str(p) for p in (policy.get("protected_paths") or []) if p]
        cfg = policy.get("claude_config_dir")
        if cfg:
            prot.append(posixpath.join(str(cfg), "settings*.json"))
        self.protected = []  # (pattern, is_glob)
        for p in prot:
            p = os.path.abspath(os.path.expanduser(p))
            for v in {posixpath.normpath(p), _real(p)}:
                self.protected.append((v, bool(_GLOB.search(v))))
        # text that names a protected path (for inline interpreter code)
        self.mentions = sorted({posixpath.dirname(v) if g else v for v, g in self.protected} - {"", "/"})
        self.hits = []
        self.remote = False  # inside `ssh host CMD`: nothing is the project
        self.depth = 0
        self.evals = 0

    # -- hits --------------------------------------------------------------
    def hit(self, rule_id, hard, summary):
        if not self.guard and rule_id not in _ALWAYS_ON:
            return
        self.hits.append(_make_hit(rule_id, hard, summary))

    # -- paths -------------------------------------------------------------
    def expand(self, word, cwd):
        """Expand ~ / $HOME / $PWD prefixes. Returns the text, or None when the
        base is unknown (cwd unknown for a relative path)."""
        w = word
        if w == "~" or w.startswith("~/"):
            w = self.home + w[1:]
        elif w.startswith("~"):
            return None  # ~user, ~+, ~-: another home or folder we do not know
        elif _HOME_VAR.match(w):
            w = self.home + _HOME_VAR.sub("", w, count=1)
        elif _PWD_VAR.match(w):
            if cwd is None:
                return None
            w = cwd + _PWD_VAR.sub("", w, count=1)
        if not w.startswith("/"):
            if cwd is None:
                return None
            w = posixpath.join(cwd, w)
        return w

    def path(self, word, cwd, dynamic=False):
        """Resolve a literal path word: absolute real path, or None if unknown."""
        if dynamic and ("$" in word or "`" in word or _GLOB.search(word)):
            return None
        w = self.expand(word, cwd)
        if w is None or "$" in w and dynamic:
            return None
        return _real(w)

    def is_protected(self, path, as_ancestor=False):
        """path is (inside) a protected path; with as_ancestor, also when a
        protected path is inside it (deleting/moving a parent folder)."""
        if not path:
            return False
        for pat, is_glob in self.protected:
            if is_glob:
                if fnmatch.fnmatchcase(path, pat):
                    return True
                folder = posixpath.dirname(pat)  # settings*.json: deleting/moving its folder
                if as_ancestor and not _GLOB.search(folder) and _within(folder, path):
                    return True
                continue
            if _within(path, pat) or (as_ancestor and _inside(pat, path)):
                return True
        return False

    def project_or_none(self):
        return None if self.remote else self.project


# ---------------------------------------------------------------------------
# Recursive-delete targets (forge rmCheck / classifyTargets)
# ---------------------------------------------------------------------------
def _target_kinds(ctx, word, dyn, cwd):
    """What a delete target covers: [(abs path, glob)] where glob is None for
    the path itself, a broad glob ('*', '.*') for everything in it (judged as
    the folder itself, as forge does) or a narrower glob for some children.
    None when the target cannot be resolved (unknown variable, unknown cwd)."""
    words = [word]
    if dyn and re.search(r"(?<!\$)\{", word):
        words = _brace_expand(word)
        if words is None:
            return None
    out = []
    for w in words:
        prefix_ok = not w.startswith(("$", "`")) or bool(_HOME_VAR.match(w) or _PWD_VAR.match(w))
        e = ctx.expand(w, cwd)
        if e is None:
            return None
        if dyn and ("$" in e or "`" in e):
            # unknown part: fine only behind a literal folder strictly inside the project
            lit = re.split(r"[$`]", e, maxsplit=1)[0]
            folder = _real(lit.rsplit("/", 1)[0] or "/")
            project = ctx.project_or_none()
            if prefix_ok and "/" in re.split(r"[$`]", w, maxsplit=1)[0] and project and _inside(folder, project):
                out.append((folder, _SOME))  # some children of folder
                continue
            return None
        norm = posixpath.normpath(e)
        head, tail = posixpath.split(norm)
        if dyn and _GLOB.search(head):
            lit = _GLOB.split(norm, maxsplit=1)[0]
            out.append((_real(lit.rsplit("/", 1)[0] or "/"), _SOME))
            continue
        if dyn and _GLOB.search(tail):
            out.append((_real(head), "*" if _BROAD.fullmatch(tail) else tail))
            continue
        out.append((_real(e), None))
    return out


_BROAD = re.compile(r"[*?.]*\*[*?.]*")
_SOME = "\x00some"  # some (unknown) children of a folder


def _protect_delete(ctx, path, glob, summary):
    """self-protect for deleting/moving `path` (glob: which children)."""
    if ctx.is_protected(path, as_ancestor=glob is None):
        ctx.hit("self-protect", True, summary)
        return
    if glob is None:
        return
    for pat, is_glob in ctx.protected:
        base = posixpath.dirname(pat) if is_glob else pat
        if is_glob and _GLOB.search(base):
            continue
        if _inside(base, path):
            first = base[len(path):].lstrip("/").split("/")[0]
            if glob == _SOME or _BROAD.fullmatch(glob) or fnmatch.fnmatchcase(first, glob):
                ctx.hit("self-protect", True, summary)
                return
        elif is_glob and base == path and (_BROAD.fullmatch(glob) or glob == _SOME):
            ctx.hit("self-protect", True, summary)
            return


def _protect_find(ctx, root, names, summary):
    """self-protect for `find ROOT <filter> -delete`: names are the -name /
    -iname patterns (None: some other filter, judged conservatively)."""
    for pat, is_glob in ctx.protected:
        base = posixpath.dirname(pat) if is_glob else pat
        if _GLOB.search(base) or not _within(base, root):
            continue
        cands = base[len(root):].strip("/").split("/") if base != root else []
        if is_glob:
            leaf = posixpath.basename(pat)
            cands += [_GLOB.sub("", leaf), _GLOB.sub("x", leaf)]
        cands = [c for c in cands if c]
        if names is None or any(fnmatch.fnmatch(c.lower(), n.lower()) for c in cands for n in names):
            ctx.hit("self-protect", True, summary)
            return


def _classify(ctx, targets, cwd, summary, narrow=False, names=None):
    """targets: [(word, dynamic)]. Records rm-rf-outside / self-protect hits.
    narrow: only things strictly inside each target go (find with a filter;
    names: its -name patterns, or None for another kind of filter)."""
    level = None  # None | 'confirm' | 'hard'
    home, project = ctx.home, ctx.project_or_none()
    for word, dyn in targets:
        kinds = _target_kinds(ctx, word, dyn, cwd)
        if kinds is None:
            level = level or "confirm"
            continue
        for path, glob in kinds:
            if narrow and glob is None:
                glob = _SOME
                _protect_find(ctx, path, names, summary)
            else:
                _protect_delete(ctx, path, glob, summary)
            whole = glob is None or _BROAD.fullmatch(glob) is not None
            if whole:
                if path == "/" or path == home or _inside(home, path):
                    level = "hard"
                    continue
                if project and _inside(path, project) and path != project + "/.git":
                    continue
            elif project and _within(path, project):
                continue
            level = level or "confirm"
    if level:
        ctx.hit("rm-rf-outside", level == "hard", summary)


def _rm_check(ctx, argv, dyn, cwd, summary, from_xargs):
    recursive, end = False, False
    targets = []
    for a, d in zip(argv[1:], dyn[1:]):
        if not end and a == "--":
            end = True
            continue
        if not end and a.startswith("--") and not d:
            if a == "--recursive":
                recursive = True
            continue
        if not end and re.fullmatch(r"-[a-zA-Z]+", a):
            if "r" in a or "R" in a:
                recursive = True
            continue
        targets.append((a, d))
    if from_xargs:
        targets.append(("$XARGS", True))
    if _sh.command_name(argv[0]) == "rm" and recursive:
        if targets:
            _classify(ctx, targets, cwd, summary)
        return
    for word, d in targets:  # any other delete of a protected path
        for path, glob in _target_kinds(ctx, word, d, cwd) or ():
            if ctx.is_protected(path):
                ctx.hit("self-protect", True, summary)


def _find_check(ctx, argv, dyn, cwd, summary):
    args, dargs = argv[1:], dyn[1:]
    i = 0
    while i < len(args) and args[i] in ("-H", "-L", "-P", "-O0", "-O1", "-O2", "-O3"):
        i += 1
    roots = []
    while i < len(args) and not (args[i].startswith("-") or args[i] in ("(", "!", ")")):
        roots.append((args[i], dargs[i]))
        i += 1
    expr = args[i:]
    deletes = "-delete" in expr
    for j, a in enumerate(expr):
        if a in ("-exec", "-execdir", "-ok", "-okdir") and j + 1 < len(expr):
            inner = _sh.expand_wrappers(expr[j + 1:]) or []
            if inner and _sh.command_name(inner[0]) in _DELETERS:
                deletes = True
    if not deletes:
        return
    narrow = any(a in _FIND_NARROW for a in expr)
    names = [expr[k + 1] for k, a in enumerate(expr) if a in ("-name", "-iname") and k + 1 < len(expr)]
    only_names = names and not any(a in _FIND_NARROW and a not in ("-name", "-iname") for a in expr)
    _classify(ctx, roots or [(".", False)], cwd, summary, narrow=narrow, names=names if only_names else None)


# ---------------------------------------------------------------------------
# git (forge parseGit / pushForceMain / wholeTree / gitCheck)
# ---------------------------------------------------------------------------
_MAIN = frozenset({"main", "master"})


def _branch_of(directory):
    """Current branch read from .git/HEAD, or None when unknown/detached."""
    if directory is None:
        return None
    d = directory
    for _ in range(64):
        g = posixpath.join(d, ".git")
        try:
            if os.path.isfile(g):
                with open(g, encoding="utf-8", errors="replace") as fh:
                    line = fh.read(4096).strip()
                if not line.startswith("gitdir:"):
                    return None
                g = posixpath.join(d, line[7:].strip())
            head = posixpath.join(g, "HEAD")
            if os.path.isfile(head):
                with open(head, encoding="utf-8", errors="replace") as fh:
                    ref = fh.read(4096).strip()
                m = re.fullmatch(r"ref:\s*refs/heads/(.+)", ref)
                return m.group(1) if m else None
        except OSError:
            return None
        parent = posixpath.dirname(d)
        if parent == d:
            return None
        d = parent
    return None


def _parse_git(ctx, args, dyn, cwd):
    d, i = cwd, 0
    while i < len(args) and args[i].startswith("-"):
        a = args[i]
        if a == "-C":
            if i + 1 < len(args):
                d = None if dyn[i + 1] or d is None and not args[i + 1].startswith("/") \
                    else ctx.path(args[i + 1], d, False)
            i += 2
            continue
        if a in ("-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"):
            i += 2
            continue
        i += 1
    sub = args[i].lower() if i < len(args) else ""
    return d, sub, args[i + 1:]


def _push_force_main(rest, directory):
    value_opts = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}
    force = all_ = delete = False
    positional = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in value_opts:
            i += 2
            continue
        if a in ("--force", "-f") or a.startswith("--force-with-lease") or re.fullmatch(r"-[a-zA-Z]*f[a-zA-Z]*", a):
            force = True
        elif a in ("--all", "--mirror", "--branches"):
            all_ = True
        elif a in ("--delete", "-d"):
            delete = True
        elif not a.startswith("-"):
            positional.append(a)
        i += 1
    refspecs = positional[1:]
    plus = [r for r in refspecs if r.startswith("+")]
    deletes = [r[1:] for r in refspecs if r.startswith(":")]
    if delete:
        deletes += refspecs
    if any(r.replace("refs/heads/", "").lower() in _MAIN for r in deletes):
        return True
    if not force and not plus:
        return False
    if all_:
        return True
    considered = refspecs if force else plus
    if considered:
        dsts = [r.lstrip("+").split(":")[-1].replace("refs/heads/", "", 1) for r in considered]
    else:
        dsts = ["HEAD"]
    for dst in dsts:
        if dst in ("HEAD", "", "@"):
            br = _branch_of(directory)
            if br is None or br.lower() in _MAIN:
                return True  # unknown branch: fail closed
        elif dst.lower() in _MAIN:
            return True
    return False


def _git_check(ctx, argv, dyn, cwd, summary):
    d, sub, rest = _parse_git(ctx, argv[1:], dyn[1:], cwd)
    rule = None
    if sub == "push" and _push_force_main(rest, d):
        rule = "git-force-push-main"
    elif sub == "reset" and "--hard" in rest:
        rule = "git-reset-hard"
    elif sub in ("checkout", "restore"):
        at_root = d is not None and d == ctx.project
        if any(a in (":/", ":/.") or (a in (".", "./") and at_root) for a in rest):
            rule = "git-reset-hard"
    elif sub == "clean":
        if "-n" not in rest and "--dry-run" not in rest:
            flags = ""
            for a in rest:
                if a == "--force":
                    flags += "f"
                elif re.fullmatch(r"-[a-zA-Z]+", a):
                    flags += a[1:]
            if "f" in flags and "d" in flags and "x" in flags:
                rule = "git-clean-fdx"
    elif sub == "stash" and rest[:1] and rest[0] in ("drop", "clear"):
        rule = "git-stash-drop"
    if rule:
        ctx.hit(rule, False, summary)


# ---------------------------------------------------------------------------
# chmod (forge: 777, -R / --recursive; plus others-writable modes)
# ---------------------------------------------------------------------------
def _chmod_wide(args):
    """chmod -R / --recursive, or a mode that lets everyone write (777, 666,
    o+w, a+w, a=rwx ...)."""
    for a in args:
        if a == "--recursive" or re.fullmatch(r"-[a-zA-Z]*R[a-zA-Z]*", a):
            return True
        if re.fullmatch(r"[0-7]{3,4}", a) and a[-1] in "2367":
            return True
        for clause in a.split(","):
            m = re.fullmatch(r"([ugoa]*)[+=]([rwxXst]*)", clause)
            if m and ("o" in m.group(1) or "a" in m.group(1)) and "w" in m.group(2):
                return True
    return False


# ---------------------------------------------------------------------------
# Writes: which files a stage writes, and what text when it is literal
# ---------------------------------------------------------------------------
_FETCHED = object()


def _decode_escapes(text):
    return text.replace("\\n", "\n").replace("\\t", "\t").replace("\\\\", "\\")


def _literal_output(stage, argv, dyn):
    """Text a stage prints when it is literal, _FETCHED for a download, else None."""
    if argv:
        name = _sh.command_name(argv[0])
        if name in ("echo", "printf"):
            if any(dyn[1:]):
                return None
            args = argv[1:]
            if name == "echo":
                while args and re.fullmatch(r"-[neE]+", args[0]):
                    args = args[1:]
                return _decode_escapes(" ".join(args))
            return _decode_escapes("\n".join(args))
        if name in _FETCH:
            return _FETCHED
        if name in ("cat", "tee"):
            for _delim, quoted, body in stage.heredocs:
                if quoted or ("$" not in body and "`" not in body):
                    return body
                return None
            for op, t in stage.redirects:
                if op == "<<<":
                    return None if ("$" in t or "`" in t) else t
            return None
    return None


def _stage_writes(stage, argv, dyn):
    """[(word, dynamic, kind)] for files the stage writes. kind: 'redirect' |
    'tee' | 'copy' | 'edit' | 'fetch' | 'chmod'."""
    out = []
    for op, t in stage.redirects:
        if op in _WRITE_OPS and t and not t.isdigit() and t != "-":
            out.append((t, "$" in t or "`" in t, "redirect"))
    if not argv:
        return out
    name, args, dargs = _sh.command_name(argv[0]), argv[1:], dyn[1:]
    pos = [(a, d) for a, d in zip(args, dargs) if not a.startswith("-")]
    if name == "tee":
        out += [(a, d, "tee") for a, d in pos]
    elif name in ("cp", "mv", "install", "ln", "rsync", "scp"):
        tdir = None
        for k, a in enumerate(args):
            if a in ("-t", "--target-directory") and k + 1 < len(args):
                tdir = (args[k + 1], dargs[k + 1])
            elif a.startswith("--target-directory="):
                tdir = (a.split("=", 1)[1], dargs[k])
        if tdir:
            out.append((tdir[0], tdir[1], "copydir"))
            srcs = [p for p in pos if p != tdir]
        elif len(pos) >= 2:
            out.append((pos[-1][0], pos[-1][1], "copy"))
            srcs = pos[:-1]
        else:
            srcs = []
        if name == "mv":
            out += [(a, d, "move-src") for a, d in srcs]
    elif name == "sed" and any(re.fullmatch(r"-[a-zA-Z]*i.*", a) or a.startswith("--in-place") for a in args):
        files, script_given, k = [], False, 0
        while k < len(args):
            a = args[k]
            if a in ("-e", "-f", "--expression", "--file"):
                script_given, k = True, k + 2
                continue
            if a.startswith(("--expression=", "--file=")):
                script_given = True
            elif not a.startswith("-"):
                files.append((a, dargs[k]))
            k += 1
        out += [(a, d, "edit") for a, d in (files if script_given else files[1:])]
    elif name == "perl" and any(re.fullmatch(r"-[a-zA-Z]*i.*", a) for a in args):
        code = {c for c, _d in _short_opt_value(args, dargs, "eE")}
        out += [(a, d, "edit") for a, d in pos if a not in code]
    elif name == "dd":
        out += [(a[3:], d, "copy") for a, d in zip(args, dargs) if a.startswith("of=")]
    elif name == "truncate":
        out += [(a, d, "edit") for a, d in pos if not re.fullmatch(r"[+\-<>/%]?\d+[KMGTPEZY]?i?B?", a)]
    elif name == "touch":
        out += [(a, d, "touch") for a, d in pos]
    elif name in ("chmod", "chown", "chgrp", "chattr"):
        out += [(a, d, "chmod") for a, d in pos[1:]]
    elif name == "curl":
        for k, a in enumerate(args):
            if a in ("-o", "--output") and k + 1 < len(args):
                out.append((args[k + 1], dargs[k + 1], "fetch"))
            elif a.startswith("--output="):
                out.append((a.split("=", 1)[1], dargs[k], "fetch"))
            elif re.fullmatch(r"-[a-zA-Z]*O[a-zA-Z]*", a) or a == "--remote-name":
                for u, d in pos:
                    if "://" in u:
                        out.append((u.rstrip("/").rsplit("/", 1)[-1].split("?")[0] or "index.html", d, "fetch"))
    elif name == "wget":
        named = False
        for k, a in enumerate(args):
            if a in ("-O", "--output-document") and k + 1 < len(args):
                named = True
                if args[k + 1] != "-":
                    out.append((args[k + 1], dargs[k + 1], "fetch"))
            elif a.startswith("--output-document="):
                named = True
                out.append((a.split("=", 1)[1], dargs[k], "fetch"))
            elif re.fullmatch(r"-[a-zA-Z]*O.+", a) and not a.startswith("--"):
                named = True
                v = a[a.index("O") + 1:]
                if v != "-":
                    out.append((v, dargs[k], "fetch"))
        if not named:
            for u, d in pos:
                if "://" in u:
                    out.append((u.rstrip("/").rsplit("/", 1)[-1].split("?")[0] or "index.html", d, "fetch"))
    return out


# ---------------------------------------------------------------------------
# Interpreter one-liners
# ---------------------------------------------------------------------------
def _short_opt_value(args, dargs, letters, longs=(), stop_at_positional=False):
    """[(value, dynamic)] of -X V / -XV / clustered -qX V for X in letters, and
    of --long V / --long=V."""
    vals = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--" or (stop_at_positional and not a.startswith("-")):
            break
        matched = False
        for long_ in longs:
            if a == long_ and i + 1 < len(args):
                vals.append((args[i + 1], dargs[i + 1]))
                i += 1
                matched = True
            elif a.startswith(long_ + "="):
                vals.append((a.split("=", 1)[1], dargs[i]))
                matched = True
        if not matched and a.startswith("-") and not a.startswith("--") and len(a) > 1:
            for k, ch in enumerate(a[1:], start=1):
                if ch in letters:
                    rest = a[k + 1:]
                    if rest:
                        vals.append((rest, dargs[i]))
                    elif i + 1 < len(args):
                        vals.append((args[i + 1], dargs[i + 1]))
                        i += 1
                    break
        i += 1
    return vals


def _interp_kind(name):
    if _PY.fullmatch(name):
        return "python"
    if name in ("perl",) or re.fullmatch(r"perl[\d.]+", name):
        return "perl"
    if name in ("ruby",) or re.fullmatch(r"ruby[\d.]+", name):
        return "ruby"
    if name in _NODE or name == "deno":
        return "node"
    if name == "php" or re.fullmatch(r"php[\d.]+", name):
        return "php"
    if name in _AWK:
        return "awk"
    if name in _VIM:
        return "vim"
    return None


def _inline_code(kind, args, dargs):
    """[(code, dynamic)] given inline to an interpreter."""
    if kind == "python":
        # python [opts] -c CODE; options end at a script, -m MOD or "-"
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--" or a == "-" or not a.startswith("-"):
                return []
            if a.startswith("--"):
                i += 1
                continue
            for k, ch in enumerate(a[1:], start=1):
                if ch in "cmWX":
                    if ch == "m":
                        return []
                    rest = a[k + 1:]
                    if ch == "c":
                        if rest:
                            return [(rest, dargs[i])]
                        return [(args[i + 1], dargs[i + 1])] if i + 1 < len(args) else []
                    if not rest:
                        i += 1  # -W arg / -X opt
                    break
            i += 1
        return []
    if kind == "perl":
        return _short_opt_value(args, dargs, "eE", stop_at_positional=True)
    if kind == "ruby":
        return _short_opt_value(args, dargs, "e", stop_at_positional=True)
    if kind == "node":
        if args[:1] == ["eval"]:
            return [(" ".join(args[1:]), any(dargs[1:]))]
        return _short_opt_value(args, dargs, "ep", ("--eval", "--print"), stop_at_positional=True)
    if kind == "php":
        return _short_opt_value(args, dargs, "r", ("--run",), stop_at_positional=True)
    if kind == "awk":
        i, has_f = 0, False
        while i < len(args):
            a = args[i]
            if a == "--":
                i += 1
                break
            if a in ("-F", "-v", "-f", "--file", "--assign", "--field-separator") and i + 1 < len(args):
                has_f = has_f or a in ("-f", "--file")
                i += 2
                continue
            if a.startswith("-") and len(a) > 1:
                has_f = has_f or a.startswith("-f")
                i += 1
                continue
            break
        if has_f or i >= len(args):
            return []
        return [(args[i], dargs[i])]
    if kind == "vim":
        out = []
        for i, a in enumerate(args):
            if a in ("-c", "--cmd") and i + 1 < len(args):
                out.append((args[i + 1], dargs[i + 1]))
            elif a.startswith("+") and len(a) > 1 and not a[1:].isdigit():
                out.append((a[1:], dargs[i]))
        return out
    return []


def _code_runs_shell(kind, code):
    pat = _CODE_PATTERNS.get(kind)
    return bool(pat and pat.search(code))


# ---------------------------------------------------------------------------
# ssh host CMD
# ---------------------------------------------------------------------------
_SSH_VALUE = set("BbcDEeFIiJLlmOoPpQRSWw")


def _ssh_local_commands(args):
    """-o ProxyCommand=CMD / LocalCommand=CMD run on this machine."""
    out = []
    for i, a in enumerate(args):
        val = args[i + 1] if a == "-o" and i + 1 < len(args) else (a[2:] if a.startswith("-o") else None)
        if val:
            m = re.match(r"\s*(proxycommand|localcommand)\s*[= ]\s*(.*)", val, re.I | re.S)
            if m and m.group(2).strip().lower() != "none":
                out.append(m.group(2))
    return out


def _ssh_remote(args):
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            i += 1
            break
        if a.startswith("-") and len(a) > 1:
            letters = a[1:]
            for k, ch in enumerate(letters):
                if ch in _SSH_VALUE:
                    if k == len(letters) - 1:
                        i += 1
                    break
            i += 1
            continue
        break
    rest = args[i + 1:]  # args[i] is the host
    return " ".join(rest) if rest else None


# ---------------------------------------------------------------------------
# Bash evaluation
# ---------------------------------------------------------------------------
def _stage_tokens(stages):
    """Lower-cased command names of every word (split on whitespace) at any depth."""
    toks = set()
    for st in _sh.walk(stages):
        for w in st.argv:
            for t in str(w).split():
                toks.add(_sh.command_name(t.strip("'\"();{}")))
    return toks


def _argv_tokens(stages):
    """Like _stage_tokens, for these stages only (not their nested text)."""
    return frozenset(_sh.command_name(t.strip("'\"();{}")) for st in stages for w in st.argv for t in str(w).split())


def _dangerous(tokens):
    for t in tokens:
        if t in _DANGEROUS_VERBS or t.startswith("mkfs"):
            return True
    return "find" in tokens and bool(tokens & _FIND_DELETE)


class _Bash:
    def __init__(self, ctx, raw):
        self.ctx = ctx
        self.raw = raw
        self.written = {}  # abs path -> text | None | _FETCHED

    def run(self, command, cwd):
        res = _sh.parse(command)
        if res.too_complex:
            self.ctx.hit("unparseable", True, self.raw)
        self.result(res, cwd)
        if res.errors and not any(st.uncertain for st in _sh.walk(res)) and _dangerous(_stage_tokens(res)):
            self.ctx.hit("uncertain-command", False, self.raw)

    def result(self, res, cwd, outer=frozenset()):
        """outer: command names of the stages (and their pipelines) that run
        this nested text - `bash -c "$x"` runs an unknown script in bash."""
        pipelines = {}
        for st in res:
            pipelines.setdefault(st.pipeline, []).append(st)
        for st in res:
            here = outer | _argv_tokens(pipelines.get(st.pipeline, [st]))
            for sub in st.nested:
                self.result(sub, cwd, here)
            argv = _sh.expand_wrappers(st)
            off = len(st.argv) - len(argv) if argv else 0
            dyn = list(st.dynamic[off:]) if argv else []
            dyn += [False] * (len(argv or ()) - len(dyn))
            self.stage(st, argv, dyn, cwd, pipelines.get(st.pipeline, [st]), outer)
            cwd = self.after_cd(argv, dyn, cwd)

    def after_cd(self, argv, dyn, cwd):
        if not argv or _sh.command_name(argv[0]) not in ("cd", "pushd", "chdir"):
            return cwd
        args = [(a, d) for a, d in zip(argv[1:], dyn[1:]) if not (a.startswith("-") and len(a) > 1)]
        if not args:
            return self.ctx.home
        word, d = args[0]
        if word == "-" or d and not (_HOME_VAR.match(word) and "$" not in _HOME_VAR.sub("", word)):
            return None
        return self.ctx.path(word, cwd, False)

    # -- files a stage writes ---------------------------------------------------
    def writes(self, st, argv, dyn, cwd, pipeline):
        ctx, raw = self.ctx, self.raw
        recursive = any(a == "--recursive" or re.fullmatch(r"-[a-zA-Z]*R[a-zA-Z]*", a) for a in (argv or [])[1:])
        for word, d, kind in _stage_writes(st, argv, dyn):
            if kind in ("redirect", "tee", "edit") and not d and is_secret_name(word):
                ctx.hit("secret-files", False, raw)
            p = ctx.path(word, cwd, d)
            if p is None:
                continue
            if ctx.is_protected(p, as_ancestor=kind == "move-src" or (kind == "chmod" and recursive)):
                ctx.hit("self-protect", True, raw)
            if kind == "move-src":
                self.written.pop(p, None)
                continue
            if kind == "copydir":
                continue
            content = None
            if kind == "fetch":
                content = _FETCHED
            elif kind in ("redirect", "tee"):
                content = _literal_output(st, argv, dyn)
                if content is None and kind == "tee":
                    prev = [x for x in pipeline if x.index == st.index - 1]
                    if prev:
                        pargv = _sh.expand_wrappers(prev[0]) or []
                        pdyn = prev[0].dynamic[len(prev[0].argv) - len(pargv):]
                        content = _literal_output(prev[0], pargv, pdyn)
            gk = _git_internal_kind(p)
            if gk and (gk != "gitmodules" or not isinstance(content, str) or _GITMODULES_EXEC.search(content)):
                ctx.hit("git-internals", False, raw)
            if kind == "chmod":
                continue
            append = kind == "redirect" and any(op == ">>" and t == word for op, t in st.redirects)
            old = self.written.get(p)
            if append and p in self.written:
                content = old + "\n" + content if isinstance(old, str) and isinstance(content, str) else None
            self.written[p] = content

    # -- one stage -----------------------------------------------------------
    def stage(self, st, argv, dyn, cwd, pipeline, outer=frozenset()):
        ctx, raw = self.ctx, self.raw
        name = _sh.command_name(argv[0]) if argv else ""
        args, dargs = (argv[1:], dyn[1:]) if argv else ([], [])

        # writes: secret-files (redirects + tee, as forge), git-internals, self-protect, write-then-run
        self.writes(st, argv, dyn, cwd, pipeline)

        if not argv:
            return

        # recursive deletes
        if name in _DELETERS:
            _rm_check(ctx, argv, dyn, cwd, raw, "xargs" in st.wrappers or "parallel" in st.wrappers)
        if name == "find":
            _find_check(ctx, argv, dyn, cwd, raw)
        if name == "git":
            _git_check(ctx, argv, dyn, cwd, raw)
        if name == "chmod" and _chmod_wide(args):
            ctx.hit("chmod-wide", False, raw)

        # pipe-to-shell: a download earlier in the same pipeline feeds a shell/interpreter
        self.pipe_to_shell(st, argv, dyn, name, args, pipeline)

        # interpreter one-liners
        kind = _interp_kind(name)
        if kind:
            for code, d in _inline_code(kind, args, dargs):
                if d or _code_runs_shell(kind, code):
                    ctx.hit("uncertain-command", False, raw)
                if "co_hook" in code or (_WRITES_TEXT.search(code) and any(
                        m in code for m in ctx.mentions)):
                    ctx.hit("self-protect", True, raw)
            if kind not in ("awk", "vim") and (not args or args[:1] == ["-"]):
                for _dl, _q, body in st.heredocs:
                    if _code_runs_shell(kind, body):
                        ctx.hit("uncertain-command", False, raw)
                for op, t in st.redirects:
                    if op == "<<<" and _code_runs_shell(kind, t):
                        ctx.hit("uncertain-command", False, raw)

        # running co-guard's hook by hand
        if name not in _READERS and any("co_hook" in w for w in argv):
            ctx.hit("self-protect", True, raw)

        # ssh host CMD
        if name in ("ssh", "autossh"):
            for local in _ssh_local_commands(args):
                self.nested_eval(local, cwd)
            remote = _ssh_remote(args)
            if remote:
                self.nested_eval(remote, ctx.home, remote=True)

        # write-then-run
        self.write_then_run(st, argv, dyn, name, args, cwd, kind)

        # uncertainty near a dangerous verb
        if st.uncertain:
            toks = _stage_tokens(pipeline) | outer
            first_unknown = bool(st.dynamic[:1] and st.dynamic[0]) or bool(dyn[:1] and dyn[0])
            if _dangerous(toks) or (first_unknown and any(_DANGEROUS_ARGS.fullmatch(a) for a in argv[1:])):
                ctx.hit("uncertain-command", False, raw)

    def pipe_to_shell(self, st, argv, dyn, name, args, pipeline):
        ctx, raw = self.ctx, self.raw
        if argv and dyn[0] and _FETCH_SUB.search(argv[0]):
            ctx.hit("pipe-to-shell", False, raw)  # $(curl ...) as the command
        shell_name = _sh._shell_name(name)
        if shell_name:
            mode, path = _sh._shell_mode(args)
            script = path if mode in ("c", "file") else None
            if script and _FETCH_SUB.search(script):
                ctx.hit("pipe-to-shell", False, raw)
        elif name in ("source", ".", "eval"):
            if any(_FETCH_SUB.search(a) for a in args):
                ctx.hit("pipe-to-shell", False, raw)
        sink = False
        if shell_name:
            sink = _sh._shell_mode(args)[0] == "stdin"
        elif name in ("source", "."):
            sink = bool(args) and args[0] in ("/dev/stdin", "-", "/dev/fd/0")
        elif _INTERP_STDIN.fullmatch(name):
            sink = not args or args[0] == "-" or (_interp_kind(name) == "node" and args[:1] == ["-"])
        if sink:
            for p in pipeline:
                if p.index >= st.index:
                    continue
                pargv = _sh.expand_wrappers(p) or []
                if pargv and _sh.command_name(pargv[0]) in _FETCH:
                    ctx.hit("pipe-to-shell", False, raw)
                    break

    def write_then_run(self, st, argv, dyn, name, args, cwd, kind):
        if not self.written:
            return
        target, as_shell = None, True
        if _sh._shell_name(name):
            mode, path = _sh._shell_mode(args)
            if mode == "file":
                target = path
        elif name in ("source", "."):
            a = args[1:] if args[:1] == ["--"] else args
            target = a[0] if a else None
        elif kind in ("python", "perl", "ruby", "node", "php"):
            pos = [a for a in args if not a.startswith("-")]
            if pos and not _inline_code(kind, args, [False] * len(args)):
                target, as_shell = pos[0], False
        elif "/" in argv[0] and not dyn[0]:
            target = argv[0]
        if not target:
            return
        p = self.ctx.path(target, cwd, False)
        if p is None or p not in self.written:
            return
        content = self.written[p]
        if content is _FETCHED:
            self.ctx.hit("pipe-to-shell", False, self.raw)
        elif content is None:
            self.ctx.hit("uncertain-command", False, self.raw)
        elif as_shell:
            self.nested_eval(content, cwd)
        elif _code_runs_shell(kind, content):
            self.ctx.hit("uncertain-command", False, self.raw)

    def nested_eval(self, text, cwd, remote=False):
        ctx = self.ctx
        ctx.evals += 1
        if ctx.depth >= _MAX_DEPTH or ctx.evals > _MAX_EVALS:
            ctx.hit("unparseable", True, self.raw)
            return
        ctx.depth += 1
        was = ctx.remote
        ctx.remote = ctx.remote or remote
        try:
            sub = _Bash(ctx, self.raw)
            sub.written = dict(self.written)
            sub.run(text, cwd)
        finally:
            ctx.remote = was
            ctx.depth -= 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _mcp_action(ctx, tool_name):
    tools = ctx.mcp_tools if isinstance(ctx.mcp_tools, dict) else {}
    if tool_name in tools:
        return tools[tool_name]
    for pat, action in tools.items():
        if isinstance(pat, str) and any(c in pat for c in "*?[") and fnmatch.fnmatchcase(tool_name, pat):
            return action
    return None


def _file_tool(ctx, tool_name, tool_input, cwd):
    raw = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    raw = str(raw)
    if not raw:
        return
    p = ctx.path(raw, cwd or ctx.project, False)
    if ctx.is_protected(p):
        ctx.hit("self-protect", True, raw)
    if is_secret_name(raw):
        ctx.hit("secret-files", False, raw)
    gk = _git_internal_kind(p or "")
    if gk:
        if gk == "gitmodules":
            texts = [tool_input.get("content"), tool_input.get("new_string")]
            texts += [e.get("new_string") for e in tool_input.get("edits") or [] if isinstance(e, dict)]
            if any(isinstance(t, str) and _GITMODULES_EXEC.search(t) for t in texts):
                ctx.hit("git-internals", False, raw)
        else:
            ctx.hit("git-internals", False, raw)


def _order(hits):
    seen, out = set(), []
    for h in sorted(hits, key=lambda h: not h.hard):
        if h.rule_id in seen:
            continue
        seen.add(h.rule_id)
        out.append(h)
    return out


def evaluate(tool_name, tool_input, policy, cwd):
    """Decide one tool call. See the module docstring."""
    tool_name = str(tool_name or "")
    ctx = _Ctx(policy, cwd)
    start = _real(os.path.abspath(str(cwd))) if cwd else ctx.project
    if not isinstance(tool_input, dict):
        ctx.hit("unparseable", True, f"{tool_name}: malformed tool input")
    elif tool_name == "Bash":
        command = tool_input.get("command")
        command = command if isinstance(command, str) else ("" if command is None else str(command))
        _Bash(ctx, command).run(command, start)
    elif tool_name in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
        _file_tool(ctx, tool_name, tool_input, start)
    elif tool_name.startswith("mcp__"):
        action = _mcp_action(ctx, tool_name)
        try:
            text = json.dumps(tool_input, sort_keys=True, default=str)
        except (TypeError, ValueError, RecursionError):
            text = "<input>"
        summary = f"{tool_name} {text}"
        if action == "block":
            ctx.hit("mcp-block", True, summary)
        elif action == "require_approval":
            ctx.hit("mcp-require-approval", False, summary)
    hits = _order(ctx.hits)
    if any(h.hard or h.rule_id == "mcp-block" for h in hits):
        decision = "deny"
    elif hits:
        decision = "approve"
    else:
        decision = "allow"
    return Verdict(decision, hits)


def _main():
    req = json.load(sys.stdin)
    v = evaluate(req.get("tool_name"), req.get("tool_input"), req.get("policy") or {}, req.get("cwd"))
    json.dump(v.to_dict(), sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    _main()
