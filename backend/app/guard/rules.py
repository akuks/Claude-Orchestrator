"""co-guard rules engine: decides whether one Claude Code tool call may run.

Python port of jspark-forge ``scripts/lib/blocklist.js`` and
``rules/default-blocklist.json`` (MIT, same owner): the built-in block list
(inventory F28-F31, F34-F36, F38, F39), plus MCP tool policies. Bash is
parsed with :mod:`shell` (the co-guard parser); nothing is ever executed.

RULING R1: this module uses only the Python standard library and the siblings
``shell.py`` and ``dbrules.py``. It is copied next to them into the co-guard
plugin and must work both as ``app.guard.rules`` and as a standalone file run with
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
                                           #   keys may be fnmatch patterns; names
                                           #   match case-insensitively; the most
                                           #   restrictive matching action wins;
                                           #   an unknown action or a non-dict
                                           #   value means require_approval
      "protected_paths": ["/abs/co-guard", "/abs/_tasks/T1", ...],
                                           #   dirs/files (fnmatch patterns ok)
      "claude_config_dir": "/abs/.claude", # its settings*.json are protected
      "tmpdir": "/abs/tmp",                # optional: one more temp root ($TMPDIR comes from the env)
      "db_dev": ["host:*.dev.internal", "mcp:pg-dev*", "staging-db"],
                                           #   dev databases (forge "database dev:"
                                           #   syntax, see dbrules.py); not a list or
                                           #   a non-text entry: ignored (nothing dev)
      "mcp_servers": {"<server prefix, lower-cased>": {"name": str,
                      "environment": "dev"|"prod"|"unknown"}},
                                           #   dev/prod marks a database server;
                                           #   dev skips db-write (not sql-destroy)
      "env_paths": {"CO_POLICY_FILE": "/abs/...", "CO_TASK_DIR": "/abs/...",
                    "CLAUDE_CONFIG_DIR": "/abs/...", "CLAUDE_PLUGIN_ROOT": "/abs/..."}
                                           #   values of the agent's env vars that
                                           #   name guard files: "$CO_POLICY_FILE"
                                           #   is expanded like "$HOME"
    }

Rule ids
--------
HARD (never approvable):

* ``rm-rf-outside`` when a target is home, ``/`` or an ancestor of home
  (``~``, ``~/``, ``~/*``, ``~<server user>``, ``$HOME``, ``${HOME:?}/``,
  ``/``, ``/*``, a glob that can match one of them such as ``/Users/?``, a
  ``for`` loop variable bound to one of them), ``find``/``fd`` rooted at one
  of them with a delete action, ``mv`` of one of them, and ``rsync --delete``
  into one of them;
* ``self-protect``: a write, delete, move, chmod, link source or copy
  destination that is (or holds) a protected path - the folder of a protected
  glob such as ``CLAUDE_CONFIG_DIR`` counts as protected for copy/move
  destinations and command arguments; a protected path in the arguments of
  any command that is not a pure reader (``vim``, ``tar -C``, ``git -C`` ...);
  a target or ``cd`` that names ``$CO_POLICY_FILE``, ``$CO_TASK_DIR``,
  ``$CLAUDE_CONFIG_DIR`` or ``$CLAUDE_PLUGIN_ROOT`` when its value is not
  known; inline interpreter code that names those variables or a protected
  path - the same checks run on every code text an interpreter gets
  (``-c``/``-e``, a heredoc or here-string on stdin - also one attached to a
  compound command around a bare interpreter, ``{ python3; } <<E``,
  ``if ..; then node; fi <<E``, ``..; done <<E`` - and ``uv run -`` as
  Python, literal text piped in, a file written and run in the same line); a writer whose substituted
  arguments or ``xargs`` input come from an environment dump (``env``,
  ``printenv``, ``export -p``, ``declare -p``, ``set``, ``/proc/*/environ``)
  or from ``ls``/``find`` of protected paths; running ``co_hook.py`` by
  hand. Backstop: a guard variable NAME
  (or a ``$NAME`` reference) anywhere in the command line -
  ``p=$CO_POLICY_FILE``, ``${CO_POLICY_FILE%.json}``, ``$(printenv
  CO_TASK_DIR)``, ``${!v}`` - is hard unless every stage is a pure reader
  with no file writes (``cat $CO_POLICY_FILE``, ``echo $CO_TASK_DIR``,
  ``ls $CLAUDE_CONFIG_DIR``). The WHOLE ``CLAUDE_CONFIG_DIR`` is protected
  (Ruling R10). Protected and agent-config paths are compared case-folded
  (Ruling R9), and path globs are matched against protected paths per
  component (``/work/_tasks/T?/policy.json``). With the guard off
  ``rm -rf ~`` is still denied when home holds the Claude config dir;
* ``unparseable``: the parser gave up (``too_complex``, Ruling R3), a malformed
  ``tool_input``, or write-then-run/ssh re-checks deeper than 6 levels or more
  than 64 per call;
* ``mcp-block`` (marked hard: never approvable);
* ``unsupported-shell``: the ``PowerShell`` tool (co-guard parses Bash only).

Tools (Ruling R17, fail closed by name): ``Bash`` and ``Monitor``
(``tool_input.command``) get the Bash rules; Edit/Write/NotebookEdit/MultiEdit
the file rules; ``mcp__*`` the MCP policy; the read-only/meta tools in
``SAFE_TOOLS`` are allowed; ANY other tool is ``unknown-tool`` (confirmable,
always on), so a new shell-capable tool fails closed without blocking work.

CONFIRMABLE (a person may approve once): ``rm-rf-outside`` (recursive delete
outside the project, of the project itself or its ``.git``, or of a target we
cannot resolve; a delete strictly INSIDE a temp dir is free - ``/tmp``,
``/private/tmp``, ``$TMPDIR`` (the hook's env, plus policy ``tmpdir``; not
expanded when the command names ``TMPDIR`` other than as ``$TMPDIR`` or may
assign it), ``/private/var/folders/<a>/<b>/T`` - never a root itself, nor
``/private/var/folders``, ``<a>`` or ``<a>/<b>`` (caches), nor a root that is
or holds home or the project, nor over ssh), ``git-force-push-main``, ``git-reset-hard``,
``git-clean-fdx``, ``git-stash-drop``, ``secret-files``, ``chmod-wide``,
``pipe-to-shell``, ``git-internals`` (writes to ``.git/config``,
``.git/hooks``, ``.git/info/attributes``, ``.gitmodules`` with an
``update = !cmd`` key), ``agent-config`` (Rulings R6/R7/R20: writes to a
``.claude/settings*.json``, ``.claude/hooks/**``, ``.claude/agents/**``,
``.claude/skills/**``, ``.claude/commands/**`` or ``.mcp.json``),
``uncertain-command``, ``mcp-require-approval``, ``unknown-tool``,
``sql-destroy`` and ``db-write`` (database rules, ported in ``dbrules.py``:
DROP/TRUNCATE/DELETE without WHERE in the SQL a client or a database MCP
server receives, any target; any non-read statement, script file or unknown
stdin against a target that is neither local nor dev, prisma/supabase
commands that write a database, database MCP write tools - checked for
every stage, nested and wrapped ones included, with ``PGHOST``-style
variables from the hook's own env (Ruling P6), prefixes (also into
``bash -c`` bodies) and earlier ``export``s - ``source``/``.``, ``set -a``,
``export $(..)``, ``read``, ``eval "$x"``, ``env -S`` and env loaders
(``dotenv``/``env-cmd``/``with-env`` also via npx..., ``op|infisical|doppler
run --``, ``direnv exec``) make them unknown; ``exec < file`` makes every later
client's input unknown; shell escapes inside a client (psql ``\\!``, ``\\o |cmd``,
``COPY .. PROGRAM``, sqlite ``.shell``, mysql ``system``) are checked as
commands; a client with no inline SQL on a line with a pipe reads unknown
input; when both fire on one call only ``sql-destroy`` is kept; an
``mcp-block`` tool gets no database hits).

Paths: write targets, file-tool paths, ``cd`` targets, glob folders and
delete targets ending in ``/`` or ``/.`` are resolved through every symlink
(``os.path.realpath``); a bare delete target keeps its last component as is
(``rm -rf link`` removes the link only). Relative paths resolve against
``cwd`` and literal ``cd``/``pushd`` earlier in the same command line (a
``cd`` is assumed to take effect; subshells are flattened by the parser, so a
``cd`` inside ``( )`` is also followed - this errs towards asking). Long
options are matched by any unambiguous GNU-style prefix (``--rec``,
``--force-w``, ``--har``); an ambiguous prefix that could mean the dangerous
option counts as it.

Bash: every stage is checked, nested ones (``$( )``, ``sh -c``, ``eval``,
``find -exec``, heredocs fed to shells ...) included, after wrappers
(``sudo``, ``env``, ``xargs`` ...) are stripped. Extra checks:

* ``uncertain-command``: a stage the parser marked uncertain, when its
  pipeline (with everything nested in it) or the stages that run it
  (``bash -c "$x"``, ``eval "$x"``) mention a dangerous verb (rm, git, curl,
  a shell, eval, dd, mkfs, chmod, sudo, xargs, at, tmux ...) or, when the
  command word itself is unknown, dangerous arguments (``-rf``, ``--force``,
  ``~``, ``/``). A command word that comes from a command substitution, or
  from a variable assigned from one, is always ``uncertain-command`` (a
  ``$(cat F)`` of a file written literally earlier in the line is checked as
  that text). Narrow allowance: ``$(which X)``, ``$(command -v X)``,
  ``$(type -P X)``, ``$(pyenv which X)`` (X a plain tool name),
  ``$(git rev-parse --show-toplevel)/REL`` (the project dir), ``$(npm bin)/X``,
  ``$(npm root)/.bin/X``, ``$(go env GOPATH|GOROOT)/bin/X``,
  ``$(brew --prefix [pkg])/bin/X`` and ``$(poetry env info -p)/bin/X`` - also
  through a variable assigned exactly once from one of them - are checked as
  ``X args`` (``$(which bash) -c 'rm -rf ~'`` is still checked as bash).
  Uncertain stages with only harmless words (``"$EDITOR" f``) are allowed. Interpreter one-liners that run shell or delete files
  (``python -c``, ``perl -e``, ``ruby -e``, ``node -e``, ``php -r``,
  ``awk 'system(..)'``, ``vim -c '!..'``, GNU ``sed`` ``e``) are also
  ``uncertain-command``.
* ``ssh [opts] host CMD``: CMD is parsed and checked as a command on a machine
  where nothing is "the project"; ``-o ProxyCommand=``/``LocalCommand=``
  values are checked as local commands.
* ``find ROOT ... -delete`` / ``-exec rm ...`` (and ``fd ... -x/-X rm``,
  ``rimraf``/``npx rimraf``): ROOT is a delete target (with a name/path/time
  filter, only things strictly inside ROOT - except home and ``/``, which
  are always hard). ``find -L``/``-follow`` with a delete is at least
  confirmable. ``find ROOT ... -exec CMD {}`` with a CMD that may write is
  hard when ROOT covers a protected path (judged by its ``-name`` filters).
* ``git checkout|restore [<tree>] -- PATHS`` into ``.claude``/``.mcp.json``
  is ``agent-config``; ``git push --prune`` with a wildcard covering main is
  ``git-force-push-main``.
* write-then-run in one command line (``echo .. > x.sh; bash x.sh``,
  ``cat > x <<EOF .. EOF; sh x``, ``sh < x``, ``cat x | bash``,
  ``curl -o x u && ./x``, a written ``Makefile`` then ``make``): the written
  text is checked when it is literal, a download is ``pipe-to-shell``,
  anything else is ``uncertain-command``.
* ``pipe-to-shell``: a download (curl, wget, httpie, xh) earlier in the same
  pipeline than a stage that reads its program from stdin (a shell, or an
  interpreter with no script / ``-`` / ``/dev/stdin``, ``deno|bun|uv run -``,
  ``pwsh``, ``lua``, ``Rscript``, ``tclsh``, ``osascript``, ``env -S 'python3'``);
  ``<(curl ..)``/``$(curl ..)`` as a shell's or interpreter's argument; a
  downloaded file run later in the same line.
* ``secret-files``: redirects, ``tee``, ``sed -i``/``perl -i``, ``truncate``
  and the destinations of ``cp``/``mv``/``install``/``ln``/``rsync``/``dd``
  (``cp`` from a template such as ``.env.example`` stays free). A template
  word (example, sample, template, dist) exempts a file only as its last
  suffix.

Seatbelt limits
---------------
co-guard is a seatbelt, not a sandbox. It does not see: scripts that already
exist on disk (``bash script.sh``, ``make`` with an existing Makefile,
``npm run``), interpreter code obfuscated past the patterns (``getattr`` on
builtins built from strings, base64-decoded code), GNU ``sed`` scripts read
from files, ``ssh host bash -s < file``, symlinks created by earlier tool
calls that point somewhere harmless at check time, and anything a program does
with paths it computes at run time - including environment variable names
computed in interpreter code (``os.getenv('CO_'+'POLICY_FILE')``). Archive and
patch tools write files whose names it cannot see: ``tar -x``, ``unzip``,
``git apply``, ``patch``, ``git stash pop``, ``cp -r evil/. .`` and
``rsync -a evil/ .`` can therefore drop ``.claude/`` or ``.mcp.json`` files
unnoticed. ``"$(dirname "$0")"/run.sh`` stays ``uncertain-command``.
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

def _sibling(name):
    """``name``.py from this package, or (standalone copy in the co-guard
    plugin, python3 -I) the file next to this one."""
    if __package__:
        return importlib.import_module(__package__ + "." + name)
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(
            name, os.path.join(os.path.dirname(os.path.abspath(__file__)), name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


_sh = _sibling("shell")
_db = _sibling("dbrules")

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
    "agent-config": (
        "this changes Claude Code's project settings, hooks or MCP servers, which can run commands "
        "outside co-guard", None),
    "uncertain-command": ("co-guard could not determine what this command runs", None),
    "self-protect": (
        None,
        "this changes or runs co-guard's own files (plugin, policy, spool or Claude settings), "
        "which only the orchestrator does"),
    "unparseable": (None, "this command is too complex for co-guard to check; split it into simpler commands"),
    "mcp-require-approval": ("this MCP tool needs a person to approve each call", None),
    "mcp-block": (None, "this MCP tool is blocked for this task"),
    "unsupported-shell": (None, "co-guard cannot check this shell (only Bash is supported); use the Bash tool"),
    "unknown-tool": ("co-guard does not know this tool, so a person must approve each call", None),
    "sql-destroy": (
        "this deletes database tables or every row of a table (DROP, TRUNCATE, or DELETE without WHERE)", None),
    "db-write": (
        "this writes to a production database (an insert, update, schema change, migration, merge, reset or "
        "restore; reads are never blocked)", None),
}
_ALWAYS_ON = frozenset({"self-protect", "unparseable", "mcp-block", "mcp-require-approval", "unsupported-shell",
                        "unknown-tool"})

# Ruling R17: tools are checked fail closed, by name.
_BASH_TOOLS = frozenset({"Bash", "Monitor"})  # tool_input.command is a Bash command line
_UNSUPPORTED_SHELLS = frozenset({"PowerShell"})
_FILE_TOOLS = frozenset({"Edit", "Write", "NotebookEdit", "MultiEdit"})
# Read-only or meta tools (names checked against the CLI 2.1.329 binary): always allowed.
SAFE_TOOLS = frozenset({
    "Read", "Glob", "Grep", "LS", "NotebookRead", "WebFetch", "WebSearch", "TodoWrite", "Task", "Agent",
    "BashOutput", "KillShell", "KillBash", "ExitPlanMode", "EnterPlanMode", "ListMcpResourcesTool",
    "ReadMcpResourceTool", "SlashCommand", "Skill", "AskUserQuestion", "TaskCreate", "TaskGet", "TaskList",
    "TaskUpdate", "TaskStop", "TaskOutput", "ToolSearch", "LSP", "Cd", "SendMessage", "SendUserMessage",
    "StructuredOutput", "Sleep",
})

_SUMMARY_MAX = 300
_MAX_DEPTH = 6  # write-then-run / ssh re-evaluation depth
_MAX_EVALS = 64  # write-then-run / ssh re-evaluations per tool call

SHELLS = _sh.SHELLS
_GUARD_VARS = ("CO_POLICY_FILE", "CO_TASK_DIR", "CLAUDE_CONFIG_DIR", "CLAUDE_PLUGIN_ROOT")
_GUARD_VAR_REF = re.compile(r"\$\{?(" + "|".join(_GUARD_VARS) + r")\b")
_GUARD_REF_RAW = re.compile(r"\$\{?[#!]?(" + "|".join(_GUARD_VARS) + r")(?![A-Za-z0-9_])")
_GUARD_NAME = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(_GUARD_VARS) + r")(?![A-Za-z0-9_])")
_FETCH = frozenset({"curl", "wget", "http", "https", "xh", "xhs"})
_PY = re.compile(r"(python|pypy)[\d.]*")
_TEMPLATE_WORDS = frozenset({"example", "sample", "template", "dist"})
_SECRET_NAMES = frozenset({".netrc", "_netrc", ".npmrc", ".pypirc", "authorized_keys", ".git-credentials"})
_READERS = frozenset({"printenv", "cat", "less", "more", "head", "tail", "grep", "egrep", "fgrep", "rg", "ag", "wc", "file",
                      "stat", "ls", "diff", "cmp", "sha256sum", "sha1sum", "sha512sum", "md5sum", "md5", "shasum",
                      "b2sum", "cksum", "bat", "strings", "od", "xxd", "hexdump", "test", "[", "[[", "echo",
                      "printf", "realpath", "readlink", "dirname", "basename", "tree", "du", "jq", "nl", "cd",
                      "pushd", "popd", "true", ":", "type", "which", "command"})
_DANGEROUS_VERBS = frozenset({"rm", "rmdir", "unlink", "shred", "dd", "chmod", "chown", "chgrp", "git", "curl",
                              "wget", "eval", "source", ".", "exec", "truncate", "mv", "sudo", "doas", "su",
                              "xargs", "parallel", "ssh", "scp", "rsync", "kill", "pkill", "killall", "crontab",
                              "launchctl", "systemctl", "at", "batch", "tmux", "screen", "systemd-run", "rimraf",
                              "fd", "fdfind"}) | SHELLS
_DANGEROUS_ARGS = re.compile(r"-[a-zA-Z]*([rR][a-zA-Z]*f|f[a-zA-Z]*[rR])[a-zA-Z]*|--recursive|--force.*|--hard|"
                             r"--no-preserve-root|~|~/.*|/|/\*|\$\{?HOME\}?(/.*)?")
_FIND_ACTIONS = frozenset({"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf",
                           "-fls"})
_FIND_NARROW = frozenset({"-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename", "-regex", "-iregex",
                          "-newer", "-anewer", "-cnewer", "-mtime", "-mmin", "-atime", "-amin", "-ctime", "-cmin",
                          "-size", "-empty", "-user", "-group", "-perm", "-links", "-inum", "-samefile", "-lname",
                          "-ilname", "-newermt", "-uid", "-gid"})
_DELETERS = frozenset({"rm", "rmdir", "unlink", "shred", "rimraf"})
_WRITE_OPS = frozenset({">", ">>", ">|", "&>", "&>>", "<>", ">&"})
# Final fix wave (Ruling R29): recursive deletes strictly INSIDE a temp dir are
# free. Roots: /tmp, /private/tmp, $TMPDIR (the hook's own environment - the
# same value the agent's shell has - and policy "tmpdir" if set) and every
# macOS per-user temp dir /private/var/folders/<a>/<b>/T. Never the roots
# themselves, and never /private/var/folders, <a> or <a>/<b> (they also hold
# caches, <a>/<b>/C); paths are resolved like every delete target.
_TEMP_ROOTS = ("/tmp", "/private/tmp")
_VAR_FOLDERS = "/private/var/folders"
# a bare TMPDIR name (not a plain $TMPDIR / ${TMPDIR} reference): the command
# may set it, so $TMPDIR is not expanded for that command
_TMPDIR_REF = re.compile(r"\$TMPDIR(?![A-Za-z0-9_])|\$\{TMPDIR\}")
_PWD_VAR = re.compile(r"^(\$\{PWD\}|\$PWD|\$\(pwd\)|`pwd`)(?=/|$)")
_GLOB = re.compile(r"[*?\[]")
_FETCH_SUB = re.compile(r"(<\(|\$\(|`)\s*(sudo\s+)?(curl|wget|http|https|xh)\b")
_CAT_SUB = re.compile(r"^(?:\$\(\s*cat\s+([^\s()`$;|&<>]+)\s*\)|`\s*cat\s+([^\s()`$;|&<>]+)\s*`)$")

# Interpreter one-liners that run shell commands or delete files (carried from Task 1).
_CODE_PATTERNS = {
    "python": re.compile(r"\bos\s*\.\s*(system|popen|exec\w*|spawn\w*|remove|unlink|rmdir|removedirs|posix_spawn\w*)"
                         r"\b|\bsubprocess\b|\bshutil\s*\.\s*(rmtree|move)\b|\bpty\s*\.\s*spawn|\b__import__\b|"
                         r"\.\s*(unlink|rmdir)\s*\(|\bcommands\s*\.\s*getoutput|\bgetattr\s*\(|\bimportlib\b|"
                         r"\bexec\s*\(|\beval\s*\(|\bcompile\s*\(|__builtins__"),
    "perl": re.compile(r"\bsystem\b|\bexec\b|`|\bqx\b|\bunlink\b|\brmtree\b|\bremove_tree\b|\bopen\s*\(?[^;]*\||"
                       r"\beval\b"),
    "ruby": re.compile(r"\bsystem\b|\bexec\b|`|%x|\bspawn\b|\bFileUtils\b|\bFile\s*\.\s*(delete|unlink)|"
                       r"\bIO\s*\.\s*popen|\bOpen3\b|\bDir\s*\.\s*(rmdir|delete|unlink)|\beval\b|\bsend\b"),
    "node": re.compile(r"child_process|\bexecSync\b|\bexecFileSync\b|\bspawn(Sync)?\b|\brmSync\b|\bunlinkSync\b|"
                       r"\brmdirSync\b|\bfs\s*\.\s*(rm|unlink|rmdir)\b|\bDeno\s*\.\s*(run|Command|remove)|"
                       r"\bBun\s*\.\s*(spawn|\$)|\beval\s*\(|\bFunction\s*\(|\brequire\s*\(\s*[^'\"]"),
    "php": re.compile(r"\b(system|exec|shell_exec|passthru|popen|proc_open|pcntl_exec|unlink|rmdir|eval)\s*\(|`"),
    "awk": re.compile(r"\bsystem\s*\(|\|\s*\"|\"\s*\|\s*getline|\|&|\|\s*[A-Za-z_]\w*\s*$|\|\s*cmd\b"),
    "vim": re.compile(r"(^|[|:\s%])(\d+(,\d+)?|%|\.)?\s*(r|read|w|write)?\s*!(?!\s*$)|\bsystem\s*\(|"
                      r"\bterm(inal)?\b|\bsystemlist\s*\("),
}
_SED_EXEC = re.compile(r"(^|[;{}\n])\s*(\d+|\$|/[^/]*/)?\s*(,\s*(\d+|\$|/[^/]*/))?\s*e(\s|$|;)|"
                       r"(^|[;{}\n])\s*(\d+|\$|/[^/]*/)?\s*s(.).*?\8.*?\8[gpiImM0-9w]*e")
_NODE = frozenset({"node", "nodejs", "bun", "deno"})
_AWK = frozenset({"awk", "gawk", "mawk", "nawk"})
_VIM = frozenset({"vim", "vi", "nvim", "ex", "view", "gvim", "vimdiff", "rvim"})
# forge WRITES_TEXT: inline code that may change a file
_WRITES_TEXT = re.compile(r"write|append|copy|rename|move|unlink|remove|\brm|symlink|truncate|chmod|mkdir|"
                          r"\bos\.replace\b|open\s{0,5}\([^)]{0,300}?['\"](?=[rbt]{0,3}[wax+])[rwaxbt+]{1,4}['\"]|"
                          r"system|exec|spawn|popen|subprocess", re.I)
_AGENT_CODE = re.compile(r"\.claude[\\/]+(settings[^'\"\s]*\.json|hooks\b|agents\b|skills\b|commands\b)"
                         r"|\.claude['\"]|\.mcp\.json", re.I)
# Ruling R20: subagent, skill and slash-command definitions can start processes
# (frontmatter mcpServers, hooks, inline !`cmd`) outside PreToolUse.
_AGENT_DIRS = frozenset({"hooks", "agents", "skills", "commands"})
_GITMODULES_EXEC = re.compile(r"(?im)^\s*update\s*=\s*!")
_STDIN_PATHS = frozenset({"-", "/dev/stdin", "/dev/fd/0", "/proc/self/fd/0"})
_MAKEFILES = frozenset({"makefile", "gnumakefile"})

# Long options per command, for GNU-style prefix matching.
_RM_LONG = ("--recursive", "--force", "--interactive", "--one-file-system", "--no-preserve-root",
            "--preserve-root", "--dir", "--verbose", "--help", "--version")
_CHMOD_LONG = ("--recursive", "--reference", "--changes", "--silent", "--quiet", "--verbose",
               "--no-preserve-root", "--preserve-root", "--help", "--version")
_PUSH_LONG = ("--all", "--branches", "--mirror", "--tags", "--follow-tags", "--atomic", "--dry-run", "--porcelain",
              "--delete", "--prune", "--force", "--force-with-lease", "--force-if-includes", "--repo",
              "--receive-pack", "--exec", "--push-option", "--set-upstream", "--thin", "--no-thin", "--quiet",
              "--verbose", "--progress", "--recurse-submodules", "--verify", "--no-verify", "--signed",
              "--no-signed", "--ipv4", "--ipv6", "--no-force-with-lease")
_PUSH_FORCE = frozenset({"--force", "--force-with-lease", "--force-if-includes"})
_PUSH_VALUE = frozenset({"--repo", "--receive-pack", "--exec", "--push-option"})
_RESET_LONG = ("--hard", "--soft", "--mixed", "--merge", "--keep", "--quiet", "--patch", "--no-refresh",
               "--recurse-submodules", "--no-recurse-submodules", "--pathspec-from-file", "--pathspec-file-nul",
               "--intent-to-add")
_CHECKOUT_LONG = ("--force", "--ours", "--theirs", "--track", "--no-track", "--detach", "--orphan", "--merge",
                  "--conflict", "--patch", "--quiet", "--progress", "--overlay", "--no-overlay",
                  "--pathspec-from-file", "--recurse-submodules", "--ignore-other-worktrees",
                  "--ignore-skip-worktree-bits", "--guess", "--no-guess")
_CLEAN_LONG = ("--force", "--dry-run", "--interactive", "--quiet", "--exclude")


def _long(arg, options):
    """Full long options `arg` may abbreviate (GNU getopt), ignoring `=value`."""
    if not arg.startswith("--") or len(arg) < 3:
        return set()
    name = arg.split("=", 1)[0]
    if name in options:
        return {name}
    return {o for o in options if o.startswith(name)}


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
def _norm(path):
    p = posixpath.normpath(path)
    if p.startswith("//"):
        p = "/" + p.lstrip("/")
    return p


def _real(path):
    """Absolute path with every symlink resolved (writes, cd, file tools)."""
    return os.path.realpath(_norm(path))


def _real_last(path):
    """Symlinks resolved in the parent folders only: `rm -rf link` removes the
    link itself, not its target."""
    p = _norm(path)
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


def _ancestors(path):
    out = []
    while path not in ("/", ""):
        path = posixpath.dirname(path)
        out.append(path)
    return out


def _glob_could_match(pattern, path):
    """A path glob (per component, like the shell) could match `path`."""
    pp, tp = pattern.strip("/").split("/"), path.strip("/").split("/")
    if path == "/":
        return pattern.strip("/") == "" or bool(re.fullmatch(r"[/*?.]*", pattern))
    return len(pp) == len(tp) and all(fnmatch.fnmatchcase(t, p) for t, p in zip(tp, pp))


def is_secret_name(path):
    """forge isSecretName (.env*, *.pem, *.key, id_rsa*, *credentials*, *secret*)
    plus SSH keys, .netrc, .npmrc, .pypirc, .docker/config.json, .kube/config.
    A template word (example, sample, template, dist) exempts a file only as
    its LAST suffix (.env.example yes, .env.sample.local no)."""
    parts = [p for p in re.split(r"[\\/]", str(path)) if p]
    if not parts:
        return False
    base = parts[-1].lower()
    parent = parts[-2].lower() if len(parts) > 1 else ""
    sufs = base.split(".")
    if len(sufs) > 1 and sufs[-1] in _TEMPLATE_WORDS:
        return False
    if ".ssh" in (p.lower() for p in parts[:-1]):
        return not base.endswith(".pub") or base.startswith("id_")
    if base in _SECRET_NAMES or (parent == ".docker" and base == "config.json") or \
            (parent == ".kube" and base == "config"):
        return True
    return (base.startswith(".env") or base.endswith(".pem") or base.endswith(".key") or base.startswith("id_rsa")
            or base.startswith(("id_ed25519", "id_ecdsa", "id_dsa")) or "credentials" in base or "secret" in base)


def _is_template(path):
    sufs = re.split(r"[\\/]", str(path))[-1].lower().split(".")
    return len(sufs) > 1 and sufs[-1] in _TEMPLATE_WORDS


def _git_internal_kind(path):
    """'config' | 'hooks' | 'attributes' | 'gitmodules' | None for an absolute path."""
    parts = path.lower().split("/")
    if parts and parts[-1] == ".gitmodules":
        return "gitmodules"
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == ".git":
            rest = parts[i + 1:]
            if rest == ["config"]:
                return "config"
            if rest[:1] == ["hooks"]:
                return "hooks"
            if rest == ["info", "attributes"] or rest == ["info"]:
                return "attributes"
            return None
    return None


def _agent_config(path):
    """A .claude/settings*.json, .claude/{hooks,agents,skills,commands}/** or
    .mcp.json path (R6/R7/R20)."""
    parts = path.lower().split("/")
    if parts and parts[-1] == ".mcp.json":
        return True
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == ".claude":
            rest = parts[i + 1:]
            return (len(rest) == 1 and fnmatch.fnmatchcase(rest[0], "settings*.json")) or \
                (bool(rest) and rest[0] in _AGENT_DIRS)
    return False


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
        self.mcp_tools = policy.get("mcp_tools", {})
        # database rules: dev patterns and MCP server environments (malformed -> nothing is dev)
        db_dev = policy.get("db_dev")
        self.db_dev = [p for p in db_dev if isinstance(p, str)] if isinstance(db_dev, list) else []
        servers = policy.get("mcp_servers")
        self.mcp_servers = servers if isinstance(servers, dict) else {}
        # PGHOST-style variables: inherited from the hook's env (Ruling P6, the agent's shell has them too),
        # then set earlier in the command line (None: unknown)
        self.db_env = {n: os.environ[n] for n in _db.DB_ENV_NAMES if n in os.environ}
        self.exec_stdin = False  # `exec < file` earlier in the line: every later client reads unknown input
        env = policy.get("env_paths")
        env = {k: str(v) for k, v in env.items() if isinstance(k, str) and v} if isinstance(env, dict) else {}
        cfg = policy.get("claude_config_dir")
        if cfg:
            env.setdefault("CLAUDE_CONFIG_DIR", str(cfg))
        self.vars = {k: _real(os.path.abspath(v)) for k, v in env.items() if k in _GUARD_VARS}
        self.vars["HOME"] = self.home
        # $TMPDIR as the agent's shell sees it: the hook runs with Claude Code's env
        tmpdir = os.environ.get("TMPDIR") or policy.get("tmpdir")
        self.tmpdir = _real(os.path.abspath(str(tmpdir))) if tmpdir else None
        self.temp_roots = self._temp_roots(policy.get("tmpdir"))
        if self.tmpdir and self.tmpdir in self.temp_roots:
            self.vars["TMPDIR"] = self.tmpdir
        self._build_var_re()
        prot = [str(p) for p in (policy.get("protected_paths") or []) if p]
        if cfg:  # Ruling R10: the whole config dir (Claude Code writes it itself, never through tools)
            prot += [str(cfg), posixpath.join(str(cfg), "settings*.json")]
        prot += [v for k, v in self.vars.items() if k in ("CO_POLICY_FILE", "CO_TASK_DIR", "CLAUDE_PLUGIN_ROOT")]
        self.protected = []  # (pattern, is_glob)
        self.glob_dirs = set()  # folders of protected globs (CLAUDE_CONFIG_DIR)
        for p in prot:
            p = os.path.abspath(os.path.expanduser(p))
            for v in {_norm(p), _real_last(p) if _GLOB.search(posixpath.basename(p)) else _real(p)}:
                is_glob = bool(_GLOB.search(v))
                v = v.lower()  # Ruling R9: compared case-folded everywhere
                self.protected.append((v, is_glob))
                if is_glob and not _GLOB.search(posixpath.dirname(v)):
                    self.glob_dirs.add(posixpath.dirname(v))
        # text naming a protected path (for inline interpreter code)
        self.mentions = sorted({posixpath.dirname(v) if g else v for v, g in self.protected} - {"", "/"})
        self.hits = []
        self.remote = False  # inside `ssh host CMD`: nothing is the project
        self.depth = 0
        self.evals = 0

    def _build_var_re(self):
        names = "|".join(map(re.escape, self.vars))
        self._var_re = re.compile(r"^\$(?:\{(" + names + r")(?:[:]?[-?=+][^}]*)?\}|(" + names + r"))(?=/|$)")

    def _temp_roots(self, policy_tmpdir=None):
        """Fixed temp roots plus $TMPDIR; a root that is (or holds) home or
        the project, is / or a top-level folder, or is /private/var/folders
        or one of its first two levels, is no temp root."""
        roots = {_real(r) for r in _TEMP_ROOTS} | set(_TEMP_ROOTS)  # '/tmp' as named (a link on macOS)
        extra = {self.tmpdir} | ({_real(os.path.abspath(str(policy_tmpdir)))} if policy_tmpdir else set())
        roots |= {r for r in extra if r and r.count("/") >= 2 and not _within(_VAR_FOLDERS, r)
                  and not (_inside(r, _VAR_FOLDERS) and r.count("/") < 6)}
        return {r for r in roots if r != "/" and not _within(self.home, r) and not _within(self.project, r)}

    def distrust_tmpdir(self):
        """The command may set TMPDIR itself: do not expand $TMPDIR."""
        if self.vars.pop("TMPDIR", None) is not None:
            self._build_var_re()

    def in_temp(self, path, whole):
        """A delete of ``path`` (whole: the path itself; else some children of
        it) stays strictly inside a temp root. Never home or above, the
        project, an ancestor of it or its .git."""
        if self.remote or not path:
            return False
        if _within(self.home, path) or _within(self.project, path) or path == self.project + "/.git":
            return False
        roots = set(self.temp_roots)
        parts = path.split("/")
        if path.startswith(_VAR_FOLDERS + "/") and len(parts) > 6 and parts[6] == "T":
            root = "/".join(parts[:7])  # /private/var/folders/<a>/<b>/T
            if not _within(self.home, root) and not _within(self.project, root):
                roots.add(root)
        return any(_inside(path, r) if whole else _within(path, r) for r in roots)

    # -- hits --------------------------------------------------------------
    def hit(self, rule_id, hard, summary):
        if not self.guard and rule_id not in _ALWAYS_ON:
            return
        self.hits.append(_make_hit(rule_id, hard, summary))

    # -- paths -------------------------------------------------------------
    def expand(self, word, cwd):
        """Expand ~ / ~user / $HOME / guard env vars / $PWD prefixes and make the
        path absolute. None when the base is unknown (relative path with an
        unknown cwd, ~otheruser)."""
        w = word
        if w == "~" or w.startswith("~/"):
            w = self.home + w[1:]
        elif w.startswith("~"):
            user, _, rest = w[1:].partition("/")
            if user != posixpath.basename(self.home):
                return None  # ~otheruser, ~+, ~-
            w = self.home + ("/" + rest if rest else "")
        else:
            m = self._var_re.match(w)
            if m:
                w = self.vars[m.group(1) or m.group(2)] + w[m.end():]
            elif _PWD_VAR.match(w):
                if cwd is None:
                    return None
                w = cwd + _PWD_VAR.sub("", w, count=1)
        if not w.startswith("/"):
            if cwd is None:
                return None
            w = posixpath.join(cwd, w)
        return w

    def path(self, word, cwd, dynamic=False, follow=True):
        """Resolve a path word: absolute real path, or None if unknown."""
        w = self.expand(word, cwd)
        if w is None:
            return None
        if dynamic and ("$" in w or "`" in w or _GLOB.search(w)):
            return None
        return _real(w) if follow else _real_last(w)

    def guard_var_unknown(self, word):
        """The word names a guard env var whose value we do not know."""
        m = _GUARD_VAR_REF.search(word)
        return bool(m) and m.group(1) not in self.vars

    def is_protected(self, path, as_ancestor=False):
        """path is (inside) a protected path; with as_ancestor, also when a
        protected path is inside it (deleting/moving a parent folder)."""
        if not path:
            return False
        path = path.lower()
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

    def is_protected_dest(self, path):
        """A copy/move destination or command argument: protected, or the
        folder of a protected glob (CLAUDE_CONFIG_DIR for settings*.json)."""
        return bool(path) and (self.is_protected(path) or path.lower() in self.glob_dirs or
                               self.glob_hits_protected(path))

    def glob_hits_protected(self, pattern):
        """A path glob that could name a protected path or something inside one
        (/work/_tasks/T?/policy.json), compared per component, case-folded."""
        if not pattern or not _GLOB.search(pattern):
            return False
        pp = pattern.lower().strip("/").split("/")
        for pat, is_glob in self.protected:
            base = posixpath.dirname(pat) if is_glob else pat
            if _GLOB.search(base):
                continue
            pc = base.strip("/").split("/")
            if len(pp) >= len(pc) and all(fnmatch.fnmatchcase(c, g) for c, g in zip(pc, pp)):
                return True
        return False

    def guard_var_unresolved(self, word, cwd=None):
        """The word names a guard env var and cannot be fully expanded."""
        if not _GUARD_NAME.search(word):
            return False
        if self.guard_var_unknown(word):
            return True
        e = self.expand(word, cwd)
        return e is None or "$" in e or "`" in e

    def project_or_none(self):
        return None if self.remote else self.project


# ---------------------------------------------------------------------------
# Recursive-delete targets (forge rmCheck / classifyTargets)
# ---------------------------------------------------------------------------
_BROAD = re.compile(r"[*?.]*\*[*?.]*")
_SOME = "\x00some"  # some (unknown) children of a folder


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
        e = ctx.expand(w, cwd)
        if e is None:
            return None
        follow = w.endswith("/") or w.endswith("/.") or w in (".", "..")
        if dyn and ("$" in e or "`" in e):
            # unknown part: fine only behind a literal folder strictly inside the project
            lit = re.split(r"[$`]", e, maxsplit=1)[0]
            folder = _real(lit.rsplit("/", 1)[0] or "/")
            project = ctx.project_or_none()
            literal_prefix = not w.startswith(("$", "`")) or e != w
            if literal_prefix and "/" in re.split(r"[$`]", w, maxsplit=1)[0] and project and \
                    _inside(folder, project):
                out.append((folder, _SOME))
                continue
            return None
        norm = _norm(e)
        head, tail = posixpath.split(norm)
        if dyn and _GLOB.search(norm):
            for cand in [ctx.home, "/"] + _ancestors(ctx.home):  # /Users/?, /U*/u, /*
                if _glob_could_match(norm, cand):
                    out.append((cand, None))
            for pat, is_glob in ctx.protected:
                base = posixpath.dirname(pat) if is_glob else pat
                for cand in [base] + _ancestors(base):
                    if cand != "/" and _glob_could_match(norm, cand):
                        out.append((cand, None))
        if dyn and _GLOB.search(head):
            lit = _GLOB.split(norm, maxsplit=1)[0]
            out.append((_real(lit.rsplit("/", 1)[0] or "/"), _SOME))
            continue
        if dyn and _GLOB.search(tail):
            out.append((_real(head), "*" if _BROAD.fullmatch(tail) else tail))
            continue
        out.append((_real(e) if follow else _real_last(e), None))
    return out


def _protect_delete(ctx, path, glob, summary):
    """self-protect for deleting/moving `path` (glob: which children)."""
    if ctx.is_protected(path, as_ancestor=glob is None):
        ctx.hit("self-protect", True, summary)
        return
    if glob is None:
        return
    path, glob = path.lower(), glob if glob == _SOME else glob.lower()
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
    if ctx.is_protected(root):
        ctx.hit("self-protect", True, summary)  # rooted inside a protected folder
        return
    root = root.lower()
    for pat, is_glob in ctx.protected:
        base = posixpath.dirname(pat) if is_glob else pat
        if _GLOB.search(base) or not _within(base, root):
            continue
        cands = base[len(root):].strip("/").split("/") if base != root else []
        if is_glob:
            leaf = posixpath.basename(pat)
            cands += [_GLOB.sub("", leaf), _GLOB.sub("x", leaf)]
        cands = [c for c in cands if c]
        if not is_glob and not os.path.isfile(base):
            cands.append("*")  # a protected folder: any name may match inside it
        if names is None or "*" in cands or any(fnmatch.fnmatch(c.lower(), n.lower()) for c in cands for n in names):
            ctx.hit("self-protect", True, summary)
            return


def _is_home_or_above(ctx, path):
    return path == "/" or path == ctx.home or _inside(ctx.home, path)


def _classify(ctx, targets, cwd, summary, narrow=False, names=None):
    """targets: [(word, dynamic)]. Records rm-rf-outside / self-protect hits.
    narrow: only things strictly inside each target go (find with a filter;
    names: its -name patterns, or None for another kind of filter). Home, /
    and ancestors of home are hard even when narrow."""
    level = None  # None | 'confirm' | 'hard'
    project = ctx.project_or_none()
    for word, dyn in targets:
        kinds = _target_kinds(ctx, word, dyn, cwd)
        if kinds is None:
            if ctx.guard_var_unknown(word):
                ctx.hit("self-protect", True, summary)
            level = level or "confirm"
            continue
        for path, glob in kinds:
            if narrow and glob is None:
                if _is_home_or_above(ctx, path):
                    level = "hard"
                glob = _SOME
                _protect_find(ctx, path, names, summary)
            else:
                _protect_delete(ctx, path, glob, summary)
            whole = glob is None or _BROAD.fullmatch(glob) is not None
            if whole:
                if _is_home_or_above(ctx, path):
                    level = "hard"
                    continue
                if project and _inside(path, project) and path != project + "/.git":
                    continue
                if ctx.in_temp(path, True):  # judged as the folder: never a root itself (/tmp/*)
                    continue
            elif project and _within(path, project):
                continue
            elif ctx.in_temp(path, False):
                continue
            level = level or "confirm"
    if level:
        ctx.hit("rm-rf-outside", level == "hard", summary)


def _rm_targets(argv, dyn):
    """(recursive, [(word, dyn)]) for rm-style arguments."""
    recursive, end = False, False
    targets = []
    for a, d in zip(argv[1:], dyn[1:]):
        if not end and a == "--":
            end = True
            continue
        if not end and a.startswith("--") and not d:
            if "--recursive" in _long(a, _RM_LONG):
                recursive = True
            continue
        if not end and re.fullmatch(r"-[a-zA-Z]+", a):
            if "r" in a or "R" in a:
                recursive = True
            continue
        targets.append((a, d))
    return recursive, targets


def _find_check(ctx, argv, dyn, cwd, summary):
    args, dargs = argv[1:], dyn[1:]
    i, follow = 0, False
    while i < len(args) and args[i] in ("-H", "-L", "-P", "-O0", "-O1", "-O2", "-O3"):
        follow = follow or args[i] in ("-H", "-L")
        i += 1
    roots = []
    while i < len(args) and not (args[i].startswith("-") or args[i] in ("(", "!", ")")):
        roots.append((args[i], dargs[i]))
        i += 1
    expr = args[i:]
    deletes = "-delete" in expr
    writes = False
    for j, a in enumerate(expr):
        if a in ("-exec", "-execdir", "-ok", "-okdir") and j + 1 < len(expr):
            inner = _sh.expand_wrappers(expr[j + 1:]) or []
            if inner and _sh.command_name(inner[0]) in _DELETERS:
                deletes = True
            elif inner and _sh.command_name(inner[0]) not in _READERS:
                writes = True  # cp evil {}, sed -i .. {}, sh -c '.. > {}' ...
    follow = follow or "-follow" in expr
    acts = any(a in _FIND_ACTIONS for a in expr)
    roots = roots or [(".", False)]
    names = [expr[k + 1] for k, a in enumerate(expr) if a in ("-name", "-iname") and k + 1 < len(expr)]
    only_names = names and not any(a in _FIND_NARROW and a not in ("-name", "-iname") for a in expr)
    if writes:  # N3: a command that may write {} : protect what the roots cover
        for w, d in roots:
            p = ctx.path(w, cwd, d)
            if p is None:
                if ctx.guard_var_unresolved(w, cwd):
                    ctx.hit("self-protect", True, summary)
                continue
            if ctx.is_protected(p, as_ancestor=False) or p.lower() in ctx.glob_dirs:
                ctx.hit("self-protect", True, summary)
            else:
                _protect_find(ctx, p, names if only_names else None, summary)
    if follow and deletes:  # N7: -L / -follow: links inside the tree are followed out of it
        for w, d in roots:
            p = ctx.path(w, cwd, d)
            ctx.hit("rm-rf-outside", p is not None and _is_home_or_above(ctx, p), summary)
    if acts:  # find rooted inside a protected folder that runs or deletes anything
        for w, d in roots:
            p = ctx.path(w, cwd, d)
            if p and ctx.is_protected_dest(p):
                ctx.hit("self-protect", True, summary)
    if not deletes:
        return
    if follow:
        roots = [(w if w.endswith("/") else w + "/", d) for w, d in roots]
    narrow = any(a in _FIND_NARROW for a in expr)
    _classify(ctx, roots, cwd, summary, narrow=narrow, names=names if only_names else None)


_FD_VALUE = frozenset({"-e", "--extension", "-t", "--type", "-E", "--exclude", "-d", "--max-depth",
                       "--min-depth", "--exact-depth", "-S", "--size", "--changed-within", "--changed-before",
                       "-o", "--owner", "-j", "--threads", "--max-results", "--base-directory", "--search-path",
                       "-c", "--color", "--path-separator", "--batch-size", "--ignore-file"})


def _fd_check(ctx, argv, dyn, cwd, summary, bash):
    """fd [opts] [pattern] [path...] -x|-X CMD...: CMD runs on what fd finds."""
    args, dargs = argv[1:], dyn[1:]
    pos, cmd, i, filtered = [], None, 0, False
    while i < len(args):
        a = args[i]
        if a.split("=", 1)[0] in ("-e", "--extension", "-S", "--size", "--changed-within", "--changed-before",
                                  "-g", "--glob"):
            filtered = True
        if a in ("-x", "--exec", "-X", "--exec-batch"):
            cmd = (args[i + 1:], dargs[i + 1:])
            if ";" in cmd[0]:
                k = cmd[0].index(";")
                cmd = (cmd[0][:k], cmd[1][:k])
            break
        if a in _FD_VALUE:
            i += 2
            continue
        if not a.startswith("-"):
            pos.append((a, dargs[i]))
        i += 1
    if not cmd or not cmd[0]:
        return
    inner = cmd[0]
    if _sh.command_name(inner[0]) in _DELETERS:
        roots = pos[1:] or [(".", False)]
        pattern = pos[0][0] if pos else ""
        _classify(ctx, roots, cwd, summary, narrow=filtered or pattern not in ("", ".", "^", ".*"))
    bash.nested_eval(" ".join(_quote(w) for w in inner), cwd)


def _quote(w):
    return w if re.fullmatch(r"[\w@%+=:,./~{}-]+", w) else "'" + w.replace("'", "'\\''") + "'"


def _rimraf_targets(name, args):
    """npx rimraf X / pnpm dlx rimraf X / npm exec rimraf X / rimraf X -> targets, else None."""
    if name == "rimraf":
        return args
    rest = args
    if name in ("pnpm", "yarn", "npm"):
        if not rest or rest[0] not in ("dlx", "exec", "x"):
            return None
        rest = rest[1:]
    elif name not in ("npx", "pnpx", "bunx"):
        return None
    rest = [a for a in rest if a != "--"]
    while rest and rest[0].startswith("-"):
        rest = rest[2:] if rest[0] in ("-p", "--package") else rest[1:]
    if rest and re.fullmatch(r"rimraf(@[\w.^~-]+)?", rest[0]):
        return rest[1:]
    return None


# ---------------------------------------------------------------------------
# git (forge parseGit / pushForceMain / wholeTree / gitCheck)
# ---------------------------------------------------------------------------
_MAIN = ("main", "master")


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
    """(dir, subcommand, rest, [(key, value)] from -c)."""
    d, i, conf = cwd, 0, []
    while i < len(args) and args[i].startswith("-"):
        a = args[i]
        if a == "-C":
            if i + 1 < len(args):
                d = None if dyn[i + 1] or (d is None and not args[i + 1].startswith("/")) \
                    else ctx.path(args[i + 1], d, False)
            i += 2
            continue
        if a == "-c" and i + 1 < len(args):
            k, _, v = args[i + 1].partition("=")
            conf.append((k.lower(), v))
            i += 2
            continue
        if a in ("--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"):
            i += 2
            continue
        i += 1
    sub = args[i].lower() if i < len(args) else ""
    return d, sub, args[i + 1:], conf


def _is_main(dst, directory):
    dst = dst.strip()
    if dst in ("HEAD", "", "@"):
        br = _branch_of(directory)
        return br is None or br.lower() in _MAIN  # unknown branch: fail closed
    low = dst.lower()
    short = low[len("refs/heads/"):] if low.startswith("refs/heads/") else low
    if _GLOB.search(low):
        return any(fnmatch.fnmatchcase(m, short) or fnmatch.fnmatchcase("refs/heads/" + m, low) for m in _MAIN)
    return short in _MAIN


def _push_force_main(rest, directory, conf_specs, conf_mirror):
    force = all_ = delete = mirror = prune = False
    positional = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in ("-o",) or (a.startswith("--") and "=" not in a and _long(a, _PUSH_LONG) and
                             _long(a, _PUSH_LONG) <= _PUSH_VALUE):
            i += 2
            continue
        if a.startswith("--"):
            m = _long(a, _PUSH_LONG)
            if m & _PUSH_FORCE:
                force = True
            if m & {"--all", "--branches"}:
                all_ = True
            if "--mirror" in m:
                mirror = True
            if "--delete" in m:
                delete = True
            if "--prune" in m:
                prune = True
        elif re.fullmatch(r"-[a-zA-Z]+", a):
            force = force or "f" in a
            delete = delete or "d" in a
        else:
            positional.append(a)
        i += 1
    if mirror or conf_mirror:
        return True  # --mirror force-updates and deletes every ref
    refspecs = positional[1:] or list(conf_specs)
    if prune and any(_GLOB.search(r) and _is_main(r.lstrip("+").split(":")[-1], directory) for r in refspecs):
        return True  # --prune deletes remote refs the wildcard covers
    if delete and any(_is_main(r.lstrip("+").split(":")[-1], directory) for r in refspecs):
        return True
    if any(r.startswith(":") and _is_main(r[1:], directory) for r in refspecs):
        return True
    plus = [r for r in refspecs if r.startswith("+")]
    if not force and not plus:
        return False
    if all_:
        return True
    considered = refspecs if force else plus
    dsts = [r.lstrip("+").split(":")[-1] for r in considered] if considered else ["HEAD"]
    return any(_is_main(dst, directory) for dst in dsts)


def _git_check(ctx, argv, dyn, cwd, summary, bash):
    d, sub, rest, conf = _parse_git(ctx, argv[1:], dyn[1:], cwd)
    rule = None
    if sub == "config":
        pos = [a for a in rest if not a.startswith("-")]
        if len(pos) >= 2:
            key = pos[0].lower()
            if re.fullmatch(r"remote\..+\.push", key):
                bash.git_push_specs.append(pos[1])
            elif re.fullmatch(r"remote\..+\.mirror", key) and pos[1].lower() in ("true", "yes", "on", "1"):
                bash.git_mirror = True
    elif sub == "push":
        specs = [v for k, v in conf if re.fullmatch(r"remote\..+\.push", k)] + bash.git_push_specs
        mirror = bash.git_mirror or any(re.fullmatch(r"remote\..+\.mirror", k) and v.lower() in
                                        ("true", "yes", "on", "1", "") for k, v in conf)
        if _push_force_main(rest, d, specs, mirror):
            rule = "git-force-push-main"
    elif sub == "reset":
        if any("--hard" in _long(a, _RESET_LONG) for a in rest):
            rule = "git-reset-hard"
    elif sub in ("checkout", "restore"):
        at_root = d is not None and d == ctx.project
        if any(a in (":/", ":/.") or (a in (".", "./") and at_root) for a in rest):
            rule = "git-reset-hard"
        if sub == "checkout" and any(re.fullmatch(r"-[a-zA-Z]*f[a-zA-Z]*", a) or "--force" in _long(a, _CHECKOUT_LONG)
                                     for a in rest if a != "--"):
            rule = "git-reset-hard"
        if "--" in rest:
            paths = rest[rest.index("--") + 1:]
        else:
            pos = [a for a in rest if not a.startswith("-")]
            paths = pos if sub == "restore" else pos[1:]
        for w in paths:
            p = ctx.path(w, d, False) if d is not None or w.startswith("/") else None
            if p and (_agent_config(p) or posixpath.basename(p).lower() == ".claude" or
                      _agent_config(p + "/settings.json")):
                ctx.hit("agent-config", False, summary)
    elif sub == "clean":
        flags, longs = "", set()
        for a in rest:
            if a.startswith("--"):
                longs |= _long(a, _CLEAN_LONG)
            elif re.fullmatch(r"-[a-zA-Z]+", a):
                flags += a[1:]
        dry = "n" in flags or "--dry-run" in longs
        force = "f" in flags or "--force" in longs
        if force and not dry and ("x" in flags or "X" in flags):
            rule = "git-clean-fdx"
    elif sub == "stash":
        pos = [a for a in rest if not a.startswith("-")]
        if pos[:1] and pos[0] in ("drop", "clear"):
            rule = "git-stash-drop"
    if rule:
        ctx.hit(rule, False, summary)


# ---------------------------------------------------------------------------
# chmod (forge: 777, -R / --recursive; plus others-writable modes)
# ---------------------------------------------------------------------------
def _mode_wide(mode):
    """An octal (1-5 digits) or symbolic mode that lets everyone write."""
    if re.fullmatch(r"[0-7]{1,5}", mode):
        return mode[-1] in "2367"
    for clause in mode.split(","):
        m = re.fullmatch(r"([ugoa]*)[+=]([rwxXst]*)", clause)
        if m and ("o" in m.group(1) or "a" in m.group(1)) and "w" in m.group(2):
            return True
    return False


def _chmod_wide(args):
    for a in args:
        if re.fullmatch(r"-[a-zA-Z]*R[a-zA-Z]*", a) or "--recursive" in _long(a, _CHMOD_LONG):
            return True
        if _mode_wide(a):
            return True
    return False


def _mode_option(args):
    """Value of -m MODE / -mMODE / --mode[=]MODE (install, mkdir)."""
    for i, a in enumerate(args):
        if a in ("-m", "--mode") and i + 1 < len(args):
            return args[i + 1]
        if a.startswith("--mode="):
            return a.split("=", 1)[1]
        if re.fullmatch(r"-[a-zA-Z]*m.+", a) and not a.startswith("--"):
            return a[a.index("m") + 1:]
    return None


# ---------------------------------------------------------------------------
# Writes: which files a stage writes, and what text when it is literal
# ---------------------------------------------------------------------------
_FETCHED = object()
_CURL_VALUE = set("AbcCdDeEFHKmoPQrTtuUwxXyYz")
_WGET_VALUE = set("oaeiBtOTwQPUlARDIX")
_RSYNC_VALUE = frozenset({"-e", "--rsh", "--exclude", "--include", "--filter", "-f", "--files-from", "--rsync-path",
                          "--log-file", "--password-file", "--chmod", "--chown", "-B", "--block-size", "--timeout",
                          "--bwlimit", "--max-size", "--min-size", "--partial-dir", "-T", "--temp-dir",
                          "--compare-dest", "--copy-dest", "--link-dest", "--backup-dir", "--suffix", "--port"})
_CP_VALUE = frozenset({"-S", "--suffix", "-m", "--mode", "-o", "--owner", "-g", "--group", "--context",
                       "--backup", "--reflink", "--sparse", "--preserve", "--strip-program"})


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


def _url_name(url):
    return url.split("?")[0].split("#")[0].rstrip("/").rsplit("/", 1)[-1] or "index.html"


def _downloads(name, args, dargs):
    """[(word, dyn)] files curl / wget write."""
    out = []
    if name == "curl":
        urls, outputs, remote, outdir, i = [], [], False, "", 0
        while i < len(args):
            a, d = args[i], dargs[i]
            if a.startswith("--"):
                key, eq, val = a.partition("=")
                if key == "--output":
                    outputs.append((val, d) if eq else ((args[i + 1], dargs[i + 1]) if i + 1 < len(args) else None))
                    i += 1 if eq else 2
                    continue
                if key == "--output-dir":
                    outdir = val if eq else (args[i + 1] if i + 1 < len(args) else "")
                    i += 1 if eq else 2
                    continue
                if key in ("--remote-name", "--remote-name-all"):
                    remote = True
                i += 1
                continue
            if a.startswith("-") and len(a) > 1:
                for k, ch in enumerate(a[1:], start=1):
                    if ch == "O":
                        remote = True
                        continue
                    if ch in _CURL_VALUE:
                        val = a[k + 1:]
                        if not val and i + 1 < len(args):
                            val, d = args[i + 1], dargs[i + 1]
                            i += 1
                        if ch == "o":
                            outputs.append((val, d))
                        break
                i += 1
                continue
            urls.append((a, d))
            i += 1
        out += [o for o in outputs if o and o[0] != "-"]
        if remote:
            out += [(posixpath.join(outdir, _url_name(u)) if outdir else _url_name(u), d) for u, d in urls]
        return out
    # wget
    urls, named, prefix, i = [], False, "", 0
    while i < len(args):
        a, d = args[i], dargs[i]
        if a.startswith("--"):
            key, eq, val = a.partition("=")
            if key in ("--output-document", "--directory-prefix"):
                if not eq:
                    val, d = (args[i + 1], dargs[i + 1]) if i + 1 < len(args) else ("", d)
                    i += 1
                if key == "--output-document":
                    named = True
                    if val != "-":
                        out.append((val, d))
                else:
                    prefix = val
            i += 1
            continue
        if a.startswith("-") and len(a) > 1:
            for k, ch in enumerate(a[1:], start=1):
                if ch in _WGET_VALUE:
                    val = a[k + 1:]
                    if not val and i + 1 < len(args):
                        val, d = args[i + 1], dargs[i + 1]
                        i += 1
                    if ch == "O":
                        named = True
                        if val != "-":
                            out.append((val, d))
                    elif ch == "P":
                        prefix = val
                    break
            i += 1
            continue
        urls.append((a, d))
        i += 1
    if not named:
        out += [(posixpath.join(prefix, _url_name(u)) if prefix else _url_name(u), d) for u, d in urls if "://" in u]
    return out


def _copy_parts(name, args, dargs):
    """(dest (word, dyn) | None, dest_is_dir, srcs [(word, dyn)], flags) for cp/mv/install/ln/rsync/scp."""
    pos, tdir, no_tdir, flags, i, end = [], None, False, set(), 0, False
    values = _RSYNC_VALUE if name == "rsync" else _CP_VALUE
    while i < len(args):
        a, d = args[i], dargs[i]
        if end or not a.startswith("-") or a == "-":
            pos.append((a, d))
            i += 1
            continue
        if a == "--":
            end = True
            i += 1
            continue
        if a in ("-t", "--target-directory") and i + 1 < len(args):
            tdir = (args[i + 1], dargs[i + 1])
            i += 2
            continue
        if a.startswith("--target-directory="):
            tdir = (a.split("=", 1)[1], d)
            i += 1
            continue
        if a.startswith("--"):
            key = a.split("=", 1)[0]
            flags.add(key)
            if key.startswith("--del"):
                flags.add("--delete")
            if key in ("--symbolic-link", "--link", "--symbolic"):
                flags.add("-s" if key != "--link" else "-l")
            if key == "--no-target-directory":
                no_tdir = True
            i += 2 if (a in values and "=" not in a) else 1
            continue
        letters = a[1:]
        flags.update("-" + c for c in letters)
        if "T" in letters and name != "rsync":
            no_tdir = True
        if name != "rsync" and any("-" + c in values for c in letters[-1:]) and len(letters) == 1:
            i += 2
            continue
        if name == "rsync" and ("-" + letters[-1]) in values:
            i += 2
            continue
        if name == "install" and "m" in letters and letters.endswith("m"):
            i += 2
            continue
        i += 1
    if name == "install" and "-d" in flags:
        return None, True, pos, flags  # install -d DIR...: creates folders
    if tdir:
        return tdir, True, pos, flags
    if len(pos) < 2:
        return None, False, pos, flags
    dest, srcs = pos[-1], pos[:-1]
    return dest, (None if no_tdir else (len(srcs) > 1 or dest[0].endswith("/"))), srcs, flags


class _Bash:
    def __init__(self, ctx, raw):
        self.ctx = ctx
        self.raw = raw
        self.written = {}  # abs path -> text | None | _FETCHED
        self.loopvars = {}  # for-loop / set -- variables bound to literal words
        self.nonliteral = set()  # variables assigned from a substitution or unknown value
        self.git_push_specs = []
        self.git_mirror = False
        self.subst_vars = {}
        self.res_stack = []  # the ParseResult (nesting level) being checked
        self.text = raw  # the command text at this nesting level

    def run(self, command, cwd):
        self.text = command
        res = _sh.parse(command)
        pctx = getattr(res, "_ctx", None)
        if "TMPDIR" in _TMPDIR_REF.sub("", command) or pctx is None or pctx.poison or "TMPDIR" in pctx.writes:
            self.ctx.distrust_tmpdir()
        if res.too_complex:
            self.ctx.hit("unparseable", True, self.raw)
        self.subst_vars = _subst_assignments(command)
        if (_names_guard_var(res) or _GUARD_REF_RAW.search(command)) and \
                not all(_pure_reader(st) for st in _sh.walk(res)):
            self.ctx.hit("self-protect", True, self.raw)  # N1 backstop: a guard var NAME near a non-reader
        self.result(res, cwd)
        if res.errors and not any(st.uncertain for st in _sh.walk(res)) and _dangerous(_stage_tokens(res)):
            self.ctx.hit("uncertain-command", False, self.raw)

    def result(self, res, cwd, outer=frozenset()):
        """outer: command names of the stages (and their pipelines) that run
        this nested text - `bash -c "$x"` runs an unknown script in bash."""
        pipelines = {}
        for st in res:
            pipelines.setdefault(st.pipeline, []).append(st)
        self.res_stack.append(res)
        try:
            self._result(res, cwd, outer, pipelines)
        finally:
            self.res_stack.pop()

    def _result(self, res, cwd, outer, pipelines):
        for st in res:
            here = outer | _argv_tokens(pipelines.get(st.pipeline, [st]))
            argv = _sh.expand_wrappers(st)
            if st.nested:
                # PGHOST=x bash -c 'psql ...': the nested commands inherit the stage's database variables
                env, overlay = self.ctx.db_env, self.db_prefix(st, argv)
                saved = {k: env[k] for k in overlay if k in env}
                env.update(overlay)
                try:
                    for sub in st.nested:
                        self.result(sub, cwd, here)
                finally:
                    for k in overlay:
                        if k in saved:
                            env[k] = saved[k]
                        else:
                            env.pop(k, None)
            off = len(st.argv) - len(argv) if argv else 0
            dyn = list(st.dynamic[off:]) if argv else []
            dyn += [False] * (len(argv or ()) - len(dyn))
            self.note_vars(st, argv, dyn)
            self.note_db_env(st, argv, dyn)
            self.stage(st, argv, dyn, cwd, pipelines.get(st.pipeline, [st]), outer)
            cwd = self.after_cd(argv, dyn, cwd)

    def note_vars(self, st, argv, dyn):
        for name, val in st.env.items():
            if not st.argv:  # x=$(cmd) on its own
                if val is None:
                    self.nonliteral.add(name)
                else:
                    self.nonliteral.discard(name)
        if not argv:
            return
        name = _sh.command_name(argv[0])
        if name in ("export", "declare", "typeset", "local", "readonly"):
            for a, d in zip(argv[1:], dyn[1:]):
                m = re.match(r"([A-Za-z_]\w*)=", a)
                if m and d:
                    self.nonliteral.add(m.group(1))
        if name in ("for", "select") and len(argv) >= 3 and argv[2] == "in":
            self.loopvars[argv[1]] = list(zip(argv[3:], dyn[3:]))
        elif name == "set" and argv[1:2] == ["--"]:
            vals = list(zip(argv[2:], dyn[2:]))
            self.loopvars["@"] = self.loopvars["*"] = vals
            for k, v in enumerate(vals[:9], start=1):
                self.loopvars[str(k)] = [v]
        elif name in ("read", "mapfile", "readarray"):
            for a in argv[1:]:
                if re.fullmatch(r"[A-Za-z_]\w*", a):
                    self.nonliteral.add(a)

    def db_unknown(self):
        """Every database variable may now hold anything (``source .env``,
        ``set -a``, ``export $(...)``, ``eval "$x"``)."""
        for n in _db.DB_ENV_NAMES:
            self.ctx.db_env[n] = None

    def note_db_env(self, st, argv, dyn=()):
        """``export PGHOST=x`` / ``PGHOST=x`` on its own: later commands in the
        line connect there (forge noteDbEnv). A value from a substitution, a
        sourced file or a dynamic export is unknown (None)."""
        env = self.ctx.db_env
        if st.argv and not argv and _sh.command_name(st.argv[0]) == "exec" and (
                st.heredocs or any(op in ("<", "<>", "<<<") for op, _t in st.redirects)):
            self.ctx.exec_stdin = True  # exec < x.sql; psql -h prod
        if not st.argv:
            for k, v in st.env.items():
                name = _db.db_env_name(k)
                if name:
                    env[name] = v
            return
        if not argv:
            return
        name = _sh.command_name(argv[0])
        args, dargs = argv[1:], list(dyn[1:]) + [False] * len(argv)
        if name in ("source", "."):
            self.db_unknown()
        elif name == "set" and any(re.fullmatch(r"-[a-zA-Z]*a[a-zA-Z]*", a) for a in args) or (
                name == "set" and "allexport" in args):
            self.db_unknown()
        elif name == "eval" and any(dargs[:len(args)]):
            self.db_unknown()
        elif name in ("read", "mapfile", "readarray"):
            for a in args:
                n = _db.db_env_name(a)
                if n:
                    env[n] = None
        elif name in ("export", "declare", "typeset", "local", "readonly"):
            for a, d in zip(args, dargs):
                m = re.match(r"([A-Za-z_]\w*)=(.*)\Z", a, re.S)
                if m:
                    n = _db.db_env_name(m.group(1))
                    if n:
                        env[n] = None if d else m.group(2)
                elif d:
                    self.db_unknown()  # export $(cat .env | xargs)
                else:
                    n = _db.db_env_name(a)
                    if n and n not in env:
                        env[n] = None  # export PGHOST: whatever it holds

    def db_prefix(self, st, argv):
        """The stage's own database variables: prefix assignments and the
        ones wrappers take (``env PGHOST=x psql``, ``sudo PGHOST=x psql``)."""
        out = {}
        for k, v in st.env.items():
            n = _db.db_env_name(k)
            if n:
                out[n] = v
        off = len(st.argv) - len(argv) if argv else len(st.argv)
        if st.argv[:1] and _sh.command_name(st.argv[0]) == "env" and any(
                a.startswith(("-S", "--split-string")) or re.fullmatch(r"-[a-zA-Z]*S.*", a) for a in st.argv[1:off or None]):
            out.update({x: None for x in _db.DB_ENV_NAMES})  # env -S '...': a command line we do not follow
        for w, d in zip(st.argv[:off], list(st.dynamic[:off]) + [False] * off):
            m = re.match(r"([A-Za-z_]\w*)=(.*)\Z", w, re.S)
            n = m and _db.db_env_name(m.group(1))
            if n:
                out[n] = None if d else m.group(2)
            elif d and not m:
                out.update({x: None for x in _db.DB_ENV_NAMES})  # env $(cat .env) psql
        return out

    def db_stdin(self, st, pipeline):
        """(stdin, feed) for a database client stage: ("literal", text) for a
        literal heredoc / here-string / echo-printf-cat feed, ("unknown",
        None) for a file, a non-literal feed or xargs input, else ("none",
        None)."""
        texts, unknown = [], False
        for op, t in st.redirects:
            if op in ("<", "<>", "<&"):
                unknown = True
            elif op == "<<<":
                if "$" in t or "`" in t:
                    unknown = True
                else:
                    texts.append(t)
        for _dl, quoted, body in st.heredocs:
            if quoted or ("$" not in body and "`" not in body):
                texts.append(body)
            else:
                unknown = True
        piped = st.index > 0 and not texts and not unknown
        if piped or "xargs" in st.wrappers or "parallel" in st.wrappers:
            prev = [x for x in pipeline if x.index == st.index - 1]
            text = None
            if prev:
                pargv = _sh.expand_wrappers(prev[0]) or []
                pdyn = prev[0].dynamic[len(prev[0].argv) - len(pargv):]
                text = _literal_output(prev[0], pargv, pdyn)
            if isinstance(text, str):
                texts.append(text)
            else:
                unknown = True
        if not texts and not unknown and (self.compound_input(st) or self.ctx.exec_stdin):
            unknown = True  # { psql; } < x.sql, exec < x.sql; psql
        if unknown:
            return "unknown", None
        if texts:
            return "literal", "\n".join(texts)
        return "none", None

    def db_check(self, st, argv, name, args, pipeline, cwd, prefix=None, depth=0):
        """sql-destroy / db-write for SQL and NoSQL clients and ORM CLIs;
        shell escapes inside a client are checked as commands."""
        ctx = self.ctx
        prefix = self.db_prefix(st, argv) if prefix is None else prefix
        inner = _env_runner(name, args) if depth < 3 else None
        if inner is not None:  # dotenv -e .env.prod -- psql ..., op run -- ..., direnv exec . ...
            if inner:
                unknown = {x: None for x in _db.DB_ENV_NAMES}
                self.db_check(st, inner, _sh.command_name(inner[0]), inner[1:], pipeline, cwd,
                              dict(prefix, **unknown), depth + 1)
            return
        if name in _db.DB_CLIENT_SPECS:
            stdin, feed = self.db_stdin(st, pipeline)
            piped = "|" in self.text.replace("||", "")
            f = _db.client_finding(name, args, prefix_env=prefix, line_env=ctx.db_env, stdin=stdin, feed=feed,
                                   patterns=ctx.db_dev, remote=ctx.remote, piped=piped)
            if f.destroy:
                ctx.hit("sql-destroy", False, self.raw)
            if f.write:
                ctx.hit("db-write", False, self.raw)
            for cmd in f.shell:  # psql \! / \o |cmd / COPY ... PROGRAM, sqlite .shell, mysql system ...
                self.nested_eval(cmd, cwd)
                if ctx.evals > _MAX_EVALS or ctx.depth >= _MAX_DEPTH:
                    break  # unparseable already recorded once
            if f.uncertain:  # psql \\! with no command, sqlite .once -x, an unknown dot-command ...
                ctx.hit("uncertain-command", False, self.raw)
            for path in f.outputs:  # psql -o / \\o FILE, sqlite .output, mysql tee, sqlcmd :out ...
                self.check_write(path, "$" in path or "`" in path, "redirect", cwd)
        if _db.cli_write(name, args, ctx.db_dev):
            ctx.hit("db-write", False, self.raw)

    def bind(self, targets):
        """Replace "$d" bound by `for d in ...` / `set -- ...` with its words."""
        out = []
        for w, d in targets:
            m = re.fullmatch(r"\$(?:\{([A-Za-z_]\w*|[@*1-9])\}|([A-Za-z_]\w*|[@*1-9]))", w) if d else None
            name = m and (m.group(1) or m.group(2))
            if name and name in self.loopvars:
                out += self.loopvars[name]
            else:
                out.append((w, d))
        return out

    def after_cd(self, argv, dyn, cwd):
        if not argv or _sh.command_name(argv[0]) not in ("cd", "pushd", "chdir"):
            return cwd
        args = [(a, d) for a, d in zip(argv[1:], dyn[1:]) if not (a.startswith("-") and len(a) > 1)]
        if not args:
            return self.ctx.home
        word, d = args[0]
        if word == "-":
            return None
        return self.ctx.path(word, cwd, d)

    # -- files a stage writes ---------------------------------------------------
    def check_write(self, word, d, kind, cwd, content=None, src=None):
        """One written path: self-protect, git-internals, agent-config,
        secret-files; records literal content for write-then-run."""
        ctx, raw = self.ctx, self.raw
        if ctx.guard_var_unresolved(word, cwd):
            ctx.hit("self-protect", True, raw)
        e = ctx.expand(word, cwd)
        if e and ctx.glob_hits_protected(_norm(e)):
            ctx.hit("self-protect", True, raw)
        if kind in ("redirect", "tee", "edit", "copy", "link") and not d and is_secret_name(word):
            if not (kind == "copy" and src is not None and _is_template(src)):
                ctx.hit("secret-files", False, raw)
        p = ctx.path(word, cwd, d, follow=kind != "move-src" or word.endswith(("/", "/.")))
        if p is None:
            return None
        if kind in ("copy", "link", "copydir") and ctx.is_protected_dest(p):
            ctx.hit("self-protect", True, raw)
        if ctx.is_protected(p, as_ancestor=kind in ("move-src", "chmod-r")):
            ctx.hit("self-protect", True, raw)
        if kind in ("copy", "link") and d is False and is_secret_name(p) and not is_secret_name(word):
            ctx.hit("secret-files", False, raw)
        if kind == "move-src":
            if _is_home_or_above(ctx, p):
                ctx.hit("rm-rf-outside", True, raw)
            self.written.pop(p, None)
            return p
        if kind == "copydir":
            if _git_internal_kind(p) == "hooks" or _git_internal_kind(p + "/x") == "hooks":
                ctx.hit("git-internals", False, raw)
            if _agent_config(p + "/settings.json"):
                ctx.hit("agent-config", False, raw)
            return p
        gk = _git_internal_kind(p)
        if gk and (gk != "gitmodules" or not isinstance(content, str) or _GITMODULES_EXEC.search(content)):
            ctx.hit("git-internals", False, raw)
        if _agent_config(p) or (kind in ("copy", "link") and posixpath.basename(p).lower() == ".claude"):
            ctx.hit("agent-config", False, raw)
        if kind in ("copy", "link") and posixpath.basename(p).lower() == ".git":
            ctx.hit("git-internals", False, raw)
        if kind in ("chmod", "chmod-r", "touch"):
            return p
        self.written[p] = content
        return p

    def writes(self, st, argv, dyn, cwd, pipeline):
        for op, t in st.redirects:
            if op in _WRITE_OPS and t and not t.isdigit() and t != "-":
                d = "$" in t or "`" in t
                content = _literal_output(st, argv, dyn) if argv else None
                append = op == ">>"
                p = self.ctx.path(t, cwd, d)
                old = self.written.get(p) if p else None
                had = p in self.written if p else False
                p = self.check_write(t, d, "redirect", cwd, content)
                if p and append and had:
                    self.written[p] = old + "\n" + content if isinstance(old, str) and isinstance(content, str) \
                        else None
        if not argv:
            return
        name, args, dargs = _sh.command_name(argv[0]), argv[1:], dyn[1:]
        pos = [(a, d) for a, d in zip(args, dargs) if not a.startswith("-")]
        if name == "tee":
            content = _literal_output(st, argv, dyn)
            prev = [x for x in pipeline if x.index == st.index - 1]
            if content is None and prev:
                pargv = _sh.expand_wrappers(prev[0]) or []
                pdyn = prev[0].dynamic[len(prev[0].argv) - len(pargv):]
                content = _literal_output(prev[0], pargv, pdyn)
            for a, d in pos:
                self.check_write(a, d, "tee", cwd, content)
        elif name in ("cp", "mv", "install", "ln", "rsync", "scp"):
            self.copy_like(name, args, dargs, cwd)
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
            for a, d in (files if script_given else files[1:]):
                self.check_write(a, d, "edit", cwd)
        elif name == "perl" and any(re.fullmatch(r"-[a-zA-Z]*i.*", a) for a in args):
            code = {c for c, _d in _short_opt_value(args, dargs, "eE")}
            for a, d in pos:
                if a not in code:
                    self.check_write(a, d, "edit", cwd)
        elif name == "dd":
            for a, d in zip(args, dargs):
                if a.startswith("of="):
                    self.check_write(a[3:], d, "copy", cwd)
        elif name == "truncate":
            for a, d in pos:
                if not re.fullmatch(r"[+\-<>/%]?\d+[KMGTPEZY]?i?B?", a):
                    self.check_write(a, d, "edit", cwd)
        elif name == "touch":
            for a, d in pos:
                self.check_write(a, d, "touch", cwd)
        elif name in ("chmod", "chown", "chgrp", "chattr"):
            recursive = any(re.fullmatch(r"-[a-zA-Z]*R[a-zA-Z]*", a) or "--recursive" in _long(a, _CHMOD_LONG)
                            for a in args)
            for a, d in pos[1:]:
                self.check_write(a, d, "chmod-r" if recursive else "chmod", cwd)
        elif name in ("curl", "wget"):
            for a, d in _downloads(name, args, dargs):
                self.check_write(a, d, "fetch", cwd, _FETCHED)

    def copy_like(self, name, args, dargs, cwd):
        ctx, raw = self.ctx, self.raw
        dest, is_dir, srcs, flags = _copy_parts(name, args, dargs)
        link = name == "ln" or (name == "cp" and flags & {"-s", "-l"})
        if link:  # a link to a protected path, or to home, gives write access to it
            for s, d in srcs:
                base = cwd
                if (name == "ln" and ("-s" in flags or "--symbolic" in flags)) or "-s" in flags:
                    if dest and not dest[1] and is_dir is not True:
                        base = posixpath.dirname(ctx.expand(dest[0], cwd) or cwd or "/") or cwd
                    elif dest and not dest[1]:
                        base = ctx.expand(dest[0], cwd) or cwd
                p = ctx.path(s, base, d)
                if (p and (ctx.is_protected(p, as_ancestor=True) or ctx.is_protected_dest(p)
                           or p == ctx.home)) or (d and ctx.guard_var_unknown(s)):
                    ctx.hit("self-protect", True, raw)
        if name == "mv":
            for s, d in srcs:
                self.check_write(s, d, "move-src", cwd)
        if name == "rsync" and "--delete" in flags and dest:
            _classify(ctx, [(dest[0] if dest[0].endswith("/") else dest[0] + "/", dest[1])], cwd, raw)
        if name == "install" and _mode_option(args) and _mode_wide(_mode_option(args)):
            ctx.hit("chmod-wide", False, raw)
        if dest is None:
            return
        dword, dd = dest
        dpath = ctx.path(dword, cwd, dd)
        if dd and ctx.guard_var_unknown(dword):
            ctx.hit("self-protect", True, raw)
        if dpath is None:
            return
        if is_dir is None:
            is_dir = False
        elif dword in (".", "..") or dword.endswith(("/.", "/..")):
            is_dir = True
        elif is_dir is False:
            is_dir = os.path.isdir(dpath) or dpath in ctx.glob_dirs or ctx.is_protected(dpath) and \
                os.path.isdir(dpath) or _git_internal_kind(dpath) == "hooks" or dpath.endswith("/.claude") or \
                dpath.lower().endswith(tuple("/.claude/" + d for d in _AGENT_DIRS))
        kind = "link" if link else "copy"
        if not is_dir:
            src = srcs[0][0] if srcs else None
            self.check_write(dword, dd, kind, cwd, None, src=src)
            return
        self.check_write(dword, dd, "copydir", cwd)
        for s, d in srcs:
            base = posixpath.basename(s.rstrip("/"))
            if name == "rsync" and s.endswith("/"):
                continue  # contents of s: names unknown, judged by the folder (copydir)
            if d or not base or base in (".", ".."):
                continue
            self.check_write(posixpath.join(dword, base), False, kind, cwd, None, src=s)

    # -- one stage -----------------------------------------------------------
    def stage(self, st, argv, dyn, cwd, pipeline, outer=frozenset()):
        ctx, raw = self.ctx, self.raw
        name = _sh.command_name(argv[0]) if argv else ""
        args, dargs = (argv[1:], dyn[1:]) if argv else ([], [])

        # writes: secret-files, git-internals, agent-config, self-protect, write-then-run
        self.writes(st, argv, dyn, cwd, pipeline)

        # env -S 'python3 -u' fed by a download
        if st.argv[:1] and _sh.command_name(st.argv[0]) == "env" and \
                any(a.startswith(("-S", "--split-string")) or re.fullmatch(r"-[a-zA-Z]*S.*", a) for a in st.argv):
            for res in st.nested:
                for s2 in res:
                    a2 = _sh.expand_wrappers(s2) or []
                    if a2 and _reads_stdin(_sh.command_name(a2[0]), a2[1:]) and _fetch_before(st, pipeline):
                        ctx.hit("pipe-to-shell", False, raw)

        if not argv:
            return

        # database rules (sql-destroy, db-write)
        self.db_check(st, argv, name, args, pipeline, cwd)

        # a protected path (or an unknown guard variable) in the arguments of a non-reader
        if name not in _READERS or name in ("cd", "pushd"):
            for a, d in zip(args, dargs):
                for w in ([a] + ([a.split("=", 1)[1]] if "=" in a else [])):
                    if ctx.guard_var_unresolved(w, cwd):
                        ctx.hit("self-protect", True, raw)
                    if name in ("cd", "pushd"):
                        continue
                    p = ctx.path(w, cwd, d) if ("/" in w or w.startswith(("~", "$"))) else None
                    e = ctx.expand(w, cwd) if "/" in w else None
                    if (p and ctx.is_protected_dest(p)) or (e and ctx.glob_hits_protected(_norm(e))):
                        ctx.hit("self-protect", True, raw)

        # R2: a writer whose arguments (substitutions) or xargs input come from an
        # environment dump, or from ls/find of protected paths
        if name not in _READERS:
            for res in st.nested:
                self.producer_check(res, cwd)
            if "xargs" in st.wrappers or "parallel" in st.wrappers:
                self.producer_check([x for x in pipeline if x.index < st.index], cwd)

        # recursive deletes
        if name in _DELETERS:
            recursive, targets = _rm_targets(argv, dyn)
            targets = self.bind(targets)
            if "xargs" in st.wrappers or "parallel" in st.wrappers:
                targets.append(("$XARGS", True))
            if name == "rimraf" or (name == "rm" and recursive):
                if targets:
                    _classify(ctx, targets, cwd, raw)
            else:
                for word, d in targets:
                    for path, _g in _target_kinds(ctx, word, d, cwd) or ():
                        if ctx.is_protected(path):
                            ctx.hit("self-protect", True, raw)
        rim = _rimraf_targets(name, args)
        if rim is not None and name != "rimraf":
            _classify(ctx, self.bind([(a, False) for a in rim if not a.startswith("-")]), cwd, raw)
        if name == "find":
            _find_check(ctx, argv, dyn, cwd, raw)
        if name in ("fd", "fdfind"):
            _fd_check(ctx, argv, dyn, cwd, raw, self)
        if name == "git":
            _git_check(ctx, argv, dyn, cwd, raw, self)
        if name == "chmod" and _chmod_wide(args):
            ctx.hit("chmod-wide", False, raw)
        if name == "mkdir" and _mode_option(args) and _mode_wide(_mode_option(args)):
            ctx.hit("chmod-wide", False, raw)

        # pipe-to-shell: a download earlier in the same pipeline feeds a shell/interpreter
        self.pipe_to_shell(st, argv, dyn, name, args, pipeline)

        # interpreter one-liners
        kind = _interp_kind(name)
        if kind:
            for code, d in _inline_code(kind, args, dargs):
                self.check_code(kind, code, d)
        stdin_kind = "python" if name == "uv" else (kind if kind not in ("awk", "vim") else None)
        if stdin_kind and _reads_stdin(name, args):
            # R1: code given on stdin - a heredoc, a here-string, or literal text piped in
            for _dl, _q, body in st.heredocs:
                self.check_code(stdin_kind, body)
            for op, t in st.redirects:
                if op == "<<<":
                    self.check_code(stdin_kind, t, "$" in t or "`" in t)
            prev = [x for x in pipeline if x.index == st.index - 1]
            if prev:
                pargv = _sh.expand_wrappers(prev[0]) or []
                pdyn = prev[0].dynamic[len(prev[0].argv) - len(pargv):]
                text = _literal_output(prev[0], pargv, pdyn)
                if isinstance(text, str):
                    self.check_code(stdin_kind, text)
            for op, t in st.redirects:
                if op == "<" and t.startswith("<("):  # python3 < <(echo ..)
                    self.procsub_stdin(stdin_kind, t, cwd)
            if not st.heredocs and not any(op in ("<", "<<<", "<>") for op, _t in st.redirects):
                # Task 3 carry: stdin inherited from a compound command
                # ({ python3; } <<E, if ..; then python3; fi <<E, ...; done <<E):
                # the heredoc / here-string sits on the closing word's stage
                self.compound_stdin(st, stdin_kind, cwd)
        elif _sh.shell_name(name) and _reads_stdin(name, args) and not st.heredocs and not any(
                op in ("<", "<<<", "<>") for op, _t in st.redirects):
            # { bash; } <<'E' rm -rf ~ E: the body runs as commands (hard rules stay hard)
            self.compound_stdin(st, "shell", cwd)
        if name == "sed":
            scripts = [c for c, _d in _short_opt_value(args, dargs, "e", ("--expression",))]
            if not scripts:
                pos = [a for a in args if not a.startswith("-")]
                scripts = pos[:1]
            if any(_SED_EXEC.search(s) for s in scripts):
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

        # a command word from a command substitution, or a variable assigned from one
        cw = argv[0]
        allowed = False
        if dyn[0]:
            m = _CAT_SUB.match(cw)
            ref = re.fullmatch(r"\$\{?([A-Za-z_]\w*)\}?", cw)
            repl = _allowed_sub(ctx, cw)
            if repl is None and ref and ref.group(1) in self.subst_vars:
                repl = _allowed_sub(ctx, self.subst_vars[ref.group(1)])
            if repl is not None:  # $(which X) args ...: checked as `X args ...`
                allowed = True
                self.nested_eval(" ".join(_quote(w) for w in [repl] + args), cwd)
            elif m:
                p = ctx.path(m.group(1) or m.group(2), cwd)
                if p in self.written and isinstance(self.written[p], str):
                    self.nested_eval(self.written[p], cwd)
                else:
                    ctx.hit("uncertain-command", False, raw)
            elif "$(" in cw or "`" in cw or (ref and ref.group(1) in self.nonliteral):
                ctx.hit("uncertain-command", False, raw)

        # write-then-run
        self.write_then_run(st, argv, dyn, name, args, cwd, kind, pipeline)

        # uncertainty near a dangerous verb
        if allowed and all(r.startswith("command word is not a literal") for r in st.reasons):
            pass
        elif st.uncertain:
            toks = _stage_tokens(pipeline) | outer
            first_unknown = bool(st.dynamic[:1] and st.dynamic[0]) or bool(dyn[:1] and dyn[0])
            if _dangerous(toks) or (first_unknown and any(_DANGEROUS_ARGS.fullmatch(a) for a in argv[1:])):
                ctx.hit("uncertain-command", False, raw)

    def closers(self, st):
        """The closing stages of the compound commands around ``st`` (only
        the groups that contain it: Stage.depth falls below ``st``'s level),
        innermost first. Input redirected onto them reaches ``st``."""
        res = self.res_stack[-1] if self.res_stack else []
        floor = st.depth
        after = False
        for s2 in res:
            if s2 is st:
                after = True
                continue
            if not after or s2.low >= floor:
                continue
            a2 = _sh.expand_wrappers(s2)
            if s2.depth > s2.low or (a2 and _sh.command_name(a2[0]) not in _COMPOUND_CLOSERS):
                break  # st's group closed earlier (and maybe another one opened): nothing reaches st
            yield s2
            floor = s2.low
            if floor <= 0:
                break

    def compound_stdin(self, st, kind, cwd):
        """Input redirected onto the compound commands around ``st``."""
        for s2 in self.closers(st):
            self.feed(kind, s2, cwd)

    def compound_input(self, st):
        """Some stdin redirect sits on a compound command around ``st``."""
        return any(s2.heredocs or any(op in ("<", "<<<", "<>", "<&") for op, _t in s2.redirects)
                   for s2 in self.closers(st))

    def feed(self, kind, s2, cwd):
        """Check the stdin text a closer stage redirects in, as ``kind``
        (an interpreter's code, or "shell": commands)."""
        for _dl, _q, body in s2.heredocs:
            self.code_or_commands(kind, body, cwd)
        for op, t in s2.redirects:
            if op == "<<<":
                self.code_or_commands(kind, t, cwd, "$" in t or "`" in t)
            elif op == "<" and t.startswith("<("):
                self.procsub_stdin(kind, t, cwd)

    def code_or_commands(self, kind, text, cwd, dynamic=False):
        if kind == "shell":
            if dynamic:
                self.ctx.hit("uncertain-command", False, self.raw)
            self.nested_eval(text, cwd)
        else:
            self.check_code(kind, text, dynamic)

    def procsub_stdin(self, kind, target, cwd):
        """``< <(cmd)`` into an interpreter or shell: the literal text cmd
        prints is checked, a download is pipe-to-shell, anything else is
        uncertain-command."""
        inner = target[2:-1] if target.endswith(")") else target[2:]
        res = _sh.parse(inner)
        text = None
        if len(res) == 1:
            a = _sh.expand_wrappers(res[0]) or []
            d = res[0].dynamic[len(res[0].argv) - len(a):]
            text = _literal_output(res[0], a, d)
        if text is _FETCHED:
            self.ctx.hit("pipe-to-shell", False, self.raw)
        elif isinstance(text, str):
            self.code_or_commands(kind, text, cwd)
        else:
            self.ctx.hit("uncertain-command", False, self.raw)

    def pipe_to_shell(self, st, argv, dyn, name, args, pipeline):
        ctx, raw = self.ctx, self.raw
        if argv and dyn[0] and _FETCH_SUB.search(argv[0]):
            ctx.hit("pipe-to-shell", False, raw)  # $(curl ...) as the command
        runner = _sh.shell_name(name) or name in ("source", ".", "eval") or _interp_kind(name) in (
            "python", "perl", "ruby", "node", "php")
        if runner:
            script = None
            if _sh.shell_name(name):
                mode, path = _sh.shell_mode(args)
                script = path if mode in ("c", "file") else None
            words = [script] if script else args
            if any(w and _FETCH_SUB.search(w) for w in words):
                ctx.hit("pipe-to-shell", False, raw)
        if _reads_stdin(name, args) and _fetch_before(st, pipeline):
            ctx.hit("pipe-to-shell", False, raw)

    def tracked(self, word, cwd):
        p = self.ctx.path(word, cwd)
        return (p in self.written, self.written.get(p)) if p else (False, None)

    def producer_check(self, stages, cwd):
        ctx, raw = self.ctx, self.raw
        for s2 in _sh.walk(stages):
            if _env_dump(s2):
                ctx.hit("self-protect", True, raw)
                return
            a2 = _sh.expand_wrappers(s2) or []
            if not a2:
                continue
            n2, args2 = _sh.command_name(a2[0]), a2[1:]
            if n2 == "find":
                i = 0
                while i < len(args2) and args2[i] in ("-H", "-L", "-P"):
                    i += 1
                roots = []
                while i < len(args2) and not (args2[i].startswith("-") or args2[i] in ("(", "!")):
                    roots.append(args2[i])
                    i += 1
                expr = args2[i:]
                names = [expr[k + 1] for k, a in enumerate(expr) if a in ("-name", "-iname") and k + 1 < len(expr)]
                only = names and not any(a in _FIND_NARROW and a not in ("-name", "-iname") for a in expr)
                for r in roots or ["."]:
                    p = ctx.path(r, cwd)
                    if p:
                        _protect_find(ctx, p, names if only else None, raw)
            elif n2 in ("ls", "realpath", "readlink", "stat", "dirname", "basename", "printf", "echo"):
                for w in args2:
                    if w.startswith("-"):
                        continue
                    e = ctx.expand(w, cwd)
                    if e and (ctx.glob_hits_protected(_norm(e)) or ctx.is_protected_dest(_real(e))):
                        ctx.hit("self-protect", True, raw)
                        return

    def check_code(self, kind, code, dynamic=False):
        """The same checks for every code text an interpreter receives: inline
        (-c/-e), heredoc, here-string, piped literal text, a file written and
        run in the same line."""
        ctx, raw = self.ctx, self.raw
        if dynamic or _code_runs_shell(kind, code):
            ctx.hit("uncertain-command", False, raw)
        low = code.lower()
        if "co_hook" in code or _GUARD_NAME.search(code) or any(m in low for m in ctx.mentions):
            ctx.hit("self-protect", True, raw)
        if _AGENT_CODE.search(code) and _WRITES_TEXT.search(code):
            ctx.hit("agent-config", False, raw)

    def run_text(self, content, cwd, kind=None):
        if content is _FETCHED:
            self.ctx.hit("pipe-to-shell", False, self.raw)
        elif content is None:
            self.ctx.hit("uncertain-command", False, self.raw)
        elif kind in (None, "shell"):
            self.nested_eval(content, cwd)
        else:
            self.check_code(kind, content)

    def write_then_run(self, st, argv, dyn, name, args, cwd, kind, pipeline):
        if not self.written:
            return
        target, as_kind = None, "shell"
        if _sh.shell_name(name):
            mode, path = _sh.shell_mode(args)
            if mode == "file":
                target = path
            elif mode == "c" and path and _CAT_SUB.match(path):
                m = _CAT_SUB.match(path)
                target = m.group(1) or m.group(2)
        elif name in ("source", "."):
            a = args[1:] if args[:1] == ["--"] else args
            target = a[0] if a else None
        elif name == "eval" and len(args) == 1 and _CAT_SUB.match(args[0]):
            m = _CAT_SUB.match(args[0])
            target = m.group(1) or m.group(2)
        elif kind in ("python", "perl", "ruby", "node", "php"):
            script = _script_arg(name, args)
            if script and script not in _STDIN_PATHS:
                target, as_kind = script, kind
        elif name in ("make", "gmake"):
            mf = [args[i + 1] for i, a in enumerate(args[:-1]) if a in ("-f", "--file", "--makefile")]
            for f in mf or ["GNUmakefile", "makefile", "Makefile"]:
                found, content = self.tracked(f, cwd)
                if found:
                    if isinstance(content, str):
                        recipe = "\n".join(line[1:] for line in content.split("\n") if line.startswith("\t"))
                        self.nested_eval(recipe, cwd)
                    else:
                        self.run_text(content, cwd)
                    break
            return
        elif "/" in argv[0] and not dyn[0]:
            target = argv[0]
        # a stdin-reading runner fed from a written file: sh < a.sh, cat a.sh | bash
        if target is None and _reads_stdin(name, args):
            for op, t in st.redirects:
                if op == "<":
                    target = t
            prev = [x for x in pipeline if x.index == st.index - 1]
            if target is None and prev:
                pargv = _sh.expand_wrappers(prev[0]) or []
                if pargv[:1] and _sh.command_name(pargv[0]) == "cat" and len(pargv) == 2:
                    target = pargv[1]
            if kind in ("python", "perl", "ruby", "node", "php"):
                as_kind = kind
        if not target:
            return
        found, content = self.tracked(target, cwd)
        if found:
            self.run_text(content, cwd, as_kind)

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
            sub.loopvars = dict(self.loopvars)
            sub.nonliteral = set(self.nonliteral)
            sub.run(text, cwd)
        finally:
            ctx.remote = was
            ctx.depth -= 1


# ---------------------------------------------------------------------------
# Interpreters
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
    if name == "perl" or re.fullmatch(r"perl[\d.]+", name):
        return "perl"
    if name == "ruby" or re.fullmatch(r"ruby[\d.]+", name):
        return "ruby"
    if name in _NODE:
        return "node"
    if name == "php" or re.fullmatch(r"php[\d.]+", name):
        return "php"
    if name in _AWK:
        return "awk"
    if name in _VIM:
        return "vim"
    return None


# per interpreter: (options that take a value, options that give inline code / a module)
_INTERP_OPTS = {
    "python": ({"-W", "-X", "--check-hash-based-pycs"}, {"-c", "-m"}),
    "perl": ({"-I", "-M", "-m", "-x", "-C", "-0", "-l", "-F", "-d", "-D"}, {"-e", "-E"}),
    "ruby": ({"-r", "-I", "-C", "-E", "-K", "-F", "-T", "-x", "--encoding", "--external-encoding",
              "--internal-encoding", "--enable", "--disable"}, {"-e"}),
    "node": ({"-r", "--require", "--import", "--loader", "--experimental-loader", "-C", "--conditions",
              "--title", "--env-file", "--inspect-port", "--input-type", "--stack-size"},
             {"-e", "--eval", "-p", "--print"}),
    "php": ({"-c", "-d", "-z", "-B", "-R", "-E", "-F", "-t"}, {"-r", "-f"}),
}


def _script_arg(name, args):
    """The script argument an interpreter runs ('' = stdin), or None when it
    runs inline code / a module / a subcommand."""
    kind = _interp_kind(name)
    if name in ("deno", "bun"):
        pos = [a for a in args if not a.startswith("-") or a == "-"]
        if not pos:
            return "" if name == "deno" else None
        if pos[0] in ("run", "x"):
            return pos[1] if len(pos) > 1 else ("" if name == "deno" else None)
        if pos[0] in ("eval", "-e"):
            return None
        return pos[0]
    values, code = _INTERP_OPTS.get(kind, (set(), set()))
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            return args[i + 1] if i + 1 < len(args) else ""
        if a == "-" or not a.startswith("-"):
            return a
        key = a.split("=", 1)[0]
        if key in code or (kind in ("python", "perl", "ruby") and not a.startswith("--")
                           and any("-" + c in code for c in a[1:])):
            if kind == "python" and not a.startswith("--"):
                for c in a[1:]:
                    if "-" + c in code:
                        return None
                    if "-" + c in values:
                        break
            else:
                return None
        if a in values and "=" not in a:
            i += 2
            continue
        i += 1
    return ""


def _reads_stdin(name, args):
    """The stage reads its program from stdin (shell, interpreter, source)."""
    if _sh.shell_name(name):
        return _sh.shell_mode(args)[0] == "stdin"
    if name in ("source", "."):
        a = args[1:] if args[:1] == ["--"] else args
        return bool(a) and a[0] in _STDIN_PATHS
    if _interp_kind(name) in ("python", "perl", "ruby", "node", "php"):
        s = _script_arg(name, args)
        return s is not None and (s == "" or s in _STDIN_PATHS)
    if name in _OTHER_INTERP or re.fullmatch(r"lua[\d.]*|luajit", name):
        pos = [a for a in args if not a.startswith("-") or a == "-"]
        return not pos or pos[0] in _STDIN_PATHS or "-" in args
    if name == "uv":
        return args[:1] == ["run"] and any(a in _STDIN_PATHS for a in args[1:])
    return False


_DOTENV = frozenset({"dotenv", "dotenvx", "dotenv-cli", "env-cmd", "with-env"})
_RUN_SECRETS = frozenset({"op", "infisical", "doppler", "dotenvx", "varlock", "teller", "chamber", "aws-vault"})
_JS_RUNNERS = frozenset({"npx", "pnpx", "bunx", "pnpm", "yarn", "npm", "bun"})
_ENV_CMD_VALUE = frozenset({"-f", "--file", "-e", "--environments", "-r", "--rc-file", "-c", "--path", "-v"})


def _env_runner(name, args):
    """The command a program that loads an environment runs (its argv), []
    when it names none we can see, None when ``name`` is no such program."""
    args = [str(a) for a in args]
    if name in _JS_RUNNERS:  # npx dotenv -e x -- psql
        for k, a in enumerate(args):
            if not a.startswith("-") and a not in ("exec", "dlx", "x", "run"):
                base = _sh.command_name(re.sub(r"@[^/@]*$", "", a))
                return _env_runner(base, args[k + 1:]) if base in _DOTENV | {"dotenv-cli"} else None
        return None
    if name in _RUN_SECRETS and args[:1] in (["run"], ["exec"]) or name in _DOTENV:
        if "--" in args:
            return args[args.index("--") + 1:]
        if name in ("env-cmd", "with-env", "aws-vault", "chamber"):
            k = 1 if name in ("aws-vault", "chamber") and args[:1] == ["exec"] else 0
            while k < len(args) and args[k].startswith("-"):
                k += 2 if args[k] in _ENV_CMD_VALUE else 1
            return args[k:]
        return []
    if name == "direnv" and args[:1] == ["exec"]:
        return args[2:]
    return None
_COMPOUND_CLOSERS = frozenset({"}", "fi", "done", "esac", ")"})

_OTHER_INTERP = frozenset({"pwsh", "powershell", "rscript", "tclsh", "wish", "osascript"})


def _fetch_before(st, pipeline):
    for p in pipeline:
        if p.index < st.index:
            pargv = _sh.expand_wrappers(p) or []
            if pargv and _sh.command_name(pargv[0]) in _FETCH:
                return True
    return False


_ENVIRON = re.compile(r"/proc/[^/\s]+/(task/[^/\s]+/)?environ")


def _env_dump(st):
    """env / printenv with no names, export -p, declare|typeset -p, bare set,
    or /proc/*/environ: the whole environment, guard paths included."""
    if any(_ENVIRON.search(w) for w in st.argv) or any(_ENVIRON.search(t) for _op, t in st.redirects):
        return True
    if not st.argv:
        return False
    name, args = _sh.command_name(st.argv[0]), st.argv[1:]
    if name == "printenv":
        return not [a for a in args if not a.startswith("-")]
    if name == "env":
        return _sh.expand_wrappers(st) is None and not any(a.startswith(("-S", "--split-string")) for a in args)
    if name in ("export", "declare", "typeset", "readonly"):
        return any(re.fullmatch(r"-[a-zA-Z]*p[a-zA-Z]*", a) for a in args)
    if name in ("set", "compgen"):
        return not args or name == "compgen" and "-e" in args
    return False


_PURE_SINK = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr"})


def _pure_reader(st):
    """Reads only: a reader command (or an assignment), no file writes."""
    if any(op in _WRITE_OPS and t not in _PURE_SINK and not t.isdigit() for op, t in st.redirects):
        return False
    argv = _sh.expand_wrappers(st)
    if not argv:
        return not st.argv or _sh.command_name(st.argv[0]) in ("env", "printenv", "export") and \
            all("=" not in a for a in st.argv[1:])
    name = _sh.command_name(argv[0])
    return (name in _READERS or name in ("printenv", "env", "set")) and \
        not (st.wrappers and set(st.wrappers) & {"xargs", "parallel", "sudo", "doas"})


def _names_guard_var(res):
    """A guard variable NAME appears as a command-line word (not prose data):
    in a word without spaces, a word that still holds $..., a redirect
    target or an assignment value, at any depth."""
    for st in _sh.walk(res):
        for w, d in zip(st.argv, st.dynamic):
            if _GUARD_NAME.search(w) and (d or not re.search(r"\s", w)):
                return True
        for _op, t in st.redirects:
            if _GUARD_NAME.search(t):
                return True
        for v in st.env.values():
            if v and _GUARD_NAME.search(v):
                return True
    return False


_SUBST_ASSIGN = re.compile(r"(?:^|[;&|\n(]|\bexport)\s*([A-Za-z_]\w*)=([\"']?)(\$\([^()`]*\))\2(?=[\s;&|)]|$)")


def _subst_assignments(text):
    """{name: "$(...)"} for variables assigned exactly once, from one substitution."""
    out = {}
    for m in _SUBST_ASSIGN.finditer(text):
        name = m.group(1)
        if len(re.findall(r"(?<![\w$])" + re.escape(name) + r"\+?=", text)) == 1:
            out[name] = m.group(3)
    return out


_TOOL_NAME = r"[A-Za-z0-9._+-]+"


def _allowed_sub(ctx, word):
    """The narrow $(which X) allowance: the command name or path a well-known
    lookup substitution stands for, or None."""
    m = re.fullmatch(r"\$\(([^()`$]*)\)(/[^$`]*)?", word)
    if not m:
        return None
    res = _sh.parse(m.group(1))
    if len(res) != 1 or res.uncertain or res[0].nested or res[0].redirects or any(res[0].dynamic):
        return None
    a, rest = res[0].argv, m.group(2) or ""
    if not rest:
        if len(a) == 2 and a[0] == "which" or len(a) == 3 and a[:2] in (["command", "-v"], ["type", "-P"],
                                                                         ["pyenv", "which"]):
            return a[-1] if re.fullmatch(_TOOL_NAME, a[-1]) else None
        return None
    if a == ["git", "rev-parse", "--show-toplevel"]:
        target = _norm(ctx.project + rest)
        return target if _inside(target.lower(), ctx.project.lower()) else None
    tail = None
    if a == ["npm", "bin"]:
        tail = re.fullmatch(r"/(" + _TOOL_NAME + ")", rest)
    elif a == ["npm", "root"]:
        tail = re.fullmatch(r"/\.bin/(" + _TOOL_NAME + ")", rest)
    elif a[:2] == ["go", "env"] and a[2:] in (["GOPATH"], ["GOROOT"]) or a[:2] == ["brew", "--prefix"] and \
            len(a) <= 3 or a in (["poetry", "env", "info", "-p"], ["poetry", "env", "info", "--path"]):
        tail = re.fullmatch(r"/bin/(" + _TOOL_NAME + ")", rest)
    return tail.group(1) if tail else None


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
# Uncertainty helpers
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
    return "find" in tokens and bool(tokens & _FIND_ACTIONS)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
_MCP_RANK = {"auto_approve": 0, "require_approval": 1, "block": 2}


def _mcp_action(ctx, tool_name):
    """Most restrictive action among the matching entries; an unknown action
    or a malformed table means require_approval; no entry means allow."""
    tools = ctx.mcp_tools
    if tools is None:
        return None
    if not isinstance(tools, dict):
        return "require_approval"
    name = tool_name.strip().lower()
    found = []
    for pat, action in tools.items():
        if not isinstance(pat, str):
            continue
        p = pat.strip().lower()
        if p == name or (_GLOB.search(p) and fnmatch.fnmatchcase(name, p)):
            act = action.strip().lower() if isinstance(action, str) else None
            found.append(act if act in _MCP_RANK else "require_approval")
    if not found:
        return None
    return max(found, key=_MCP_RANK.get)


def _file_tool(ctx, tool_name, tool_input, cwd):
    raw = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    raw = str(raw)
    if not raw:
        return
    p = ctx.path(raw, cwd or ctx.project, False)
    if ctx.is_protected(p) or (ctx.guard_var_unknown(raw)):
        ctx.hit("self-protect", True, raw)
    if is_secret_name(raw) or (p and is_secret_name(p)):
        ctx.hit("secret-files", False, raw)
    if p and _agent_config(p):
        ctx.hit("agent-config", False, raw)
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
    elif tool_name in _BASH_TOOLS:
        command = tool_input.get("command")
        command = command if isinstance(command, str) else ("" if command is None else str(command))
        _Bash(ctx, command).run(command, start)
    elif tool_name in _UNSUPPORTED_SHELLS:
        ctx.hit("unsupported-shell", True, f"{tool_name}: {_short(str(tool_input.get('command', '')))}")
    elif tool_name in _FILE_TOOLS:
        _file_tool(ctx, tool_name, tool_input, start)
    elif tool_name in SAFE_TOOLS:
        pass
    elif tool_name.strip().lower().startswith("mcp__"):
        action = _mcp_action(ctx, tool_name)
        try:
            text = json.dumps(tool_input, sort_keys=True, default=str)
        except (TypeError, ValueError, RecursionError):
            text = "<input>"
        summary = f"{tool_name} {text}"
        if action == "block":
            ctx.hit("mcp-block", True, summary)
        else:
            if action == "require_approval":
                ctx.hit("mcp-require-approval", False, summary)
            f = _db.mcp_finding(tool_name, tool_input, ctx.mcp_servers, ctx.db_dev)
            if f.destroy:
                ctx.hit("sql-destroy", False, summary)
            if f.write:
                ctx.hit("db-write", False, summary)
    else:  # Ruling R17: a tool co-guard does not know (maybe a new shell) - a person decides
        try:
            text = json.dumps(tool_input, sort_keys=True, default=str)
        except (TypeError, ValueError, RecursionError):
            text = "<input>"
        ctx.hit("unknown-tool", False, f"{tool_name} {text}")
    hits = _order(ctx.hits)
    if any(h.rule_id == "sql-destroy" for h in hits):  # it already holds the statement: one approval, not two
        hits = [h for h in hits if h.rule_id != "db-write"]
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
