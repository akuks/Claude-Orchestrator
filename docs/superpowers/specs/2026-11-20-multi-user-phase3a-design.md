# Multi-user Phase 3a — Personal credentials — design

Parent design: `docs/superpowers/specs/2026-11-19-multi-user-design.md`, section
"Phase 3 — Working as yourself" (its goals, anti-goals and trust model bind).
Phase 3 is split: **3a (this doc)** = personal credentials and a clean task
environment; **3b (later)** = worktree per unit of work, worker push, PRs.

## Intent
Each person's agent work runs on their own Claude account and carries their
own GitHub/git identity. Today every task inherits the server's environment,
so everything runs on the server owner's Claude login and `gh` login.

## Decisions
- **Missing Claude credential → the task refuses to run** with a clear error
  (option (a), user's choice). An admin may set an **org fallback API key**
  explicitly; the server's own Claude login / keychain / `~/.claude` is never
  used by tasks.
- 3a before 3b (user's choice).

## Goals
- Per-user credentials, encrypted with the existing vault (`app/crypto.py`),
  write-only: the API never returns secret values, only presence/kind.
  - Claude: `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) **or**
    `ANTHROPIC_API_KEY` — exactly one kind at a time.
  - GitHub: personal access token; git author name + email.
  - "Test connection" for Claude and GitHub.
- Org fallback API key (admin-managed, write-only), off unless set.
- Task env built from a **clean allowlisted base** + the task creator's
  credentials + project env (admin-managed, unchanged) + `CO_API_TOKEN`
  (unchanged). Per-user `CLAUDE_CONFIG_DIR`, `GH_CONFIG_DIR` and a per-user
  `GIT_CONFIG_GLOBAL` under `workspaces/_users/<user_id>/`, so CLI state and
  global git config/credential helpers of the server owner are not inherited.
- Server-side Claude calls (memory update/rebuild, findings extraction) use
  the credentials of the user whose task triggered them; manual memory
  regenerate uses the clicking user. Admin server actions that aren't agent
  work (skill-pack install, `/system/claude` binary check) keep the server env.
- `/system/claude` additionally reports the *calling user's* credential status
  (has Claude credential / using org fallback / none; has GitHub token).
- Schedules whose owner can't run (no credential and no fallback) are skipped
  like disabled owners, with a reason, and run-now returns 409.
- Project **automation owner** (a maintainer; defaults to the project creator):
  webhook-triggered PR reviews run as them. Maintainers can change it.
- UI: "My credentials" (from the user menu); admin "Org fallback key" on the
  Users page; tasks failing for missing credentials show the message.

## Non-goals (3a)
- Worktrees, branches, pushing, PRs, `--disallowedTools` for git push (3b).
- Teammate follow-ups priming a fresh session (3b; today follow-ups still
  `--resume` — a different user's follow-up must not resume another account's
  session: in 3a it starts a fresh session with no resume, see Constraints).
- OS-level isolation (trust model unchanged).

## Anti-goals
- A task running on anyone's Claude account other than its creator's (or the
  explicit org fallback).
- Any secret value in an API response, event, log line, file in a workspace,
  task row, or error message.
- The server's own `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, `GH_TOKEN`,
  `GITHUB_TOKEN`, keychain/`~/.claude` login reaching a task.

## Constraints
- Base env allowlist (my call): `PATH, HOME, USER, LOGNAME, SHELL, LANG, LC_*,
  TERM, TMPDIR, TZ`; nothing else from the server env. HOME stays the server
  user's HOME (tools need it) but Claude/gh/git config are redirected by the
  three *_CONFIG* variables above.
- Per-user `GIT_CONFIG_GLOBAL` file contains `user.name`/`user.email` (if set)
  and `[safe] directory = *` (my call: repos are owned by the server user).
- A follow-up whose sender differs from the parent's creator gets no
  `resume_session_id` (sessions are per-account) — my call for 3a.
- Tests run with the mock `claude`; the suite enables an org fallback key in
  conftest so existing tests keep running; credential tests toggle it.

## Builder's latitude
Table/endpoint naming, UI layout, error copy, test structure.
