# Forge port — Phase 2: Approvals and policy — design

Program: port every jspark-forge feature into the orchestrator. Inventory and phasing:
`docs/research/jspark-forge-feature-inventory.md` (§1A F4–F10, §1C F32/F33/F37/F39, §2, §4.4–§4.5,
§5 Phase 2). Phase 1 (co-guard plugin, built-in block list, live inline action approvals) is merged
(PR #10, 6e93ee7): `docs/superpowers/specs/2026-11-21-forge-guard-phase1-design.md`. Its ledger rulings
R1–R29 (`.superpowers/sdd/2026-11-21-forge-guard-phase1/progress.md` in the guard worktree) are binding
precedent here. This doc is Phase 2.

## Intent
Phase 1 made co-guard block a fixed list of dangerous commands and made a person approve each
confirmable call, once, live. Phase 2 makes that policy **the org's own**. People can add rules, mark
databases dev or production, and say how far a project's repo is trusted. An approval can cover the
rest of a thread, and an unattended run that nobody answers can resume once someone approves. The
database guard (forge F32/F33) and read-only sources (F37) arrive with it.

## Decisions (owner: proceed autonomously; these are designer's calls)
- **Custom rules (F10).** Table `ActionRule`: scope `org` | `project`; kind `confirm` (needs approval)
  | `never` (hard deny); match `text` (case-insensitive substring) | `regex`; pattern; reason;
  enabled; created_by/at. Admins edit org rules. Maintainers (and admins) edit their project's rules.
  Rule ids are `org.<slug>`, `project.<slug>` and `repo.<slug>`. Built-in ids never contain a dot, so
  custom ids cannot collide with them (a test asserts this). The optional repo file
  `.claude-orchestrator/rules.md` (forge's `## Confirm` / `## Never` lines: `` - `text` : reason ``
  or `` - `re:regex` : reason ``) is read **add-only**, at task start, only for projects with trust
  `own`. Its other sections and any `disable:` / `database dev:` lines are ignored and reported with
  line numbers. Regexes are validated server-side when saved: they must compile with Python `re`,
  are at most 500 chars, and a compile error rejects the save. Repo regexes are validated by the
  worker in the same way, and bad lines are skipped and reported. In the hook, every custom regex
  runs under **one shared SIGALRM budget of 250 ms per tool call**. A rule still running when the
  budget ends counts as a `confirm` hit (also for `never` rules, as in forge). This is verified on
  the server's CPython: `re` checks signals during matching, so `(a+)+$` on `"a"*40+"b"` was stopped
  at 0.26 s. Custom rules apply even when the project's guard is off, because they are explicit
  policy.
- **Text matched by custom rules.** For Bash and Monitor: the command with whitespace collapsed. For
  file tools: `<tool> <file_path>` (the path only, not the content). For MCP and other tools:
  `<tool> <compact sorted JSON input>`. Quoted data is matched too, because a substring rule is the
  author's choice, as in forge. Reasons are stripped of control, zero-width and bidi characters and
  cut to 200 chars, since the agent reads them in deny messages.
- **Org guard settings (admin).** Singleton `GuardOrgSettings`: `disabled_rules` and
  `dev_db_patterns`. A built-in can be disabled only if it is in `DISABLEABLE`: `rm-rf-outside` (its
  confirmable part only; home/root stays hard), `git-force-push-main`, `git-reset-hard`,
  `git-clean-fdx`, `git-stash-drop`, `secret-files`, `chmod-wide`, `pipe-to-shell`, `sql-destroy`,
  `db-write`. The rules that keep co-guard's own reach can never be disabled: `self-protect`,
  `agent-config`, `git-internals`, `unparseable`, `uncertain-command`, `unsupported-shell`,
  `unknown-tool`, `mcp-*`, `readonly-source` and `guest-quarantine`. The server rejects them with a
  400, and `rules.py` ignores them if they reach a policy anyway. Dev DB patterns use forge's
  syntax: `host:<glob>`, `mcp:<server>` or `mcp:<prefix>*`, or a bare exact host or server name. A
  bare name with `*` is rejected.
- **MCP.** `McpServer.environment` is `dev` | `prod` | `unknown`. The default is `unknown`, which is
  treated as prod for `db-write`. `McpServer.default_action` (R14 follow-up) is the server-wide
  action for tools with no policy row: `auto_approve` | `require_approval` | `block`. Existing
  servers get `auto_approve`, which is today's behaviour. New servers get `require_approval`. An
  explicit tool policy always beats the server default. Both fields are admin-only, because MCP
  writes already are and marking a database dev relaxes the guard. The "Action policy" UI has three
  places:
  - Settings → Agent guard (a new admin page): org rules, disabled built-ins, dev DB patterns.
  - The project Guard tab: project rules, trust, quarantine, no-answer mode, readonly paths,
    reference projects, and the repo rules file status.
  - The MCP Servers view: environment and default action.
- **Database rules (F32/F33), ported from forge `blocklist.js` (MIT, same owner).**
  - `sql-destroy` (confirmable) fires on DROP TABLE/DATABASE/SCHEMA, TRUNCATE, or DELETE without
    WHERE. It checks the SQL a client actually receives, after `sql_strip` blanks string literals,
    quoted identifiers and comments. That SQL comes from `-c`/`-e`/`--query`/`--eval`, positional SQL
    (sqlite3/duckdb), an `echo`/`printf` literal piped in, or a heredoc body with no `$` or backtick
    in it. `sql-destroy` ignores the target, so it fires on local databases too.
  - `db-write` (confirmable) fires on any non-read statement against a target that is neither local
    nor dev. Forge's read list is used: SELECT/WITH/VALUES/TABLE without write words or side-effect
    functions, plain EXPLAIN, SHOW/DESCRIBE/…, read PRAGMAs, `COPY … TO STDOUT`.
  - Clients covered: psql/pgcli/usql, pg_restore -d, mysql/mariadb/mycli/mysqlsh, sqlite3/sqlite
    (file databases are local, so never `db-write`), sqlcmd, clickhouse(-client), duckdb (`md:` is
    remote), cockroach, mongosh/mongo write methods, redis-cli/valkey-cli non-read commands.
  - Also covered: `prisma db push|db execute|migrate deploy|migrate resolve|migrate reset`,
    `supabase db push|migration repair` (remote unless `--local`), and `supabase db reset|migration
    up` with `--linked` or a remote `--db-url`. These are matched directly or through
    npx/pnpx/bunx/pnpm/yarn/npm/bun (`dlx`/`exec`).
  - Targets are read from flags, URLs, conninfo, `PG*`/`MYSQL_HOST`/`DATABASE_URL`-style prefixes,
    and earlier `export`s in the same line. Local means localhost, 127.x, ::1, 0.0.0.0, `*.localhost`,
    a Unix socket (not `/cloudsql/`) or a file. A variable, `service=` or `--login-path` is unknown,
    which means production.
  - Unknown SQL (a script file, a stdin redirect, or a pipe from a non-literal stage) is a `db-write`
    unless the target is local or dev.
  - DB MCP servers: a server counts as a database when its `environment` is set explicitly (`dev` or
    `prod`), or when its name matches forge's `DB_SERVER` pattern. `db-write` follows forge
    `mcpDbWrite`: write verbs in the tool name, `create`+object, `$out`/`$merge`, or run-words whose
    SQL fields (`sql`, `query`, `statement`, `command`, …) do not read. It is skipped for `dev`
    servers and `mcp:` dev patterns. `sql-destroy` on MCP checks only those SQL fields, on database
    servers, in every environment.
  - When both rules fire, `sql-destroy` suppresses the duplicate `db-write`.
  - Both rules are built-ins, so a project's guard switch and the org's disabled list apply to them.
- **Trust and quarantine (F4–F7).** `Project.trust` is `own` | `external`. All existing projects
  become `own`. No import from a git URL exists today, so a newly created or imported project gets
  forge's heuristic, generalised to the org: the directory has a git remote, at least one org member
  has a `git_email`, and no commit in `git log --all` (5 s timeout) is by any of those emails. Then
  the project is external. A git failure after a remote is known also means external. If no member
  email is known, the project stays `own` and the UI says the trust was not checked. Maintainers can
  change trust. For `external`:
  - the worker appends forge's data line through `--append-system-prompt`;
  - co-guard's SessionStart hook re-adds it as `additionalContext` (spike d: this reaches the model,
    also after compaction);
  - the repo rules file is not read.

  `Project.quarantine` is a bool. It defaults to false, and to true for new projects that come out
  external. When it is on, the repo's install/build/test/run commands need approval under
  `guest-quarantine`. Forge's tables are ported as they are: `READONLY_SUBS` per package manager,
  `PM_ALWAYS`, docker read-only subcommands, interpreters and shells running a script inside the
  repo (also through a pipe), and the repo's own executables run by path. `--version`/`--help` and
  `make -n` are never held. Quarantine applies even when the project's guard is off.
- **Grants with scope (F39).** Cards get **Approve once** (today's behaviour) and **Approve for this
  run**. "This run" means this thread: the same `Task.root_id` (or the task's own id if it has
  none), for 12 h. A run grant is stored as one `ActionGrant` row per rule id, and its key depends
  on the rule:
  - `guest-quarantine`: the rule only (`key_kind` `rule`, key `*`);
  - `mcp-require-approval` or `db-write` on an `mcp__*` tool: the tool name (`tool`);
  - every other rule, including `sql-destroy`, custom rules, `unknown-tool` and
    `uncertain-command`: the call's `grant_key` (`command`).

  A call is covered only if **every** confirmable hit is covered by a live grant for that rule. A
  `db-write` run grant therefore never covers a `sql-destroy`, and a quarantine grant never covers
  the other rule hits in the same command. Hard hits are never filed, so they are never grantable.

  `grant_key` is computed by the hook. For Bash/Monitor it is `sha256("shell\0" + command)`, because
  the tool input's `description` changes between calls. For every other tool it is `sha256(tool +
  "\0" + sorted-JSON input)`.

  Grants reach the hook in two ways:
  - The worker writes the live grants into the policy file at task start, and the hook checks them
    locally with no network.
  - Within a running task, `POST /guard/approvals` answers at once with `status: "approved"` and
    records an auto-approved row when a live run grant of the thread covers the call. This is how a
    grant made during the run reaches the same task.

  Revoking a grant stops server answers and later tasks at once. A running task keeps its
  policy-file copy until it ends, and the UI says so.
- **Deny-and-resume (F39, unattended runs).** The project setting `on_no_answer` is `deny` (the
  default, Phase 1 behaviour) or `deny_and_resume`. With `deny_and_resume`, the filed request gets
  `resume_until = created_at + 24 h`. When the inline wait ends with no decision (the hook's
  deadline, the hook's give-up report, or the task stops running), the request stays `pending` in
  the **resume** phase instead of expiring. The hook tells the agent: "request <id> was filed and is
  waiting for a person; do not try an equivalent command; end your turn now, saying what is blocked
  and why you need it."
  - If someone approves in the resume phase, the server creates a one-shot `ActionGrant` (scope
    `once`, bound to the new task, keyed by `grant_key`) and a **follow-up task** with the existing
    follow-up mechanism. The follow-up resumes the thread's latest finished session, runs on the
    thread owner's credentials, and has the prompt "co-guard: a person approved request <id>. You may
    now run this once, exactly as before: <summary>. Then continue the task."
  - If a step of the thread is still active, the follow-up is created when that step finishes.
    Approvals that land while a follow-up is still queued join that follow-up: one more grant, and
    one more line in its prompt.
  - Rejecting in the resume phase only records the decision.
  - A pending request whose task was **cancelled** expires.
  - Decisions inside the last `DECIDE_MARGIN` (5 s) of the inline wait get 409 "the agent is giving
    up on this request; try again in a few seconds". This keeps a decision from landing on both
    phases.
- **Readonly paths and reference projects (F37).** `Project.readonly_paths` is a list of `{path,
  reason}`. A path is relative to the project directory or absolute; `/` and ancestors of home are
  rejected. `Project.reference_project_ids` lists other registered projects that the editor can see.
  At run time the worker keeps only those the task's owner can see, mounts them with `--add-dir`,
  and adds their directories to the readonly list. Any change under a readonly root is a **hard**
  deny under `readonly-source`. "Change" means:
  - Edit, Write, MultiEdit or NotebookEdit;
  - every Phase 1 write target: redirects, tee, cp/ln/rsync destinations, mv on both ends, touch,
    mkdir, `sed -i`, chmod;
  - deletes, including deleting an ancestor of a readonly root;
  - git work-tree changing subcommands whose directory (`-C` or the tracked cwd) is under a root.

  Reads stay free. `readonly-source` applies even when the project's guard is off.
- **classify_risk (task-level risk).** Its behaviour is kept. Per-action guards now cover force-push
  to main, deletes outside the project, `reset --hard` and database writes. Normal pushes and merges
  are still gated only by the task-level keyword check, so removing that check is not yet a safe
  simplification. The function gets a docstring and the README gets a note; Phase 5 (launch scope)
  revisits it.
- **Agent-token allowlist unchanged.** Grants travel in the policy file and in the answer to the
  existing `POST /guard/approvals`. Grant use is reported through the existing `POST /guard/report`,
  which gains a `granted` record. Every new route is for people only.

## Goals (Phase 2 scope)
1. `sql-destroy` and `db-write` for SQL/NoSQL clients, ORM/migration CLIs and database MCP servers,
   with dev patterns and MCP environments. At least 5 positive and 5 negative tests per rule.
2. Custom org and project rules, plus the add-only repo rules file, with a regex budget that fails
   to confirm.
3. Org settings: disabled built-ins (never the hard parts) and dev DB patterns.
4. MCP server environment and server-wide default action, in the policy and the UI.
5. Project trust (heuristic default), the external data line, quarantine (`guest-quarantine`) and
   the no-answer mode.
6. Readonly paths and reference projects (`readonly-source`, hard; `--add-dir`).
7. Approve once or for this run (thread grants, 12 h), checked locally by the hook and by the
   server, with a grants list and revoke.
8. Deny-and-resume: requests that outlive the run, approval follow-ups with one-shot grants, and a
   24 h window.
9. Unified Action policy UI. TaskDetail shows guard lines for granted calls. README upgrade note.

## Non-goals (later phases or never)
A rule tester / preview endpoint. Policy export (F41, Phase 6). The re-clone (root commit change)
warning from F4. Per-user rules files (forge's `~/.claude/jspark-forge/rules.md` becomes org rules).
Repo-provided readonly or settings lines. The guest data line for decisions and launch lists
(Phases 4–5). Handoff and compaction re-injection beyond the data line (Phase 3). PowerShell (F27).
A Linux spike re-run (no real claude). Real isolation (a per-task UID or container) stays the
recorded follow-up.

## Anti-goals
- Any repo-controlled input that relaxes policy. The repo file only adds rules, an external repo's
  file is never read, and a repo's `disable:` / `database dev:` lines are reported, never applied.
- A grant that covers more than the person saw: a different thread, another rule id, a hard rule,
  after it expires or is revoked, or a one-shot grant used twice.
- A catastrophic regex that makes the hook time out. A hook timeout lets the call run (spike c), so
  the budget must answer well inside the hook's own time.
- False `db-write` holds on local and dev work (`psql -h localhost`, `sqlite3 app.db`, `prisma
  migrate dev`, SELECTs on prod), or SQL keywords inside string literals or comments read as
  statements.
- A follow-up that runs without a person's approval, or runs on a cancelled thread.
- Any path where an agent creates or widens a grant.

## Approaches considered
| Question | Options | Outcome |
|---|---|---|
| Where custom rules live | repo file only (forge) / **DB table + add-only repo file** / fold into `McpToolPolicy` | DB table: org and project scopes and a UI; `McpToolPolicy` keeps its per-tool shape, and the UI presents both as one "Action policy" |
| Regex safety in the hook | `re2` / `regex` with timeout (not stdlib: breaks R1) / server-only pre-check / **SIGALRM budget in the hook + server compile check** | SIGALRM: stdlib, proven to interrupt `re` on CPython 3.14; a timeout means confirm |
| Grant delivery | network check on every approve verdict / policy snapshot only / **snapshot + server answer on POST** | both: snapshot for grants that existed at start (no network), POST answer for grants made during the run |
| Grant key | full tool-input hash (Phase 1 `command_hash`) / **`grant_key` (shell: the command only)** | `grant_key`: the Bash `description` field changes between calls and would make resume grants useless |
| Resume requests | a separate "resume request" row / **the same `ActionApproval` with `resume_until` + `delivery`** | same row: one card, one history, and the existing two-person decide path |
| DB rules placement | inside `rules.py` (2.5k lines) / **sibling `dbrules.py` (stdlib) used by `rules.py`** | sibling: forge's database guard is a self-contained unit, so it can be tested on its own |
| Trust default | forge's per-user email / maintainer-set only / **org-email heuristic at create/import, then maintainer-set** | heuristic only with positive evidence, so nothing is quarantined on a fresh install with no member emails |

## Builder's latitude
Module names beyond the interfaces fixed in the plan, the table layout of the rules UI, copy (apart
from the data line and the deny texts fixed in the plan), test structure, how the follow-up prompt
lines are formatted, and the slug suffix scheme within a scope.
