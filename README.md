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

## Claude plan limits

Claude subscriptions (not API keys) have usage limits. The app now records what Claude reports about the plan and
reacts to it:

- A banner at the top says when your plan limit was reached ("Work starts again at 14:05") or is close.
- Work that needs a limited credential waits instead of failing: tasks show "Paused" with the time they start again,
  and workflow steps are held, then continue after the reset. Nothing is switched to the org fallback key.
- The Usage page has a "Claude plan" card (status, reset time, last checked; raw figures under "Details").
- Admins see a tag per person on the Users page and a "Try now" button that clears a stale hold.

To know:

- Not run against the real `claude` CLI. The event's exact shape and when it is sent come from the documentation.
- There is no live "% of your 5-hour window" between events: the headless stream does not give one reliably.
- No automatic fallback to the org key.
- The 3rd limit hit in a row stops the task ("Stopped — the Claude plan limit was reached three times in a row.").
- A hold that is not confirmed is capped at 6 hours and re-checked every few minutes.
- API keys and the org fallback key are never held: plan limits do not apply to them.

Owner checklist, on one real run with a subscription token:

1. Does the run emit a `rate_limit_event` at all (a setup-token run may not)?
2. Is `resetsAt` in seconds or milliseconds (the app guesses: above 10^12 means milliseconds)?
3. What scale is `utilization` (0-1 or 0-100)? It is shown only under "Details".
4. Which window does it describe (5-hour or 7-day)?
5. What does a run that hits the limit print, and is it recognised (task pauses with the right time)?

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
environment when the task has a project, as for memory calls), or the
organisation's fallback key when one is set and the asker has no credential,
the same way it pays for the person's own tasks; with neither the ask is
refused with 422 *Add your Claude credential to ask about runs*. The
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

## Decision memory

Phase 4a of the forge port. A project can keep a short list of decisions its
agents should respect: *we use X, never touch Y, here is why*. Each gets a
number (`D-001`, `D-002`, … never reused) and can name the files it covers.
Open it in a project's drawer, on the **Decisions** tab.

**What an entry holds.** A kind (`decision`, `rule`, `lesson` or `reference`),
a title (200 characters), a *why* (2 000), up to 20 *touches* (file paths or
globs such as `src/billing/**`, relative to the project, 300 characters each),
a *source* (300) and, for a `reference`, a *see* path in the project (a
reference must have one; the other kinds must not). Texts are cleaned and
redacted when saved (common secret shapes only, see
[Seatbelt limits](#seatbelt-limits)).

**Who can read and edit.** Anyone who can see the project reads; project
maintainers (and admins) add, edit, verify, retire and supersede. Agent tokens
have no access. Verify, retire, supersede and edit only work on an *active*
decision (409 otherwise); an edit made on a stale copy is refused with 409
*changed by someone else, reload*. A decision is retired or superseded, never
deleted. Adding a decision counts as verifying it.

**When agents see notes.** Only on an *own* project, only when the
`decisions` ground-rules switch is on, and only for the project's active
decisions as they were when the run started (a decision added mid-run is not
seen by that run). co-guard shows notes right before an agent edits a file with
`Edit`, `Write`, `MultiEdit` or `NotebookEdit`, when the file matches a
decision's touches (an exact path, a folder, or a glob). Edits made through the
shell (`sed`, `echo >`, and so on) get **no** notes. Each note is the title, the
kind and date, the why, and the see path. At most 3 notes per edit, most
specific match first (exact, then folder, then glob; a decision ranks by its
best-matching touches entry), then newest, and a
decision is shown **once per conversation and per helper**; after a context
compaction they can be shown again. The notes are data, not orders, and tell the
agent: if the change would contradict one, do not make it, file a question with
your recommendation and end the turn. Each showing is logged on the run as
*Showed D-012, D-015 before editing path*. The matching is time-boxed (0.15 s and
4000 pattern checks per note), so a pile of very costly glob patterns cannot slow an
edit down. Decisions are checked newest first; when the time box runs out the older
ones are not checked, and the run-log line ends with *· some recorded decisions were
not checked*.

**The run-start index.** The same runs also get a short index (the header, then
one-line `D-n title` lines, at most 150 words and 1500 characters, with "and N
more" when cut) added to the
system prompt after the ground rules and again after a context compaction. At
most the 200 newest active decisions are considered.

**Health.** The tab checks the active decisions against the project folder
with two read-only git calls: a touches entry that matches no file, a missing
*see* path, files a decision covers that were changed in commits after it was
last verified (count and up to 3 commit subjects), and a decision with no
touches not verified for 30 days. **Verify** clears the "changed since" and
"not verified" flags; an unmatched touches entry or a missing see path needs an
edit. The panel is hidden
when all is well. It shows a note instead when the folder is missing, git
fails, or the project has more than 200 000 files; a project with no folder has
no health.

**Make this a rule.** For a decision, this opens the project's guard rule
editor pre-filled with a *never* rule that matches a path (a plain text match;
the editor offers text or regex), the first touches entry without wildcards
(a trailing `/**` or `/*` dropped), and the reason `D-012 title`. If no entry
qualifies the pattern is blank. Nothing is saved until a maintainer reviews it.

**The `decisions` switch.** **Agent guard → Ground rules** has a *Decisions*
switch, on by default. Off, runs get no notes and no index. It adds no rule
text of its own.

**Limits.** 200 active decisions are used per run; 20 touches per decision; a
glob pattern may be 300 characters with at most 30 `*` or `?`; notes are
capped at 2 000 characters in all.

**Not for external projects.** On a project whose trust is not *own*, agents
get no notes and no index (the people on it can still keep the list).

---

## Suggested decisions

Phase 4b of the forge port. Instead of waiting for someone to write every
decision, the server and the agents can *suggest* them. Suggestions wait in a
queue on the project's **Decisions** tab; **nothing becomes a decision until a
person accepts it.** Accepting makes a normal decision (next `D-` number, source
`suggestion: …`, you may edit the fields first); rejecting only closes the
suggestion.

**Who reviews.** Anyone who can work on the project (contributor and up) sees
the queue and can add a suggestion; project maintainers (and admins) accept or
reject. Two people racing on one suggestion: the first wins, the other gets 409.
The tab lists the newest 100.

**Where suggestions come from.**

- **After a run.** When a step on an own project finishes without failing, the
  server reads the commits it made (since the step started). Commits that are
  only housekeeping (`chore`, `docs`, `style`, `bump`, `release`, `merge`,
  `revert` or a version number as the subject) or touch only docs, text files
  and lockfiles are skipped; plans and specs under `docs/superpowers/` still
  count. If anything is left, **one** cheap model call (`CO_PROPOSALS_MODEL`) is
  asked for at most 2 entries. A commit already looked at is not asked about again.
- **Thread close.** Closing a thread's handoff asks **one** call for at most 3
  *lessons* (once per thread).
- **Answers.** A question's answer form has a checkbox that also suggests the
  question and answer as a decision (the answer never fails because of it; the
  reply says `added` or why it was skipped).
- **Agents.** The run's agent is told, in the decision index, how to file a
  suggestion (`POST /tasks/{id}/decision-proposals` with its own token); at most
  **3 per task**.
- **People.** `POST /projects/{id}/decisions/proposals` (there is no button on the Decisions tab yet), or
  the task route above as a task manager.

A suggestion is a `decision`, `rule` or `lesson` (not `reference`) with the same
text rules as a decision; a person or agent filing one with invalid touches
gets 422. Texts are redacted like decisions. Model replies are parsed
defensively; a reply that is not the expected shape adds nothing.

**Maybe out of date.** After the same run, active decisions whose *touches*
match a file the commits changed are flagged with a reason (*path changed in
abc123 (task title)*), shown as a "May be out of date" tag with the reason on the decision's row. Rules: only a
decision last verified more than **14 days** ago; not re-flagged within **1 day**;
at most **2 per commit** and **6 per run**. **Still valid** (verify) clears the
flag. Flagging changes neither the version nor the edited time.

**Conditions.** The automatic parts (run, close) need all of: an *own*
project, the `decisions` ground-rules switch on, `CO_PROPOSALS_ENABLED`, and a
Claude credential for the run's owner (or, at close, the person closing). A missing
credential skips only the model call (stale flags still run); missing own trust, the
`decisions` switch or `CO_PROPOSALS_ENABLED` skips the automatic parts entirely. They
run in the background and never fail the run or the close.

**Caps.** At most **50 pending** suggestions per project
(`CO_PROPOSALS_PENDING_CAP`): automatic ones are dropped when full, a person or
agent gets 429. A run's commits, a thread's lessons and an answer are each suggested
once (people's and agents' suggestions are not de-duplicated).

| Setting | Default |
|---|---|
| `CO_PROPOSALS_ENABLED` | `true` (off: no automatic suggestions or flags) |
| `CO_PROPOSALS_PENDING_CAP` | `50` per project |
| `CO_PROPOSALS_MODEL` | `haiku` |

**Not yet checked against the real CLI.** The tests use a mock. That `claude`
answers these two prompts in the format the parser expects (and what it costs),
and that agents really follow the index line and file suggestions, are checked
on the first live use. Run a task that commits to an own project and look at the
queue before you rely on it.

## Helper tiers and briefs

Phase 4c of the forge port. Two things: agents can be bound to a **model tier**
instead of a fixed model, and a task can carry a **brief** that says what it may
change, which co-guard then enforces for file tools.

### Model tiers

Three tiers, **strongest**, **standard** and **light**, each stand for a model.
The defaults are `opus`, `sonnet` and `haiku`. Admins edit them under **Agent
guard → Model tiers** (`PUT /guard/org-settings`, `model_tiers`, always the whole
map; a key left out takes its default). A value is `opus`, `sonnet`, `haiku` or a
`claude-…` model id of at most 32 characters; anything else is a 400.

**Which model an agent runs.** Its own `model` if set; otherwise the model of its
`tier` from the org map; otherwise the project's default. Changing the map moves
every agent on a tier at once. An agent's model must be a base model or one of the
three tier models.

**Helper presets.** `GET /agents/presets` has a *Helpers* group (all on the tier
shown, none with a fixed model):

| Preset | Tier | Read-only | Promises |
|---|---|---|---|
| Planner | strongest | no | designs a change and writes the plan and a brief per step; stops and asks when the goal conflicts with a recorded decision |
| Reviewer | strongest | yes | ranks findings H/M/L with file:line and evidence; "no findings" when there are none |
| Builder | standard | no | implements one brief test first, inside CAN change |
| Checker | light | yes | runs the named checks and reports facts only, "not run" for anything it could not run |

All four end their report with *Outputs* and the evidence. These are prompts, not
guarantees; what co-guard enforces is below. A read-only agent stays
read-only when run as a workflow step or a dispatched child, and follow-ups,
Continue and Duplicate of a read-only task stay read-only. A writable child agent
dispatched by a read-only coordinator is writable (its own setting).

**Lint (warnings only).** Agent responses carry `lint`, shown under the form. A
save is never refused for it. It warns when: the agent's own model is not one of
the org's tier models; the description lacks "Use for …" (when to use it); the
description lacks "because …" (why).

### Briefs

A brief is an object with these fields. Required: `goal`, `can_change`,
`cannot_change`, `done`, `stop`. Optional: `setup`, `log`, `retries`, `tie_break`,
`outputs`. A missing or invalid field is a 422 with a plain reason.

| Field | Limit |
|---|---|
| `goal` | text, 2000 characters |
| `done`, `stop`, `setup` | text, 1000 characters; `stop` may not be "never" |
| `log`, `tie_break` | text, 500 characters |
| `can_change`, `cannot_change` | lists of paths or globs inside the project, at most 20 entries and 30 wildcards; `can_change` empty means anything not under CANNOT |
| `retries` | whole number 0 to 10 |
| `outputs` | at most 10 names of 60 characters |

Texts are redacted like decisions. The brief is added to the run's system prompt
and shown on the task page as **Brief**.

**Where to give one.** The collapsible **Brief** section of the New task form and
of the agent *Run* form (single run), or `brief` in `POST /tasks`,
`POST /agents/{id}/run` and the items of `POST /agents/dispatch` (not
`/agents/{id}/bulk`).

**Inheritance.** Steps of a thread (follow-up, Continue, answer follow-up, a
guard deny-and-resume, Duplicate) keep the thread's brief. A dispatched child
started under a parent with a brief inherits it when given none. Workflow steps
do not inherit a brief. When given its own, the parent's CAN/CANNOT stay as a **bound**
the child cannot widen: a path must be inside every non-empty CAN and outside every
CANNOT of the brief and its bounds. Briefs nest at most **5 levels**.

### What is enforced

- **File tools, inside the project.** co-guard denies `Edit`, `Write`,
  `MultiEdit` and `NotebookEdit` (`notebook_path`) on a path outside CAN or inside
  CANNOT (rule `brief-scope`, shown as "Outside the task brief"). Always on, and
  never approvable. Paths are compared as given and after resolving symlinks, with
  Unicode NFC normalisation on case-insensitive systems, so a symlink or `..`
  cannot hide a path. It **fails closed**: a brief the hook cannot read, or a
  policy without a project folder, blocks every file change; a malformed brief
  stops the run before it starts.
- **Shell writes into CANNOT folders.** A CANNOT entry that is a path or
  `folder/**` becomes a read-only folder for shell commands (the same rule as a
  project's read-only paths). The task page notes how many folders this covers.

**Not enforced.**

- Shell commands against **CAN** (a command that writes outside CAN but not into
  CANNOT is not stopped).
- CANNOT entries containing `*` or `?` are **file tools only** for shell; so is a
  CANNOT path that is a symlink leaving the project. The task page lists these.
- Anything outside the project. That includes temp files and the orchestrator's own
  files, which are never blocked by a brief.

For tasks in a sandbox workspace, put the output paths under CAN.

**Not yet checked against the real CLI.** The tests use a mock. That the
PreToolUse deny reason reaches the model as worded (so it stops and reports rather
than retrying), and that agents follow the brief text, are unverified until a real
`claude` run. Run a brief with a CANNOT folder and read the log before relying on it.

## Command gates

Phase 5a of the forge port. A workflow stage can now be a **gate** (the server
runs commands itself and the real exit code decides) or an **approval** (a person
clicks). An agent can no longer say "the tests pass" and be believed: a gate has
no agent in it.

### Gate and approval stages

A stage has an optional `kind`: `agent` (the default, as before), `gate` or
`approval`. Only admins create or edit workflows, so an admin's save is the step
where a person approved the exact commands. Starting a run needs contributor on
the gate's and approval's projects, as for any stage.

| Kind | Fields |
|---|---|
| `gate` | `name`, `project` (an existing project with a directory), `run` (1 to 10 commands), `retry` (one command or null), `before` (at most 5 lines), `timeout_min` (1 to 720, default 60, per command) |
| `approval` | `name`, `message` (at most 2000 characters), optional `project`, `before` |

Agent stages may carry `before` too.

**Command rules** (a 422 on save, with a reason that names the character): 1 to
500 characters, none of `;` `&` `|` `>` `<` a backtick or `$(`, and no control
character (newline, NUL ...). One command is one program call, such as
`pytest -q` or `npm test -- --runInBand`. A shell is only the way to start it:
`/bin/bash` if it exists, else `/bin/sh`, as `[shell, "-c", cmd]`, never
`$SHELL` or a PATH lookup.

**How a command runs.** In the project directory, in its own process group,
stdin `/dev/null`. The environment is built from scratch: the server's allowlisted
slice, the project's env (blocked keys removed, `PATH_PREPEND` honoured), then
`CI=1`. It never holds an agent or user credential, an org key, `CO_*`, a task
token or an ssh agent socket. `HOME`, `XDG_CONFIG_HOME` and `XDG_CACHE_HOME` point to
a private folder under `_gates`, and the global and system git config are
skipped. **A gate is not a sandbox.** It runs as the server's OS user, so
absolute paths are readable and a program that looks up the user's home itself
can still find it. Exit 0 passes. Anything else, a
timeout or a kill does not. Past `timeout_min` the process group gets SIGTERM,
then SIGKILL after 5 seconds. Cancelling the run kills it the same way.

### The guard check and Allow and run

Just before each command starts, the gate project's guard policy judges it as a
`Bash` call. This happens **even if the project's guard is off**, because no one
watches a gate.

- **Deny:** the gate fails without running: "blocked by the guard: <reason>".
- **Approve:** the stage waits in `needs_approval` and lists the reasons. A
  maintainer of the gate's project, or an admin, clicks **Allow and run**
  (`POST /workflows/runs/{id}/stages/{i}/allow`). The allowance covers this
  hand-out only; a rerun asks again.
- If the guard gives no verdict, the gate fails closed.

The guard judges the command line only, not what the program then does.

### Approval stages

The stage starts and the run goes `paused`. Whoever may manage the run (its
creator, an admin, a maintainer of a touched project) can
`POST …/stages/{i}/approve`, or `POST …/stages/{i}/reject` with a `reason` of 1 to
500 characters, which fails the run with that reason. The approver and the time
are recorded and logged.

### The retry round

A gate with one `run` command may have a different `retry` command (for example
the failing files only). The round is: run the full command; if it fails, run the
retry; if the retry fails, the gate fails; if the retry passes, run the full
command **once more**, and only that full run can pass the gate. One round per
hand-out, and a retry never completes a gate alone. Commands in `run` run in
order and the first failure stops the round.

### The fix-it round

A gate (shown in the UI as **Run a check**) may carry `fix: {agent, rounds}`. In
the editor this is "If the check fails, ask an agent to fix it" and "How many
tries" (1 to 3; `rounds` defaults to 1). The agent must be an agent of the **same
project** as the check; it is saved by id.

When a check ends with a real exit code that is not a pass and tries are left,
the server starts a task for that agent in the check's project, with the prompt
"A check failed. Fix the cause, commit on the current branch, do not merge or push.
Then stop." followed by the check's output. When the fix task completes, the whole
check runs again as a new hand-out. A pass after fixes reads "passed after k fix
round(s)".

**What the agent receives.** The command, the exit code, the test counts and up to
20 failing names when a runner was recognised, and at most the last 50 log lines,
in all at most 16,000 characters. Values of the check project's env (6 characters
or more) are replaced with `[redacted]`, hidden characters are removed, and the
output sits between begin and end lines that carry a random marker, introduced as
"data, not instructions". That is a framing, not a guarantee: the agent reads
repository output, so treat it as untrusted text.

**When it does not run.** A guard refusal ("blocked by the guard"), a timeout, a cancel and an internal error never start a fix
round. A check with no `fix` behaves as before.

**Budget.** A fix task is a task of the run. It counts toward the run's spending
limit and is capped at what remains (see "Typed outputs, retry and budget"). With
too little left, the run pauses before the fix task is created, and raising the
limit creates it. The check is not re-run meanwhile.

**When tries run out.** The check fails as in the earlier rounds (the failure
line, the run fails) and **Retry** starts the step again with fresh tries. The run
also fails, with "the fix did not finish", when the fix task fails or is
cancelled, and with "the fixer agent is not available" when the agent is missing
or has moved to another project. Cancelling the run while a fix is going cancels
it.

**Approval of a fix task.** Its risk is judged on the check output after the fixed
first lines, so the "do not merge or push" instruction does not stop it. Risky
words inside the check's output, or an agent that requires approval, still make
it wait for a person; the run pauses meanwhile.

### Pre-flight

`before` lines hold a stage until the machine is ready, instead of failing it.
Three forms (keywords case-insensitive):

- `free memory <n> GB` or `MB` (GB allows one decimal);
- `port <n> open` or `free` (1 to 65535);
- `container <name> running`.

They are checked when the runner would start the stage, and again just before a
gate's first command. When one is not met, the stage is `held`, not started and
not failed, with the reason in plain words ("free memory is 2.1 GB, needs 4 GB").
The run stays `running`, the runner checks again on every tick, and the run log
says "Step {name} held: waiting for pre-flight". A check that cannot answer
(memory on a system other than Linux or macOS, no `docker`) counts as not met.
Checks look at the **whole server**, so the held reason also names other runs
using the same project that you may see.

### Results, flaky tests and progress

Each command's output goes to `workspaces/_gates/<run id>/<stage>-<n>.log`
(directories 0700, files 0600, 20 MB cap: past it writing stops and the command
keeps running). The last 5 logs per stage are kept, and agents cannot read the
folder (it is a guard protected path). The parser (`app/gates/test_results.py`)
understands pytest, jest, vitest, mocha, node:test, TAP, Playwright and
`go test`. The final summary line wins; with none, it counts per-test lines.
An unknown runner gives no counts and the exit code still decides. One row per
command goes in `workflow_gate_runs` (outcome, exit code, counts, failing names,
log name; the last 50 per stage are kept).

**Flaky, never "fixed".** Before each command the server hashes the code state
(`HEAD`, the diff against it, and the untracked files' names, sizes and times).
If a full run passes and an earlier full run of the same stage in this run
failed with a real exit code at the **same** code state, the earlier failing
names are marked flaky: "flaky (a flaky test is not a fixed test): a, b", or
"failed then passed unchanged" when the names were unreadable. A changed code
state, or a run with no exit code, never counts.

**Progress.** While a command runs, the run detail shows "31 of 63 tests done, 0
failed" (or "31 tests done") from the last 256 KB of the log, refreshed at most
once per 2 seconds per run. The log tail is at
`GET /workflows/runs/{id}/gate-runs/{rid}/log` (the last 200 lines, up to 64 KB,
project env values redacted).

### The cost line and the test chip

When a stage finishes, the run log line ends "So far: 3 of 6 steps done, 41 min,
$2.10, 1.2M tokens (last: Develop, 18 min, $1.40)". The same line is in the run
view header. Cost and tokens count every task of the run, every attempt.

**Test chip on agent tasks.** When an agent's `Bash` command looks like a test run
(`pytest`, `python -m pytest`, `npm test`, `npm run test…`, `yarn test`, `pnpm
test`, `npx vitest|jest|playwright test`, `vitest`, `jest`, `mocha`, `node
--test`, `go test`, after any leading `VAR=value` words), the worker parses the
full result with the same parser. If it finds a runner, the run log shows a line
such as "Tests: 61 passed, 2 failed (test_a, test_b)", from a `test_result`
event (counts, at most 10 failing names, the command cut to 200 characters,
all redacted with the task's secrets). `echo npm test` or `cat pytest.ini` do not
count. It is a convenience, not a gate: the agent still ran the command.

### Restarts, attempts and visibility

- **Restart.** Live process handles live in the one server process. If the
  server dies mid-command, nothing is recorded for that command. On the next
  tick the runner starts again at that command (those that already passed in this
  hand-out are not rerun) and the run log notes the resume. A result is recorded
  only if the run is active, the stage is current and the **hand-out** (a new id
  for each stage start and each retry of the run) is unchanged, so cancel or retry
  drops a late result.
- **Attempts.** Retrying a run no longer deletes the failed stage's tasks. They
  keep their `stage_attempt`, the board shows earlier attempts collapsed, and
  `{{prev}}`/`{{stageN}}` use the current attempt only.
- **Honest words.** A gate reads "running `pytest -q` (1 of 3 commands passed)" only
  while the server has a live child; otherwise "not yet passed (k of n commands
  passed, none running)".
- **Hidden from other projects' viewers.** A viewer who cannot see a gate's
  project still sees the run and that the step exists, but not its commands'
  records, summary, failing names, hold reasons, flaky names, who allowed it or
  progress; its log is a 404. A held reason never counts runs the viewer cannot
  see.

### Hardened git

The code-state hash reads a user's repo, so `gitsafe` gets exactly two new
entries (`diff --no-ext-diff --no-textconv --binary HEAD` and `ls-files --others
--exclude-standard -z`) and was hardened for them. Repo-defined **filter
drivers** (`filter.<name>.clean|smudge|process`) are switched off with `-c`
before every command, so a `.gitattributes` `filter=` line cannot run a program;
more than 50 drivers, or a driver name that is not printable ASCII, refuses the
command. **Submodules** are never entered: `status` and `diff` use
`--ignore-submodules=dirty` and short submodule diffs, so a submodule's own
config cannot run anything (a dirty submodule is not reported, only a changed
gitlink). Git run by a gate command has `GIT_SSH` and `GIT_SSH_COMMAND` set to `false`
and skips the global and system config; the repo's own `.git/config` still
applies. The diff is hashed, never stored or shown.

### Not covered

- **Fixing a guard refusal or a timeout.** The fix round only answers a check
  that ran and exited non-zero. The fix agent is a single agent in the check's
  project; the run never jumps back to an earlier step.
- **Windows.** The shell rule is POSIX only.
- **A plain `ssh` in a gate.** Only git over ssh is blocked. A plain `ssh`
  runs as the server's OS user, and OpenSSH finds keys through the user database
  rather than `HOME`, so it can use that user's `~/.ssh` keys. No ssh agent socket
  is passed. Gate commands are written by an admin and checked by the guard, but
  they are not sandboxed.
- **Code the gate runs.** A gate runs repository code (`conftest.py`, `package.json`
  scripts, a `Makefile`) that an earlier agent stage may have written. The guard
  checks the command line, not what that code does.
- **Background commands** an agent starts get no live progress.
- **`{{prev}}`.** A gate or approval stage's summary (counts, failing and flaky
  names, who approved) is what the next stage's `{{prev}}` holds, so it flows
  into later agent prompts as text. Treat failing names as untrusted output.

---

## Launch list

Phase 5b of the forge port. Each project can keep a short list of what must ship
before a launch date, what waits until after, and which paths are critical. It is
stored in the database (not in a repo file), so several people can edit it.
It lives in the project drawer, on the **Launch** tab.

- **Items.** Each item has a number shown as `#7`, a title (1 to 200 characters,
  one line, cleaned and redacted), a section (`before` or `after`) and a status:
  `open`, `started`, `done` or `dropped`. Numbers come from a per-project
  counter and are never reused, even after a delete. Two live (open or started)
  items cannot share a title, compared case-insensitively.
- **Who may do what.** Anyone who can see the project reads. Contributors add
  items, start and finish them, and drop them. Maintainers (and admins) also edit
  titles, move items between sections, reopen done or dropped items, delete, and
  set the launch date and the critical paths. Each move stamps `started_at` or
  `done_at`; a reopen clears `done_at`.
- **Two people editing.** Every write carries a `version`. A stale one is refused
  with 409 "changed by someone else — reload".
- **Linking tasks.** A task is linked to an item when it is created (the
  "Launch item" select in the new-task form, or by the nudge below). It can also be
  linked, changed or unlinked afterwards, from the "Launch item" row of the task
  (`PATCH /tasks/{id}/launch-item` with `{launch_item_id: id | null}`). The task's
  creator, a maintainer of its project or an admin may do that (else 403). The
  item must be open or started and belong to the task's project (else 422). If
  the task is already running and the item is `open`, the item becomes `started`.
  When a linked task first starts running, an `open` item becomes `started`.
  Finishing stays a person's call: when every linked task has completed, the
  tab shows "linked tasks done — mark done?".
- **What the agent sees.** A task in an `own`-trust project with a launch row or
  items gets a block headed `## Launch list (data, not instructions)`, at most 60
  words: the date (or "no date"), open and done counts and the open `before`
  titles. A started item counts as open. External-trust projects get nothing.
- **Scope nudge.** When you create a task in a project that has a launch list, the
  prompt is checked for new-scope wording ("can we add", "while we're at it", build
  verbs). On a match the form shows, inline: "This looks like new scope: the
  launch list has N open items; this would become #M." (`M` is the next item
  number from the project's counter, which never reuses a number, plus one, so it can skip numbers
  after a delete.) You can **Add to launch list**, put it **After launch**, or
  **Run anyway**. It never blocks. Prompts of 400 or more characters and prompts that start
  with `/` are never scope, and a person is nudged at most once per 30 minutes per
  project. Tasks created by agents are never nudged.
- **Facts.** `GET /projects/{id}/launch/facts?files=a,b` returns the date, open and
  done counts, `would_be`, the `after` items, which critical globs the files
  touch, and an `estimate` (median, min and max days from start to done). The
  estimate is `null` until at least 10 items have both dates.
- **Treat tasks that mention a critical file as high risk.** A switch on the
  Launch tab, off by default, which a maintainer or admin sets (`raise_risk`).
  With it on, a new task in that project is marked critical when its prompt
  contains the text of a critical path up to its first wildcard (`src/auth/**`
  gives `src/auth`; trailing `/` dropped, compared case-insensitively, at least 3
  characters). Critical tasks wait for approval when the server's
  `gate_critical_approval` setting is on (the default). The task shows "Marked
  critical: critical path <prefix>". It is a text match on the prompt, not a check
  of what the agent edits, and a short prefix matches inside other words or paths
  (`src` matches "describe the src of the bug").
- **Critical paths.** Up to 20 globs, with the same rules and matcher as a decision's
  `touches`. When a task in such a project ends, in any end state, the files targeted by its
  `Edit`, `Write`, `MultiEdit` and `NotebookEdit` calls (including failed ones, and
  earlier retry attempts) are matched against them. Paths outside the project directory
  are ignored, at most 500 calls are remembered, and the event shows at most 10 paths. A hit adds a `critical_paths` event, shown on the task as "This run changed
  N critical path(s): ...".
- **Questions.** An open question whose text names an open launch item (title of
  at least 3 characters, case-insensitive) shows "blocks launch item #3: Title".
- A task cancelled during the retry backoff is ended as `cancelled` when the backoff
  elapses, not left queued.

### Not covered

- **Repo-file sync.** There is no `launch.md` sync, and items that arrived with git
  are not picked up.
- **Critical paths raise risk only when the switch above is on, and only from
  the prompt text.** After a run they warn about the files it changed. Short prefixes
  over-match.
- **Shell-command edits.** A change made by a shell command (`sed -i`, `git apply`)
  is not seen by the critical-path check.
- **Done dates.** A finished task does not mark its item done.
- **The nudge cooldown** is in memory per server process, so a restart forgets it.
- **Duplicate titles** are checked by reading first, so two identical adds at the same
  instant could both pass (their numbers stay distinct).

---

## Workflow run control

Phase 7a of the forge port. Four things on a workflow run, all on the run's own page.

- **Approve ahead.** A person who may manage the run (its starter, an admin, a maintainer of
  a project it touches) can approve an "Ask a person" step before the run gets there, and
  withdraw that approval (`POST` / `DELETE /workflows/runs/{id}/stages/{i}/approve-ahead`).
  When the run reaches the step it goes straight past, and the step reads "Approved ahead by
  <name>". Only steps marked "Can be approved ahead" (a switch in the step's form; the template's
  "Approve plan") can be approved early; workflows saved before this update have none marked until
  you switch it on. Any other approval step's card says "Needs you when the run gets here". An early
  approval is dropped when the run fails, is cancelled or is run again from an earlier step.
- **One check at a time per project.** A check step takes a lock on the project folder before
  its first command and lets go when the step ends. If another run holds it, the step waits
  ("Waiting for checks from another run in this project to finish.") and tries again; it never
  fails because of it. Agent steps are not locked.
- **Run again from a step.** On a finished (completed or failed) run, "Run again from here" in a
  started step restarts from that step (`POST /workflows/runs/{id}/stages/{i}/rerun`). That step and the
  steps that wait for it start over; steps that don't wait for it keep their results (on a failed
  run, other unfinished branches start over too); old tasks stay in the history. The
  spending limit still applies. The failed-step Retry is unchanged.
- **Time and spend per step.** Each run box shows a small line such as "4 min · $0.12"; tokens
  are under the step's Details. A check step's card leads with "Waiting its turn" (another run's
  checks hold the folder) or "Interrupted — will continue" (its process is gone and it is about to
  be resumed) when that is so; the step's tag and the run box's label use the server's word
  (Running, Waiting its turn, Waiting for approval, Failed, Interrupted — will continue).

The handoff card now lists "Re-check before acting" items (note lines of the form
`Caution: … | measured: … | check again before acting`) and shows the newest 5 files, with the
rest under "The full list".

### To know

- Steps run one after another unless you set them up to run side by side (next section).
- The project check lock is in memory (one server process, as today).
- An early approval is approval of the step as defined when the run started; nothing about later
  output is checked.
- Not run against the real `claude` CLI.
- Ruling: gate-command re-approval by hash is not built. Workflows are database records only
  admins can change and the guard judges each command when it runs. Cost if wrong: an admin's
  edited command runs without a second person.
- Ruling: agent steps are not covered by the project lock. Cost if wrong: two runs' agents can
  still edit one checkout at once (as today).

---

## Steps side by side

Phase 7b of the forge port. A step can say which earlier steps it waits for, so several steps run at
the same time: for example "Write tests" and "Write docs" both after "Build", and "Review" after both.
Workflows saved before this update keep running one step after another, exactly as before.

- **In the editor.** Click a step and use **Waits for**: pick the earlier steps by name (the step
  before is the default; leave it empty and the step "Starts at once"). **Run beside the step before**
  makes a step wait for the same steps as the one before it. **Settings** has **Steps at the same
  time** (1 to 4, default 2). The picture puts steps in columns by how long a chain of waits leads to
  them; steps that run together are stacked, with a line from each step waited for. "+" on a line still
  adds a step in sequence.
- **Moving and deleting.** Moving a step re-numbers what the others wait for; a step cannot go above
  a step it waits for (the arrow is greyed and says why). Deleting a step takes it out of what the
  others wait for, and every step that waited for it now also waits for what the deleted step
  waited for, so nothing starts earlier than before; the editor says so.
- **Problems are listed all at once**, one plain sentence per line above the picture, for example
  "Step 4 waits for step 6, which comes later." or "Run at most 1 to 4 steps at once."
- **On the run page** every step that is working shows "Running" at the same time; the header still
  reads "N of M steps done". A step that fails stops the others that are working, the same way Cancel
  does; Retry continues. "Run again from here" restarts that step and every step that waits for it.
  Approvals waiting for a person do not stop other steps; the run shows "Waiting for you" only when
  nothing else can run. A step that waits for several steps gets their results one after another,
  each under "From <step name>:" in "the previous step's result".

### To know

- "Run this only if ..." and "continue when any one of these finishes" are not built.
- Two agent steps working at once in one project share its folder, as two agents of one step already do.
- Past the limit of steps at the same time, ready steps wait their turn, lowest number first.
- A plain line workflow can be moved around freely; once any step says what it waits for, the order
  rule above applies to the whole workflow.
- Not run against the real `claude` CLI.

---

## Workflow templates

Five built-in templates start a workflow in the editor: **software**, **planned**
(approve the plan, parallel builders, gate, review, release approval), **documents**,
**spike** and **ui**. They use the helper presets by name (Planner, Builder,
Reviewer, Checker) plus approval and gate stages.

- **New from template** (Workflows tab) opens the editor pre-filled. Nothing is
  saved until an admin saves it.
- **Gates need a project.** A template's gate carries `run: ["npm test"]` and no
  project. The editor shows "Set the project's own test command in the gate." The admin picks
  the project and the real command; save is refused until a project is set.
- **Builders** are told: "Commit on the current branch. Do not merge or push."
- **Missing helpers.** If a template names an agent that does not exist, the editor
  lists it ("Add these helpers from Agents → presets first") and save stays refused
  by the normal check.
- **Export.** `GET /workflows/{id}/export` returns Markdown with a title, the
  description and exactly one fenced `json` block holding `{name, description, stages}`.
- **Import (admin).** `POST /workflows/import` takes `{text}` (at most 64 KB, exactly
  one `json` block) or `{project_id, file}` for a file from the project's
  `.claude-orchestrator/workflows/` folder (own-trust projects only; at most 20 files,
  `*.md`, regular files, no symlinks, 64 KB each). It runs the same validation as a
  save. The server creates the workflow, then the UI opens it in the editor.
  Import never runs anything.

### Not covered

- **Writing into project directories.** The server never writes a workflow file into a
  repository; export is a download.
- **Review before create.** Import creates the workflow on the server first. The
  editor opens it afterwards for review and a later save.
- **External-trust projects.** They list no repo workflows (with a reason).
- **Phase 5c items.** The built-in templates do not set outputs, "Try once more
  if it fails", a spending limit or a fixer agent; an admin adds them in the
  editor (see "Typed outputs, retry and budget").
- The built-in Canonizer presets are unchanged.

---

## Typed outputs, retry and budget

Phase 5c of the forge port. Steps can hand named values to later steps, an agent
step can run once more after a failure, and a run can have a spending limit.

### Named outputs

In the editor, each agent under an agent step has "What should this agent hand
over? (optional)". In JSON it is `outputs` on a stage item: up to 10 names, each
`^[a-z][a-z0-9_]{0,39}$` and unique. The server adds an instruction to that
agent's prompt: end the final message with a fenced `json` block holding exactly
those keys, each a short string.

When the task completes, the server reads the **last** `json` block in the final
64 KB of its result and checks it:

- it is valid JSON, an object with exactly the declared keys, and every value is a
  string of at most 2000 characters (hidden characters removed);
- a name ending in `_path` is a non-empty path that is relative, has no `..`, exists
  in the task's project folder and, after following symlinks, is still inside it;
- a name ending in `_commit` is 7 to 40 lowercase hex characters that name a commit
  in the project's repository (checked with `git rev-parse`).

A path or commit name needs the task to have a project folder. When any check
fails, the task is marked failed with "outputs invalid: <reason>" (for example
"plan_path must not contain ..") and the step fails like any failed task. The
values are stored on the task and shown on the run page as "Handed over".

**Using them.** A later prompt may hold `{{stageN.name}}` (step N, 1-based) or
`{{prev.name}}` (the step before). They use the current attempt of that step; if
several agents in the step gave the same name, the values are joined with a blank
line. An unknown step or name gives an empty string. A check can only supply its
summary through `{{prev}}` or `{{stageN}}` as before.

**Single pass.** The server substitutes placeholders in one pass over the prompt,
so a value is never expanded again. `{{prev}}` typed into the run input is no
longer expanded (before 5c it was), and neither are placeholders inside outputs
or results. A prompt that contains no placeholder still gets the run input
appended.

### "Try once more if it fails"

An agent step may set `auto_retry: true` (the switch "Try once more if it
fails"). When a task of the current attempt has failed (an "outputs invalid" task
counts) and none was cancelled, the step runs again as a new attempt. This happens
**once per step per run**: a failure of the second attempt fails the run, and a
person's **Retry** later does not re-arm it. It never happens after a cancel. While
other tasks of the same attempt are still active, the server waits for them first.
Log line: "Step {name} retried automatically". The retry is subject to the
spending limit.

### The spending limit per run

A workflow may have `budget_usd` (0.01 to 10000; the editor's "Spending limit for
each run"), copied onto each run when it starts, so changing the workflow does not
change runs already started. The run page shows "Spent $X of $Y". Spent is the
cost of every task of the run, every attempt, fix tasks included.

- **It pauses before new work.** Before an agent step, a retry or a fix task
  starts, the server checks the spent amount. If less than $0.01 remains, the run
  pauses with "Step {name} held: budget reached ($S of $B)". Nothing already
  running is stopped. Checks and approvals cost nothing and are not held.
- **Each new task is capped at the remainder.** Its own cap is the smaller of the
  agent's (or the server default) and what is left.
- **It can still be exceeded.** Agents in the same step start together, each
  capped at the remainder, so together they can spend more than is left.
- **Raising it.** Whoever may manage the run (its creator, an admin, a maintainer
  of a touched project) uses "Raise the limit and continue" on the run page, or
  `POST /workflows/runs/{id}/budget` with `{budget_usd}`. The new value must be
  more than what is spent (else 409), and the run must still be active. The held
  step then starts.

### Not covered

- **Non-string outputs.** Values are short strings. Lists, numbers and objects are
  refused.
- **`--json-schema`.** Outputs rely on the fenced block and the checks above, not
  on the CLI's schema option. An agent can format badly; "Try once more if it
  fails" covers one miss.
- **Fixing a guard refusal.** A refused or timed-out check is not handed to the
  fixer.
- **The critical-file switch over-matches.** Short prefixes match inside other
  text, and it does not look at which files an agent edits.
- **Output values are not proof.** The checks show a path exists or a commit
  exists, not that its content is right.

---

## Skill lint

The **Skills** tab has a **Skill template & lint** panel, ported from jspark-forge's
skill lint. It shows a template `SKILL.md` and a box where you paste your own.
**Lint** reports problems by line.

- **Rules.** The file starts with frontmatter that has `name` and a `description`
  beginning "Use when". If the body has numbered parts with `Input:`, every part needs
  `Input:`, `Action:` and `Output:`, and the last part is `Verify:` with `Evidence:`.
  HTML comments are ignored, with line numbers kept. A plain skill with no `Input:`
  parts needs only `name` and `description`. Text over 256 KB is refused (422).
- **API.** `POST /skills/lint {text}` returns `{ok, problems: [{line, message}]}` and
  `GET /skills/template` returns `{text}`. Any signed-in user may call them.
- **Installed packs.** The **Lint** button on a pack calls `GET /skills/{id}/lint`,
  which lints every `SKILL.md` in the pack's directory: at most 200 files and 256 KB
  each (an oversized file reports "file too large"). It does not follow symlinks.
  Project packs are only visible to that project's members.
- **Warnings only.** Nothing is blocked, changed or saved.

### Not covered

- **Writing skills.** The server writes no skill, brief or workflow into a project
  directory; copy the template yourself.
- **Other lint.** Briefs are checked by their own validator (phase 4c); agent and
  graph file lint from forge are not ported.
- **Packs on disk only.** Files beyond the 200-file cap are not checked.

---

## System health

Admins get a **System health** page (nav entry, `#/health`). It shows one overall line
("Everything looks good", "2 things need attention" or "1 problem") and a card for each
check, problems first. Each card has a coloured dot, a plain title, one sentence and a
collapsed **Details** with versions, timings and a suggested fix.

- **The fifteen checks.** Claude is installed; Python; Git; Node; The guard is intact;
  The guard blocks what it should; Guard errors in the last 7 days; The app can write its
  folders; Database is up to date; Connected tools; Skill packs; Code map; Project folders (a warning when a
  folder now holds a different repository, see "Upkeep"); Docker (for 'wait until
  ready' checks); Free disk space. A missing Node or Docker is only "attention"; a
  missing Claude or Git is a problem.
- **When it runs.** Only when an admin opens the page or presses **Check again**; there
  is no background job. Results are reused for 30 seconds (per server process), and
  **Check again** skips that reuse (`GET /admin/health?refresh=true`).
- **Time limits.** Each check gets 10 seconds and the whole run 60. A check that fails,
  times out or does not finish shows as a problem with the summary "could not check".
- **What it runs.** The only Claude command is `claude --version`; a prompt is never
  sent. It also runs `git --version`, `node --version` and `docker version`.
- **Guard self-test.** It feeds the guard's hook one harmless command and one
  dangerous command (`rm -rf /`) and checks the answers are allow and deny. The
  dangerous command is only evaluated, never executed. The test uses a throw-away
  policy and a throw-away folder, and is pointed away from the real server.
- **Secrets.** Every summary and detail is scrubbed: known secrets are masked and home
  folders show as `~`. Text is cut at 500 characters.
- **API.** `GET /admin/health` returns `{checked_at, overall, checks}`. Admin only.

### Not covered

- **No auto-fix.** It reports and suggests; it changes nothing.
- **No background monitoring and no alerts.** Nothing is recorded between visits and
  nothing is sent when a check fails.

## Licence hints

A project shows a small licence badge when its code might not be yours to reuse freely.
The badge shows on the project card, the table row and the drawer title. The project
drawer also has a "Licence:" line.

- **When the badge shows.** For every project whose trust is `external`, and for any
  project whose licence is share-alike (copyleft), proprietary, or missing. Your own
  project with a permissive or unrecognised licence shows nothing. The wording is the
  licence name (for example "MIT"), "GPL-3.0 — share-alike", "All rights reserved" or
  "… — not free for commercial use", "No licence found", or "Licence not recognised".
  The tooltip says "A hint, not legal advice."
- **Kinds.** `permissive`, `copyleft`, `proprietary` (this includes non-commercial
  licences and "All rights reserved"), `none` and `unknown`.
- **When it is worked out.** When a project is created, when projects are imported,
  when a project's directory changes, and when a maintainer presses **Check again**
  (`POST /projects/{id}/licence/refresh`). It is stored on the project and is never
  recomputed on a page load, so a licence file you edit later is not seen until one of
  those happens.
- **File rules.** Only the project's root folder is looked at. The first file, in name
  order, whose name starts with `LICENSE`, `LICENCE` or `COPYING` is used. It must be a
  regular file (a symlink or other special file is not followed and gives
  "not recognised"), and only the first 64 KB is read.
- **A hint, not legal advice.** The classifier is a small port of jspark-forge's. It
  matches licence text and can be wrong.

### Not covered

- **Sub-folder licences.** Licence files in sub-folders, and licences of dependencies,
  are not read.
- **Compatibility.** Nothing checks whether one licence is compatible with another or
  with how you use the code.

## Rules export

The rules agents work under can be downloaded as one plain-text Markdown file
(`rules.md`).

- **Where.** A **Download the rules as a file** button on the organisation Guard page
  (the whole organisation) and in each project's guard settings (that project).
  API: `GET /guard/export.md`, with `?project_id=` for a project.
- **What it contains.** A header line ("Rules for agents working on … generated …. For
  use outside the orchestrator; inside it these are enforced, not just written."), then:
  1. **Ground rules** that are switched on.
  2. **Always blocked** and **Needs a person's OK**: the built-in rules, one line each,
     in words.
  3. The organisation's own custom rules, or for a project, **This project's own
     rules**.
  4. For your own projects, rules from the project's rules file, if it has one.
  5. For your own projects, the project's active **Decisions** (number, title and why).

  Custom rules are shown as written. At most 500 rules and 100 decisions are listed,
  with an "and N more not shown" line.
- **Guard off for a project.** The built-in rules section is replaced by a sentence
  saying the guard is switched off for that project and the built-in rules are not
  enforced there. The ground rules, custom rules and decisions are still listed.
- **Who can download.** Any signed-in person. For a project they must be able to see it,
  otherwise the answer is 404. Agent tokens are refused.
- **Secrets.** The project's environment values are masked wherever they appear, and
  hidden characters are cleaned from the text. No environment value is written out.

### Not covered

- **A copy, not enforcement.** The file is for use elsewhere, such as another tool or
  a human reader. Nothing enforces it outside the orchestrator.

## Updating skill packs

The **Skills** tab lists installed packs. An admin sees an **Updates** column for each
pack that has a source address.

- **Check for update.** Asks the pack's stored source address which commit its main
  branch is on (`git ls-remote`, 20 second limit, https or ssh addresses only) and
  compares it with the commit installed. The answer is saved on the pack. If a newer
  commit exists, the row shows **Update to** the short commit; clicking it asks you to
  confirm, and **Update** goes ahead.
- **What Update refuses.** A pack with local changes (any uncommitted change in its
  folder); a pack with no source address, or one that is not https or ssh; a pack that
  is not a git checkout; a pack whose git settings were changed by hand (its
  `.git/config` may only hold the settings a plain clone writes, so things like a
  custom ssh command, an address rewrite or an include are refused, and so is
  **Check for update**); a pack that is not behind the remote; and anything that is not a
  fast-forward, meaning the remote history no longer continues from the installed
  commit. A refusal changes nothing and says why.
- **Nothing from the pack is run.** Git hooks are switched off, no credential helpers
  or prompts are used, and only fetch, status, rev-parse, config, ls-remote and a
  fast-forward-only merge are issued (plus a checkout when a preset is installed at its
  pinned commit). Any filter programs the pack defines are switched off, and git never
  follows an HTTP redirect to another address. Only one update per pack runs at a time.
- **Shallow packs.** Installs download only the latest commit. A pack in that state
  downloads its full history on its first update, so the first update can be slower
  and larger.
- **After an update.** The pack's lint is run again (see Skill lint) and any warnings
  are shown; the pack's version is re-read from its plugin file and the page refreshes.
- **API.** `POST /skills/{id}/check-update` and `POST /skills/{id}/update`. Admin only.
- **The superpowers preset.** The preset list includes **superpowers**
  (`https://github.com/obra/superpowers`) with a pinned commit (`22f0b463...`, the
  superpowers 7.0.2 plugin). An admin chooses to install it; nothing is installed
  automatically. Installing from the preset checks out exactly that commit (the
  Skills page sends it with the install request; if it cannot be fetched or checked
  out, the install fails and the folder is removed). **Check for update** then reports
  any newer commits, and **Update** moves the pack to the remote's newest commit, only
  when the admin chooses. Installing a pack by URL without a pinned commit still takes
  the repository's current latest commit.

### Not covered

- **Automatic updates.** Nothing checks or updates on a schedule.
- **Rollback.** There is no undo to an earlier commit.
- **Pack code is not reviewed.** The lint checks the shape of skill files only.
- **Update uses the stored source address.** It does not read the pack's own git remote.
- **SSH uses the server's own keys.** A pack whose address is an ssh address is
  fetched with the SSH keys and agent of the account the server runs as (host keys must
  already be trusted; there are no prompts). Anyone who can add such a pack makes the
  server use those keys.

## Where connected tools start

A connected tool that runs as a program on the server (the "stdio" kind) used to start
inside the project's own folder. Some launchers (such as `npx`) read files from the
folder they start in. So a file in the project, committed or written by an agent in an
earlier task, could decide what ran as the tool, before any tool call and outside the
agent guard.

- **What changed.** Every such tool now starts from a private folder that holds only a
  few inert marker files, made for that task and that tool, and then runs its own command unchanged (a small launcher,
  `backend/app/mcp_launch.py`, changes folder and starts the command). The tool is also
  told where the project is, in the environment variable `CO_PROJECT_DIR`, when the task
  has a project folder. Web (HTTP) tools, the **Test** button and the policies are not
  changed.
- **Marker files.** The tool's folder holds an empty `node_modules/`, an empty `.npmrc`
  and an empty `uv.toml`, so `npm`, `npx` and `uv` treat the folder itself as the project
  instead of looking for one in the folders above. There is deliberately no
  `package.json` (one would let a `package.json` higher up adopt the folder as a
  workspace).
- **The folders above.** npm also puts the `node_modules/.bin` of every folder above on
  the tool's PATH, and no marker can stop that. So the agent guard refuses, in any folder
  above the tool folders and the Code map build folder (up to `/`), direct writes of the
  files that steer launchers (`package.json`, `package-lock.json`, `node_modules`,
  `.npmrc`, `pnpm-workspace.yaml`, `.pnpmfile.cjs`, `uv.toml`, `bunfig.toml`,
  `.yarnrc.yml` and `pyproject.toml`), package-manager installs there (`npm`/`pnpm`/
  `yarn`/`bun` install, add, ci, link, update - also run through `npx`, `npm exec`,
  `pnpm dlx` or `corepack`), and `init`/`create`/`npm pkg set` writing a `package.json`
  there. The folder comes from `--prefix`, `--dir`, `--cwd`, `-C`, or the folder after a
  `cd`. In the task's own project or workspace all of these work as before; the project
  folder is also the exception for the folders above (see below). A `package.json` that
  already exists in a folder above (put there by a person, for example `~/package.json`)
  still makes a plain `npm install` land in that folder; `CO_TOOL_RUN_DIR` is the stricter
  option.
- **Own package cache and global folder.** Tools use their own npm cache,
  `workspaces/_mcp_cache/npm`, and their own empty npm global folder,
  `workspaces/_mcp_cache/npm-global` (both owner-only; agents cannot write them),
  instead of the server user's `~/.npm` and global packages, unless the tool's own
  settings set `npm_config_cache` / `npm_config_prefix`. So a package an agent installs
  with `npm install -g` is not picked up by `npx`. The Code map build has its own cache
  (under `_codemap/_home/`) and uses the same empty global folder.
- **A stricter setup.** `CO_TOOL_RUN_DIR` (unset by default) puts the tools' per-task
  start folders in a folder of the admin's choice instead of under `workspaces/`. Point
  it at a folder whose parent folders the server user cannot write (for example one an
  administrator creates under `/var/lib` and hands to the server user); then nothing can
  be planted above a tool folder at all, whatever the guard sees. The tool folders go in
  its own subfolder, `co-tool-folders/`; the same protection and cleanup apply there, and
  the cleanup only ever removes folders named like a task id, so other files in the
  configured folder are never touched.
- **The switch.** On a tool's form, under **More options**, **Start in the project
  folder** (admins only, stdio tools only, off by default). Use it only for a tool that
  must start inside the project; the project's own files can then change what the tool
  runs. With it on the tool starts exactly as before, and the list shows a small tag,
  "Starts in the project folder". The **System health** page counts such tools under
  **Connected tools**.
- **A command that starts with `./`** (a file in the project) cannot work from the tool's
  own folder. The form refuses to save it unless the switch is on: "A command that starts
  with ./ needs 'Start in the project folder'". A bare name such as `npx` or an absolute
  path is fine.
- **Tools already added** move to the new behaviour on their next task. Nothing needs
  doing.
- **Cleanup.** The tool settings folder for a task (the tool config and the tools' start
  folders) is removed when the task ends. Before this change it was left behind.

### Not covered / to know

- **This has not been run against the real `claude` command-line tool or real connected
  tools.** The change is a plain change of folder followed by the tool's command. If a
  tool stops working, turn its **Start in the project folder** switch on.
- **The guard is a seatbelt, not a sandbox.** A tool can still read the project: it is
  told where it is and runs as the same user, with the same credentials as before. The
  folder a tool starts in, its package cache and its global folder are out of agents'
  reach. The folders above it are protected only as far as the agent guard sees the
  write: archive and tree-copy tools (`tar -C`, `unzip`, `rsync`, `cp -r` into a parent),
  inline scripts and paths hidden behind unknown variables are known limits, as they are
  for the rest of the guard. Use `CO_TOOL_RUN_DIR` (above) to close this without relying
  on the guard.
- **A project whose folder contains the workspaces folder** (the orchestrator checkout
  itself, used as a project) keeps its own `package.json`, `node_modules`, `.npmrc` and
  the like writable, because they are its own work. Its agents can therefore still
  influence what every connected tool and the Code map build run, in every project.
  Its agents can also change `start.sh`, `frontend/`, `backend/scripts/` and
  `backend/tests/` without approval; the server's code (`backend/app`, migrations, the
  venv, `.env`) stays protected.
- **Settings an admin puts in a project's env** (for example `NODE_OPTIONS`,
  `NODE_PATH`, `PATH_PREPEND` or any `npm_config_*` value) reach the Code map build, and
  reach tools if `claude` passes its environment on. Point them only at folders agents
  cannot write.
- **`npx` and `uvx` are covered best.** `pnpm dlx` and `bunx` get the protected names
  above, but have not been looked at as closely. `uvx` still reads the server user's own
  `~/.config/uv/uv.toml` and uv cache.
- `npx` still reads the server user's own `~/.npmrc` (needed for private registries).
- If the server stops in the middle of a task, that task's folder may stay on disk; it is
  removed the next time the server starts.

### Project folders that are part of the orchestrator

A project's folder cannot be the server's own code, migrations or virtual environment
(`backend/app`, `backend/migrations`, the venv) or one of the app's private
`workspaces/_…` folders, or any folder inside them. Creating, importing or moving a
project there answers 422 "That folder is part of the orchestrator itself". A folder
that only contains them (the orchestrator checkout, `backend/`, `workspaces/`) is
allowed, and saving a project with an unchanged folder is never refused. Whatever the
folder, the server's own code stays protected from the project's agents.

## Code map

A code map is a searchable index of a project's code. An agent can ask it "where is
this function used?" instead of reading many files. An outside tool,
**codebase-memory-mcp**, builds and answers from the map; this app does not build one
itself. What the app adds around it is freshness (how current each answer is),
recorded decisions for the files an answer names, and labels for old code.

- **Setting it up.** Nothing is built until both steps are done.
  1. An admin opens **Connected tools** and adds the **Code map** tool from the preset
     list (`npx -y codebase-memory-mcp@0.12.1`, a pinned version). The preset only
     fills in the form; nothing installs until the admin saves it.
  2. A maintainer opens the project, goes to its **Code map** tab and switches on
     **Keep a map of this project's code**. The project needs a folder that exists. For
     a project whose trust is `external` (outside code), only an admin can switch it on;
     the server answers 403 to anyone else. Nothing switches it on by itself.

  Until a code map tool is added (and enabled) for the project, no build runs: not after
  a task, not in the 10-minute pass, and **Refresh now** answers 409 "Add the Code map
  tool first".
- **When it refreshes.** A map is rebuilt when a task of the project finishes and the
  project folder's commit differs from the one the map was built at; every 10 minutes
  (a server-wide pass over switched-on projects whose commit differs); and when a
  maintainer presses **Refresh now** (`POST /projects/{id}/code-map/refresh`). After a
  failed build the next automatic try waits 2, 4, 8 ... minutes, up to 60 (the count
  doubles with each failure in a row); **Refresh now** ignores that wait. A build is
  stopped after 15 minutes. Only one build per project runs at a time, and at most two
  run across the server. A build left "running" by a server restart is marked failed.
  A folder with no saved changes history (not a git folder, a branch with no commit
  yet, or a folder whose commit the app cannot read without git, such as a linked
  worktree) has no commit to compare, so the automatic refresh rebuilds it at most once
  a day; **Refresh now** always works.
- **How the build runs.** The command is `npx -y codebase-memory-mcp@0.12.1 cli
  index_repository` followed by the project's folder. It runs without credentials: no
  API keys or tokens (no Claude, GitHub or `CO_` values) and no ssh agent. The
  project's own settings values (the project env) **are** passed, as for command
  gates. The home folder is a private one of its own (`_codemap/_home/`, owner-only),
  not the one command gates share. Git's repo-local `core.fsmonitor` and
  `core.hooksPath` settings are switched off for every git the tool runs, so a
  repository's own git config cannot run commands during the build. It runs from a private folder that holds only a few inert marker files, under the
  workspaces folder (`_codemap/run/<project id>/`), not from the project folder. This
  is on purpose: `npx` reads the `.npmrc` of the folder it runs in and prefers that
  folder's own `node_modules`, so a hostile repository could otherwise swap in its own
  code for the indexer. npm's own user and global settings files are also switched off.
  Output goes to a capped log (1 MB, owner-only) that is scrubbed before it is shown.
- **What the agent is told after each map answer.** When a task calls one of the code
  map's query tools (`search_graph`, `trace_path`, `detect_changes`,
  `get_file_outline`, `get_architecture`, `get_code_snippet`, `search_code`,
  `query_graph`), the guard adds one line to the answer:
  - `Code map: current (built at <commit>).`
  - `Code map: may be out of date. It was built at <commit>; the folder is now at
    <commit>. Check a file before relying on this answer.`
  - `Code map: freshness unknown.`

  The guard reads the project folder's git state directly (`.git/HEAD` and the branch
  file) and never runs git, so it can say the map "may be out of date" but not by how
  much. The project's **Code map** tab shows how many changes behind it is. These lines
  never change a guard decision, and the run log gets a line, "Code map answer:
  current", "may be out of date" or "freshness unknown". A map built from a folder with
  no saved changes history always gets "freshness unknown".
- **Decisions and old code.** The guard looks for file names in the answer (at most 20
  checked, and only files that exist inside the project folder). For those files it adds
  up to 3 of the project's recorded decisions, framed as data, not instructions. Each
  decision is shown at most once per conversation for map answers; this is kept
  separately from the note shown before an edit, so a map answer does not use that note
  up. Starting again after a compaction shows them again. The whole addition is capped
  at 2000 characters. For a project that has read-only reference projects, a map
  answer that names files inside a reference folder (a reference project that has its
  own code map switched on and built) gets `Old code: these files are from a read-only
  reference (built at <commit>). Never edit them.` The decision matching is time-boxed
  as for the note before an edit; when it runs out, the run-log line ends with "· some
  recorded decisions were not checked".
- **Where to look.** The project's **Code map** tab shows a plain status line ("Up to
  date", "3 changes behind — refreshing soon", "Built — this folder has no saved changes
  history, so freshness can't be checked", "Last refresh failed — the tool stopped with
  an error" (or "the tool took too long", "the tool could not start"), "Not built yet",
  "Add the Code map tool first"), **Refresh now**, and a collapsed **Details** with
  commits, times, the exact failure reason and the last 50 log lines
  (`GET /projects/{id}/code-map/log`). The **System health** page has a **Code map** card:
  how many projects keep a map, and it asks for attention if any refresh is failing or
  the tool is missing while a project has it switched on. Status is
  `GET /projects/{id}/code-map` (after a failed build, `failure` is `error`, `timeout`
  or `start`, and `error` holds the exact reason).

### Not covered

- **The default build command has not been run against the real tool.** The command is
  the setting `CO_CODE_MAP_INDEX_COMMAND` (a JSON list, default
  `["npx","-y","codebase-memory-mcp@0.12.1","cli","index_repository"]`; the project
  folder is added as the last argument). If it is wrong, the build fails and the error
  shows on the project's Code map tab and in the health card, so an admin can correct
  it. Related settings: `CO_CODE_MAP_SCAN_INTERVAL_SECONDS` (600),
  `CO_CODE_MAP_TIMEOUT_SECONDS` (900), `CO_CODE_MAP_MAX_JOBS` (2).
- **The tool when a task starts it.** A task starts the Code map tool from a private
  folder that holds only a few inert marker files, like every connected tool now (see
  "Where connected tools start"), unless its **Start in the project folder** switch is
  on. The build always runs from its own private folder with the same marker files, its
  own package cache and an empty npm global folder, and the agent guard refuses direct
  writes and package installs that would put launcher files in the folders above it.
  The limits listed under "Where connected tools start" apply here too: the guard is a
  seatbelt (archives, tree copies and inline scripts are known limits), the
  orchestrator checkout used as a project, and settings in a project's env, can still
  influence the build. (`CO_TOOL_RUN_DIR` moves only the connected tools' folders, not
  the build's.)
- **The exact commit list is not told to the agent.** It gets a short note (current, N commits behind
  with up to 10 changed files, a different history, or could not check); see "Upkeep".
- **No index of our own.** The app does not build or store a map; it depends on the
  outside tool.
- **A separate copy.** The freshness line reports on the project folder, even when a
  task works in a separate copy of it.

## Upkeep: closed handoffs, tidying, sharper notes

- **Closed handoffs stay closed.** Closing a thread's handoff now sticks: a heartbeat, a
  saved note or the end of the same run no longer reopens it. A new run of the thread
  (Continue, Retry, a follow-up) does. When a task **completes** and nothing is left open
  (no open question, no open to-do, no step still running), its handoff is closed by the
  system. Failed, cancelled and waiting tasks stay open. Notes are kept.
- **Tidying old run history (off until an admin switches it on).** In **System health**, the
  **Run history** card has the switch **Tidy old run history**, **Keep full history for:
  30 / 60 / 90 days** (60 by default), "N tasks could be tidied (about X MB)", "Last tidied
  <date>: N tasks" and **Tidy now**. When on, a daily job handles tasks that **finished** more
  than that many days ago: their run events are replaced by one short summary (how many
  events of each kind, first and last time, and the last 20 log lines as plain text), and the
  previous version of their **closed** handoffs is dropped (the current one stays). It never
  touches running, queued, waiting or unfinished tasks, and never deletes a task, handoff,
  note or decision. **It can't be undone**: what it replaced does not come back (the summary
  stays). The run log shows the summary as "Older run history kept as a summary (N events)."
  with the kept lines under **Details**. API (admin): `GET/PATCH /admin/health/retention`,
  `POST /admin/health/retention/run`.
- **Sharper code-map notes.** At task start the app compares the code map's indexed commit
  with the folder's current one, and the agent is told one of: current; N commits behind with up
  to 10 changed files; built on a different history; could not check. A reference project that
  changed since its map was built says so, and references with no map yet are listed once.
  Only a few read-only git commands are allowed for this.
- **Decisions before shell commands.** When a shell command is allowed and writes files, the
  decisions recorded for up to 50 of those files are shown before it runs, as for Edit/Write.
- **Re-clone warning.** Each project remembers its repository's first commit (set when the
  project is created, when its folder changes, and at the first task start if empty). If a later
  task finds a different first commit, the run shows "This folder now holds a different repository
  than before (it may have been cloned again). Decisions and the code map may not apply." and
  **System health** shows a **Project folders** warning. A maintainer presses **This is
  expected** (`POST /projects/{id}/root-commit/accept`) to store the new one; nothing else
  changes it. A folder that is not a git repository, a missing git, or a shallow clone with no
  root gives no warning and stores nothing.

### To know

- Tidying is off until switched on; once on, it cannot bring back what it replaced. A Continue
  of a tidied thread rebuilds from the summary, so things worked out from events (files touched,
  step progress) start empty.
- The code-map note is worked out at task start, so within a long session it can be up to one
  task old.
- The re-clone check looks at the folder when a task starts and when System health is opened; it
  is not a background watch.
- Not run against the real `claude` CLI.

## Database tab

A project's **Database** tab shows its tables as cards: the columns, a key mark on the
primary-key columns, and the links between tables as small "→ customers" chips (click
a chip to jump to that table). It is worked out by reading the project's SQL migration
files in order. It never connects to a database and never runs anything. With more than
8 tables a search box appears.

- **Where it reads.** Inside the project folder only: `migrations/`, `db/migrations/`,
  `supabase/migrations/` and `prisma/migrations/` (each `<name>/migration.sql`), each
  folder on its own. Down and undo files are skipped. At most 5,000 files and 5 MB are
  read; at most 60 tables, 200 columns per table and 50 links per table are shown, and a
  limit that was hit is said on the tab. Links (symbolic links) are never followed.
- **What it understands.** CREATE TABLE, ALTER TABLE (add, drop, rename column; add,
  drop constraint), DROP TABLE and RENAME TABLE. A statement it cannot read is counted
  ("3 statements were not understood"), never guessed. A table changed inside a `DO`
  block or a function body is marked "May differ from what is shown". File positions,
  the commit and the decisions that cover a table are under the collapsed **Details**.
- **Who and how.** Any project member (`GET /projects/{id}/schema`, optional `?table=`).
  A task may read its own project's answer with its `CO_API_TOKEN`. Work runs in the
  background with a 10-second limit; over that the tab says "Reading the migration files
  took too long." with **Try again**. Nothing is stored; answers are kept in memory
  until the files change.

### To know

- Migrations written as code (Alembic, Django, Rails, Prisma schema files) are not read.
  Only SQL files.
- The schema is what the files say, not what a live database holds.
- Someone with two deliberately slow projects can make the tab answer "took too long"
  for a short while.
- Supabase projects show a high "not understood" count: security and ownership
  statements are counted, not applied.
- A very large single statement can run a few seconds past the time limit.

## Recommended tools for projects with a user interface

A project that has a user interface (a front-end dependency such as `react`, `next`,
`vue`, `svelte`, `vite` in `package.json`, or `.html`, `.tsx`, `.jsx`, `.vue` or `.svelte`
files in the folder, `src/`, `frontend/` or `frontend/src/`) gets a **Recommended** tab.
Projects without one do not show it (`GET /projects/{id}/recommended` says `has_ui`).

- **The list.** Browser (Playwright), Browser inspector (DevTools), Interface parts
  (shadcn, React projects only) and the frontend-design skill. Each shows **Added** or,
  for an admin, **Add** with a question first ("Add <name> to this project?"). Add
  creates a connected tool for this project only, named "<tool> (<project>)" (cut to the name length limit of 120), switched
  on (it is an admin's explicit click) and starting in the private folder, not in the
  project folder, through the same call as the **Connected tools** page. Non-admins see
  "Ask an admin to add this". The skill is added under **Skills**. Nothing is installed
  without a click.
- **Design skills text.** A maintainer can switch **Tell agents to use the design
  skills** (on by default) and name a brand skill (letters, digits, `.`, `_`, `:`, `-`;
  up to 120). When on, and the project has a user interface, one paragraph is added to
  the main session's ground rules and to every helper's start text, asking it to load the
  frontend-design skill and a brand skill if it has them.

### To know

- The two new presets (Chrome DevTools `chrome-devtools-mcp@1.13.0`, shadcn
  `shadcn@4.23.0 mcp`) were taken from the npm registry on 2026-11-24 and have not been
  run. Chrome DevTools needs Node 20.19 or newer.
- In a folder with more than 5,000 entries, a page file listed after that point is
  missed, so the Recommended tab may not appear for that project.
- From the first task after this update, projects with a user interface get one extra
  sentence telling agents to use the design skills. Switch it off per project on the
  Recommended tab.
- None of this has been run against the real `claude` CLI.

## Published outside the project

When a run puts something somewhere else (a page, a document, a pull request), the task
page shows a card, **Published outside the project**: the title, the link, and either
"Copy in the project: <file>" or an amber **No copy in the project**. The card is hidden
when there is nothing. A new ground rule, **Keep a copy in the project** (on by default,
under Settings, Agent guard, Ground rules), tells agents to keep the source of anything
they publish inside the project.

- **How it is found.** A publication is noted when a connected tool (a tool whose name
  starts with `mcp__`) has a last name part that contains `create`, `publish`, `upload`,
  `post`, `share`, `deploy` or `send`, or when the command is `gh pr create`, `gh gist
  create`, `gh release create` or `gh issue create`, **and** its answer holds a web link.
  Only the first link is used. Links to webhooks, links whose path looks like a secret,
  and links with non-ASCII or unusual host names are left out. The link is stored without
  its query string, fragment or password. At most 20 per task; the same link is listed
  once. The list fills in when a step finishes.

### To know

- A tool that publishes without returning a link is missed, and a link in a `create`
  answer that is not a publication is listed.
- Published links whose path looks like a secret are not listed.
- The "Keep a copy in the project" rule is on for everyone after the update; an admin
  can switch it off on the ground-rules screen.

---

## REST API

Full interactive docs at `/docs`. Highlights:

**Tasks**

| Method | Path | Description |
|---|---|---|
| `POST` / `GET` | `/tasks` | Create + queue (optional `brief`) / list (`?status=`, `?project_id=`, `?roots_only=`) |
| `GET` | `/tasks/{id}` · `/events` | Detail / full event log (`?after_seq=`) |
| `POST` | `/tasks/{id}/cancel` · `/retry` · `/duplicate` · `/followup` | Lifecycle actions |
| `POST` | `/tasks/{id}/continue` | Continue a stopped step (`mode`: `auto`, `resume`, `fresh`) |
| `GET` · `POST` | `/tasks/{id}/handoff` · `/handoff/refresh` · `/handoff/close` | Where the thread stands |
| `GET` · `PUT` | `/tasks/{id}/notes` | The agent's notes (`PUT` is also open to the run's own agent token) |
| `GET` | `/tasks/{id}/usage` | The step's token figures, the thread's so-far totals and tips |
| `GET` · `POST` | `/tasks/{id}/questions` | The thread's questions / file one (also open to the run's own agent token) |
| `GET` | `/questions` | Questions you can see (`?status=open\|answered\|dismissed\|all`, default `open`) |
| `POST` | `/questions/{id}/answer` · `/dismiss` | Answer (`{"answer": …}` or `{"accept": true}`) and continue the thread / close it |
| `GET` · `POST` | `/tasks/{id}/asks` | Your private asks on a step (newest 20) / ask a question about the run (202; 409, 422, 429 limits) |
| `GET` · `POST` | `/projects/{id}/decisions` | Decisions (`?status=active\|all`, `?kind=`) / add one (maintainer) |
| `GET` · `PATCH` | `/projects/{id}/decisions/{n}` | One decision / edit it (send its `version`; 409 when stale or not active) |
| `POST` | `/projects/{id}/decisions/{n}/verify` · `/retire` · `/supersede` | Mark checked / retire / supersede (`{"by": n}`) |
| `GET` | `/projects/{id}/decisions/health` | Which active decisions may be out of date |
| `GET` · `POST` | `/projects/{id}/decisions/proposals` | Suggested decisions (`?status=pending\|all`, newest 100) / add one (contributor; 429 when 50 are pending) |
| `POST` | `/projects/{id}/decisions/proposals/{pid}/accept` · `/reject` | Accept (send the final fields; makes a decision) / reject (maintainer; 409 when already reviewed) |
| `POST` | `/tasks/{id}/decision-proposals` | Suggest a decision (also open to the run's own agent token; 3 per task) |
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

**Agents** — `/agents` CRUD (`tier`; responses carry `lint`) · `/agents/presets` · `/agents/{id}/run` and `/agents/dispatch` (with optional project/prompt override and `brief`)

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
