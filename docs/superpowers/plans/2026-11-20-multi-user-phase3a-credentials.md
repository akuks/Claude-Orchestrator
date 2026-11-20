# Multi-user Phase 3a — Personal credentials Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every agent task and server-side Claude call runs with its owner's own Claude credential and git/GitHub identity (or an explicit org fallback key), never the server's login.

**Architecture:** Encrypted per-user credentials (vault) + one env builder (`app/agent_env.py`) that every `claude` subprocess for agent work goes through. Missing credential → the task fails with a clear message.

**Tech Stack:** FastAPI, async SQLAlchemy, Alembic, pytest (mock claude), React + antd.

**Spec:** `docs/superpowers/specs/2026-11-20-multi-user-phase3a-design.md` (parent: `docs/superpowers/specs/2026-11-19-multi-user-design.md` Phase 3).

## Global Constraints
- Secrets are write-only: no API response, event, log, error message, DB column other than the encrypted blob, or workspace file ever contains a credential value.
- Encryption: `app.crypto.encrypt_json` / `decrypt_json` (existing vault).
- Missing credential detail, exactly: `"No Claude credential: add one under My credentials (or ask an admin to set an org fallback key)."`
- Base env allowlist: `PATH, HOME, USER, LOGNAME, SHELL, LANG, TERM, TMPDIR, TZ` and any `LC_*`. Nothing else from `os.environ`.
- Per-user dirs under `settings.workspaces_dir / "_users" / <user_id>`: `claude/` → `CLAUDE_CONFIG_DIR`, `gh/` → `GH_CONFIG_DIR`, `gitconfig` → `GIT_CONFIG_GLOBAL`. Created on demand, mode 0700.
- Injected names: `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` (exactly one); `GH_TOKEN` (if set); `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME`, `GIT_COMMITTER_EMAIL` (if set).
- Migration `0005_credentials` after `0004`; SQLite + Postgres.
- Full suite: `cd backend && CO_DB_NULL_POOL=1 .venv/bin/python -m pytest -q -p no:warnings` stays green; `tests/test_scoping_coverage.py` stays green (new non-project routes go into its `NOT_PROJECT_BOUND` with a reason).
- Commits folder-wise; **no Co-Authored-By trailer**. Never run the real `claude` binary; never touch ports 8200/5200, the other checkout's DB files, or the Postgres DB `claude_orchestrator`.

## Review Focus
1. Server env contains `ANTHROPIC_API_KEY` / `GH_TOKEN` / `GITHUB_TOKEN` / `CLAUDE_CODE_OAUTH_TOKEN` → none reach the task env unless they are the creator's own (Task 2 test).
2. User switches from OAuth token to API key → the old kind is gone; never both injected (Task 1 + Task 2 tests).
3. Org fallback set, user has no credential → task runs with the fallback; fallback removed → same task retried fails with the exact message (Task 2 test).
4. A teammate's follow-up on another user's thread → runs with the sender's credential and no `--resume` of the other account's session (Task 3 test).
5. "Test connection" failure → returns `ok: false` with a reason that contains no secret (Task 1 test).

---

### Task 1: Credential storage, API, org fallback

**Files:** Modify `backend/app/models.py`; Create `backend/migrations/versions/0005_credentials.py`, `backend/app/credentials.py`, `backend/app/routers/credentials.py`; Modify `backend/app/schemas.py`, `backend/app/main.py`, `backend/tests/conftest.py`; Test `backend/tests/test_credentials_api.py`.

**Interfaces — Produces:**
- Model `UserCredential(user_id PK→users.id, claude_kind: "oauth_token"|"api_key"|None, claude_enc: Text|None, github_enc: Text|None, git_name: str|None, git_email: str|None, updated_at)`.
- Model `OrgSetting(key PK str, value_enc Text, updated_at, updated_by)`; key `"fallback_anthropic_api_key"`.
- Model `Project.automation_owner_id: str|None` (column in the same migration; used in Task 4).
- `app/credentials.py`: `async def get_claude_secret(s, user_id) -> tuple[str, str] | None` → `(env_name, value)`; `async def get_fallback_key(s) -> str | None`; `async def get_github(s, user_id) -> tuple[str|None, str|None, str|None]` → `(token, git_name, git_email)`; `async def credential_status(s, user_id) -> dict` → `{"claude": "own"|"fallback"|"none", "claude_kind": ..., "github": bool, "git_name", "git_email"}`.
- Routes (any logged-in user, own record only): `GET /me/credentials` → status (no secrets); `PUT /me/credentials` body `{claude_token?: str, claude_kind?: "oauth_token"|"api_key", github_token?: str, git_name?: str|null, git_email?: str|null}` (setting a Claude secret requires `claude_kind`; replaces the previous Claude secret entirely); `DELETE /me/credentials/claude`, `DELETE /me/credentials/github`; `POST /me/credentials/test {"which": "claude"|"github"}` → `{ok: bool, detail: str}`.
  - Claude test: run `settings.claude_bin -p "Reply with OK" --model claude-haiku-4-5 --max-turns 1` with an env from `agent_env` for this user (Task 2 provides the builder — in Task 1 implement the test endpoint by calling a small `run_claude_probe(env)` in `credentials.py` that Task 2 will reuse; for now build a minimal env: allowlisted base + the secret) with a 30s timeout; ok iff exit 0. detail = first 200 chars of stderr with any occurrence of the secret replaced by `"***"`.
  - GitHub test: `GET https://api.github.com/user` with `Authorization: Bearer <token>` via httpx (mock it in tests with `monkeypatch`), ok iff 200, detail = the login.
- Admin: `GET /admin/org-fallback` → `{set: bool, updated_at, updated_by}`; `PUT /admin/org-fallback {api_key}`; `DELETE /admin/org-fallback`.
- conftest: after admin login, `PUT /admin/org-fallback {"api_key": "sk-test-fallback"}` so existing tests keep running.

- [ ] Step 1: failing tests: `test_put_get_never_returns_secret` (GET body and PUT response contain no secret substring); `test_switch_kind_replaces` (oauth → api_key: status kind api_key, only one stored); `test_delete_parts`; `test_other_user_cannot_read` (no route takes a user id; admin GET /me shows admin's own); `test_org_fallback_admin_only` (member 403); `test_test_connection_claude_failure_redacted` (mock claude exits 1 printing the secret on stderr → ok false, detail has `***` not the secret); `test_test_connection_github` (monkeypatched httpx 200 → ok, login); migration test for 0005.
- [ ] Step 2: run → FAIL. Step 3: implement. Step 4: run → PASS, full suite green. Step 5: commit (`feat(db): user credentials, org settings, project automation owner`; `feat(api): personal credentials and org fallback key`; tests).

### Task 2: Clean agent env + worker uses the owner's credentials

**Files:** Create `backend/app/agent_env.py`; Modify `backend/app/worker.py`, `backend/app/credentials.py` (probe reuses the builder), `backend/tests/conftest.py` (mock records selected env vars to a probe file); Test `backend/tests/test_agent_env.py`.

**Interfaces — Produces:** `class MissingCredential(Exception)`; `async def build_agent_env(s, user_id: str | None, *, project_env: dict | None = None) -> dict[str, str]` — raises `MissingCredential` (message = the exact Global Constraints text) when the user has no Claude credential and there is no fallback, or `user_id is None`. Applies project env (with `PATH_PREPEND` semantics moved from worker) after the base and before credentials (credentials win). Writes the per-user gitconfig each call (user.name/email if set; `[safe] directory = *`).
- Worker: replace the inherited-env block with `build_agent_env(...)`; on `MissingCredential` the task fails (status failed, error = message, an `error` event with the message) before spawning. `CO_API_TOKEN` added after, as today. Emit `env` event with key NAMES only, never values (credential names may be listed as `"credentials": ["claude", "github"]`).
- [ ] Step 1: failing tests (mock claude writes `env | sort` filtered to the names of interest into `$TEST_ENV_PROBE`-style file; extend the mock): `test_server_secrets_not_inherited` (monkeypatch os.environ with ANTHROPIC_API_KEY/GH_TOKEN/GITHUB_TOKEN/CLAUDE_CODE_OAUTH_TOKEN/RANDOM_VAR → task env has none of the server values, no RANDOM_VAR); `test_owner_credentials_injected` (member with oauth token + github + git identity → those values present, exactly one Claude var); `test_fallback_used_then_removed` (Review Focus 3); `test_missing_credential_fails_task_with_message`; `test_per_user_config_dirs` (CLAUDE_CONFIG_DIR etc. point under `_users/<user_id>/`, differ per user, gitconfig contains the identity); `test_project_env_still_applied` (PATH_PREPEND + a project var).
- [ ] Steps 2–5 as usual; commit `feat(worker): clean per-user agent env with owner credentials`; tests.

### Task 3: Server-side Claude calls, follow-ups, system status

**Files:** Modify `backend/app/memory.py`, `backend/app/security.py`, `backend/app/worker.py` (pass the owner to memory/findings), `backend/app/routers/projects.py` (manual regenerate uses the clicking user), `backend/app/routers/tasks.py` (follow-up resume rule), `backend/app/main.py` (`/system/claude` adds `"you": credential_status`); Test `backend/tests/test_server_calls_env.py`.
- memory/security `_run_claude`-style helpers take `env: dict` (from `build_agent_env`) and skip quietly (log at info, no exception) on `MissingCredential`.
- Follow-up by a user other than the parent's creator: `resume_session_id = None` (fresh session); same user keeps `--resume`.
- [ ] Tests: `test_memory_update_uses_owner_env` (mock records env; memory call carries the owner's credential, not the server's); `test_manual_regenerate_uses_clicker`; `test_followup_by_other_user_no_resume` / `test_followup_same_user_resumes`; `test_system_claude_reports_you` (own/fallback/none; no secret). Commit `feat: server-side Claude calls and follow-ups use the right person's credentials`; tests.

### Task 4: Schedules, automation owner, webhook

**Files:** Modify `backend/app/scheduler.py` (`owner_block_reason` adds "owner has no Claude credential" when `credential_status(...)["claude"] == "none"`), `backend/app/routers/schedules.py` (run-now 409 message from the same function), `backend/app/routers/projects.py` (`automation_owner_id` in ProjectUpdate/Out: maintainer-editable; must be a user with maintainer role on that project or an admin → else 400; default None means project creator), `backend/app/routers/webhooks.py` (webhook review task `created_by = project.automation_owner_id or project.created_by`), `MAINTAINER_FIELDS` gains `automation_owner_id`; Test `backend/tests/test_automation_owner.py`.
- [ ] Tests: `test_schedule_skipped_without_credential` (fallback off, owner without credential → skipped with reason; run-now 409); `test_automation_owner_validation`; `test_webhook_runs_as_automation_owner`. Commit `feat: automation owner for webhook reviews; schedules pause without credentials`; tests.

### Task 5: Frontend

**Files:** Create `frontend/src/components/MyCredentials.jsx`, `frontend/src/components/OrgFallbackCard.jsx`; Modify `frontend/src/api.js`, `frontend/src/App.jsx` (user menu "My credentials"; banner when `/system/claude` says `you.claude == "none"`), `frontend/src/components/UsersView.jsx` (OrgFallbackCard for admins), `frontend/src/components/ProjectsView.jsx` (automation owner select for maintainers, choices = project maintainers + admins from members/directory), `frontend/vite.config.js` (`/me`, `/admin` proxies).
- MyCredentials: status tags (Claude: own oauth / own api key / org fallback / none; GitHub: set/none), password inputs that are never pre-filled, Save, Remove, Test buttons showing `detail`; link text explaining `claude setup-token`.
- [ ] Verify `npm run build`; Playwright click-through on a scratch backend (ports 8312/5312, temp SQLite, throwaway admin creds; mock claude via `CO_CLAUDE_BIN` pointing at a script that prints nothing and exits 1 — never the real claude): set/replace/remove a credential, see the no-credential banner, admin sets/removes fallback. Commit `frontend: my credentials, org fallback key, automation owner`.
