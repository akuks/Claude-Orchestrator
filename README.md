# Claude-Orchestrator

A self-hosted, **team orchestration layer for Claude Code** — run, stream, govern,
and audit Claude Code tasks from a web dashboard instead of one-off terminal
sessions. Where `claude` runs one agent locally, this runs *many* — with a queue,
isolation, project memory, scheduling, human approvals, MCP credential governance,
cost caps, security reviews, and reusable agent roles.

> **Status:** Phases 1–5 complete, plus agents, security reviews, cost tracking,
> reports, headless deploy, and CI. Remaining: Team & Auth. Full roadmap in
> `claude-orchestrator-features.md`.

> **Upgrading to the guard release?** Every task now runs under **co-guard**,
> a hook that checks each tool call before it runs. What changes for you:
>
> - **The guard is on for every project** (Project → Guard). Turning it off
>   switches off the built-in command rules only; MCP approvals/blocks and the
>   orchestrator's self-protection always apply.
> - **MCP tools set to "Require approval" now pause the run** for up to the
>   project's approval wait (default **300 s**, 60–900) until a person decides in
>   **Approvals → Actions**; if nobody answers, the call is **denied**. Change a
>   tool to **Auto-approve** in MCP Servers if it must run unattended.
> - **Some commands need a person's approval, some are always denied** (see
>   [Agent guard](#agent-guard-co-guard)) — e.g. `git reset --hard`, force-push to
>   main, `rm -rf` outside the project (temp dirs are fine) need approval;
>   `rm -rf ~` / `rm -rf /` and touching the orchestrator's own files are denied.
> - **Inline `` !`cmd` `` in skills, slash commands and skill packs is disabled**
>   (`disableSkillShellExecution`), and the **PowerShell tool is disallowed**.
> - **Rarely, project settings are dropped for a run** (`--setting-sources
>   user`): when a project's `.claude/settings*.json` would switch hooks off.
> - **`CO_BIND_HOST` / `CO_BACKEND_PORT` are now load-bearing**: they are the
>   approval address the hook calls. Use a literal IP for `CO_BIND_HOST`.
> - **Tools co-guard does not know** (e.g. new ones after a Claude Code CLI
>   update) **need approval** until co-guard learns them.
> - It is a **seatbelt, not a sandbox** — see the accepted limits below.

> **Upgrading to the policy release?** co-guard's policy becomes the org's own
> ([Action policy](#action-policy)). What changes for you:
>
> - **New MCP servers default to *Require approval*** for tools with no policy
>   row. Servers that existed before the upgrade keep *Auto-approve* (today's
>   behaviour). Set a server's default action in MCP Servers.
> - **Database writes to a target that is neither local nor dev need approval**
>   (`db-write`): `psql -h prod.db … -c "update …"`, a script file or piped SQL
>   against a remote host, `prisma db push` / `migrate deploy`, `supabase db
>   push`, write tools of a database MCP server. Local databases (localhost,
>   sockets, SQLite files) are free. Mark dev databases in **Agent guard → Dev
>   databases** or a database MCP server's environment as *Dev*.
> - **Check your database MCP servers' environment.** A server whose name looks
>   like a database (postgres, mysql, sql, mongo, redis …) starts as
>   *Unknown*, which counts as **production** for `db-write`: every write tool
>   call on it needs approval. Set dev and local servers to *Dev* in MCP
>   Servers (or add them to the dev database patterns).
> - **Destructive SQL needs approval on every database, local ones included**
>   (`sql-destroy`): `DROP TABLE/DATABASE/SCHEMA`, `TRUNCATE`, `DELETE` without
>   `WHERE`.
> - **Projects created or imported after the upgrade may come out *external* and
>   quarantined.** When the folder has a git remote, at least one org member has
>   a git email, and no commit is by any of them, the project is marked external:
>   the agent is told the repo's text is data, not instructions; the repo's
>   `.claude-orchestrator/rules.md` is not read; and quarantine holds the repo's
>   install/build/test/run commands (`npm install`, `make`, `pytest`, `./run.sh`
>   …) for approval. Existing projects stay *own* with quarantine off. Change
>   either in Project → Guard.
> - **Read-only folders block project-wide changes that reach them.** With a
>   read-only folder inside the project, `rm -rf *`, `chmod -R .`, `git checkout
>   .` and formatters run on `.` (`prettier --write .`, `black .`) are denied
>   outright. Name the folders to change instead (`black src`); a tool's own
>   exclude options are not taken into account.
> - **Task-level risk (`classify_risk`) is unchanged**: prompts that mention
>   pushes, merges or deletes are still gated before the task starts.

> **Upgrading to the resume release (3a)?** Runs now keep a record of where
> they stand ([Resume and handoff](#resume-and-handoff)). What changes for you:
>
> - **Migration `0008_resume` runs on start** (`alembic upgrade head`; SQLite and
>   Postgres). It adds columns and one table (`task_handoffs`), and its downgrade
>   drops them. Take your usual database backup first.
> - **A run cut off by a server restart or crash now resumes its own Claude
>   session** (with a note of what was in flight) instead of starting over from
>   the original prompt. It is tried at most **2** times; the third interruption
>   fails the step and says to use Continue.
> - **A fresh continuation now carries the handoff.** A follow-up that cannot
>   resume (another person's step, a missing session file) and a new
>   *Continue fresh* start with where the thread stands, not just the prompt.
> - **A changed Claude credential forces a fresh session.** The old session
>   belonged to the old account; the step starts fresh with the handoff and the
>   run log says why.
> - **The system prompt gains the ground rules** (grounding, short answers,
>   usage care and note keeping), plus any extra text an admin adds. An admin can
>   switch each one off in **Agent guard → Ground rules**.
> - **One new agent-token route**: `PUT /tasks/{id}/notes`, for the run's own
>   task only. Everything else on the API stays for people only.
> - **The server must stay a single process** (no `--workers`, no replicas):
>   crash recovery assumes it is the only one that owns running rows.

> **Upgrading to the token-care release (3b)?** Steps now report their token
> use and agents can ask you questions ([Token care and questions](#token-care-and-questions)).
> What changes for you:
>
> - **Migration `0009_tokens` runs on start** (SQLite and Postgres). It adds the
>   per-step usage and question tables and a few columns; its downgrade drops
>   them. Take your usual database backup first.
> - **Every later step of a thread now keeps its parent's `max_budget_usd`**
>   (follow-up, Continue, automatic Continue, answer and co-guard follow-up
>   steps), as it already kept the model and max turns. Before, a later step of
>   a thread ran with no budget cap. **Duplicate** now copies the source's
>   budget too, or applies `CO_DEFAULT_TASK_BUDGET_USD` when the source had none,
>   the way a new task does.
> - **The system prompt gains a *Questions* ground rule** (file a blocking
>   question with a recommendation, then end the turn). An admin can switch it
>   off in **Agent guard → Ground rules**.
> - **Two new agent-token routes**, for the run's own task only:
>   `POST /tasks/{id}/questions` and `GET /tasks/{id}/questions`. An agent can
>   never answer or dismiss a question.
> - **Nothing continues on its own until you set it**: the automatic fresh
>   Continue needs a project's *Fresh session at* setting, which is off by default.
> - **The figures are not yet checked against the real `claude` CLI** (see the
>   section): treat them as estimates until your first live run confirms them.

---

## What it does

| Area | Capabilities |
|---|---|
| **Tasks** | Queue + isolated worker pool, live WebSocket streaming, cancel / retry / duplicate / delete, **follow-up threads** (resume a Claude session), input files, artifacts |
| **Projects & memory** | Projects mapped to repos, **Discover** existing Claude Code dirs, repo-backed runs, **context assembly** (instructions + living memory + relevant past summaries), auto-updated `memory.md` |
| **MCP governance** | Server registry (3 scopes), **AES-256-GCM credential vault**, tool policies (auto-approve / require-approval / **block**), connection testing + tool discovery, call observability |
| **Scheduling** | Cron schedules (presets + raw), next-run preview, pause/resume, run-now, run history, reusable **templates** + 6 automation presets, Slack notifications |
| **Approvals** | Human-in-the-loop **inbox**, **auto-gate critical-risk** tasks, blast-radius context, approve/reject + reason, bulk approve, timeout auto-reject |
| **Agents** | Reusable, **governed roles** — system prompt + model + budget + optional project — run under all guardrails |
| **Security** | **VAPT/STQC security reviews** (read-only), a **findings tracker** (severity, CWE, status lifecycle, recurrence), and **GitHub PR → auto-review** webhooks |
| **Cost & reliability** | Token/cost capture, **Usage dashboard**, per-project budgets + over-budget flags, **hard spend caps** (`--max-budget-usd`), **auto-retry** transient failures, fallback model |
| **Reports** | Download any task's result as **PDF or DOCX** |
| **Ops** | Headless auth (`ANTHROPIC_API_KEY`), readiness endpoint, `start.sh`, pytest suite + GitHub Actions CI |

---

## Architecture

```
frontend/  React + Vite + Ant Design + ECharts dashboard
backend/   FastAPI + async SQLAlchemy (SQLite or PostgreSQL) + in-process asyncio worker pool
```

- **Worker manager** spawns `claude --print --output-format stream-json` per task
  (in the project's repo, or an isolated sandbox), parses the JSONL event stream,
  and enforces concurrency / timeout / max-turns / spend caps. Transient failures
  (rate limit / overload / network) are auto-retried with exponential backoff.
- **Streaming** is an in-memory pub/sub broker over WebSocket; every event is also
  persisted so a reconnecting client replays what it missed.
- **No external services required** — SQLite (or Postgres for teams) + an in-process queue/scheduler. Everything
  (worker pool, cron, MCP config, approvals) runs in one process.

---

## Quickstart

The simplest way to run the whole stack:

```bash
./start.sh          # sets up deps on first run, then backend :8200 + frontend :5200
```

`start.sh` binds the backend to **127.0.0.1** by default (`CO_BIND_HOST` to change) and frees the ports first (so stale processes can't pile up) and stops both
on Ctrl+C. Then open **http://localhost:5200**. Override ports with
`CO_BACKEND_PORT` / `CO_FRONTEND_PORT`.

<details>
<summary>Or run the two services manually</summary>

```bash
# Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                  # optional; defaults work
uvicorn app.main:app --host 127.0.0.1 --port 8200 --proxy-headers   # single process: no --workers

# Frontend (new terminal)
cd frontend
npm install
VITE_PROXY_TARGET=http://localhost:8200 VITE_PORT=5200 npm run dev
```
</details>

**Prerequisites:** the `claude` CLI on your `PATH` (the worker shells out to it),
Python 3.10+, and Node 18+. Interactive API docs at http://localhost:8200/docs;
the dashboard header shows a live **Claude readiness** tag (`GET /system/claude`).

---

## Deploying for a team

The orchestrator is multi-user: every request needs a login, and tasks/projects are
owned by the user who created them. The server is a **single process** — the login
lockout and first-admin setup lock are held in memory, so **do not run multiple
uvicorn workers** (or multiple replicas).

**First login.** Either set `CO_ADMIN_EMAIL` and `CO_ADMIN_PASSWORD` before the first
start (the admin is created if no users exist), or leave them unset and open the UI:
the first visit shows a **setup screen** to create the first admin. Further users are
added from the admin Users page. Rows that predate multi-user are assigned to the
first admin.

**PostgreSQL.** SQLite is fine for one person; for a team use Postgres:

```bash
createdb claude_orchestrator
# backend/.env
CO_DATABASE_URL=postgresql+asyncpg://user:password@localhost/claude_orchestrator
```

The schema is created/upgraded automatically on start (Alembic). To bring existing
SQLite data across (stop the app first; run from `backend/`):

```bash
python scripts/migrate_sqlite_to_postgres.py \
  --from sqlite:///claude_orchestrator.db \
  --to   postgresql://user:password@localhost/claude_orchestrator
```

The source file is never modified (a temporary copy is upgraded and read). The
target must be empty unless you pass `--force`, which **destroys all existing data in the target** (it deletes every row before copying). Source and target must not be the same database; that is always refused.

**HTTPS via a reverse proxy.** For anything beyond localhost, put nginx/Caddy/etc.
in front with TLS. The session cookie is marked `Secure` automatically when the
request arrives over HTTPS. Keep `CO_BIND_HOST` at `127.0.0.1` and let the proxy
reach it locally. Set `CO_FORWARDED_ALLOW_IPS` to the **proxy's IP** (default
`127.0.0.1`) so uvicorn honours `X-Forwarded-For` and the login lockout sees real
client IPs rather than the proxy's. Proxy WebSocket upgrades through as well.

**Origins.** `CO_CORS_ORIGINS` (a JSON list, e.g. `["https://orchestrator.example.com"]`)
is both the CORS allowlist **and** the WebSocket `Origin` allowlist. A UI served from
any other origin must be added, or live task streams are rejected.

---

## Configuration (`backend/.env`, prefix `CO_`)

| Variable | Default | Purpose |
|---|---|---|
| `CO_DATABASE_URL` | `sqlite+aiosqlite:///./claude_orchestrator.db` | Task/event store (SQLite, or `postgresql+asyncpg://…`) |
| `CO_ADMIN_EMAIL` / `CO_ADMIN_PASSWORD` | *(unset)* | Bootstrap first admin (else use the setup screen) |
| `CO_BIND_HOST` | `127.0.0.1` | Interface `start.sh` binds the backend to (shell or `backend/.env`). Also co-guard's **approval address**: use a literal IP (a hostname is resolved once and must have exactly one address; `0.0.0.0` means loopback). Logged at startup |
| `CO_BACKEND_PORT` | `8200` | Backend port (`start.sh` reads it from the shell or `backend/.env` and passes it on); the approval address's port |
| `CO_PUBLIC_URL` | *(unset)* | The UI's public base URL, for links in notifications |
| `CO_FORWARDED_ALLOW_IPS` | `127.0.0.1` | Reverse-proxy IP(s) trusted for `X-Forwarded-For` (read by `start.sh` from the shell or `backend/.env`; not by the app) |
| `CO_CORS_ORIGINS` | localhost dev origins (`start.sh` sets it from `CO_FRONTEND_PORT` if unset) | JSON list; CORS **and** WebSocket Origin allowlist |
| `CO_SESSION_TTL_DAYS` | `7` | Login session lifetime |
| `CO_LOGIN_MAX_FAILURES` / `CO_LOGIN_WINDOW_SECONDS` | `5` / `900` | Login lockout |
| `CO_WORKSPACES_DIR` | `./workspaces` | Isolated per-task working dirs (resolved absolute) |
| `CO_CLAUDE_BIN` | `claude` | Claude Code CLI to spawn |
| `CO_DEFAULT_MODEL` | `sonnet` | Fallback default model (blank ⇒ CLI default) |
| `CO_WORKER_CONCURRENCY` | `3` | Max simultaneous tasks |
| `CO_TASK_TIMEOUT_SECONDS` | `1800` | Kill tasks exceeding this |
| `CO_CLAUDE_PERMISSION_MODE` | `bypassPermissions` | Permission mode passed to the CLI |
| `CO_DEFAULT_TASK_BUDGET_USD` | `2.0` | Default per-task spend cap (`--max-budget-usd`) |
| `CO_FALLBACK_MODEL` | *(unset)* | Fallback model if the primary is unavailable |
| `CO_RETRY_MAX_ATTEMPTS` / `CO_RETRY_BACKOFF_SECONDS` | `2` / `30` | Auto-retry transient failures |
| `CO_RECOVERY_SWEEP_SECONDS` | `60` | How often `RUNNING` steps with no live worker are recovered ([Resume and handoff](#resume-and-handoff)) |
| `CO_GATE_CRITICAL_APPROVAL` | `true` | Force approval for critical-risk tasks (merge/deploy/delete) |
| `CO_GITHUB_WEBHOOK_SECRET` | *(unset)* | HMAC secret for the GitHub PR webhook (required: the endpoint returns 503 while unset) |
| `CO_SECRET_KEY` | *(auto)* | AES-256 vault key (else generated to `.secret.key`) |
| `ANTHROPIC_API_KEY` | *(unset)* | Headless auth for the spawned `claude` |

**Headless auth.** The worker inherits the server's environment, so set
`ANTHROPIC_API_KEY` (or run `claude setup-token`) to authenticate `claude` with no
interactive login — check readiness at `GET /system/claude`.

**`bypassPermissions`.** Tasks run unattended (no human to answer CLI prompts), so
the default is `bypassPermissions`. Governance comes from the **approval inbox**
(critical tasks are auto-gated) and **MCP tool policies** (e.g. `merge_pull_request`
= Block for review-only). Tighten to `default`/`acceptEdits` for more CLI-level
guardrails.

---

## Agent guard (co-guard)

Every task runs with **co-guard**, a Claude Code plugin the server generates
(`workspaces/_system/co-guard`, read-only) and loads first. Its `PreToolUse`
hook checks **every tool call** before it runs, with the task's policy:

| Decision | What |
|---|---|
| **Allowed** | Everyday work: reads, edits inside the project, builds, tests, `git` on feature branches, `rm -rf` inside the project or strictly inside a temp dir (`/tmp/…`, `$TMPDIR/…`, `/private/var/folders/…` — never the temp root itself) |
| **Needs approval** (a person, once) | `rm -rf` outside the project (or of the project / its `.git`), force-push to `main`/`master`, `git reset --hard`, `git clean -fdx`, `git stash drop`, secret files (`.env`, keys, `secrets.yml`; source and docs such as `secrets.ts` or `docs/secrets.md` are free), wide `chmod`, `curl … \| sh`, `.git/config` / hooks, Claude Code settings / hooks / agents / `.mcp.json`, commands too unclear to check, MCP tools set to *Require approval*, **unknown tools** |
| **Needs approval** (database) | `sql-destroy`: `DROP TABLE/DATABASE/SCHEMA`, `TRUNCATE`, `DELETE` without `WHERE` sent to any database client or database MCP server, local ones included. `db-write`: any non-read statement, script file or unknown stdin sent to a target that is neither local nor dev; `prisma db push` / `migrate deploy|resolve|reset`, `supabase db push`; database MCP write tools (not on *Dev* servers) |
| **Needs approval** (policy) | `guest-quarantine`: the repo's install / build / test / run commands in a quarantined project (also with the built-in rules off). `org.<slug>` / `project.<slug>` / `repo.<slug>` *confirm* rules |
| **Denied** (never approvable) | `rm -rf ~` / `/`, anything touching the orchestrator itself (its code, `.env`, DB, venv, the guard, other tasks' data, the Claude config dir) — **self-protection**, commands too complex to parse, MCP tools set to *Block*, the PowerShell tool |
| **Denied** (policy) | `readonly-source`: any change under a read-only folder or a reference project (always on, never filed as a request). `org.<slug>` / `project.<slug>` / `repo.<slug>` *never* rules |

**Approvals.** The call pauses and shows up under **Approvals → Actions** with the
full command; it waits up to the project's **approval wait** (Project → Guard,
default 300 s, 60–900) and is denied if nobody answers (or, with *Deny and resume*, stays
open for a later follow-up — see [Action policy](#action-policy)). Two-person rule: another
maintainer of the project (or an admin) decides — the task's owner cannot approve
their own task's actions unless they are an admin. A scheduled run with a Slack
webhook also posts "Agent action waiting for approval: *task* — *rule*" (first
line of the redacted command, link via `CO_PUBLIC_URL`). **Approve once** lets
exactly that call run; **Approve for this run** also lets the same rules through
for the rest of the thread (see [Action policy](#action-policy)).

**What else changes per task.** Inline `` !`cmd` `` in skills / slash commands /
skill packs is disabled (`disableSkillShellExecution`: it would run unchecked);
the PowerShell tool is disallowed; if a project's `.claude/settings*.json` would
switch hooks off, the run uses `--setting-sources user` (project settings and
`CLAUDE.md` are skipped for that run, and the run log says so) — user-level
settings that switch hooks off fail the task. A watchdog stops a task if a tool
runs without co-guard having checked it.

**Approval address.** The hook calls the server at `CO_BIND_HOST:CO_BACKEND_PORT`
(a literal IP; the startup log prints it and warns loudly about a hostname). The
worker proves the address is this server before each task; if it cannot, the
task does not start.

**After a Claude Code CLI update.** A tool co-guard does not know yet (a new
built-in tool) needs approval each time (`unknown-tool`), so new shell-capable
tools fail closed. Approve it, or wait for a co-guard update that knows it.

### Action policy

The policy lives in three places: **Agent guard** (admin page: org rules,
built-in rules, dev databases), each project's **Guard** tab (project rules,
trust, quarantine, no-answer mode, read-only folders, reference projects, the
repo rules file's status) and **MCP Servers** (environment and default action).
Every task gets the policy as it stands when it starts.

**Custom rules.** A rule is *confirm* (needs a person's approval) or *never*
(denied outright), and matches *text* (a case-insensitive piece of text) or a
*regex* (Python syntax, case-insensitive, at most 500 characters, checked when
saved). It is checked against the shell command (and its de-quoted words), the
file path of file tools (never the content) and the input of MCP tools — for
every tool, reads included, and also when the project's built-in rules are off.
Admins edit org rules (`org.<slug>`), maintainers their project's
(`project.<slug>`); at most 200 per scope. The reason (at most 200 characters;
control, zero-width and bidi characters are removed) is what the agent and the
approver read. Every custom regex of one tool call shares a 250 ms budget: a
repo rule or a *confirm* rule that runs past it needs approval ("took too long
to check"); an org/project *never* rule that runs past it stays denied.

**The repo rules file.** A project trusted as *own* may carry
`.claude-orchestrator/rules.md`, read when each task starts:

```markdown
## Confirm
- `terraform apply` : applying infrastructure needs a person
- `re:kubectl\s+delete` : deleting cluster resources

## Never
- `DROP DATABASE` : never from a task
```

It is **add-only**: its rules become `repo.<slug>` and can only ask for more
approvals or blocks. `disable:` and `database dev:` lines, other sections and
malformed lines are ignored and listed (with line numbers) in the project's
Guard tab and the task's run log. At most 64 KiB and 200 rules. An *external*
project's file is never opened. An agent's change to `.claude-orchestrator/`
needs approval (`agent-config`: it would change the rules a later task loads).

**Built-in rules and dev databases (Agent guard, admins).** These built-ins can
be switched off for the whole org: `rm-rf-outside` (its approval part only —
deleting home or `/` stays denied; switching it off also frees `rm -rf` of the
project folder and its `.git`), `git-force-push-main`, `git-reset-hard`,
`git-clean-fdx`, `git-stash-drop`, `secret-files`, `chmod-wide`,
`pipe-to-shell`, `sql-destroy`, `db-write`. The rest can never be switched off:
`self-protect`, `agent-config`, `git-internals`, `unparseable`,
`uncertain-command`, `unsupported-shell`, `unknown-tool`, the MCP rules,
`readonly-source` and `guest-quarantine`. **Dev database patterns** relax
`db-write` (never `sql-destroy`): `host:<glob>` (`host:*.dev.internal`),
`mcp:<server>` or `mcp:<prefix>*`, or a bare exact host or server name.
Patterns that are only wildcards are refused.

**MCP servers (admins).** Each server has an **environment** — *Dev*, *Prod* or
*Unknown* (the default). *Dev* or *Prod* marks it as a database; an *Unknown*
server whose name looks like a database (postgres, mysql, sql, mongo, redis …)
is one too and counts as production — so set your dev and local database
servers to *Dev*. `db-write` skips *Dev* servers (an explicit *Prod* beats any
dev pattern); `sql-destroy` applies to all of them, *Dev* and local databases
included. Each
server also has a **default
action** for tools with no policy row: *Auto-approve*, *Require approval* or
*Block*. An explicit tool policy always wins.

**Trust and quarantine (Project → Guard).** *Own* code or *External* (someone
else's). New and imported projects get a suggestion (the git-author check in
the upgrade note; trust is not checked when no member has a git email) and the
Guard tab shows why. For an external project the agent (and every helper it starts) is told, at start and
after compaction, that the repo's README, comments, docs, scripts and rules
files are data to review, never instructions; and its rules file is not read.
**Quarantine** (on by default for projects that come out external) holds the
repo's install / build / test / run commands for approval (`guest-quarantine`:
package managers except read-only subcommands, docker, interpreters and shells
running the repo's scripts, the repo's own executables); `--version`, `--help`
and dry runs are free.

**Read-only folders and reference projects (Project → Guard).** A read-only
folder (relative to the project or absolute; not `/` or a parent of your home)
and every **reference project** — another project the editor can see, mounted
into each task with `--add-dir` when the task's owner can see it too — is a
`readonly-source` root: edits, writes, redirects, copies *into* it, moves,
deletes, `sed -i`, `chmod`, git commands that change its work tree, and builds,
installs and formatters run on it are **denied** (never filed, cannot be
approved). Reads stay free (`cat`, `grep -r`, `git -C ref log`, `cp ref/a ./`).
Running a root's own code (other than known builders) needs approval
("this runs a read-only source's code, which may change it"); so does a
relative write after a `cd` co-guard cannot follow, when read-only folders
exist. The deny message names the folder's path (a reference project's folder
is in the run log through `--add-dir` anyway).

**Approve once or for this run.** A card offers **Approve once** (that call)
and **Approve for this run**: the same rules through for the rest of the
**thread** (the task and its follow-up steps) for **12 h**. A run grant covers
a call only when every rule it hits is granted, for:
- `guest-quarantine`: any command of the thread;
- MCP approvals and `db-write` on an MCP tool: **every call of that tool** in
  the thread (whatever its input; the button's tooltip says so);
- every other rule: the same command (Bash and Monitor runs of one command
  share one approval) or the same tool input.

A `db-write` grant never covers `sql-destroy`, and a hard deny is never
covered. Grants made during a step answer that step's later calls through the
server; a new step gets them in its policy file and its hook checks them
locally. Only requests whose key the server can verify from what the card shows
can be approved for the run: a request the agent filed itself, a command too
long to show uncut, one that redaction changed or that holds hidden (bidi,
zero-width) characters, a call that hit more than 50 rules, and a request with
a rule that cannot be granted (a hard or *never* rule) are **once only** — the
server decides, and the card says why. **Approvals → Run and follow-up grants**
lists live grants with **Revoke**; revoking stops server answers and later
steps at once, but a step already running keeps its copy until it ends.

**When nobody answers (Project → Guard).** *Deny* (default): the call is denied
when the approval wait ends. *Deny and resume*: the agent is told the request
is filed and waits for a person, and ends its turn; the request stays open for
**24 h** (Approvals shows "Agent stopped"). Approving it starts a **follow-up
step** of the thread, on the thread owner's credentials, resuming their latest
session, with a one-time grant for exactly that call ("a person approved
request … You may now run this once, exactly as before"). Approvals that land
while that follow-up is still queued join it; a step still running delays it
until the step ends. Rejecting only records the decision. The request expires
after 24 h or when the thread is cancelled (approvals still waiting to start
are dropped then too). A decision in the last 5 s of the agent's wait gets
"the agent is giving up on this request; try again in a few seconds".

### Seatbelt limits

co-guard is a **seatbelt, not a sandbox**: it reads the command text and tool
input before a call runs, not what the program then does. Accepted limits
(the full lists are in the docstrings of `backend/app/guard/rules.py`,
`dbrules.py` and `custom.py`):

- **General.** `sed`'s `e` command, scripts already on disk (`bash x.sh`,
  `make`, `npm run`), obfuscated interpreter code, `ssh host bash -s < file`;
  `rg | xargs rm` (non-recursive), `git branch -D` / `update-ref`, `umask` /
  `setfacl`, `python -c` writing `.env`, copying/syncing/unpacking into the
  project (`cp -r src/. .`, `rsync`, `tar`, `git apply`, `stash pop`) that lands
  in agent config; while the server is down a same-user process could listen
  on the approval address; agent definitions and `.mcp.json` committed to the
  repo can start processes outside the hook (repo content is trusted, like repo
  hooks).
- **Database guard.** Clients run inside another program (`docker exec`,
  `kubectl exec`, `make`, scripts); SQL the server runs on its own (existing
  functions, triggers, `dblink`, prepared statements, `pg_cron`); SQL built at
  run time (`format()`, psql `:vars`, shell variables in SQL — a write when not
  local, never a destroy); MongoDB code written to hide a method name; files a
  client reads (`-f`, `\i`, `.read`) are never opened (a write when not local,
  never a destroy); a quote in a psql meta-command can hide the SQL after it; a
  dev pattern trusts the host name as written (no DNS). It errs towards asking:
  a client co-guard cannot parse with certainty is treated as a write, so some
  reads of production need approval (e.g. MySQL `'it\'s'`, `sqlcmd` `PRINT`,
  `mongosh` with `require(` in a string, a client with no inline SQL on a line
  with a pipe).
- **Custom rules.** A word the parser cannot resolve (`$(..)`, `$G` from
  `G=git`, a computed name, a script on disk) is matched as written, so a
  *never* rule is a seatbelt, not a boundary. A broad text rule also holds
  reads. On very long command lines, an org/project *never* regex can run out
  of its budget and block (it never lets the call through).
- **Read-only folders.** Not seen: git subcommands outside the work-tree list
  (`fetch`, `branch -D`, `tag`, `gc`, `config`), `GIT_DIR` / `GIT_WORK_TREE` in
  the environment, an enclosing repo's `git stash` / `clean -fd` / `pull` that
  touches a read-only folder inside it without naming it, `rsync -a src/
  parent/` whose contents land in a root, shells and interpreters run by `find
  -exec` / `xargs`, code that builds a root's path at run time, a symlink made
  earlier and written through later, archive members with absolute or `..`
  names, `-prune` / `-not -path` in `find` (reach is over-estimated), a tool's
  own exclude options. A `cd` co-guard cannot follow makes relative writes need
  approval (not a deny).
- **Quarantine.** It keeps forge's tables, so it misses tools they do not list
  (`tsc`, `prettier`, `black`, `ruff`, `mypy`, `gcc`, `javac`, `awk -f`, …),
  runners that start a held command later (`entr make`, `git bisect run`), code
  loaded through the environment (`NODE_OPTIONS=--require`, `PYTHONSTARTUP`)
  and `find -exec {}`; it holds some reads (`python -c`, `node -p`, `bash -n`,
  `pytest --collect-only`, `--dry-run` installs). Trust follows git author
  emails, which anyone can set, so treat *own* as a convenience, not proof.
- **Hook errors.** `PreToolUse` fails closed. Any other hook that fails
  (session start/end, helpers, `PostToolUse`, settings changes) is recorded as
  a "co-guard hook error" line in the run's log, and **Agent guard** shows how
  many there were in the last 48 h (`GET /guard/health`).
- **Redaction** (run logs, approval cards, spool) knows common shapes — API
  keys (`sk-…`, GitHub, Slack, AWS key ids), JWTs, PEM private keys, URL
  passwords, `Bearer …`, `password=` / `token:` / `api_key=`-style values and
  `mysql -p…` — not every secret.
- **Grants.** Revoking a grant does not reach a step that is already running
  (it keeps its policy copy until it ends). Bash and Monitor runs of the same
  command share one approval. Requests the agent filed itself and commands too
  long to show uncut can only be approved once.

Real isolation (OS-level sandboxing, a per-task user or container) is a later
phase.

---

## Resume and handoff

Phase 3a of the forge port: a thread can be stopped, crash or be handed to
someone else, and the next step still knows where it stands.

**What you see.** A task's page shows a **Where it stands** card (collapsible):
the task, *Step n of m* and the next action (from the agent's todo list, plan
file or tasks), what is done, what is running or waiting, the last reply, files
touched, Claude's own notes with the time they were written, recent actions
and who else is active in the directory. **Refresh** rebuilds it now,
**Mark done** closes it. Above it, short notices say when a step was recovered
after an interruption (or given up on), when commits were made outside the
thread, when a step started a fresh session and why (credentials changed,
another person continued it, the session was not available), and, in a
project's drawer, that the repo has **no commits yet**. The card is redacted
and cut to 600 words; it is saved when a run ends, when notes arrive, about
every 60 s while a run is active and before a Continue.

**Continue.** A finished step of a thread (the API accepts a completed, failed
or cancelled one, never an agent's dispatched child; the button is shown on
stopped, failed and cancelled steps) has a **Continue** button:

- **Continue (resume)** carries on in the step's own Claude session with the
  handoff and the last log lines. Only the person who started the step may do
  it, and the step needs a session (otherwise 409). If the session file has
  vanished by then, or the credential changed, the new step starts fresh with
  the handoff and a notice says why.
- **Continue fresh with the handoff** starts a new session with the handoff,
  the log tail and the original request. Anyone who may follow up can do it.
- The API's `auto` mode (`POST /tasks/{id}/continue`, body `{"mode":
  "auto"|"resume"|"fresh"}`) resumes when it can and starts fresh otherwise.

Who may use it is the same as for a follow-up: the step's creator, a
maintainer on it, or an admin, who also hold at least contributor on its
project. The buttons follow the server's `can_manage` / `can_resume` on the
task, which are the same checks `POST /continue` makes. Only one step of a thread
runs at a time (409 while one is active).

A plain follow-up with an intact session gets no handoff, only a one-line
note when commits were made outside the thread since the last step. A repo
with no commits at all gets a warning line in the run's prompt (a bad edit
cannot be undone with git until the first commit). Git facts are read-only
(`status`, ancestry, `log`) with a 5 s timeout; on a timeout they say "unknown".

**Crash recovery.** The server marks a `RUNNING` step interrupted when its
worker is gone (a restart, a crash or `kill -9`) and requeues it to resume its
own session. The agent is told when it was cut off, what was in flight (a
shell command "may have half run", a file "may be half written"), what was
running and is now gone, and to run `git status` before repeating anything.
Limits:

- **At most 2 automatic recoveries per step.** The third interruption fails
  the step ("interrupted 3 times by server restarts; use Continue to resume
  it").
- **A leftover Claude process is killed only when its start time matches** the
  one recorded for the run (`ps lstart`). A mismatch or a dead pid is never
  signalled, so a reused pid is safe.
- If the session file is gone, or the credential changed, the step starts
  fresh with the handoff.
- Once-grants that a step already used stay spent. A command that was
  half-way through is not rolled back: the agent has to check its effect.
- It needs the server to be a **single process** (no `--workers`, no
  replicas) and runs a sweep every `CO_RECOVERY_SWEEP_SECONDS` (default 60).
- A recovery is visible as a `recovery` event and as a notice on the card.

**Notes.** The agent keeps short notes (what it is doing, what was agreed,
what is next, cautions with what was measured). It saves them with its own
agent token: `PUT /tasks/{id}/notes` (plain text or `{"text": …}`, 4 KB at
most; own task only; cleaned and redacted; stored notes are cut to 120 words
and 1000 characters). co-guard's `PostToolUse` hook
reminds it after every 12 tool calls, and re-injects the handoff after a
context compaction (`SessionStart compact`). The notes show on the card with
their time and are treated as a record to re-check, not as instructions. A
person who can manage the task can also write them (`PUT` with a session).

**Ground rules (admin).** **Agent guard → Ground rules** sets the standing
instructions every run gets in its system prompt: *Grounding* (cite
`path:line` for what was read), *Short answers*, *Usage care* (helper habits),
*Notes* (the note-keeping reminder), and up to 2000 characters of extra text.
Each switch defaults to on; only admins save them
(`PUT /guard/org-settings`, `ground_rules`). A run's log has a
`ground_rules` event listing what was included. When the *superpowers* skill pack is loaded, the
main session also gets forge's precedence line (superpowers sets the method,
co-orchestrator the guard rails), listed as `precedence`.

**Limits.** The handoff is a record rebuilt from the run's events and git, not
a transcript: a long tool output or a file's content is not in it. Files
written to a temp folder are listed
as "may vanish". Secrets are masked with the same rules as the run log, which
know common shapes only (see [Seatbelt limits](#seatbelt-limits)). Phase 3b
(below) adds token care and the Questions inbox.

---

## Token care and questions

Phase 3b of the forge port: agents run headless, so nobody sees the context
grow or the spend near the `--max-budget-usd` cut, and a closing question in the
log goes unread. The server sees every stream message, so it keeps the figures,
warns the agent before it hits a wall, hands a long conversation over to a fresh
session, and collects the agent's questions in an inbox.

**What you see on a task.** The header has two chips: **context 63k** (the size
of the latest main conversation turn; amber at 80 % of the project's
fresh-session threshold, red past it, plain when no threshold is set) and
**budget 41 % est.** (the step's cost against its `max_budget_usd`; amber at
80 %, red at 95 %; hidden when the step has no budget). Under the title, the
**so-far line** sums the thread: *4 steps · 41 min · $2.10 · 1.2M tokens — last:
&lt;title&gt;, 18 min, $1.40*; a running step adds its live estimate. The
collapsible **Where the tokens went** panel splits the step's tokens (input,
cache write, cache read, output) into the main session and each kind of helper
agent (by its `subagent_type`, e.g. *helper: Explore*), per model, lists the 5
largest tool results with their original length, says how many characters
co-orchestrator injected itself, and gives up to three tips (a tool result over
20 000 characters; a peak context over the threshold, or 150 000 when none is
set; cache reads under half of input across 20+ messages). The so-far tokens
count cache reads and writes for a running step as for a finished one. The run log shows a
line when a band fires (*budget about 80 % used ($0.83): the agent was told to
save its notes*) and on an automatic continue (*continued automatically: context
reached 160k*). The figures are polled every 5 s while the step runs
(`GET /tasks/{id}/usage`). When a run ends the page waits for the finished status before it reloads, so the closing question shows.

**Budget bands.** co-guard's `PostToolUse` hook reads `usage.json`, which the
worker writes into the run's task dir (at most every 2 s; ignored when older
than 120 s). At **80 %** of the step's `max_budget_usd` it tells the agent to save
its notes now; at **95 %** to finish the small step it is on, save notes, start
nothing new and end its turn. Each band fires at most once per step and never
changes a tool decision. The project's monthly `budget_usd` keeps its own
alerts; it is not a band. A model missing from the price table has no
estimate, so its steps get no budget band (tokens only).

**Fresh session and the automatic Continue.** A maintainer sets **Fresh session
at (tokens)** on a project (Projects → edit; empty is off, which is the
default; 50 000 to 900 000; 150 000 is a good start). When a step's context
reaches it, co-guard tells the agent once to finish the small step, save notes
with what is next and end its turn. When that step then **completes** (not
failed, cancelled or timed out) and the handoff has an open todo, or the notes
were saved after the warning, the server queues a **Continue fresh with the
handoff** as the step's owner. Limits:

- **At most 3 automatic continues in a row** per thread; any step a person
  starts (a follow-up, a Continue, an answer) resets the count.
- **An open question pauses it**: a step that filed a question (or whose final
  text was captured as one) waits for the answer instead.
- Only one step of a thread runs at a time; an automatic continue is never
  queued while one is active.

**Questions inbox.** An agent asks in two ways. (a) It files the question
itself with its own agent token (`POST /tasks/{id}/questions`: the question, up
to 4 options, a recommendation, what is waiting on it and whether it is
reversible), as the *Questions* ground rule tells it to, then ends its turn. (b)
When a run ends (completed, failed, cancelled or timed out) and the step touched
files or made a commit, a final reply whose last paragraph ends with `?` is
filed for it (*from the agent's last reply*), unless the agent already filed one
in that step; a last paragraph over 600 characters is skipped, not cut. It is
filed before the step's final status is saved, so the task page shows it as the
run ends. Every text is redacted and
cleaned; a thread holds at most **5** open questions (409 beyond). When the
thread came from a schedule with a notification webhook, filing one sends it
with event `question` (the question's first line only), unless the schedule's
policy is *never*.

Open questions show on **Home → Questions** (with a link to the task) and on the
task's page; the Home icon in the rail carries a badge with the number you may
answer. On a card you can **type an answer**, **accept the recommendation** or
**Dismiss**. An answer marks the question answered and queues a follow-up step in
the thread: it resumes the step's session when you started that step, otherwise
it starts fresh with the handoff. Its prompt quotes the question as a record
and states your answer as yours. Dismiss closes it with no step. When a step is retried, the open final-text questions from its earlier attempt are dismissed. Who may answer
or dismiss is the same as for Continue (the step's creator, a maintainer on it
or an admin, with at least contributor on its project); the agent token may
only file and read its own task's questions.

**Open handoffs.** **Home → Open handoffs** lists, newest first and at most 10 (it reads candidates 500 at a time, up to 5 pages, so a busy project does not hide yours),
the threads you started or may manage whose latest step ended in the last 14
days, is not running, and whose handoff is still open (an open todo, or a step
that did not complete), each with its **Continue** button
(`GET /handoffs/open`).

**How far to trust the figures.** Token figures come from the per-message
`usage` in Claude's stream (each message id counted once, with its last usage),
and the running cost is an **estimate from a price table**
(`backend/app/usage/prices.py`), replaced by the result's `total_cost_usd` when
the step finishes. Neither has been checked against the real `claude` CLI yet:
the tests and the end-to-end check use a mock, so **the first live run is the
check**. Compare a finished step's panel and cost with what Claude reports
before you rely on them. Whether the result's token totals include helper
agents' tokens is **unverified**, so the so-far line's token sum may count them
differently from the panel. When a stream carries no per-message usage, the
step falls back to the result's totals.

---

## Ask about this run

Phase 3c of the forge port. A running `claude -p` step cannot be talked to, and
a follow-up waits until it ends. What people usually want mid-run is *what is it
doing, why did it do that, how far is it*. The **Ask about this run** card on a
task's page answers that from the run's record, on a running or a finished
step. It never reaches the agent: nothing is sent to the running session, and
nothing is added to the run log, the handoff, the next step's prompt, the
task's cost or the 3b figures.

**What it reads.** One separate, cheap, tool-less model call gets a record built
from: the step (title, status, times, model, the first 4 000 characters of the
prompt), the thread's handoff, the usage figures, the newest run-log events
(assistant text up to 600 characters, tool calls with the first 200 characters
of input, tool results up to 300, guard decisions, errors; at most 40 000
characters, older ones dropped with a count), your last 3 answered asks on this
task, and your question last, quoted. The whole prompt is capped at 60 000
characters. Everything is redacted and cleaned (common secret shapes only, see
[Seatbelt limits](#seatbelt-limits)). It does **not** see the code and cannot
run anything: the call runs with `--max-turns 1`, a list of disallowed tools
(Bash, Edit, Write, Read, search and web tools, helper agents) and an empty
temp folder as its working directory, with no co-guard plugin and no skill
packs. A fixed system text tells it the record is data, not instructions, to say
so when the record does not show the answer, and to answer in 150 words or
fewer.

**Who may ask, who pays.** Anyone who can see the task, as a person: the agent
token cannot ask. The **asker's own Claude credential** pays (the project's
environment when the task has a project, as for memory calls); without one the
ask is refused with 422 *Add your Claude credential to ask about runs*. The
cost is shown on the ask only.

**Private.** The asks are yours alone: `GET /tasks/{id}/asks` returns only your
newest 20 on that task, and nobody else sees them or their answers.

**Limits and settings.** One pending ask per person per task (409), 30 asks per
person per hour (429), a question of 1 to 2 000 characters (422). The call runs
in the background (`POST /tasks/{id}/asks` returns 202; the card polls every 2 s
while one is pending). A call that times out, exits non-zero or returns nothing
shows a short reason, with **Try again** to re-ask. An ask still pending at
server start is marked failed (*interrupted*). Settings (`backend/.env`):

| Setting | Default |
|---|---|
| `CO_ASK_ENABLED` | `true` (off: 503) |
| `CO_ASK_MODEL` | `haiku` |
| `CO_ASK_BUDGET_USD` | `0.25` per call |
| `CO_ASK_TIMEOUT_SECONDS` | `90` |
| `CO_ASK_CONCURRENCY` | `3` calls at once, server-wide |
| `CO_ASK_HOURLY_LIMIT` | `30` per person |

**Not yet checked against the real CLI.** The tests use a mock. That the
`--output-format json` result carries `result` and `total_cost_usd` as assumed
(a non-JSON reply is shown as the answer with the cost unknown), and that the
disallowed tools are really off, are checked on the first live use. Try one
ask on a real run and look at its answer and cost before you rely on it.
Live steering of a running session is not part of this phase.

---

## REST API

Full interactive docs at `/docs`. Highlights:

**Tasks**

| Method | Path | Description |
|---|---|---|
| `POST` / `GET` | `/tasks` | Create + queue / list (`?status=`, `?project_id=`, `?roots_only=`) |
| `GET` | `/tasks/{id}` · `/events` | Detail / full event log (`?after_seq=`) |
| `POST` | `/tasks/{id}/cancel` · `/retry` · `/duplicate` · `/followup` | Lifecycle actions |
| `POST` | `/tasks/{id}/continue` | Continue a stopped step (`mode`: `auto`, `resume`, `fresh`) |
| `GET` · `POST` | `/tasks/{id}/handoff` · `/handoff/refresh` · `/handoff/close` | Where the thread stands |
| `GET` · `PUT` | `/tasks/{id}/notes` | The agent's notes (`PUT` is also open to the run's own agent token) |
| `GET` | `/tasks/{id}/usage` | The step's token figures, the thread's so-far totals and tips |
| `GET` · `POST` | `/tasks/{id}/questions` | The thread's questions / file one (also open to the run's own agent token) |
| `GET` | `/questions` | Questions you can see (`?status=open\|answered\|dismissed\|all`, default `open`) |
| `POST` | `/questions/{id}/answer` · `/dismiss` | Answer (`{"answer": …}` or `{"accept": true}`) and continue the thread / close it |
| `GET` | `/handoffs/open` | Threads to pick up again (Home) |
| `DELETE` | `/tasks/{id}` | Delete a task (or thread); cleans sandbox workspace |
| `GET` | `/tasks/{id}/report?format=pdf\|docx` | Download the result as PDF / DOCX |
| `GET` | `/tasks/{id}/artifacts[/{path}]` | List / download output files |
| `GET` | `/tasks/stats` | Dashboard stats |
| `WS` | `/tasks/{id}/stream` | Live event stream (`?last_seq=` to resume) |

**Projects & memory** — `/projects` CRUD · `/projects/discover` · `/projects/import`
· `/projects/{id}/memory[/regenerate]` · `/projects/{id}/summaries` · `/projects/{id}/stats`

**MCP** — `/mcp/servers` CRUD · `/mcp/servers/{id}/test` · `/mcp/servers/{id}/policies`
· `/mcp/observability`

**Scheduling / templates / approvals** — `/schedules` CRUD + `/run` + `/runs` +
`/preview` · `/templates` + `/presets` + `/run` · `/approvals` + `/{id}/approve|reject`

**Agents** — `/agents` CRUD · `/agents/{id}/run` (with optional project/prompt override)

**Security & usage** — `/findings` + `/summary` + `PATCH /{id}` · `/usage/summary`
· `/usage/timeseries` · `/usage/by-project` · `/usage/by-model`

**Webhooks / system** — `POST /webhooks/github` · `GET /system/claude` · `GET /health`

```bash
curl -X POST localhost:8200/tasks -H 'Content-Type: application/json' -d '{
  "prompt": "Write a haiku about orchestration to haiku.txt",
  "model": "sonnet", "priority": "high", "max_turns": 10, "max_budget_usd": 0.5
}'
```

Streamed events are normalized to: `started`, `context`, `mcp`, `system`,
`text_output`, `tool_use`, `tool_result`, `retry`, `log`, `error`, `completed`.

---

## Security reviews (VAPT / STQC)

The **Security Review** button (Tasks tab) runs a **read-only** audit of a
project's branch — OWASP Top 10 / CWE Top 25 — and produces a findings report.
After each scan, findings are extracted (via `--json-schema` for reliable output)
into the **Security** tab: severity, CWE, `file:line`, status lifecycle
(open / fixed / accepted / false-positive), and **new-vs-recurring** tracking
across scans. Set a project's **GitHub repo** + **Auto-review PRs**, add a repo
webhook to `/webhooks/github`, and every PR triggers a review whose findings land
in the tracker automatically. (Needs the server reachable by GitHub — deploy it or
tunnel with smee.io / ngrok.)

---

## Agents

An **agent** is a reusable, governed role: a **system prompt**
(`--append-system-prompt`) + model + budget + max-turns + optional **default
project** (overridable at run time). Running one creates a task under all the usual
guardrails (auto-gating, cost caps, tool policies from the project's MCP scope). So
"run the *Security Auditor* on *Hydra*" is one action, and the same role works on
any project. Manage them in **Automation → Agents**.

**Starter agents** — one-click presets (`GET /agents/presets`): *Morning Brief*,
*Weekly Review*, *Security Auditor*. Click one under "Starter agents" to drop a
ready-made role into your workspace, then edit it.

**Schedules run agents.** A schedule can carry its own prompt *or* target an
agent (`agent_id`). When it targets an agent, each fire builds a task from the
agent's role, model, budget, and default project — so "every morning at 8am, run
the *Morning Brief* agent" is a single, governed automation. Set it up in
**Automation → Schedules** by picking an agent instead of typing a prompt.

---

## Personal automations (Life Dashboard)

The engine is general-purpose, so the same primitives — agents + schedules +
MCP connectors — power personal automations, not just dev work. The built-in
**Morning Brief** preset is a chief-of-staff role that summarizes your day; on a
daily schedule it becomes a Life Dashboard.

**Wire it up:**

1. **Add the agent** — Automation → Agents → *Morning Brief* (starter). Give it a
   **Personal** project and a small **budget cap** (e.g. `$0.50`) so a daily run
   can't run away.
2. **Connect data sources (MCP).** The brief needs read access to your calendar
   and mail. In **MCP**, add a server per source. Gmail and Google Calendar are
   available in this Claude session as managed connectors — for the orchestrator's
   own `claude` subprocess, register an MCP server (stdio command or HTTP URL) and
   store any token in the vault. Set tool policies to **auto-approve** the
   *read* tools (`list_events`, `search_email`…) and **block** anything that
   sends or deletes. Use **scope = user** to make a connector available to every
   task, or **scope = project** to limit it to *Personal*.
3. **Schedule it** — Automation → Schedules → New, cron `0 8 * * *`, and pick the
   *Morning Brief* **agent** (no prompt needed). Keep it **disabled** until the
   connectors test green, then enable.

Because it runs as a normal task, you get the brief as an artifact/report, cost
tracking, and (if you flag it) an approval gate — same guardrails as everything
else.

---

## Remote Ops (SSH troubleshooting)

Point an agent at a server to diagnose it over SSH. Because a project task runs
with its **cwd set to the project directory**, an `Infra` project holds a
`servers.yaml` inventory the agents read at run time to resolve a target.

**The safe split** (approvals are task-level — the CLI can't pause mid-run at one
command, so read-only and mutating work are separate agents):

- **Remote Diagnostics** — read-only. Resolves the named server from
  `servers.yaml`, SSHes in (`ssh -o BatchMode=yes -o ConnectTimeout=10 -o
  StrictHostKeyChecking=accept-new -i <pem> …`), runs only observational
  commands, and reports *symptoms → evidence → root cause → proposed fix*.
- **Remote Remediation** — applies an **approved** fix. Marked
  **`requires_approval`**, so *every* run lands in Approvals first; you review the
  exact plan before it touches the server, then approve.

Two agent-level flags govern this (Agents form):

- **"Approval-gate every run"** (`requires_approval`) — used by Remediation.
- **"Allow remote login (SSH)"** (`allow_remote`) — **off by default**. Only agents
  with it on can open SSH; for everything else, `ssh`/`scp`/`sftp` are added to
  `--disallowedTools` and blocked (deny wins even under `bypassPermissions`). SSH
  is opt-in, not implicit.

**Setup:** create an `Infra` project, then manage its inventory from
**Automation → Servers** (add/edit/remove rows: name, host, user, PEM path, notes)
— this writes the project's `servers.yaml`, which the agents read at run time. (You
can still edit the file by hand; `docs/infra/servers.example.yaml` is a template.)
PEM keys live **anywhere on the host you choose** (not necessarily `~/.ssh/`; e.g.
`~/keys/…`, `chmod 600`) — only their *paths* are stored, never the keys. Enable
"Allow remote login" on the two Ops agents. See
[`docs/infra/RUNBOOK.md`](docs/infra/RUNBOOK.md) for the playbook. Read-only is enforced by the agent's prompt; for a hard guarantee use a
dedicated read-only SSH user with no sudo on the server.

---

## Testing

```bash
cd backend && python -m pytest -q      # 20 tests: units + API integration (mock claude)
```

Unit tests cover the vault, risk classification, transient-failure detection,
finding fingerprints, prompts, webhook signatures, and report generation.
Integration tests exercise the task lifecycle, approval auto-gate/approve/reject,
reports, usage, agents, schedules-run-agents, and the GitHub webhook — all against a mock `claude`
binary. **GitHub Actions** (`.github/workflows/ci.yml`) runs the backend suite and
the frontend build on every push/PR.

---

## Scope notes & limitations

- **Approval granularity**: tasks can be gated before they start (task
  approvals), and co-guard pauses individual risky tool calls mid-run for a
  person ([Agent guard](#agent-guard-co-guard)) — a seatbelt, not a sandbox.
- **MCP** is injected per task (stateless), not run as long-lived shared daemons;
  HTTP-transport health checks aren't validated yet.
- **Deferred:** Team & Auth (login/roles/audit), `docker-compose`, `decisions.md`,
  auto codebase overviews, full-text search, and per-project task serialization.

---

## Operations

Run with **`./start.sh`** — it kills whatever is on the ports first, so background
processes can't accumulate (a stack of stale schedulers is the usual cause of
duplicate scheduled runs and API rate-limit contention). For a durable deployment,
run the backend under a process manager (systemd) or a container with
`ANTHROPIC_API_KEY` set.
