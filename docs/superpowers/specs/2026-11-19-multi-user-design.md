# Multi-user orchestrator — design

Date: 2026-11-19 · Status: draft for approval (builder-checked)

## Intent

Turn Claude Orchestrator from a single-person tool into one an organisation
shares. People log in with their own account, are grouped into teams, teams (or
individual members) are assigned to projects, and members develop features on
those projects by running Claude agents that end in GitHub pull requests —
each person working as themselves (their own Claude account, their own GitHub
identity), on one shared server.

### Why it matters
Today anyone who can reach port 8200 can do everything (no login), all work
runs on the owner's Claude login and GitHub identity, and concurrent project
tasks share one repo checkout. None of that survives a second person.

## Goals
- Real accounts: email + password, admin-created/invited; sessions; logout.
- Roles: **Admin** (users, teams, MCP servers, skill packs, global knowledge
  docs; sees everything) and **Member** (works on projects they're assigned to).
- Teams; assign a team and/or individual users to a project. Per-project role:
  **maintainer** or **contributor**.
- Members see all tasks, results and costs on projects they belong to
  (teammates' included), and nothing on other projects.
- Approvals for risky actions (merge/deploy/delete/force-push) go to the
  project's maintainers (and admins), not the requester.
- Each user's agent tasks run with **their own** Claude credential and
  **their own** GitHub token, so usage bills to them and commits/PRs are theirs.
- Concurrent tasks on one project don't collide: each task works in its own
  git worktree on its own branch and finishes with a PR.
- A proper database: PostgreSQL, with real migrations.

## Non-goals (for now)
- "Sign in with GitHub" / SSO (designed so it can be added later as an extra
  login method without rework).
- Self-signup, password-reset email, 2FA. (Admin resets passwords.)
- Multiple organisations/tenants on one server — one server = one org.
- Running agents on users' own machines (all agents run on the server).
- OS-level isolation between users' agents (separate Unix user or container
  per user/task). Decided: trust-within-the-org for now; see "Trust model".
- Fine-grained per-agent/per-workflow ACLs beyond project membership.

## Anti-goals (would be failure even if it "works")
- A user's tasks silently running on someone else's Claude or GitHub account.
- One user able to read another user's tokens **through anything the
  orchestrator exposes**: UI, API, logs, files it writes, or the agent's working
  directory. (See "Trust model" — OS-level isolation between agents is out of
  scope for now.)
- Agents losing the ability to delegate (coordinator → `/agents/dispatch`)
  because the API now requires login.
- Breaking the existing single-user setup: the current owner and their data
  must carry over intact.

## Constraints (from the current code)
- FastAPI + async SQLAlchemy; SQLite today with an ad-hoc additive migrator
  (`database.py::_add_missing_columns`). Tests run against SQLite with a mock
  `claude` binary — keep SQLite working for tests/dev.
- AES-GCM credential vault exists (`crypto.py`) — reuse it for user tokens.
- Agents call the API via `curl` (`worker.py` coordinator block, `api_base`).
- The GitHub webhook authenticates by HMAC — stays unauthenticated-by-session.
- Task stream is a WebSocket (`/tasks/{id}/stream`) — browsers can't set
  headers on WebSockets, so auth must work via cookie.
- Project tasks currently run directly in `project.directory`
  (`task_service.py:124`).
- Frontend: React + antd, same-origin via Vite proxy in dev.

## Trust model (decided)
All agents run as the same OS user in `bypassPermissions` mode, so a
deliberately misbehaving agent could read other users' credentials, the DB or
the vault key from the filesystem/process table. Accepted for now: everyone on
the server is a trusted colleague. The orchestrator itself never exposes one
user's secrets to another (UI, API, logs, files it writes, agent cwd), and
secrets are passed only via the subprocess env, never written to disk in a
workspace. A per-user Unix account or container runner is a possible later
phase and should not be precluded (keep "build the subprocess env + cwd" in one
function the runner can be swapped behind).

## Approaches considered (login)
- **A. Built-in email/password (chosen).** Admin-created users, hashed
  passwords, server sessions. No external dependency; fits a self-hosted tool;
  matches "login creds".
- B. Sign in with GitHub — no passwords, ties to GitHub identity, but needs an
  OAuth app + public callback URL and excludes non-GitHub users. Possible
  later add-on.
- C. External IdP (Keycloak/Auth0/Google SSO) — enterprise-grade, too heavy
  for a team-sized self-hosted tool.

## Delivery: three phases, in order, each shippable

### Phase 1 — Accounts & lock-down
**Database.** PostgreSQL via `asyncpg` is the production default
(`CO_DATABASE_URL=postgresql+asyncpg://…`); SQLite remains supported for
tests/dev. Introduce **Alembic**: a baseline migration matching the current
schema, then migrations for new tables. `_add_missing_columns` is retired
(kept only to bring an old SQLite DB up to the baseline). A one-off
`scripts/migrate_sqlite_to_postgres.py` copies existing data.

**Models.**
- `User`: id, email (unique, case-insensitive), name, password_hash,
  role (`admin`|`member`), active, created_at, last_login_at.
- `Session`: id (random 256-bit, stored hashed), user_id, created_at,
  expires_at, last_seen_at, user_agent. Server-side so logout/disable is
  immediate.
- `ApiToken`: for agents/automation — id, user_id, token hash, purpose
  (`agent-task`|`personal`), task_id (nullable), expires_at.
- Ownership columns: `created_by` (user id) on tasks, schedules, agents,
  workflows, workflow runs, projects, templates, context docs; `decided_by` on
  approvals. Existing rows → the first admin.

**Auth.**
- Passwords hashed with **argon2id** (`argon2-cffi`). Min length 10.
- `POST /auth/login` → sets `co_session` cookie (HttpOnly, SameSite=Lax,
  Secure when served over HTTPS), `POST /auth/logout`, `GET /auth/me`,
  `POST /auth/password` (change own).
- Login rate limit: 5 failures / 15 min per email+IP → temporary lockout.
- CSRF: state-changing requests with cookie auth must carry an
  `X-Requested-With`/custom header the SPA always sends (SameSite=Lax +
  custom-header check); bearer-token requests are exempt.
- Every router gets an auth dependency (`current_user`); WebSocket stream
  authenticates via the session cookie. Unauthenticated: `/health`,
  `/auth/login`, `/webhooks/github` (HMAC).
- Bearer tokens (`Authorization: Bearer co_…`) accepted everywhere a session
  is.

**Agents calling the API.** For each task the worker mints a short-lived
`agent-task` token (expires with the task, max 24h) acting as the task's
creator, passes it in the subprocess env as `CO_API_TOKEN`, and the coordinator
instructions use `-H "Authorization: Bearer $CO_API_TOKEN"`. Revoked on finish.
Child tasks inherit the creator. The token is never written to disk in the
workspace. **Scope (decided):** agent-task tokens can only dispatch agents
(`/agents/dispatch`) and read tasks of their own unit (status, events,
results). They cannot approve, change settings, manage users/credentials, or
read other units — enforced server-side, independent of the creator's role.

**Bootstrap.** On first start with no users: if `CO_ADMIN_EMAIL` +
`CO_ADMIN_PASSWORD` are set, create that admin; otherwise the UI shows a
one-time "create the first admin" screen (only while zero users exist). All
existing data is assigned to that admin.

**Admin UI.** Users page: create user (email, name, role, initial password),
disable/enable, reset password, change role. Login page; user menu (change
password, logout).

**Network default.** Backend binds `127.0.0.1` by default (`CO_BIND_HOST` to
override); docs recommend HTTPS reverse proxy for team use.

### Phase 2 — Teams, membership, scoping
- `Team` (id, name), `TeamMember` (team_id, user_id).
- `ProjectMember` (project_id, subject_type `user`|`team`, subject_id,
  role `maintainer`|`contributor`). Effective role = highest across direct and
  team grants. Admins are implicitly maintainers everywhere.
- **Scoping rule:** every read/write of a project-bound object (project,
  tasks, events, artifacts, findings, schedules, workflow runs, approvals,
  usage, knowledge docs, project-scoped MCP/skills) checks membership; lists
  are filtered. Objects with no project (ad-hoc tasks) are visible to their
  creator and admins.
- **Admin-only project fields:** creating projects, `directory`, `env`
  (incl. `PATH_PREPEND`), folder import (`/projects/discover`), and anything
  that points at server paths. These grant server access, so maintainers can't
  touch them.
- Maintainers: manage members, instructions, budget, auto-review, the
  "automation owner", the worktree copy-list; approve risky tasks.
  Contributors: run tasks/agents/workflows, view everything in the project.
- Global config (global MCP servers, skill packs, global knowledge docs,
  agent presets) is admin-managed; members can *use* global agents/templates.
- Approvals queue shows only tasks the viewer can approve; requester cannot
  approve their own critical task (unless admin).
- Usage view: per project, per user.
- UI: Teams page (admin); project "Members" tab; project picker filtered.

### Phase 3 — Working as yourself
- **Per-user credentials** (stored encrypted in the vault, write-only in UI —
  never returned by the API):
  - Claude: `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) *or*
    `ANTHROPIC_API_KEY`.
  - GitHub: a personal access token (fine-grained, repo scope) + git author
    name/email.
  - "Test connection" for each.
- **Task env:** the worker builds the subprocess env from a clean base and
  injects only the task creator's credentials (`CLAUDE_CODE_OAUTH_TOKEN` or
  `ANTHROPIC_API_KEY`, `GH_TOKEN`, `GIT_AUTHOR_*`/`GIT_COMMITTER_*`), plus a
  per-user `CLAUDE_CONFIG_DIR` under `workspaces/_users/<user_id>/` so CLI
  state is not shared. The server owner's own `~/.claude` login and `gh` login
  are **not** inherited. A user with no Claude credential cannot run tasks
  (clear error), unless an admin enables an org-wide fallback key.
- **One branch + one PR per unit of work** (decided). A unit is: a
  standalone task and its follow-up thread, a coordinator run (parent + all
  children it dispatches), or a workflow run (all stages). The unit gets one
  worktree at `workspaces/_worktrees/<unit_id>` created with
  `git worktree add -b co/<user-slug>/<unit-short-id> <base>`, where `<base>` is
  a freshly fetched `origin/<default-branch>`. All tasks in the unit share it;
  tasks in a unit that would run in parallel on it are serialized (one at a
  time per worktree). Read-only tasks get a detached worktree and never push.
- **Untracked files:** each project has a `worktree_copy` list (e.g. `.env`,
  `config/local.yml`) copied from `project.directory` into a new worktree.
- **Non-git / no-remote projects** (e.g. Infra): run in `project.directory`
  as today, one task at a time per project; no PR step.
- **Who pushes (decided):** the *worker*, not the agent. After each task
  that made commits, the worker pushes the unit's branch with the task
  creator's `GH_TOKEN`, and on the first push opens the PR (`gh pr create`,
  title/body from the task); later tasks update the same PR. Inside the agent
  `git push`, `gh pr merge`, `git push --force` and `gh pr create` are
  denied via `--disallowedTools`; the agent may commit. Merging stays a
  human/approved action.
- **Worktree lifecycle:** kept while the unit is open; removed when its PR is
  merged/closed (checked on webhook or periodically), on an explicit "Close"
  action, or after 14 days idle (branch stays on the remote). Disk use shown
  per project.
- **Follow-ups by a teammate (decided):** a follow-up runs as the person who
  sends it (their Claude + GitHub identity), on the same branch/PR. Claude
  sessions can't be resumed across accounts, so a different user's follow-up
  starts a fresh session primed with a thread summary (titles, prompts, result
  excerpts) and the branch; the same user's follow-up still uses `--resume`.
- Scheduled/automated runs (cron, workflows) run as the schedule's /
  workflow's owner, using that owner's credentials. Webhook-triggered PR
  reviews run as the project's **automation owner** (a maintainer picked in
  project settings; defaults to the project creator).
- **Server-side Claude calls** that aren't tasks (memory update/rebuild,
  findings extraction, summaries) use the credentials of the user whose task
  triggered them; manual "rebuild memory" uses the clicking user. The
  `/system/claude` health check reports per-user credential status.
- **Missing credentials:** no Claude credential → can't run tasks (clear
  error) unless an admin enabled an org fallback key. No GitHub token → tasks
  run and commit, but no push/PR; the task shows a warning. A disabled owner or
  one missing credentials → their schedules pause and are flagged to admins
  (admin can reassign the owner).

## Builder's latitude
Library choices within the above (e.g. Alembic layout, exact session TTL —
suggest 7 days sliding), UI layout details, error copy, migration script
mechanics, and test structure are the builder's call. Anything that changes a
goal, non-goal or anti-goal above comes back for a decision.

## Testing
Each phase ships with API tests (SQLite + mock claude): login/logout/lockout,
every router rejects anonymous access, agent token flows through dispatch,
membership scoping on every list endpoint, approval routing, credential
isolation (task env contains only its creator's secrets; API never returns
them), worktree-per-task and cleanup. Plus a Postgres CI job running the same
suite.
