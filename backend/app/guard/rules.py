"""co-guard rules engine: decides whether one Claude Code tool call may run.

Python port of jspark-forge ``scripts/lib/blocklist.js`` and
``rules/default-blocklist.json`` (MIT, same owner): the built-in block list
(inventory F28-F31, F34-F36, F38, F39), plus MCP tool policies. Bash is
parsed with :mod:`shell` (the co-guard parser); nothing is ever executed.

RULING R1: this module uses only the Python standard library and the siblings
``shell.py``, ``dbrules.py`` and ``custom.py``. It is copied next to them into the co-guard
plugin and must work both as ``app.guard.rules`` and as a standalone file run with
``python3 -I`` (``python3 -I rules.py < request.json`` prints a verdict).

API
---
``evaluate(tool_name, tool_input, policy, cwd) -> Verdict``

``temp_roots(policy) -> list[str]`` and ``path_in_temp(path, policy) -> bool``:
the temp folders below (a file strictly inside one can vanish).

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
      "mcp_defaults": {"mcp__<prefix>__*": "auto_approve"|"require_approval"|"block"},
                                           #   server-wide default actions, used only
                                           #   when no mcp_tools entry matches (same
                                           #   matching; malformed: require_approval)
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
      "custom_rules": [{"id": "org.x"|"project.x"|"repo.x", "kind": "confirm"|"never",
                        "match": "text"|"regex", "pattern": str, "reason": str}],
                                           #   org/project/repo rules (custom.py); they
                                           #   apply even with guard_enabled false
      "disabled_rules": ["git-reset-hard", ...],
                                           #   built-ins switched off by an admin: only
                                           #   ids in DISABLEABLE count, and never a hard
                                           #   hit (rm -rf ~ stays denied)
      "quarantine": false,                 #   true: guest-quarantine holds the repo's
                                           #   install/build/test/run commands (also with
                                           #   the guard off); missing/null/false is off,
                                           #   any other value is on (fail safe)
      "readonly_paths": [{"path": "/abs" | "rel/to/project", "reason": str,
                          "source": "project"|"reference"}],
                                           #   readonly-source roots (reason and source
                                           #   optional); not a list of such entries, a
                                           #   NUL, or a "~" prefix (the worker writes
                                           #   absolute paths): hard unparseable (fail
                                           #   closed)
      "trust": "own"|"external",           #   read by the hook (SessionStart data line;
                                           #   anything but "own" is external), not here
      "on_no_answer": "deny"|"deny_and_resume",
                                           #   the server's no-answer mode (the hook
                                           #   follows the POST's resume_until), not here
      "grants": [{"id": str, "scope": "run"|"once", "rule_id": str,
                  "key_kind": "rule"|"tool"|"command", "key": str, "expires_at": epoch}],
                                           #   a person's earlier approvals; checked by the
                                           #   hook (co_hook.covering_grants) after an
                                           #   approve verdict, never here; a malformed
                                           #   entry covers nothing
      "regex_budget_ms": 250,              #   shared budget for every custom regex in
                                           #   one evaluate (at most 1000; a non-number
                                           #   means 250, <= 0 means every regex times out)
      "notes": true, "notes_after": 12,    #   the hook nudges for notes every 12 calls (notes
                                           #   false or missing: never); never denies anything
      "helper_context": "...",             #   text SubagentStart hands every helper ("" or
                                           #   missing: the data line unless trust is own)
      "compact": true,                     #   false: no handoff re-injection after a compaction
                                           #   (missing or malformed means true)
      "decisions": [{"n": 12, "kind": "decision", "title": str, "why": str, "see": str,
                     "touches": [str], "decided_at": "YYYY-MM-DD"}],
                                           #   an own project's active decisions (phase 4a):
                                           #   the hook shows the ones covering the file an
                                           #   allowed Edit/Write is about to change, as
                                           #   context only; never read here
      "decisions_index": "...",            #   their short index, re-added after a compaction
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
* ``readonly-source`` (always on, also with the guard off; never filed): a
  change under a ``readonly_paths`` root (real path and as given, case-folded
  per R9; reason ``this changes <path>, which is read-only (<reason>)``) -
  Edit/Write/MultiEdit/NotebookEdit, every write target (redirects, ``tee``,
  cp/ln/rsync/install/dd/curl destinations, ``sed -i``/``perl -i``, ``touch``,
  ``truncate``, ``mkdir``, chmod/chown, client output files, ``tar -x``/
  ``unzip`` folders), both ends of ``mv`` and the target of ``chmod -R`` (also
  as a parent of a root), deletes (``rm``, ``rmdir``, ``find -delete``, ``fd
  -X rm``, ``rimraf``, ``rsync --delete``; also a parent of a root, or a glob
  or filter that reaches into one), and git ``commit add rm mv reset checkout
  restore switch clean stash merge rebase pull apply am cherry-pick revert``
  run in a root (``-C``, ``--work-tree``, ``--git-dir`` or the tracked cwd) or
  naming paths under one (rm/mv/checkout/restore/clean; a pathspec glob by
  its literal folder) - not their ``--dry-run``/``-n`` forms, ``apply
  --check``, ``stash list|show`` or ``rm --cached`` -, ``git clone`` /
  ``worktree add`` destinations and ``apply --directory``. Also: hard links
  (``ln``, ``cp -l``) to a file in a root, ``rsync --remove-source-files``
  and ``rename`` sources, gzip-family operands and ``-o``, ``sort -o``,
  ``split`` prefixes, ``tar -c -f``, ``zip`` archives, ``patch`` targets /
  ``-d`` / ``-o`` (no target: the cwd), ``install -d``, ``pip install
  --target|-t|--prefix|--root``, ``npm|pnpm|yarn|bun --prefix|--dir|--cwd|-C``
  for a non-read subcommand, ``make -C``/``-f`` folders, ``openssl -out|-keyout``,
  ``ssh-keygen -f`` (when it writes), ``setfacl``, ``xattr -w|-d|-c``,
  ``vim``/``ex``/``emacs``/``ed`` given commands (``-c``, ``+cmd``, ``-s``,
  ``-es``, ``--batch``, ``--eval`` ...); ``find ROOT -exec|-execdir`` and
  ``fd .. -x|-X`` with a writer (a deleter, the writers above, ``sed``/``perl
  -i``) and ``xargs`` writers fed by ``ls``/``find``/``fd``/``echo``/
  ``printf`` naming a root - for a copy (``cp``/``install``/``rsync``/
  ``scp``/``ln -s``) the found paths count only as its destination (the last
  operand, or with ``-t``), so ``find ref -exec cp {} ./copy/ +`` is a read,
  while ``mv`` and ``cp -l`` sources count; a build/install/test/script run
  with the cwd in a root (quarantine's tables with the root as "the repo")
  is hard only for known writers (Rulings P18/P19: package manager
  install/add/ci/sync/update, JS ``build``/``run build``/``version``/``audit
  fix``, make/cmake/ninja/meson, cargo build/check/clippy/run/test, go
  build/generate/install, ``python setup.py``, ``python -m build``, ``pip
  install -e .``, bare ``tsc``/``npx tsc`` without ``--noEmit``,
  ``./configure``, autoreconf, docker/podman build, gradle/mvn, dotnet
  build/publish/restore, swift/bazel/zig build, mix deps.get/compile, git
  submodule update, poetry/bundle install - not their dry runs) and
  otherwise a confirmable ``uncertain-command`` ("this runs a read-only
  source's code, which may change it"); Ruling P19: in-place formatters and
  fixers (black, ``ruff format``/``check --fix``, isort, ``autopep8 -i``,
  ``prettier --write`` - also via npx/pnpm/yarn/bun -, ``eslint --fix``,
  ``gofmt -w``, ``go fmt``, ``goimports -w``, ``cargo fmt``, rustfmt,
  ``clang-format -i``, ``shfmt -w``, stylua, ``rubocop -a|-A``, swiftformat,
  ``terraform fmt``; not their check/diff modes) treat their path operands
  (default ``.``; a glob operand, quoted or not, counts as its literal
  folder: ``"**/*.js"`` is ``.``) like ``chmod -R`` targets, and builds
  aimed at a folder by a flag write it (``cmake -B|--build`` - not ``-S``,
  the source -, ``cargo --manifest-path``, ``go -C``, ``pip install -e
  DIR``, ``uv --project|--directory``, ``poetry -C|--directory``, ``tsc -p``
  without ``--noEmit``, ``git -C DIR submodule update``); a non-editable
  ``pip install DIR`` of a folder in a root is a confirmable
  ``uncertain-command`` ("pip builds this read-only folder in place"); inline
  interpreter code that may write (the agent-config pattern) and names a
  root (as written, normalised or real, case-folded) or runs with the cwd in
  one. An unknown path counts by its braces (``{../ref,/tmp}/``), a ``for``
  loop's words, its glob (``vendor/ol?/``, ``../[R]ef/z``, matched per
  component) or its literal folder (``> ref/$f``); a bare delete/move of a
  symlink to a root removes only the link. Ruling P15: after a ``cd`` we
  cannot follow, a relative target is tried against BOTH the project and the
  start cwd; Ruling P17: when that places it in no root, the change is a
  confirmable ``uncertain-command`` ("the folder this runs in is unknown, and
  read-only folders exist") - only when readonly roots exist; absolute
  targets and reads are unaffected (``git add`` changes no work tree; ``cd
  "$(mktemp -d)"`` is a fresh temp folder, not unknown). A ``f() { ..; }``
  body runs in the current shell (its cd counts); ``f() ( .. )`` is scoped
  like ``( )``. ``command -v X`` is a lookup (free under quarantine). Ruling P16: a ``cd``/``pushd`` inside ``( )`` does not leak out
  (a redirect after the group uses the outer cwd), ``popd`` pops a tracked
  stack (empty: unknown), ``cd -`` goes back, and a ``cd`` in a pipeline
  changes nothing. Reads stay free; over ssh nothing is a root;
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
``.claude/skills/**``, ``.claude/commands/**``, ``.mcp.json`` or - Ruling P13,
the repo rules file a later task loads - ``.claude-orchestrator/**``; also
deleting or moving it or a folder holding the project's one: ``rm``, ``mv``,
``git rm``/``git mv``, ``find`` whose filter can reach it),
``uncertain-command``, ``mcp-require-approval``, ``unknown-tool``,
``guest-quarantine`` (policy ``quarantine``; always on, never disableable:
forge ``quarantined()`` ported with its tables - package managers held for
every subcommand but a read-only list (``READONLY_SUBS``), ``PM_ALWAYS``
runners, docker/podman except read-only subcommands, ``docker-compose``,
``java -jar``, interpreters with ``-m/-c/-e/-r`` or a script inside the
project, shells/``source`` running a script inside the project or fed by a
pipe or a ``<`` redirect from the project, ``pwsh`` scripts, and the
project's own executables run by path; versioned names (``python3.11``,
``node22``); ``--version``/``--help`` alone and ``make -n`` are free; every
stage, nested and wrapped ones included; "inside" means the project dir,
real path, case-folded, and an unresolvable path counts as inside),
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

Custom rules (``custom_rules``, ids ``org.<slug>`` / ``project.<slug>`` /
``repo.<slug>``; built-in ids never contain a dot): checked for every tool
against one text - Bash/Monitor: the command with whitespace collapsed; file
tools: ``<tool> <file_path>`` (never the content); any other tool: ``<tool>
<compact JSON input, sorted keys>``. ``never`` is hard, ``confirm`` is
confirmable, and a regex still running when the shared ``regex_budget_ms``
budget ends is a confirmable hit (also for ``never``) with the reason ``rule
<id> took too long to check, so it needs approval``. The reason is the rule's
own (control, zero-width and bidi characters stripped, 200 chars) or ``this
matches the rule "<pattern>"``. A ``custom_rules`` or ``disabled_rules`` value
that is not a list of well-formed entries is a hard ``unparseable`` hit with
the co-guard error reason (fail closed); a missing key is empty.

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
readonly-source does not see: git subcommands outside forge's list run in a
root (``fetch``, ``branch -D``, ``tag``, ``gc``, ``config``, ``worktree
add``), ``GIT_DIR``/``GIT_WORK_TREE`` set in the environment, work-tree
commands of an enclosing repo that touch a root inside it without naming it
(``git stash``, ``git clean -fd``, ``git pull`` in the project when
``vendor/old`` is read-only), ``rsync -a src/ parent/`` whose unnamed
contents land in a root inside ``parent``, interactive editors (``vi f``,
``nano f`` - the agent has no terminal), shells and interpreters run by
``find -exec``/``xargs`` (only their own text is checked, ``{}`` unknown),
code that builds a root's path at run time, a cd to an unknown folder that
really is a root (P15/P17: ``cd $D && git commit`` needs approval, it is
not denied), a symlink created earlier and written through
later (checked when it is written), and archive members with absolute or
``..`` names. P13 does not see a nested ``sub/.claude-orchestrator`` reached
by a ``find`` rooted above it, nor ``git clean -fdx``, ``git stash -u`` or
``tar -x`` replacing an untracked rules folder.

cwd tracking (P15-P17) is straight-line: a function body is checked where
it is defined, in that cwd, not where it is called - in both directions
(``f() { echo > a; }; cd ref; f`` is missed, and a body defined inside a
root is judged there even when it is called elsewhere); path lists that
reach ``sed -i`` or ``xargs`` through a pipe or ``$(..)`` from anything but
``ls``/``find``/``fd``/``echo``/``printf`` (``git ls-files | xargs sed -i``,
``sed -i .. $(grep -l x -r .)``) are not followed; the GNU ``parallel``
command template is not parsed as a command; a ``cd`` inside ``if``/loops/``||`` is assumed to
happen (``while false; do cd /tmp; done`` moves the cwd); in zsh the last
stage of a pipeline runs in the current shell, so ``true | cd ref`` does
move it there (co-guard follows bash); and ``find`` reach ignores ``-prune``
and ``-not -path`` (``find . -path ./vendor -prune -o .. -exec sed -i``
still reaches ``vendor/old``).

guest-quarantine keeps forge's tables, so it misses tools they do not list
(``tsc``, ``prettier``, ``black``, ``ruff``, ``mypy``, ``gcc``, ``javac``,
``java X``, bare ``ant``, ``awk -f``, ``sed -f``, ``Rscript``, ``lua``,
``julia`` ...), runners that start a held command later (``entr make``,
``git bisect run make``, ``git submodule foreach make``), code loaded through
the environment (``NODE_OPTIONS=--require``, ``PYTHONSTARTUP``),
``python3 /dev/stdin < x.py`` and ``find -exec {}``. It holds some reads:
``python -c``, ``node -p``/``--check``, ``python -m json.tool``/
``py_compile``, ``bash -n``, ``docker compose ps|config|logs``, ``just|task
--list``, ``pytest --collect-only``, ``npm explain|pkg``, ``pip download``,
``--dry-run`` installs and ``go list``.
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
_cu = _sibling("custom")

__all__ = ["RULES", "DISABLEABLE", "Hit", "Verdict", "evaluate", "is_secret_name"]

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
    "guest-quarantine": (
        "this runs code from a repo you are reviewing (install, build, test or run), and that code can do "
        "anything your user account can", None),
    # the real reason names the root: "this changes <path>, which is read-only (<reason>)" (_Ctx.readonly_hit)
    "readonly-source": (None, "this changes a read-only source"),
}
_ALWAYS_ON = frozenset({"self-protect", "unparseable", "mcp-block", "mcp-require-approval", "unsupported-shell",
                        "unknown-tool", "guest-quarantine", "readonly-source"})
# Built-ins an admin may switch off (policy "disabled_rules"); only their confirmable part - a hard hit
# (rm -rf ~, rm -rf /) always stays. Anything else in disabled_rules is ignored.
DISABLEABLE = frozenset({"rm-rf-outside", "git-force-push-main", "git-reset-hard", "git-clean-fdx",
                         "git-stash-drop", "secret-files", "chmod-wide", "pipe-to-shell", "sql-destroy", "db-write"})
ERROR_REASON = "co-guard error \u2014 action blocked for safety"  # co_hook.ERROR_REASON
_BUDGET_MAX_MS = 1000  # the hook must answer well inside its own timeout
_WORD_TEXTS_MAX = 4096  # distinct argv texts kept per call for custom rules

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
                         r"|\.claude['\"]|\.mcp\.json|\.claude-orchestrator\b", re.I)
# Ruling R20: subagent, skill and slash-command definitions can start processes
# (frontmatter mcpServers, hooks, inline !`cmd`) outside PreToolUse.
_AGENT_DIRS = frozenset({"hooks", "agents", "skills", "commands"})
_GITMODULES_EXEC = re.compile(r"(?im)^\s*update\s*=\s*!")
_STDIN_PATHS = frozenset({"-", "/dev/stdin", "/dev/fd/0", "/proc/self/fd/0"})
_MAKEFILES = frozenset({"makefile", "gnumakefile"})

# guest-quarantine: forge blocklist.js quarantined() tables, ported as they are
_PM_READ = "info view show list ls why outdated help search config licenses whoami ping docs repo bugs version"
_READONLY_SUBS = {k: frozenset(v.split()) for k, v in {
    "npm": f"{_PM_READ} audit", "pnpm": f"{_PM_READ} audit", "yarn": f"{_PM_READ} audit", "bun": _PM_READ,
    "pip": "list show freeze help search check config index inspect",
    "pip3": "list show freeze help search check config index inspect",
    "uv": "help version tree", "pipx": "list help", "poetry": "show help about search check config env",
    "pdm": "list show help info config", "conda": "list info search help config env",
    "mamba": "list info search help config env", "composer": "show info help search licenses outdated why",
    "gem": "list info help search query environment specification",
    "cargo": "fmt tree metadata version help search pkgid locate-project", "go": "env fmt version doc help",
    "dotnet": "help --list-sdks --list-runtimes --info", "deno": "info help fmt lint doc", "swift": "help",
    "mix": "help", "stack": "help", "cabal": "help list info", "mvn": "", "gradle": "", "ant": "", "bundle": "",
    "hatch": "version help",
}.items()}
_PM_ALWAYS = frozenset({
    "npx", "pnpx", "bunx", "uvx", "make", "gmake", "cmake", "ninja", "just", "task", "tox", "nox", "rake",
    "msbuild", "pre-commit", "pytest", "py.test", "jest", "vitest", "mocha", "ava", "tap", "playwright", "cypress",
    "eslint", "vite", "webpack", "rollup", "next", "nuxt", "turbo", "nx", "lerna", "tsx", "ts-node", "gradlew",
    "mvnw", "rspec", "sbt", "bazel", "bazelisk", "mill", "lein", "bats", "phpunit", "ctest", "meson", "scons"})
_DOCKER_READONLY = frozenset({"ps", "images", "image", "inspect", "logs", "version", "info", "stats", "top",
                              "volume", "network", "search", "login", "logout", "pull"})
_INTERPRETERS = frozenset({"python", "python3", "py", "node", "nodejs", "ruby", "perl", "php"})
_SCRIPT_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "fish", "source", "."})
_PS_SHELLS = frozenset({"powershell", "pwsh"})
_VALUE_FLAGS = re.compile(r"(--loglevel|--prefix|-C|--cwd|--dir|--registry|--workspace|-w|--filter|-F|"
                          r"--manifest-path|--project|-p|--userconfig|--cache)")
_FLAGS_ONLY = re.compile(r"(--version|-v|-V|--help|-h)")
_MAKE_DRY = re.compile(r"(-n|--dry-run|--just-print|--recon|-q|--question)")  # make only (pytest -q runs tests)
_INTERP_RUNS = re.compile(r"-(m|c|e|-eval|r|-require|-import)")  # python -m/-c, node -e/-r
_PS_INLINE = re.compile(r"-(c|command|com|comm|comma|comman|encodedcommand|e|ec)", re.I)
_SCRIPT_EXT = re.compile(r".*\.(sh|bash|ps1|bat|cmd|py|js|mjs|rb|pl)", re.I)
_BASE_TOOL = re.compile(r"(python|pip|ruby|node|php|perl)\d+(\.\d+)*")  # python3.11 -> python, node22 -> node

# readonly-source: git subcommands that change the work tree (forge GIT_CHANGES)
_GIT_CHANGES = frozenset({"commit", "add", "rm", "mv", "reset", "checkout", "restore", "switch", "clean", "stash",
                          "merge", "rebase", "pull", "apply", "am", "cherry-pick", "revert"})
_RULES_DIR = ".claude-orchestrator"
_MKTEMP_DIR = re.compile(r"(?:\$\(|`)\s*mktemp\s+(?:-d|--directory)(?:\s+(?:-q|-t|--tmpdir|[\w.-]*X{3,}[\w.-]*))*\s*(?:\)|`)")
_UNKNOWN_CWD = "the folder this runs in is unknown, and read-only folders exist"  # Ruling P17
_RUNS_ROOT = "this runs a read-only source's code, which may change it"  # Ruling P18
_PIP_LOCAL = "pip builds this read-only folder in place"
_PM_WRITE_SUBS = frozenset({"install", "i", "add", "ci", "sync", "update", "upgrade", "up"})
_DRY = frozenset({"-n", "--dry-run", "--just-print", "--recon"})

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


# forge v0.5.1 (F89) CODE_FILE: a name holding "secret"/"credentials" with one of
# these extensions is source code or a document (secrets.ts makes tokens, it does
# not hold them), not a secrets store.
_CODE_FILE = re.compile(r"\.(?:[cm]?[jt]sx?|py|go|rs|java|kt|kts|cs|rb|php|swift|c|h|cc|cpp|hpp|scala|dart|ex|exs"
                        r"|vue|svelte|md|mdx)$")


def is_secret_name(path):
    """forge isSecretName (.env*, *.pem, *.key, id_rsa*, *credentials*, *secret*)
    plus SSH keys, .netrc, .npmrc, .pypirc, .docker/config.json, .kube/config.
    A template word (example, sample, template, dist) exempts a file only as
    its LAST suffix (.env.example yes, .env.sample.local no). Final wave
    (forge v0.5.1): the *credentials* / *secret* substring case lets source
    code and documents through (``_CODE_FILE``: secrets.ts, credentials.py,
    docs/secrets.md); data files (secrets.yml, credentials.json) and every
    other name above stay held whatever their extension."""
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
    if (base.startswith(".env") or base.endswith(".pem") or base.endswith(".key") or base.startswith("id_rsa")
            or base.startswith(("id_ed25519", "id_ecdsa", "id_dsa"))):
        return True
    if _CODE_FILE.search(base):
        return False
    return "credentials" in base or "secret" in base


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
    """A .claude/settings*.json, .claude/{hooks,agents,skills,commands}/**,
    .mcp.json (R6/R7/R20) or .claude-orchestrator[/**] (the repo rules file a
    later task loads, Ruling P13) path."""
    parts = path.lower().split("/")
    if parts and parts[-1] == ".mcp.json":
        return True
    if ".claude-orchestrator" in parts:
        return True
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == ".claude":
            rest = parts[i + 1:]
            return (len(rest) == 1 and fnmatch.fnmatchcase(rest[0], "settings*.json")) or \
                (bool(rest) and rest[0] in _AGENT_DIRS)
    return False


def _readonly_entry_ok(e):
    """{path: non-empty str, reason?: str|None, source?: "project"|"reference"|None}; a NUL anywhere or a
    ``~`` prefix is malformed (the worker writes absolute paths)."""
    return (isinstance(e, dict) and isinstance(e.get("path"), str) and bool(e["path"].strip())
            and "\x00" not in e["path"] and not e["path"].startswith("~")
            and (e.get("reason") is None or (isinstance(e.get("reason"), str) and "\x00" not in e["reason"]))
            and e.get("source") in (None, "project", "reference"))


def _braces(word, limit=64):
    """Full bash brace expansion of ``{a,b}`` groups (nested too, not ``${..}``),
    or None when there is none or it gets too big."""
    out, todo = [], [word]
    while todo:
        w = todo.pop()
        start = None
        depth, comma, k = 0, False, 0
        found = None
        while k < len(w):
            c = w[k]
            if c == "{" and not (k and w[k - 1] == "$"):
                if depth == 0:
                    start, comma = k, False
                depth += 1
            elif c == "}" and depth:
                depth -= 1
                if depth == 0 and comma:
                    found = (start, k)
                    break
            elif c == "," and depth == 1:
                comma = True
            k += 1
        if not found:
            out.append(w)
            continue
        a, b = found
        parts, depth, last = [], 0, a + 1
        for j in range(a + 1, b):
            c = w[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            elif c == "," and depth == 0:
                parts.append(w[last:j])
                last = j + 1
        parts.append(w[last:b])
        todo += [w[:a] + x + w[b + 1:] for x in parts]
        if len(out) + len(todo) > limit:
            return None
    return out if out != [word] else None


def _glob_reaches(pattern, folder, as_ancestor=False):
    """A path glob (per component, case-folded) could name ``folder`` or
    something inside it - or, as_ancestor, a parent of it."""
    pp, fc = pattern.lower().strip("/").split("/"), folder.lower().strip("/").split("/")
    n = min(len(pp), len(fc))
    if not all(fnmatch.fnmatchcase(c, g) for c, g in zip(fc[:n], pp[:n])):
        return False
    return len(pp) >= len(fc) or as_ancestor


def _home_project(policy):
    """The policy's home and project dir, absolute with symlinks resolved."""
    home = policy.get("home") or os.path.expanduser("~")
    project = policy.get("project_dir") or policy.get("cwd") or os.getcwd()
    return _real(os.path.abspath(str(home))), _real(os.path.abspath(str(project)))


def temp_roots(policy):
    """The temp roots for ``policy``: /tmp, /private/tmp, $TMPDIR (this
    process's env, else policy "tmpdir") and policy "tmpdir". A root that is
    (or holds) home or the project, is / or a top-level folder, or is
    /private/var/folders or one of its first two levels, is no temp root."""
    policy = policy if isinstance(policy, dict) else {}
    home, project = _home_project(policy)
    policy_tmpdir = policy.get("tmpdir")
    tmpdir = os.environ.get("TMPDIR") or policy_tmpdir
    roots = {_real(r) for r in _TEMP_ROOTS} | set(_TEMP_ROOTS)  # '/tmp' as named (a link on macOS)
    extra = {_real(os.path.abspath(str(t))) for t in (tmpdir, policy_tmpdir) if t}
    roots |= {r for r in extra if r and r.count("/") >= 2 and not _within(_VAR_FOLDERS, r)
              and not (_inside(r, _VAR_FOLDERS) and r.count("/") < 6)}
    return sorted(r for r in roots if r != "/" and not _within(home, r) and not _within(project, r))


def _var_folders_root(path, home, project):
    """{the macOS per-user temp dir /private/var/folders/<a>/<b>/T that holds
    ``path``}, unless it holds home or the project; else an empty set."""
    parts = path.split("/")
    if path.startswith(_VAR_FOLDERS + "/") and len(parts) > 6 and parts[6] == "T":
        root = "/".join(parts[:7])
        if not _within(home, root) and not _within(project, root):
            return {root}
    return set()


def path_in_temp(path, policy):
    """``path`` (symlinks resolved) is strictly inside a temp root of
    ``policy`` (a file there can vanish)."""
    if not path or not isinstance(path, str) or "\0" in path:
        return False
    policy = policy if isinstance(policy, dict) else {}
    home, project = _home_project(policy)
    p = _real(os.path.abspath(path))
    roots = set(temp_roots(policy)) | _var_folders_root(p, home, project)
    return any(_inside(p, r) for r in roots)


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
        self.policy_errors = []  # malformed custom_rules / disabled_rules: fail closed (hard)
        disabled = policy.get("disabled_rules", [])
        if not isinstance(disabled, list) or not all(isinstance(r, str) for r in disabled):
            self.policy_errors.append("disabled_rules")
            disabled = []
        self.disabled = DISABLEABLE.intersection(disabled)
        try:
            self.custom = _cu.policy_rules(policy.get("custom_rules", []))
        except ValueError:
            self.policy_errors.append("custom_rules")
            self.custom = []
        # guest-quarantine: only a literal false (or no value) is off; anything else holds (fail safe)
        quarantine = policy.get("quarantine")
        self.quarantine = quarantine is not None and quarantine is not False
        # readonly-source roots: (forms - normalised and real, case-folded per R9 -, path as given, reason)
        self.readonly = []
        ro = policy.get("readonly_paths", [])
        if not isinstance(ro, list) or not all(_readonly_entry_ok(e) for e in ro):
            self.policy_errors.append("readonly_paths")
        else:
            for e in ro:
                ab = posixpath.join(self.project, e["path"])  # relative to the project; absolute stays
                forms = {_norm(ab).lower(), _real(ab).lower()}
                self.readonly.append((forms, e["path"], _cu.clean_reason(e.get("reason") or "")))
        budget = policy.get("regex_budget_ms", _cu.REGEX_BUDGET_MS)
        if isinstance(budget, bool) or not isinstance(budget, (int, float)) or budget != budget:
            budget = _cu.REGEX_BUDGET_MS
        self.regex_budget_ms = min(float(budget), _BUDGET_MAX_MS)
        self.mcp_tools = policy.get("mcp_tools", {})
        self.mcp_defaults = policy.get("mcp_defaults")  # used only when no mcp_tools entry matches
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
        self.words = {}  # Ruling P12: de-quoted argv texts of every simple command (custom rules)
        self.start = self.project  # the call's cwd (evaluate sets it)
        self.remote = False  # inside `ssh host CMD`: nothing is the project
        self.depth = 0
        self.evals = 0

    def _build_var_re(self):
        names = "|".join(map(re.escape, self.vars))
        self._var_re = re.compile(r"^\$(?:\{(" + names + r")(?:[:]?[-?=+][^}]*)?\}|(" + names + r"))(?=/|$)")

    def _temp_roots(self, policy_tmpdir=None):
        """The module-level ``temp_roots`` for this home and project."""
        return set(temp_roots({"home": self.home, "project_dir": self.project, "tmpdir": policy_tmpdir}))

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
        roots = set(self.temp_roots) | _var_folders_root(path, self.home, self.project)
        return any(_inside(path, r) if whole else _within(path, r) for r in roots)

    # -- hits --------------------------------------------------------------
    def hit(self, rule_id, hard, summary):
        if not self.guard and rule_id not in _ALWAYS_ON:
            return
        if not hard and rule_id in self.disabled:  # an admin switched it off (never its hard part)
            return
        self.hits.append(_make_hit(rule_id, hard, summary))

    def readonly_root(self, path, as_ancestor=False, follow=True):
        """(path as given, reason) of the readonly root ``path`` is (inside),
        or - as_ancestor: deleting/moving a parent - that is inside ``path``;
        else None. follow=False: a bare delete/move operand, whose last link
        is not followed (``rm link`` removes the link). Over ssh nothing
        here is a root."""
        if not self.readonly or not path or self.remote:
            return None
        cands = {path.lower(), _norm(path).lower(), (_real(path) if follow else _real_last(path)).lower()}
        for forms, shown, reason in self.readonly:
            for f in forms:
                if any(_within(c, f) or (as_ancestor and _inside(f, c)) for c in cands):
                    return shown, reason
        return None

    def readonly_hit(self, root, summary):
        """Hard, always on (also with the guard off), never disableable."""
        shown, reason = root
        text = f"this changes {shown}, which is read-only" + (f" ({reason})" if reason else "")
        self.hits.append(Hit("readonly-source", text, True, _short(summary)))

    def readonly_check(self, path, summary, as_ancestor=False, follow=True):
        root = self.readonly_root(path, as_ancestor, follow)
        if root:
            self.readonly_hit(root, summary)
        return root

    def readonly_glob(self, pattern, summary, as_ancestor=False):
        """A glob path (normalised, absolute) that could name a root or
        something inside it (as_ancestor: also a parent of one)."""
        if not self.readonly or self.remote:
            return None
        for forms, shown, reason in self.readonly:
            if any(_glob_reaches(pattern, f, as_ancestor) for f in forms):
                self.readonly_hit((shown, reason), summary)
                return shown, reason
        return None

    def readonly_delete(self, path, glob, summary):
        """Deleting ``path`` (glob None: the path itself, so also a parent of
        a root; its last link is not followed) or some of its children (a
        glob - matched against the first component, so ``.??*`` does not
        reach ``vendor`` - or _SOME)."""
        if glob is None:
            return self.readonly_check(path, summary, as_ancestor=True, follow=False)
        root = self.readonly_root(path)
        if root is None and self.readonly and not self.remote:
            low = path.lower()
            for forms, shown, reason in self.readonly:
                for f in forms:
                    if _inside(f, low):
                        first = f[len(low):].lstrip("/").split("/")[0]
                        if glob == _SOME or fnmatch.fnmatchcase(first, glob.lower()):
                            root = (shown, reason)
        if root:
            self.readonly_hit(root, summary)
        return root

    def readonly_cwd(self, cwd):
        """The root a working folder is in (P15: unknown -> project or start)."""
        for b in self.bases(cwd):
            root = self.readonly_root(b)
            if root:
                return root
        return None

    def unknown_cwd(self, before, summary):
        """Ruling P17: readonly roots exist, the cwd is unknown and P15 placed
        the change (hits since index ``before``) in no root - a person decides."""
        if self.readonly and not self.remote and not any(h.rule_id == "readonly-source" for h in self.hits[before:]):
            self.hits.append(Hit("uncertain-command", _UNKNOWN_CWD, False, _short(summary)))

    def bases(self, cwd):
        """Ruling P15: folders a relative path may be relative to - the
        tracked cwd, or (unknown) both the project and the start cwd."""
        if cwd is not None:
            return [cwd]
        return list(dict.fromkeys(b for b in (self.project, self.start) if b))

    def rules_dir(self):
        """The project's repo rules folder (Ruling P13), case-folded."""
        return (self.project + "/" + _RULES_DIR).lower()

    def note_words(self, argv, unwrapped):
        """The de-quoted argv of a simple command (and without its wrappers),
        for custom rules: `"git" push`, `g\\it push`, `$'git' push` -> `git push`."""
        if not self.custom:
            return
        for words in (argv, unwrapped):
            if words and len(self.words) < _WORD_TEXTS_MAX:
                self.words.setdefault(" ".join(" ".join(map(str, words)).split()), None)

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
        if dynamic and ("$" in w or "`" in w or _GLOB.search(w) or ("{" in w and _braces(w))):
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
            out.append((_real(head), tail))  # a broad tail (*, .*, .??*) is judged as the folder by _BROAD
            continue
        out.append((_real(e) if follow else _real_last(e), None))
    return out


def _rules_dir_delete(ctx, path, glob, summary):
    """Ruling P13: deleting or moving the repo rules file (or its folder, or
    a folder holding the project's one) relaxes what a later task loads -
    agent-config, like editing it."""
    parts = path.lower().split("/") + ([glob.lower()] if glob not in (None, _SOME) else [])
    if any(c == _RULES_DIR or ("claude" in c and _GLOB.search(c) and
                               fnmatch.fnmatchcase(_RULES_DIR, c)) for c in parts):
        ctx.hit("agent-config", False, summary)
    elif glob is None and not ctx.remote and _within(ctx.rules_dir(), path.lower()):
        ctx.hit("agent-config", False, summary)
    elif glob not in (None, _SOME) and glob[:1] == "." and not ctx.remote and \
            fnmatch.fnmatchcase(_RULES_DIR, glob.lower()) and (
            path.lower() == ctx.project.lower() or os.path.isdir(posixpath.join(path, _RULES_DIR))):
        ctx.hit("agent-config", False, summary)  # rm -rf .c*, mv .[a-z]* /tmp


def _rules_dir_find(ctx, root, names, summary):
    """P13 for ``find ROOT <filter> -delete``: ROOT is in a rules folder, or
    reaches the project's one and a -name filter (None: another filter) may
    match it or something in it."""
    low = root.lower()
    if _RULES_DIR in low.split("/"):
        ctx.hit("agent-config", False, summary)
        return
    rd = ctx.rules_dir()
    if ctx.remote or not _within(rd, low):
        return
    cands = [_RULES_DIR, "rules.md"]
    try:
        cands += os.listdir(ctx.project + "/" + _RULES_DIR)
    except OSError:
        pass
    if names is None or any(fnmatch.fnmatch(c.lower(), n.lower()) for c in cands for n in names):
        ctx.hit("agent-config", False, summary)


def _protect_delete(ctx, path, glob, summary):
    """self-protect (and readonly-source) for deleting/moving `path` (glob:
    which children)."""
    _rules_dir_delete(ctx, path, glob, summary)
    ctx.readonly_delete(path, glob, summary)
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
            if cwd is None:  # Ruling P15: relative to the project or the start cwd
                before = len(ctx.hits)
                for b in ctx.bases(None):
                    for path, glob in _target_kinds(ctx, word, dyn, b) or ():
                        if narrow and glob is None:
                            _rules_dir_find(ctx, path, names, summary)
                            ctx.readonly_delete(path, _SOME, summary)
                        else:
                            _rules_dir_delete(ctx, path, glob, summary)
                            ctx.readonly_delete(path, glob, summary)
                if not word.startswith(("/", "~", "$", "`")):
                    ctx.unknown_cwd(before, summary)
            continue
        for path, glob in kinds:
            if narrow and glob is None:
                if _is_home_or_above(ctx, path):
                    level = "hard"
                glob = _SOME
                _protect_find(ctx, path, names, summary)
                _rules_dir_find(ctx, path, names, summary)
                ctx.readonly_delete(path, _SOME, summary)
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


_PACKERS = frozenset({"gzip", "gunzip", "bzip2", "bunzip2", "xz", "unxz", "zstd", "unzstd", "lz4", "lzma", "unlzma",
                      "compress", "uncompress", "pigz", "unpigz"})
_RUN_WRITERS = frozenset({"touch", "chmod", "chown", "chgrp", "mv", "cp", "ln", "install", "truncate", "tee", "dd",
                          "rsync", "setfacl", "xattr", "patch", "rename", "prename", "perl-rename", "file-rename",
                          "chattr", "mkdir"}) | _PACKERS


_FD_HOLDERS = ("{}", "{/}", "{//}", "{.}", "{/.}")


_JS_RUN = frozenset({"npx", "pnpx", "bunx"})


def _unrun(argv, dyn):
    """``npx tool ..``, ``pnpm exec|dlx tool``, ``yarn dlx|exec tool``,
    ``bun x tool`` -> (tool argv, dyn); anything else unchanged."""
    argv, dyn = list(argv), list(dyn) + [False] * len(argv)
    name = _sh.command_name(argv[0]) if argv else ""
    i = None
    if name in _JS_RUN:
        i = 1
    elif name in ("pnpm", "yarn", "bun", "npm") and len(argv) > 1 and argv[1] in ("exec", "dlx", "x"):
        i = 2
    if i is None:
        return argv, dyn[:len(argv)]
    while i < len(argv) and argv[i].startswith("-"):
        i += 2 if argv[i] in ("-p", "--package", "-c", "--call") else 1
    if i < len(argv) and argv[i] == "--":
        i += 1
    if i >= len(argv):
        return argv, dyn[:len(argv)]
    return argv[i:], dyn[i:len(argv)]


def _pos_skipping(args, dargs, values=(), default=None):
    """Positional operands with option values left out; ``-`` (stdin) dropped."""
    out, i = [], 0
    while i < len(args):
        a = args[i]
        if a in values:
            i += 2
            continue
        if a == "--":
            out += [(w, d) for w, d in zip(args[i + 1:], dargs[i + 1:]) if w != "-"]
            break
        if not a.startswith("-"):
            out.append((a, dargs[i]))
        i += 1
    return out or ([(default, False)] if default else [])


def _format_targets(name, args, dargs):
    """Ruling P19: the paths an in-place formatter/fixer rewrites (default
    ``.``), or [] when it only checks."""
    has = lambda *opts: any(a in opts or a.split("=", 1)[0] in opts for a in args)  # noqa: E731
    if name == "black":
        if has("--check", "--diff", "--version", "--help") or args == ["-"]:
            return []
        return _pos_skipping(args, dargs, ("-l", "--line-length", "-t", "--target-version", "--config", "--include",
                                           "--exclude", "--extend-exclude", "--force-exclude", "-W", "--workers",
                                           "--required-version", "--stdin-filename", "--python-cell-magics"), ".")
    if name == "ruff":
        sub = next((a for a in args if not a.startswith("-")), "")
        if (sub == "format" and not has("--check", "--diff")) or (sub in ("check", "") and has("--fix")
                                                                   and not has("--no-fix", "--diff")):
            k = args.index(sub) + 1 if sub else 0
            return _pos_skipping(args[k:], dargs[k:], ("--config", "--select", "--ignore", "--extend-select",
                                                       "--target-version", "--line-length", "--exclude",
                                                       "--extend-exclude", "--output-format", "--cache-dir",
                                                       "--stdin-filename", "--per-file-ignores"), ".")
        return []
    if name == "isort":
        if has("--check", "--check-only", "-c", "--diff", "--show-files", "--version"):
            return []
        return _pos_skipping(args, dargs, ("-l", "--line-length", "--profile", "--settings-path", "--sp", "-p",
                                           "--skip", "-s", "--src", "--filter-files"), ".")
    if name == "autopep8":
        return _pos_skipping(args, dargs, ("--max-line-length", "--select", "--ignore", "-j", "--jobs", "-a",
                                           "--global-config")) if has("-i", "--in-place") or any(
            re.fullmatch(r"-[a-z]*i[a-z]*", a) for a in args) else []
    if name == "prettier":
        if not has("--write", "-w"):
            return []
        return _pos_skipping(args, dargs, ("--config", "--ignore-path", "--plugin", "--parser", "--log-level",
                                           "--print-width", "--tab-width", "--trailing-comma", "--cache-location",
                                           "--end-of-line", "--stdin-filepath"), ".")
    if name == "eslint":
        if not has("--fix"):
            return []
        return _pos_skipping(args, dargs, ("-c", "--config", "--ext", "--rulesdir", "--ignore-path", "-f",
                                           "--format", "-o", "--output-file", "--parser", "--max-warnings",
                                           "--cache-location", "--rule", "--env", "--global", "--plugin",
                                           "--resolve-plugins-relative-to", "--fix-type"), ".")
    if name in ("gofmt", "goimports"):
        return _pos_skipping(args, dargs, ("-r", "-local")) if has("-w") else []
    if name in ("rustfmt", "stylua", "swiftformat"):
        if has("--check", "--lint", "--dryrun", "--version", "--help"):
            return []
        return _pos_skipping(args, dargs, ("--edition", "--config-path", "--config", "--emit", "-f",
                                           "--config-path", "--search-parent-directories", "--swiftversion",
                                           "--rules", "--disable", "--enable"), "." if name != "rustfmt" else None)
    if name == "clang-format":
        return _pos_skipping(args, dargs, ("--style", "-style", "--assume-filename", "--lines", "--offset",
                                           "--length")) if has("-i") else []
    if name == "shfmt":
        return _pos_skipping(args, dargs, ("-i", "--indent", "-ln", "--language-dialect", "-f")) \
            if has("-w", "--write") else []
    if name == "rubocop":
        if not (has("-a", "-A", "-x", "--autocorrect", "--auto-correct", "--autocorrect-all", "--auto-correct-all",
                    "--fix-layout") or any(re.fullmatch(r"-[a-zA-Z]*[aAx][a-zA-Z]*", a) for a in args)):
            return []
        return _pos_skipping(args, dargs, ("-c", "--config", "--only", "--except", "-f", "--format", "-o", "--out",
                                           "--require", "-r"), ".")
    if name == "terraform":
        k = next((i for i, a in enumerate(args) if a == "fmt"), None)
        if k is None or has("-check", "-write=false", "-list=true"):
            return []
        return _pos_skipping(args[k + 1:], dargs[k + 1:], default=".")
    if name == "go" and args[:1] == ["fmt"] or (name == "go" and "fmt" in args[:3] and args[:1] == ["-C"]):
        k = args.index("fmt")
        return [(re.sub(r"/\.\.\.$", "", w) or ".", d) for w, d in _pos_skipping(args[k + 1:], dargs[k + 1:],
                                                                                   default=".")]
    if name == "cargo" and "fmt" in args[:2]:
        if has("--check"):
            return []
        mp = [args[i + 1] for i, a in enumerate(args[:-1]) if a == "--manifest-path"]
        return [(posixpath.dirname(mp[0]) or ".", False)] if mp else [(".", False)]
    if name == "cargo" and "clippy" in args[:2] and has("--fix"):
        return [(".", False)]
    return []


def _build_dirs(name, args, dargs):
    """Ruling P19: folders a build is aimed at by a flag (it writes there)."""
    out = []

    def val(*opts):
        for i, a in enumerate(args):
            key, eq, v = a.partition("=")
            if key in opts and a.startswith("--"):
                if eq:
                    out.append((v, dargs[i]))
                elif i + 1 < len(args):
                    out.append((args[i + 1], dargs[i + 1]))
            elif a in opts and i + 1 < len(args):
                out.append((args[i + 1], dargs[i + 1]))
            elif not a.startswith("--") and len(a) > 2 and a[:2] in opts:
                out.append((a[2:], dargs[i]))

    if any(a in _DRY for a in args):
        return out
    sub, i = "", 0
    while i < len(args):  # the subcommand, past this function's own folder options
        if args[i] in ("-C", "-P", "--project", "--directory", "--manifest-path", "--prefix"):
            i += 2
            continue
        if not args[i].startswith("-"):
            sub = args[i].lower()
            break
        i += 1
    if name == "cmake":  # only the build folder is written (-S is read)
        val("-B", "--build")
    elif name == "cargo" and (sub not in _READONLY_SUBS["cargo"] or sub == "fmt"):
        for i, a in enumerate(args):
            if a == "--manifest-path" and i + 1 < len(args):
                out.append((posixpath.dirname(args[i + 1]) or ".", dargs[i + 1]))
            elif a.startswith("--manifest-path="):
                out.append((posixpath.dirname(a.split("=", 1)[1]) or ".", dargs[i]))
    elif name == "go" and sub in ("build", "generate", "install", "get", "mod", "fmt", "work"):
        val("-C")
    elif name in ("pip", "pip3") or (_BASE_TOOL.fullmatch(name) and name.startswith("pip")):
        if "install" in args:
            val("-e", "--editable")
    elif name == "uv" and sub not in _READONLY_SUBS["uv"]:
        val("--project", "--directory")
    elif name == "poetry" and sub not in _READONLY_SUBS["poetry"]:
        val("-C", "--directory", "-P", "--project")
    elif name == "tsc" and "--noEmit" not in args:
        start = len(out)
        val("-p", "--project")
        out[start:] = [(posixpath.dirname(w) or "." if w.endswith(".json") else w, d) for w, d in out[start:]]
    elif name == "terraform":
        for i, a in enumerate(args):
            if a.startswith("-chdir=") and "fmt" in args:
                out.append((a.split("=", 1)[1], dargs[i]))
    return out


def _known_writer(word, name, args):
    """Ruling P18/P19: commands that certainly write where they run -
    package manager install/add/ci/sync/update (bare yarn/bundle), JS
    ``build``/``run build``/``version``/``audit fix``, make/cmake/ninja/meson,
    cargo build/check/clippy/run/test, go build/generate/install, ``python
    setup.py``, ``python -m build``, bare ``tsc`` (no --noEmit),
    ./configure, autoreconf, docker/podman build, gradle/mvn, dotnet
    build/publish/restore, swift/bazel/zig build, mix deps.get/compile, git
    submodule update (not a dry run)."""
    cmd = _BASE_TOOL.sub(r"\1", name) if _BASE_TOOL.fullmatch(name) else name
    if any(a in _DRY for a in args):
        return False
    sub, rest = _subcommand_of(args)
    if cmd in ("make", "gmake", "cmake", "ninja", "autoreconf", "autogen.sh", "bootstrap"):
        return True
    if cmd == "cargo":
        return sub in ("build", "b", "check", "c", "clippy", "run", "r", "test", "t", "install", "bench", "fix",
                       "update", "generate-lockfile")
    if cmd == "go":
        return sub in ("build", "generate", "install", "get") or (sub == "mod" and rest[:1] in (["tidy"], ["vendor"]))
    if cmd in ("python", "py") and (any(posixpath.basename(a) == "setup.py" for a in args[:3]) or
                                    args[:2] == ["-m", "build"] or args[:3] == ["-m", "pip", "install"]):
        return True
    if cmd == "tsc":
        return "--noEmit" not in args and not any(a in ("--version", "-v", "--help", "-h") for a in args)
    if cmd == "meson":
        return sub in ("setup", "compile", "install", "configure") or (bool(sub) and sub not in ("help", "introspect"))
    if cmd == "dotnet":
        return sub in ("build", "publish", "restore", "pack", "test", "run", "new", "add", "tool")
    if cmd in ("swift", "zig", "bazel", "bazelisk"):
        return sub in ("build", "test", "run", "package") if cmd != "swift" else sub in ("build", "test", "run")
    if cmd == "mix":
        return sub in ("deps.get", "compile", "deps.compile", "deps.update", "release")
    if cmd == "git":
        return sub == "submodule" and bool(rest[:1]) and rest[0] in ("update", "add", "init", "sync", "deinit")
    if cmd in ("npm", "yarn", "pnpm", "bun"):
        if sub in ("build", "version", "pack", "publish", "rebuild") or (sub == "audit" and "fix" in rest):
            return True
        if sub in ("run", "run-script") and rest[:1] and "build" in rest[0]:
            return True
        if cmd == "yarn" and sub and "build" in sub:
            return True
    if posixpath.basename(word) == "configure":
        return True
    if posixpath.basename(word) == "configure":
        return True
    if cmd in ("docker", "podman"):
        return sub in ("build", "buildx") or (sub == "image" and rest[:1] == ["build"])
    if cmd in ("gradle", "gradlew", "mvn", "mvnw"):
        return sub not in ("help", "--version") and bool(args)
    if cmd in _READONLY_SUBS:
        return sub in _PM_WRITE_SUBS or (not sub and cmd in ("yarn", "bundle"))
    return False


def _is_writer(argv, holders=("{}",), appended=False):
    """The command (wrappers stripped) changes the files it is given
    (find -exec / fd -x / xargs): a deleter, a writer, or sed/perl -i.
    For a copy (cp/install/rsync/scp/ln -s) only the destination is
    written: the given paths (``holders`` such as ``{}``, or - appended -
    added after the last word) count only there (cp {} ./copy/ reads them)."""
    inner = _sh.expand_wrappers(list(argv)) or []
    if not inner:
        return False
    name = _sh.command_name(inner[0])
    copy = name in ("cp", "install", "rsync", "scp") or (name == "ln" and any(
        a in ("-s", "--symbolic") or re.fullmatch(r"-[a-zA-Z]*s[a-zA-Z]*", a) for a in inner[1:]))
    if copy:
        args = inner[1:]
        dest, _is_dir, srcs, flags = _copy_parts(name, args, [False] * len(args))
        if name == "cp" and "-l" in flags:
            return True  # hard links into the given files
        tdir = any(a in ("-t", "--target-directory") or a.startswith("--target-directory=") for a in args)
        if appended and not tdir:
            return True  # the given path becomes the last operand: the destination
        return dest is not None and any(h in dest[0] for h in holders)
    if name in _DELETERS or name in _RUN_WRITERS:
        return True
    return name in ("sed", "perl") and any(re.fullmatch(r"-[a-zA-Z]*i.*", a) or a.startswith("--in-place")
                                           for a in inner[1:])


def _exec_part(expr, j):
    """The words of a find -exec/-execdir/-ok command starting after index j."""
    part = []
    for w in expr[j + 1:]:
        if w in (";", "+"):
            break
        part.append(w)
    return part


def _xargs_holder(st):
    """(holders, appended) for the xargs/parallel in a stage's wrappers: -I R,
    -iR, --replace[=R] put the input where R is; else it is appended."""
    argv = list(st.argv)
    for i, a in enumerate(argv):
        name = _sh.command_name(a)
        if name not in ("xargs", "parallel"):
            continue
        rest = argv[i + 1:]
        if name == "parallel":
            return ("{}", "{/}", "{//}", "{.}", "{/.}", "{#}"), not any(h in w for w in rest for h in ("{",))
        for k, b in enumerate(rest):
            if not b.startswith("-"):
                break
            if b == "-I" and k + 1 < len(rest):
                return (rest[k + 1],), False
            if b.startswith("-I") and len(b) > 2:
                return (b[2:],), False
            if b in ("-i", "--replace"):
                return ("{}",), False
            if b.startswith("-i") and len(b) > 2:
                return (b[2:],), False
            if b.startswith("--replace="):
                return (b.split("=", 1)[1],), False
        return (), True
    return (), True


def _run_on_roots(ctx, roots, cwd, names, summary):
    """find -exec / fd -x with a writer: what the roots cover changes
    (readonly-source; P13 when a -name filter can reach the rules dir)."""
    for w, d in roots:
        for b in ctx.bases(cwd) if not w.startswith("/") else [cwd]:
            p = ctx.path(w, b, d)
            if p is None:
                continue
            ctx.readonly_delete(p, _SOME, summary)
            _rules_dir_find(ctx, p, names, summary)
            if _agent_config(p) or _agent_config(p + "/settings.json"):  # find .claude -exec sed -i ..
                ctx.hit("agent-config", False, summary)


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
    runs_writer = any(a in ("-exec", "-execdir", "-ok", "-okdir") and _is_writer(_exec_part(expr, j))
                      for j, a in enumerate(expr))
    if runs_writer:  # find ref -exec sed -i .. {}, find . -name rules.md -exec mv {} /tmp
        _run_on_roots(ctx, roots, cwd, names if only_names else None, summary)
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
    pos, cmd, i, filtered, exts, other = [], None, 0, False, [], False
    while i < len(args):
        a = args[i]
        key, eq, val = a.partition("=")
        if key in ("-e", "--extension", "-S", "--size", "--changed-within", "--changed-before", "-g", "--glob"):
            filtered = True
            if key in ("-e", "--extension") and (eq or i + 1 < len(args)):
                exts.append(val if eq else args[i + 1])
            else:
                other = True
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
    if _is_writer(inner, _FD_HOLDERS, appended=not any(h in w for w in inner for h in _FD_HOLDERS)):
        _run_on_roots(ctx, pos[1:] or [(".", False)], cwd, names_of_fd(exts, other, pos), summary)
    if _sh.command_name(inner[0]) in _DELETERS:
        roots = pos[1:] or [(".", False)]
        pattern = pos[0][0] if pos else ""
        loose = pattern in ("", ".", "^", ".*")
        names = ["*." + e.lstrip(".") for e in exts] if exts and not other and loose else None
        _classify(ctx, roots, cwd, summary, narrow=filtered or not loose, names=names)
    bash.nested_eval(" ".join(_quote(w) for w in inner), cwd)


def names_of_fd(exts, other, pos):
    pattern = pos[0][0] if pos else ""
    if exts and not other and pattern in ("", ".", "^", ".*"):
        return ["*." + e.lstrip(".") for e in exts]
    return None


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


def _extract_dest(name, args, dargs):
    """(folder, dyn) an archive extraction writes into, or None (not an extraction)."""
    if name in ("unzip", "7z", "7za"):
        if name != "unzip" and not (args and args[0] in ("x", "e")):
            return None
        if name == "unzip" and any(re.fullmatch(r"-[A-Za-z]*[ltvZz][A-Za-z]*", a) for a in args):
            return None  # lists or tests the archive
        for i, a in enumerate(args):
            if name == "unzip" and a == "-d" and i + 1 < len(args):
                return args[i + 1], dargs[i + 1]
            if name != "unzip" and a.startswith("-o") and len(a) > 2:
                return a[2:], dargs[i]
        return ".", False
    first = args[0] if args else ""
    extract = bool(re.fullmatch(r"-?[A-Za-z]*x[A-Za-z]*", first)) or any(
        a in ("--extract", "--get") or re.fullmatch(r"-[A-Za-z]*x[A-Za-z]*", a) for a in args)
    if not extract:
        return None
    dest = (".", False)
    for i, a in enumerate(args):
        if a in ("-C", "--directory") and i + 1 < len(args):
            dest = (args[i + 1], dargs[i + 1])
        elif a.startswith("--directory="):
            dest = (a.split("=", 1)[1], dargs[i])
    return dest


def _git_trees(ctx, args, dyn, cwd):
    """Folders ``--work-tree`` / ``--git-dir`` name (before the subcommand)."""
    out, i = [], 0
    while i < len(args) and args[i].startswith("-"):
        a = args[i]
        key, eq, val = a.partition("=")
        if key in ("--work-tree", "--git-dir"):
            vd = dyn[i] if eq else (dyn[i + 1] if i + 1 < len(dyn) else True)
            if not eq:
                val = args[i + 1] if i + 1 < len(args) else ""
                i += 1
            if val and not vd:
                out.append(ctx.path(val, cwd))
        elif a in ("-C", "-c", "--namespace", "--exec-path", "--config-env"):
            i += 1
        i += 1
    return [p for p in out if p]


_GIT_VALUE_OPTS = frozenset({"-b", "--branch", "-o", "--origin", "--depth", "-c", "--config", "--reference",
                             "--separate-git-dir", "-u", "--upload-pack", "--template", "-j", "--jobs", "--filter",
                             "--shallow-since", "--shallow-exclude", "--server-option", "--bundle-uri"})


def _git_pathspecs(rest):
    """The pathspec words of a git rm/mv/checkout/restore/clean."""
    if "--" in rest:
        return rest[rest.index("--") + 1:]
    return [a for a in rest if not a.startswith("-")]  # checkout: a branch name too (no root is named so)


def _git_dry(sub, rest):
    """A form that changes nothing: --dry-run, -n of add/rm/mv/clean, apply --check/--stat."""
    if "--dry-run" in rest:
        return True
    if sub in ("add", "rm", "mv", "clean") and any(re.fullmatch(r"-[a-zA-Z]*n[a-zA-Z]*", a) for a in rest):
        return True
    return sub == "apply" and "--apply" not in rest and bool(
        {"--check", "--stat", "--numstat", "--summary"} & set(rest))


def _git_readonly(ctx, argv, dyn, d, sub, rest, summary, bash):
    """readonly-source (and P13 for rm/mv) for one git command."""
    if _git_dry(sub, rest) or (sub == "stash" and rest[:1] and rest[0] in ("list", "show")):
        return
    bases = [d] if d is not None else ctx.bases(None)  # Ruling P15 (also -C with an unknown value)
    trees = _git_trees(ctx, argv[1:], dyn[1:], d)
    cached = sub == "rm" and "--cached" in rest  # the index only; the work tree is untouched
    specs = _git_pathspecs(rest) if sub in ("rm", "mv", "checkout", "restore", "clean") else []
    if sub == "checkout" and "--" in rest and not specs and not [a for a in rest if not a.startswith("-")]:
        return  # "git checkout --": nothing
    before = len(ctx.hits)
    if not cached:
        for folder in bases + trees:
            ctx.readonly_check(folder, summary)
    if sub == "apply":
        for a in rest:
            if a.startswith("--directory="):
                bash.check_write(a.split("=", 1)[1], False, "copydir", d)
    for base in bases:
        for w in specs:
            if _GLOB.search(w):  # a pathspec glob: "*" also matches "/"
                lit = _norm(posixpath.join(base, _GLOB.split(w, maxsplit=1)[0] or "."))
                if not cached and ctx.readonly:
                    for forms, shown, reason in ctx.readonly:
                        if any(f.startswith(lit.lower()) or _within(lit.lower(), f) for f in forms):
                            ctx.readonly_hit((shown, reason), summary)
                            break
                if sub in ("rm", "mv") and not ctx.remote:
                    pat = _norm(posixpath.join(base, w)).lower()
                    rd = ctx.rules_dir()
                    if any(fnmatch.fnmatchcase(c, pat) for c in (rd, rd + "/rules.md")):
                        ctx.hit("agent-config", False, summary)
                continue
            p = ctx.path(w, base, False, follow=False)
            if p is None:
                continue
            if not cached:
                ctx.readonly_check(p, summary, as_ancestor=True, follow=False)
            if sub in ("rm", "mv"):  # Ruling P13: git rm/mv of the rules file or a folder holding it
                _rules_dir_delete(ctx, p, None, summary)
    if d is None and sub != "add":  # git add changes the index, not the work tree
        ctx.unknown_cwd(before, summary)


def _git_check(ctx, argv, dyn, cwd, summary, bash):
    d, sub, rest, conf = _parse_git(ctx, argv[1:], dyn[1:], cwd)
    rule = None
    if sub in _GIT_CHANGES:  # readonly-source: a work-tree change run in (or on) a readonly root
        _git_readonly(ctx, argv, dyn, d, sub, rest, summary, bash)
    elif sub == "submodule" and rest[:1] and rest[0] in ("update", "add", "init", "sync", "deinit"):
        for folder in ([d] if d is not None else ctx.bases(None)):
            ctx.readonly_check(folder, summary)  # Ruling P19: it writes the work tree
    elif sub == "clone":  # git clone URL [DEST]: DEST is written
        pos, i = [], 0
        while i < len(rest):
            a = rest[i]
            if a in _GIT_VALUE_OPTS:
                i += 2
                continue
            if not a.startswith("-") or a == "-":
                pos.append(a)
            i += 1
        if pos:
            dest = pos[1] if len(pos) > 1 else re.sub(r"\.git$", "", pos[0].rstrip("/").rsplit("/", 1)[-1]
                                                      .rsplit(":", 1)[-1]) or "."
            bash.check_write(dest, False, "copydir", d)
    elif sub == "worktree" and rest[:1] == ["add"]:
        pos, i = [], 1
        while i < len(rest):
            if rest[i] in ("-b", "-B", "--reason"):
                i += 2
                continue
            if not rest[i].startswith("-"):
                pos.append(rest[i])
            i += 1
        if pos:
            bash.check_write(pos[0], False, "copydir", d)
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
# guest-quarantine (forge quarantined())
# ---------------------------------------------------------------------------
def _inside_repo(ctx, word, dynamic, cwd, folder=None):
    """forge insideRepo: the path is the project (or ``folder``) or inside
    it (real path, case-folded); a path we cannot resolve counts as inside
    (fail safe; Ruling P15: after an unknown cd, either the project or the
    start cwd)."""
    if not word or word.startswith("-"):
        return False
    project = folder or ctx.project_or_none()
    if project is None:
        return False
    if cwd is None and not word.startswith(("/", "~", "$", "`")):
        return any(_inside_repo(ctx, word, dynamic, b, folder) for b in ctx.bases(None))
    p = ctx.path(word, cwd, dynamic)
    return p is None or _within(p.lower(), project.lower())


def _subcommand_of(args):
    i = 0
    while i < len(args):
        if _VALUE_FLAGS.fullmatch(args[i]):
            i += 2
            continue
        if not args[i].startswith("-"):
            return args[i].lower(), args[i + 1:]
        i += 1
    return "", []


def _quarantined(ctx, st, word, name, args, dargs, cwd, from_pipe, folder=None):
    """forge quarantined(word, cmd, args, cwd, ctx, fromPipe), plus: a shell or
    interpreter reading a repo file on stdin (``sh < build.sh``) and any
    shell spelling co-guard knows (``bash5``) is held too."""
    cmd = _BASE_TOOL.sub(r"\1", name) if _BASE_TOOL.fullmatch(name) else name
    if args and all(_FLAGS_ONLY.fullmatch(a) for a in args):
        return False  # --version, --help
    if cmd in ("make", "gmake") and any(_MAKE_DRY.fullmatch(a) for a in args):
        return False  # make -n prints, runs nothing
    sub, rest = _subcommand_of(args)
    if cmd in _PM_ALWAYS:
        return True
    if cmd in ("docker", "podman", "docker-compose"):
        if cmd == "docker-compose":
            return True
        if sub == "image":
            return bool(re.fullmatch(r"build|load|import", rest[0] if rest else "", re.I))
        return bool(sub) and sub not in _DOCKER_READONLY
    if cmd in _READONLY_SUBS:
        if not sub:
            return cmd in ("yarn", "bundle", "gradle", "mvn")  # bare "yarn" installs
        if cmd in ("conda", "mamba") and sub == "env":
            return not re.fullmatch(r"list|export|config", rest[0] if rest else "", re.I)
        if sub == "audit" and "fix" in rest:
            return True  # npm audit fix installs
        return sub not in _READONLY_SUBS[cmd]
    stdin_files = [(t, "$" in t or "`" in t) for op, t in st.redirects if op == "<" and not t.startswith("<(")]

    def fed():  # "cat x.py | python", "sh < build.sh": runs what it is fed
        return from_pipe or any(_inside_repo(ctx, t, td, cwd, folder) for t, td in stdin_files)

    if cmd in ("java", "kotlin"):
        return "-jar" in args or any(a.lower().endswith(".jar") and _inside_repo(ctx, a, d, cwd, folder)
                                     for a, d in zip(args, dargs))
    if cmd in _INTERPRETERS:
        if folder and any(a in ("-c", "-e", "--eval") for a in args):
            return False  # readonly build check: inline code is checked as code
        if any(_INTERP_RUNS.fullmatch(a) for a in args):
            return True
        script = next(((a, d) for a, d in zip(args, dargs) if not a.startswith("-")), None)
        return _inside_repo(ctx, script[0], script[1], cwd, folder) if script else fed()
    if cmd in ("source", "."):
        script = next(((a, d) for a, d in zip(args, dargs) if not a.startswith("-")), None)
        return _inside_repo(ctx, script[0], script[1], cwd, folder) if script else fed()
    if cmd in _SCRIPT_SHELLS or _sh.shell_name(cmd):
        mode, path = _sh.shell_mode(args)
        if mode == "c":
            return False  # the -c text is checked as commands
        if mode == "file" and path:
            return _inside_repo(ctx, path, "$" in path or "`" in path, cwd, folder)
        return fed()
    if cmd in _PS_SHELLS:
        if any(_PS_INLINE.fullmatch(a) for a in args):
            return False
        i = next((k for k, a in enumerate(args) if re.fullmatch(r"-(f|file)", a, re.I)), -1)
        script = args[i + 1] if 0 <= i < len(args) - 1 else next((a for a in args if not a.startswith("-")), None)
        return _inside_repo(ctx, script, False, cwd, folder) if script else fed()
    # the repo's own executable run by path: ./x, scripts/x.sh, gradlew.bat
    if "/" in word or "\\" in word or _SCRIPT_EXT.fullmatch(word):
        return _inside_repo(ctx, word, "$" in word or "`" in word, cwd, folder)
    return False


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
        # Ruling P16: cwd is scoped - a ( ) subshell's cd does not leak; pushd/popd use a stack
        self.frames = []  # per open ( ): (cwd, dirstack, oldpwd) when it was entered
        self.dirstack = []
        self.oldpwd = None
        self.cur_cwd = None  # the cwd of the stage being checked

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
        saved = (self.frames, self.dirstack, self.oldpwd)  # a nesting level ($( ), bash -c) is its own shell
        self.frames, self.dirstack = [], list(self.dirstack)
        try:
            self._result(res, cwd, outer, pipelines)
        finally:
            self.res_stack.pop()
            self.frames, self.dirstack, self.oldpwd = saved

    def _result(self, res, cwd, outer, pipelines):
        for st in res:
            while len(self.frames) > st.plow:  # a ( ) closed: back to the cwd it was entered with
                cwd, self.dirstack, self.oldpwd = self.frames.pop()
            while len(self.frames) < st.sub:
                self.frames.append((cwd, list(self.dirstack), self.oldpwd))
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
            self.ctx.note_words(st.argv, argv)
            self.cur_cwd = cwd
            self.stage(st, argv, dyn, cwd, pipelines.get(st.pipeline, [st]), outer)
            # a cd in a pipeline runs in a subshell (bash; zsh runs the last stage here - a documented limit),
            # unless it opens a ( ) group there: then it applies to the group (restored when it closes)
            if len(pipelines.get(st.pipeline, [st])) == 1 or st.sub > st.plow:
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

    def cwd_at(self, depth, cwd):
        """The cwd at ( ) depth ``depth`` (a redirect after a group closed)."""
        return self.frames[depth][0] if depth < len(self.frames) else cwd

    def after_cd(self, argv, dyn, cwd):
        name = _sh.command_name(argv[0]) if argv else ""
        if name not in ("cd", "pushd", "chdir", "popd"):
            return cwd
        raw_args = argv[1:]
        args = [(a, d) for a, d in zip(argv[1:], dyn[1:]) if not (a.startswith("-") and len(a) > 1)]
        if name == "popd":
            if [a for a in raw_args if a != "-n"]:
                self.dirstack = []
                return None  # popd +N: we do not follow
            return self.dirstack.pop() if self.dirstack else None
        if name == "pushd":
            if not args:  # swap the top two
                if not self.dirstack:
                    return None
                top = self.dirstack.pop()
                self.dirstack.append(cwd)
                return top
            if re.fullmatch(r"[+-]\d+", args[0][0]) or "-n" in raw_args:
                self.dirstack = []
                return None
            self.dirstack.append(cwd)
            return self.ctx.path(args[0][0], cwd, args[0][1])
        old, self.oldpwd = self.oldpwd, cwd
        if not args:
            return self.ctx.home
        word, d = args[0]
        if word == "-":
            return old
        if d and _MKTEMP_DIR.fullmatch(word):  # cd "$(mktemp -d)": a fresh temp folder, no root
            return _real(posixpath.join(self.ctx.tmpdir or "/tmp", "co-guard-mktemp.XXXXXX"))
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
        self.readonly_write(word, p, kind, cwd)
        if p is None:
            self.unknown_write(word, d, kind, cwd)
            return None
        if kind == "move-src":  # Ruling P13: moving the rules file (or a folder holding it) away
            _rules_dir_delete(ctx, p, None, raw)
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

    def unknown_write(self, word, d, kind, cwd):
        """P13 for a path we could not resolve: a moved glob source
        (``mv .claude-orc* /tmp``), and (Ruling P15) a relative path after an
        unknown cd, tried against the project and the start cwd."""
        ctx = self.ctx
        if kind == "move-src":
            for path, glob in _target_kinds(ctx, word, d, cwd) or ():
                _rules_dir_delete(ctx, path, glob, self.raw)
        if cwd is None and not word.startswith(("/", "~", "$", "`")):
            for b in ctx.bases(None):
                p = ctx.path(word, b, d, follow=kind != "move-src")
                if p is None:
                    if kind == "move-src":
                        for path, glob in _target_kinds(ctx, word, d, b) or ():
                            _rules_dir_delete(ctx, path, glob, self.raw)
                elif kind == "move-src":
                    _rules_dir_delete(ctx, p, None, self.raw)
                elif _agent_config(p) or (kind == "copydir" and _agent_config(p + "/settings.json")):
                    ctx.hit("agent-config", False, self.raw)

    def readonly_write(self, word, p, kind, cwd, depth=0):
        """readonly-source for one changed path (every write kind; a moved
        source or a chmod -R target also as a parent of a root; a moved
        link is not followed). An unknown path counts by its braces, the
        words a ``for`` loop binds it to, its glob (``vendor/ol?/``) or its
        literal folder (``> ref/$f``) - unless it starts with an unknown
        variable - and (Ruling P15) after an unknown cd, a relative path is
        tried against the project and the start cwd."""
        ctx = self.ctx
        if not ctx.readonly or depth > 4:
            return
        anc = kind in ("move-src", "chmod-r")
        follow = kind != "move-src" or word.endswith(("/", "/."))
        if p is not None:
            ctx.readonly_check(p, self.raw, as_ancestor=anc, follow=follow)
            if not (_GLOB.search(word) or ("{" in word and _braces(word))):
                return  # else also as a pattern (a redirect target is not marked dynamic)
        if cwd is None and not word.startswith(("/", "~", "$", "`")):
            before = len(ctx.hits)
            for b in ctx.bases(None):
                self.readonly_write(word, ctx.path(word, b, True, follow), kind, b, depth + 1)
            ctx.unknown_cwd(before, self.raw)
            return
        alts = _braces(word) if "{" in word else None
        if alts:
            for w in alts:
                self.readonly_write(w, ctx.path(w, cwd, True, follow), kind, cwd, depth + 1)
            return
        words = self.bind([(word, True)])
        if words != [(word, True)]:
            for w, d in words:
                self.readonly_write(w, ctx.path(w, cwd, d, follow), kind, cwd, depth + 1)
            return
        e = ctx.expand(word, cwd)
        if not e or (word[:1] in ("$", "`") and cwd and e == posixpath.join(cwd, word)):
            return
        head = re.split(r"[$`]", e, maxsplit=1)[0]
        if _GLOB.search(head):  # ../Re?/x, vendor/*/f, ../[R]ef/z
            pat = _norm(head if head == e else head.rsplit("/", 1)[0] + "/*")
            ctx.readonly_glob(pat, self.raw, as_ancestor=anc)
            return
        p = _real(head if head.endswith("/") else (posixpath.dirname(head) or "/"))
        ctx.readonly_check(p, self.raw, as_ancestor=anc)

    def writes(self, st, argv, dyn, cwd, pipeline):
        stage_cwd = cwd
        for k, (op, t) in enumerate(st.redirects):
            # Ruling P16: "(cd ref && ls) > out.txt" - the redirect belongs to the group, in the outer cwd
            cwd = self.cwd_at(st.rdepth.get(k, st.sub), stage_cwd)
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
        cwd = stage_cwd
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
        elif name in ("tar", "bsdtar", "gtar", "unzip", "7z", "7za"):  # only readonly-source: extracting into a root
            dest = _extract_dest(name, args, dargs)
            if dest:
                self.readonly_write(dest[0], self.ctx.path(dest[0], cwd, dest[1]), "copydir", cwd)
        elif name == "mkdir":  # only readonly-source: a new folder is a change
            vals = {v for v, _d in _short_opt_value(args, dargs, "m", ("--mode", "--context"))}
            for a, d in pos:
                if a not in vals:
                    self.readonly_write(a, self.ctx.path(a, cwd, d), "mkdir", cwd)
        elif name in ("chmod", "chown", "chgrp", "chattr"):
            recursive = any(re.fullmatch(r"-[a-zA-Z]*R[a-zA-Z]*", a) or "--recursive" in _long(a, _CHMOD_LONG)
                            for a in args)
            for a, d in pos[1:]:
                self.check_write(a, d, "chmod-r" if recursive else "chmod", cwd)
        elif name in ("curl", "wget"):
            for a, d in _downloads(name, args, dargs):
                self.check_write(a, d, "fetch", cwd, _FETCHED)
        self.direct_writes(name, args, dargs, cwd)

    def pip_local(self, args, dargs, cwd):
        """``pip install DIR`` (not editable) builds that folder in place: a
        confirmable uncertain-command when it is in a root."""
        ctx = self.ctx
        if "install" not in args or any(a in ("-e", "--editable") or a.startswith("--editable=") for a in args):
            return
        k = args.index("install")
        for w, d in _pos_skipping(args[k + 1:], dargs[k + 1:], (
                "-r", "--requirement", "-c", "--constraint", "-t", "--target", "--prefix", "--root", "-i",
                "--index-url", "--extra-index-url", "-f", "--find-links", "--python-version", "--platform",
                "--src", "--upgrade-strategy", "--implementation", "--abi", "--only-binary", "--no-binary",
                "--progress-bar", "--log", "--cache-dir", "--trusted-host", "--proxy", "--timeout", "--retries")):
            if d or not ("/" in w or w in (".", "..")):
                continue
            p = ctx.path(w, cwd)
            root = p and ctx.readonly_root(p)
            if root:
                ctx.hits.append(Hit("uncertain-command", _PIP_LOCAL, False, _short(self.raw)))
                return

    def readonly_runs(self, st, argv, dyn, name, args, dargs, cwd):
        """A build/install/test/script run inside a root (quarantine's tables,
        the root as "the repo"; inline -c/-e code is checked as code).
        Ruling P18: a known writer (_known_writer) is a hard readonly-source
        hit; anything else is a confirmable uncertain-command."""
        ctx = self.ctx
        run, _rd = _unrun(argv, dyn)  # npx tsc, pnpm exec prettier ...
        rname, rargs = _sh.command_name(run[0]), run[1:]
        for b in ctx.bases(cwd):
            for forms, shown, reason in ctx.readonly:
                folder = next((f for f in sorted(forms) if _within(b.lower(), f)), None)
                if not folder:
                    continue
                if _known_writer(run[0], rname, rargs) or _known_writer(argv[0], name, args):
                    ctx.readonly_hit((shown, reason), self.raw)  # Ruling P18/P19: hard for known writers
                    return
                if _quarantined(ctx, st, argv[0], name, args, dargs, b, st.index > 0, folder=folder):
                    ctx.hits.append(Hit("uncertain-command", _RUNS_ROOT, False, _short(self.raw)))
                    return

    def xargs_writer(self, st, cwd, pipeline):
        """An xargs writer fed paths by ls/find/fd/echo/printf: readonly-source
        when they name a root, P13 when a find can reach the rules folder."""
        ctx = self.ctx
        for x in pipeline:
            if x.index >= st.index:
                continue
            a2 = _sh.expand_wrappers(x) or []
            if not a2:
                continue
            n2 = _sh.command_name(a2[0])
            d2 = list(x.dynamic[len(x.argv) - len(a2):]) + [False] * len(a2)
            words = [(w, d) for w, d in zip(a2[1:], d2[1:]) if not w.startswith("-")]
            if n2 == "find":
                k = next((i for i, w in enumerate(a2[1:]) if w.startswith("-") or w in ("(", "!")), len(a2) - 1)
                words = list(zip(a2[1:k + 1], d2[1:k + 1])) or [(".", False)]
            elif n2 in ("fd", "fdfind"):
                words = words[1:] or [(".", False)]
            elif n2 == "ls":
                words = words or [(".", False)]
            elif n2 not in ("echo", "printf", "realpath", "readlink"):
                continue
            expr = a2[1:]
            names = [expr[k + 1] for k, a in enumerate(expr) if a in ("-name", "-iname") and k + 1 < len(expr)]
            only = names and not any(a in _FIND_NARROW and a not in ("-name", "-iname") for a in expr)
            for w, d in words:
                p = ctx.path(w, cwd, d)
                if p is not None and n2 in ("find", "fd", "fdfind", "ls"):
                    ctx.readonly_delete(p, _SOME, self.raw)  # what it lists, inside p
                    if n2 == "find":
                        _rules_dir_find(ctx, p, names if only else None, self.raw)
                else:
                    self.readonly_write(w, p, "edit", cwd)

    def direct_writes(self, name, args, dargs, cwd):
        """Other common writers of the files they name (all write rules:
        readonly-source, agent-config, self-protect, secret-files ...)."""
        ctx = self.ctx
        pos = [(a, d) for a, d in zip(args, dargs) if not a.startswith("-")]

        def opt(longs, letters=""):  # values of --x V / --x=V (also -out V) / -X V / -XV
            out = []
            for i, a in enumerate(args):
                key, eq, val = a.partition("=")
                if key in longs:
                    if eq:
                        out.append((val, dargs[i]))
                    elif i + 1 < len(args):
                        out.append((args[i + 1], dargs[i + 1]))
                elif letters and len(a) >= 2 and a[0] == "-" and a[1] != "-" and a[1] in letters:
                    if len(a) > 2:
                        out.append((a[2:], dargs[i]))
                    elif i + 1 < len(args):
                        out.append((args[i + 1], dargs[i + 1]))
            return out

        def values_skipped(value_short, value_long=()):
            """Positionals with option values (-X V, --x V) left out."""
            out, i = [], 0
            while i < len(args):
                a = args[i]
                if a in value_short or a in value_long:
                    i += 2
                    continue
                if a == "--":
                    out += list(zip(args[i + 1:], dargs[i + 1:]))
                    break
                if not a.startswith("-") or a == "-":
                    out.append((a, dargs[i]))
                i += 1
            return out

        if name in _PACKERS:  # gzip f replaces f with f.gz; -c / -l / -t write only stdout or nothing
            if not any(re.fullmatch(r"-[a-zA-Z0-9]*[clt][a-zA-Z0-9]*", a) or a in ("--stdout", "--to-stdout",
                                                                                  "--list", "--test")
                       for a in args):
                for a, d in values_skipped(("-o", "-S", "-T", "-b"), ("--suffix", "--output")):
                    self.check_write(a, d, "edit", cwd)
            for a, d in opt(("--output",), "o"):
                self.check_write(a, d, "redirect", cwd)
        elif name == "sort":
            for a, d in opt(("--output",), "o"):
                self.check_write(a, d, "redirect", cwd)
        elif name == "split":
            rest = values_skipped(("-a", "-b", "-C", "-l", "-n", "-t"), ("--additional-suffix", "--filter"))
            self.check_write(rest[1][0] if len(rest) > 1 else "x", rest[1][1] if len(rest) > 1 else False,
                             "redirect", cwd)
        elif name in ("tar", "bsdtar", "gtar"):
            first = args[0] if args else ""
            create = bool(re.fullmatch(r"-?[A-Za-z]*[cruA][A-Za-z]*", first)) or any(
                a in ("--create", "--append", "--update", "--concatenate") or
                re.fullmatch(r"-[A-Za-z]*[cruA][A-Za-z]*", a) for a in args)
            if create and not re.fullmatch(r"-?[A-Za-z]*[xt][A-Za-z]*", first):
                for i, a in enumerate(args):
                    if a.startswith("--file="):
                        self.check_write(a.split("=", 1)[1], dargs[i], "redirect", cwd)
                    elif (re.fullmatch(r"-[A-Za-z]*f", a) or (i == 0 and re.fullmatch(r"[A-Za-z]*f", a))) and \
                            i + 1 < len(args) and args[i + 1] != "-":
                        self.check_write(args[i + 1], dargs[i + 1], "redirect", cwd)
        elif name == "zip":
            rest = values_skipped(("-b", "-n", "-t", "-tt", "-P", "-Z", "-O", "--out"), ())
            if rest and not any(a in ("-sf", "--show-files") for a in args):
                self.check_write(rest[0][0], rest[0][1], "redirect", cwd)
            for a, d in opt(("--out",), "O"):
                self.check_write(a, d, "redirect", cwd)
        elif name == "patch":
            if "--dry-run" in args or "-C" in args:
                return
            dirs = opt(("--directory",), "d")
            outs = opt(("--output",), "o")
            for a, d in dirs:
                self.check_write(a, d, "copydir", cwd)
            for a, d in outs:
                self.check_write(a, d, "redirect", cwd)
            rest = values_skipped(("-d", "-o", "-i", "-p", "-r", "-B", "-z", "-F", "-D", "-V", "-Y"),
                                  ("--directory", "--output", "--input", "--strip", "--reject-file", "--prefix",
                                   "--suffix", "--fuzz", "--ifdef"))
            if rest:
                self.check_write(rest[0][0], rest[0][1], "edit", cwd)
            elif not dirs and not outs:
                self.readonly_write(".", ctx.path(".", cwd), "copydir", cwd)  # the files the diff names
        elif name in ("pip", "pip3") or (_BASE_TOOL.fullmatch(name) and name.startswith("pip")):
            if any(a in ("install", "download", "wheel") for a in args):
                for a, d in opt(("--target", "--prefix", "--root", "--dest", "--wheel-dir"), "td"):
                    self.check_write(a, d, "copydir", cwd)
        elif name in _READONLY_SUBS and name in ("npm", "pnpm", "yarn", "bun"):
            sub, _rest = _subcommand_of(args)
            if not sub or sub not in _READONLY_SUBS[name]:
                for a, d in opt(("--prefix", "--dir", "--cwd"), "C"):
                    self.readonly_write(a, ctx.path(a, cwd, d), "copydir", cwd)
        elif name in ("make", "gmake"):
            if not any(_MAKE_DRY.fullmatch(a) for a in args):
                for a, d in opt(("--directory",), "C"):
                    self.readonly_write(a, ctx.path(a, cwd, d), "copydir", cwd)
                for a, d in opt(("--file", "--makefile"), "f"):
                    folder = posixpath.dirname(a) or "."
                    self.readonly_write(folder, ctx.path(folder, cwd, d), "copydir", cwd)
        elif name == "openssl":
            for a, d in opt(("-out", "-keyout")):
                self.check_write(a, d, "redirect", cwd)
        elif name == "ssh-keygen":
            if not any(re.fullmatch(r"-[a-zA-Z]*[lyFBLQ][a-zA-Z]*", a) for a in args):
                for i, a in enumerate(args[:-1]):
                    if a == "-f":
                        self.check_write(args[i + 1], dargs[i + 1], "redirect", cwd)
                        self.check_write(args[i + 1] + ".pub", dargs[i + 1], "redirect", cwd)
        elif name == "setfacl":
            rec = any(re.fullmatch(r"-[a-zA-Z]*R[a-zA-Z]*", a) or a == "--recursive" for a in args)
            for a, d in values_skipped(("-m", "-M", "-x", "-X", "--set", "--set-file", "--modify", "--modify-file",
                                        "--remove", "--remove-file")):
                if "=" not in a or not a.startswith("--"):
                    self.check_write(a, d, "chmod-r" if rec else "chmod", cwd)
        elif name == "xattr":
            flags = "".join(a[1:] for a in args if re.fullmatch(r"-[a-zA-Z]+", a))
            skip = 2 if "w" in flags else (1 if "d" in flags else (0 if "c" in flags else None))
            if skip is not None:
                for a, d in pos[skip:]:
                    self.check_write(a, d, "chmod-r" if "r" in flags else "chmod", cwd)
        elif name in ("rename", "prename", "perl-rename", "file-rename"):
            if not any(a in ("-n", "--no-act", "--nono", "--dry-run") for a in args):
                files = pos[1:] if pos and re.match(r"(s|y|tr)\W", pos[0][0]) else pos[2:]
                for a, d in files:
                    self.check_write(a, d, "move-src", cwd)
        elif name in _VIM or name in ("emacs", "ed", "red"):
            batch = name in ("ex", "ed", "red") or any(
                a in ("-c", "--cmd", "-s", "-S", "-e", "-E", "-es", "-Es", "--batch", "-batch", "--eval", "-l",
                      "--load", "-f", "--funcall", "--script", "-x") or a.startswith(("+", "--eval="))
                for a in args)
            if batch:
                vals = ("-c", "--cmd", "-S", "-u", "-U", "-i", "-T", "-w", "-W", "-t", "-q", "--eval", "-l", "--load",
                        "-f", "--funcall", "--script")
                if name in _VIM and name != "ex" and not any(a in ("-e", "-E", "-es", "-Es") for a in args):
                    vals += ("-s",)  # vim -s scriptin (in ex mode -s is "silent")
                for a, d in values_skipped(vals):
                    if not a.startswith("+"):
                        self.check_write(a, d, "edit", cwd)

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
        if name == "mv" or (name == "rsync" and "--remove-source-files" in flags):
            for s, d in srcs:  # rsync --remove-source-files deletes what it copied: a move
                self.check_write(s, d, "move-src", cwd)
        if (name == "ln" and not flags & {"-s", "--symbolic"}) or (name == "cp" and "-l" in flags):
            for s, d in srcs:  # a hard link to a file in a root: writing through it changes the root
                self.readonly_write(s, ctx.path(s, cwd, d), "link", cwd)
        if name == "rsync" and "--delete" in flags and dest:
            _classify(ctx, [(dest[0] if dest[0].endswith("/") else dest[0] + "/", dest[1])], cwd, raw)
        if name == "install" and _mode_option(args) and _mode_wide(_mode_option(args)):
            ctx.hit("chmod-wide", False, raw)
        if name == "install" and "-d" in flags:  # install -d DIR...: creates them
            for s, d in srcs:
                self.check_write(s, d, "copydir", cwd)
        if dest is None:
            return
        dword, dd = dest
        dpath = ctx.path(dword, cwd, dd)
        if dd and ctx.guard_var_unknown(dword):
            ctx.hit("self-protect", True, raw)
        if dpath is None:
            self.readonly_write(dword, None, "copy", cwd)  # cp x vendor/ol?/, cp x {../Ref,/tmp}/
            self.unknown_write(dword, dd, "copy", cwd)
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

        # guest-quarantine: the reviewed repo's install/build/test/run commands (every stage)
        lookup = bool(st.argv[:1]) and _sh.command_name(st.argv[0]) == "command" and any(
            re.fullmatch(r"-[pvV]*[vV][pvV]*", a) for a in st.argv[1:3])  # command -v make: a lookup
        if ctx.quarantine and not lookup and _quarantined(ctx, st, argv[0], name, args, dargs, cwd, st.index > 0):
            ctx.hit("guest-quarantine", False, raw)

        if ctx.readonly and not ctx.remote and not lookup:
            self.readonly_runs(st, argv, dyn, name, args, dargs, cwd)
            run, rdyn = _unrun(argv, dyn)
            rname, rargs, rdargs = _sh.command_name(run[0]), run[1:], rdyn[1:]
            for w, d in _format_targets(rname, rargs, rdargs):  # Ruling P19: like chmod -R targets
                if _GLOB.search(w):  # "**/*.js" (quoted too): the formatter expands it - its literal folder
                    head = _GLOB.split(w, maxsplit=1)[0]
                    w, d = (head if head.endswith("/") else (posixpath.dirname(head) or ".")), False
                self.readonly_write(w, ctx.path(w, cwd, d), "chmod-r", cwd)
            if rname in ("pip", "pip3") or (_BASE_TOOL.fullmatch(rname) and rname.startswith("pip")):
                self.pip_local(rargs, rdargs, cwd)
            elif _interp_kind(rname) == "python" and rargs[:2] == ["-m", "pip"]:
                self.pip_local(rargs[2:], rdargs[2:], cwd)
            for w, d in _build_dirs(rname, rargs, rdargs):  # builds aimed at a folder by a flag
                self.readonly_write(w, ctx.path(w, cwd, d), "copydir", cwd)
        if ("xargs" in st.wrappers or "parallel" in st.wrappers) and not ctx.remote and \
                _is_writer(argv, *_xargs_holder(st)):
            self.xargs_writer(st, cwd, pipeline)

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
                    kinds = _target_kinds(ctx, word, d, cwd)
                    for path, g in kinds or ():
                        if ctx.is_protected(path):
                            ctx.hit("self-protect", True, raw)
                        _rules_dir_delete(ctx, path, g, raw)
                        ctx.readonly_delete(path, g, raw)
                    if kinds is None and cwd is None:  # Ruling P15
                        before = len(ctx.hits)
                        for b in ctx.bases(None):
                            for path, g in _target_kinds(ctx, word, d, b) or ():
                                _rules_dir_delete(ctx, path, g, raw)
                                ctx.readonly_delete(path, g, raw)
                        if not word.startswith(("/", "~", "$", "`")):
                            ctx.unknown_cwd(before, raw)
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
        if ctx.readonly and not ctx.remote and _WRITES_TEXT.search(code):
            # code that names a root (as written, normalised or real) and may write, or runs inside one
            for forms, shown, reason in ctx.readonly:
                names = set(forms) | {shown.lower(), _norm(shown).lower()}
                if any(n not in ("", "/", ".") and re.search(re.escape(n) + r"(?![\w.-])", low) for n in names):
                    ctx.readonly_hit((shown, reason), raw)
                    break
            else:
                root = ctx.readonly_cwd(self.cur_cwd)
                if root:
                    ctx.readonly_hit(root, raw)

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
    """Most restrictive action among the matching ``mcp_tools`` entries; when
    none matches, the same over ``mcp_defaults`` (server-wide defaults); an
    unknown action or a malformed table means require_approval; no entry in
    either means allow."""
    act = _mcp_table_action(ctx.mcp_tools, tool_name)
    if act is None:
        act = _mcp_table_action(ctx.mcp_defaults, tool_name)
    return act


def _mcp_table_action(tools, tool_name):
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
    ctx.readonly_check(p, raw)
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


def _rule_texts(ctx, tool_name, tool_input, cwd):
    """What custom rules match (Ruling P12; a rule hits when ANY text matches):
    Bash/Monitor - the command with backslash-newlines removed and whitespace
    collapsed, plus the de-quoted argv of every simple command (nested and
    unwrapped ones included, collected while the Bash rules ran); file tools -
    ``<tool> <path>`` for the raw, the normalised absolute and the real path;
    others - ``<tool> <compact sorted JSON>`` and forge's newline-joined values
    (as they are, and with whitespace collapsed inside each value)."""
    if tool_name in _BASH_TOOLS:
        command = tool_input.get("command")
        command = str(command if command is not None else "")
        return [" ".join(command.replace("\\\n", "").split())] + list(ctx.words)
    if tool_name in _FILE_TOOLS:
        raw = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        texts = [f"{tool_name} {raw}"]
        if raw:
            ab = ctx.expand(raw, cwd)
            if ab:
                texts.append(f"{tool_name} {_norm(ab)}")
                texts.append(f"{tool_name} {_real(ab)}")
        return texts
    try:
        text = json.dumps(tool_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    except (TypeError, ValueError, RecursionError):
        text = "<input>"
    return [f"{tool_name} {text}", f"{tool_name}\n{_cu.values_text(tool_input)}",
            f"{tool_name}\n{_cu.values_text(tool_input, collapse=True)}"]


def _custom(ctx, tool_name, tool_input, cwd):
    """Org/project/repo rules (custom.py); they apply even with the guard off."""
    if not ctx.custom:
        return
    texts = _rule_texts(ctx, tool_name, tool_input, cwd)
    summary = _short(texts[0])
    for rule, how in _cu.match_rules(texts, ctx.custom, ctx.regex_budget_ms):
        rid = rule["id"]
        never = rule["kind"] == "never"
        if how == "timeout":  # never a pass (Ruling P11: only a repo never rule becomes confirmable)
            if never and not _cu.is_repo_rule(rule):
                ctx.hits.append(Hit(rid, f"rule {rid} took too long to check, so it is blocked", True, summary))
            else:
                ctx.hits.append(Hit(rid, f"rule {rid} took too long to check, so it needs approval", False,
                                    summary))
            continue
        reason = (_cu.clean_reason(rule.get("reason"))
                  or f'this matches the rule "{_cu.clean_reason(rule["pattern"])}"')
        ctx.hits.append(Hit(rid, reason, never, summary))


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
    ctx.start = start
    for key in ctx.policy_errors:
        ctx.hits.append(Hit("unparseable", f"{ERROR_REASON} (the task policy's {key} is malformed)", True,
                            _short(tool_name)))
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
    if isinstance(tool_input, dict):
        _custom(ctx, tool_name, tool_input, start)
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
