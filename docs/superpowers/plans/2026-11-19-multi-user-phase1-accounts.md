# Multi-user Phase 1 — Accounts & Lock-down Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every API route requires a logged-in user (or a scoped agent token); data lives in Postgres with real migrations; admins manage users; existing data belongs to the first admin.

**Architecture:** Alembic owns the schema (Postgres in production, SQLite for tests/dev). A new `app/auth.py` holds password hashing, server-side sessions and API tokens; one FastAPI dependency (`current_principal`) is attached to every router except health/auth/webhooks. The worker mints a short-lived, narrowly scoped token per task so coordinator agents can still call `/agents/dispatch`.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic, asyncpg, aiosqlite, argon2-cffi, React + antd.

**Spec:** `docs/superpowers/specs/2026-11-19-multi-user-design.md` (Phase 1 section, plus "Trust model" and the agent-token scope decision).

## Global Constraints

- Production DB: `CO_DATABASE_URL=postgresql+asyncpg://…`; SQLite (`sqlite+aiosqlite`) must keep working — the test suite runs on it.
- Passwords: argon2id via `argon2-cffi`; minimum length **10**.
- Session cookie `co_session`: HttpOnly, SameSite=Lax, Secure when the request is HTTPS; **7-day sliding** expiry; session id is 256-bit random, stored **hashed** (sha256).
- Login lockout: **5 failures / 15 min per (email, IP)** → 429 until the window passes.
- CSRF: cookie-authenticated non-GET requests must send header `X-Requested-With: co`; bearer-token requests are exempt.
- Bearer tokens look like `co_<random>`; stored hashed.
- Unauthenticated routes: `GET /health`, `POST /auth/login`, `GET /auth/setup-status`, `POST /auth/setup`, `POST /webhooks/github` (HMAC).
- Agent-task token: expires at task finish (hard cap **24h**); may only `POST /agents/dispatch` and `GET /tasks/{id}` / `GET /tasks/{id}/events` for tasks in its own unit (same `root_id`, or `parent_task_id` == token's task). Never written to disk.
- Roles: `admin` | `member`. Phase 1 admin-only: user management, MCP server writes, skill-pack writes, global context-doc writes. (Project scoping is Phase 2.)
- Bootstrap: `CO_ADMIN_EMAIL` + `CO_ADMIN_PASSWORD` create the first admin when zero users exist; otherwise `/auth/setup` works only while zero users exist. All pre-existing rows get `created_by` = first admin.
- Backend binds `127.0.0.1` by default (`CO_BIND_HOST` overrides) in `start.sh`.
- Commits: folder-wise chunks, **no Co-Authored-By trailer** (user preference).

## Review Focus

1. Email typed with different case/whitespace (`" Admin@Acme.com "`) — logs in as the same user; creating a duplicate differing only by case is rejected (409). → Task 3.
2. An admin disables a user, or the user changes their password — that user's other sessions stop working on the very next request. → Task 3 / Task 6.
3. The last active admin demotes or disables themselves — refused (400), so the org can't lock itself out. → Task 6.
4. A session expires while the UI is open — the next API call returns 401 and the UI shows the login screen, not a blank or broken page. → Task 7.
5. GitHub webhook deliveries keep working with no session (HMAC only), and a coordinator agent can still dispatch children. → Task 4 / Task 5.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/alembic.ini`, `backend/migrations/env.py`, `backend/migrations/versions/0001_baseline.py`, `0002_auth.py` | Schema migrations (async-aware env) |
| `backend/app/database.py` | engine; `init_db()` runs Alembic upgrade; legacy-SQLite stamping |
| `backend/app/auth.py` | hashing, sessions, tokens, `Principal`, `current_principal`, `require_admin`, CSRF check, lockout |
| `backend/app/routers/auth.py` | `/auth/*` endpoints + bootstrap |
| `backend/app/routers/users.py` | admin user management |
| `backend/app/models.py` | `User`, `AuthSession`, `ApiToken`; `created_by` columns |
| `backend/app/main.py` | router registration with auth dependency |
| `backend/app/worker.py` | mint/revoke agent token; inject `CO_API_TOKEN` |
| `backend/scripts/migrate_sqlite_to_postgres.py` | one-off data copy |
| `frontend/src/auth/LoginPage.jsx`, `SetupPage.jsx`, `frontend/src/components/UsersView.jsx` | UI |
| `frontend/src/api.js`, `frontend/src/App.jsx` | header, 401 handling, user menu, gating |

---

### Task 1: Alembic migrations + Postgres support

**Files:**
- Create: `backend/alembic.ini`, `backend/migrations/env.py`, `backend/migrations/script.py.mako`, `backend/migrations/versions/0001_baseline.py`
- Modify: `backend/app/database.py`, `backend/app/models.py` (all `DateTime` → `DateTime(timezone=True)`), `backend/requirements.txt` (+`alembic>=1.13`, `asyncpg>=0.29`), `.github/workflows/ci.yml`
- Test: `backend/tests/test_migrations.py`

**Interfaces:**
- Produces: `async def init_db() -> None` (same name; now runs `alembic upgrade head`); `def run_migrations(url: str) -> None` in `app/database.py`; revision id `"0001"` for the baseline.

- [ ] **Step 1: Write the failing tests**

```python
def test_fresh_db_migrates_to_head(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/fresh.db"
    run_migrations(url)
    tables = _tables(tmp_path / "fresh.db")
    assert {"tasks", "projects", "context_docs", "alembic_version"} <= tables

def test_legacy_db_is_stamped_not_recreated(tmp_path):
    # A DB made by the old create_all path (with a row) is brought to baseline
    # and stamped, keeping its data.
    db = tmp_path / "legacy.db"
    _make_legacy_db(db)          # create_all of current models + insert one project
    run_migrations(f"sqlite+aiosqlite:///{db}")
    assert _alembic_version(db) is not None
    assert _count(db, "projects") == 1
```

- [ ] **Step 2: Run to verify failure** — `cd backend && python -m pytest tests/test_migrations.py -v` → FAIL (`run_migrations` not defined).

- [ ] **Step 3: Implement.** Baseline `0001` = the current models' schema (generate with `alembic revision --autogenerate` against an empty DB, then review). `run_migrations(url)`: if `alembic_version` table is absent **and** `tasks` exists → run the legacy `_add_missing_columns` (SQLite only, guard on dialect) then `alembic stamp 0001`; then `alembic upgrade head`. `env.py` reads the URL from `settings.database_url` unless given via `config.attributes["url"]`. `init_db()` calls `run_migrations` in a thread (`asyncio.to_thread`). Switch every `DateTime` column to `DateTime(timezone=True)` (asyncpg rejects aware datetimes in naive columns).

- [ ] **Step 4: Run full suite** — `python -m pytest -q -p no:warnings` → all pass (40 + 2).

- [ ] **Step 5: Postgres CI job.** Add a `backend-postgres` job to `ci.yml` with a `postgres:16` service and `CO_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/co_test`, running the same `pytest`. Make `conftest.py` respect a pre-set `CO_DATABASE_URL` instead of always overriding it. Run locally if Postgres is available (`docker run -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16` or Homebrew) and fix any dialect issues it shows.

- [ ] **Step 6: Commit** (backend, then CI separately)

```bash
git add backend/alembic.ini backend/migrations backend/app/database.py backend/app/models.py backend/requirements.txt backend/tests/test_migrations.py backend/tests/conftest.py
git commit -m "feat(db): Alembic migrations + Postgres support (SQLite kept for tests/dev)"
git add .github/workflows/ci.yml && git commit -m "ci: run backend tests against Postgres too"
```

---

### Task 2: Auth core — users, sessions, tokens

**Files:**
- Create: `backend/app/auth.py`, `backend/migrations/versions/0002_auth.py`
- Modify: `backend/app/models.py`, `backend/requirements.txt` (+`argon2-cffi>=23.1`), `backend/app/config.py`
- Test: `backend/tests/test_auth_core.py`

**Interfaces:**
- Produces (models): `User(id, email, name, password_hash, role, active, created_at, last_login_at)`; `AuthSession(id_hash, user_id, created_at, expires_at, last_seen_at, user_agent)`; `ApiToken(id, token_hash, user_id, purpose: "agent-task"|"personal", task_id|None, expires_at, revoked_at|None)`.
- Produces (functions in `app/auth.py`):
  - `normalize_email(email: str) -> str` (strip + lower)
  - `hash_password(pw: str) -> str`, `verify_password(hash: str, pw: str) -> bool`, `validate_password(pw: str) -> None` (raises `ValueError` if < 10)
  - `async def create_session(s, user: User, user_agent: str | None) -> str` (returns raw id)
  - `async def resolve_session(s, raw: str) -> User | None` (checks active user + expiry; slides expiry; updates `last_seen_at`)
  - `async def revoke_user_sessions(s, user_id: str, except_raw: str | None = None) -> None`
  - `async def mint_token(s, user_id: str, purpose: str, task_id: str | None, ttl_seconds: int) -> str` (raw `co_…`)
  - `async def resolve_token(s, raw: str) -> tuple[User, ApiToken] | None`
  - `async def revoke_task_tokens(s, task_id: str) -> None`
  - `@dataclass Principal(user: User, token: ApiToken | None)`; `.is_admin`, `.is_agent`
- Config: `session_ttl_days: int = 7`, `login_max_failures: int = 5`, `login_window_seconds: int = 900`, `admin_email: str = ""`, `admin_password: str = ""`.

- [ ] **Step 1: Write the failing tests** — `test_password_hash_roundtrip` (verify true/false; `validate_password("short")` raises), `test_session_create_resolve_expire` (resolve returns user; after setting `expires_at` in the past returns None; stored id ≠ raw), `test_disabled_user_session_rejected`, `test_revoke_user_sessions_keeps_current`, `test_token_mint_resolve_revoke` (raw starts with `co_`; after `revoke_task_tokens` resolve returns None), `test_normalize_email(" A@B.com ") == "a@b.com"`.
- [ ] **Step 2: Run** `python -m pytest tests/test_auth_core.py -v` → FAIL (import error).
- [ ] **Step 3: Implement** models + migration `0002` (unique index on `lower(email)` via storing normalized email in `email`), and `app/auth.py` functions above. Use `secrets.token_urlsafe(32)`; hash with `hashlib.sha256`.
- [ ] **Step 4: Run** the file then the full suite → PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(auth): users, server-side sessions, hashed API tokens"`.

---

### Task 3: `/auth` routes, bootstrap and lockout

**Files:**
- Create: `backend/app/routers/auth.py`
- Modify: `backend/app/main.py` (lifespan bootstrap, register router), `backend/app/schemas.py`
- Test: `backend/tests/test_auth_api.py`

**Interfaces:**
- Consumes: Task 2 functions.
- Produces endpoints:
  - `GET /auth/setup-status` → `{"needs_setup": bool}`
  - `POST /auth/setup {email, name, password}` → 201 `UserOut`, logs in; **409** once any user exists
  - `POST /auth/login {email, password}` → 200 `UserOut` + `co_session` cookie; 401 bad creds; 429 when locked
  - `POST /auth/logout` → 204, clears cookie, deletes session
  - `GET /auth/me` → `UserOut`
  - `POST /auth/password {current_password, new_password}` → 204; revokes the user's other sessions
- Produces: `async def bootstrap_admin() -> None` (env admin if zero users) and `async def assign_orphans_to(user_id: str) -> None` (used by Task 5) called in lifespan after `init_db()`.
- `UserOut {id, email, name, role, active, created_at, last_login_at}` — never includes the hash.

- [ ] **Step 1: Write the failing tests** — `test_setup_only_when_no_users`, `test_login_case_insensitive_email` (login with `" ADMIN@x.com "`), `test_login_wrong_password_401`, `test_lockout_after_5_failures` (6th attempt with the *correct* password → 429), `test_logout_invalidates_session` (`/auth/me` → 401 after), `test_password_change_revokes_other_sessions` (two clients; second gets 401), `test_env_bootstrap_admin` (conftest env creates admin).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Lockout: in-memory dict keyed `(email, ip)` → deque of failure timestamps (single-process server; note it in a comment). Cookie `secure=request.url.scheme == "https"`.
- [ ] **Step 4: Run** file + full suite → PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(auth): login/logout/me/password, first-admin setup, lockout"`.

---

### Task 4: Lock down every route (+ CSRF, WebSocket)

**Files:**
- Modify: `backend/app/auth.py` (add dependencies), `backend/app/main.py`, `backend/app/routers/stream.py`, `backend/app/routers/mcp.py`, `backend/app/routers/skills.py`, `backend/app/routers/context_docs.py`, `backend/tests/conftest.py`
- Test: `backend/tests/test_lockdown.py`

**Interfaces:**
- Produces in `app/auth.py`:
  - `async def current_principal(request: Request) -> Principal` — cookie `co_session` or `Authorization: Bearer co_…`; 401 otherwise; enforces CSRF header for cookie auth on non-GET/HEAD/OPTIONS (403 `"missing X-Requested-With"`); for agent tokens enforces the scope in Global Constraints (403 otherwise); stores principal on `request.state.principal`.
  - `async def require_admin(p: Principal = Depends(current_principal)) -> Principal` (403 for members and agent tokens).
  - `async def ws_principal(websocket: WebSocket) -> Principal | None` (cookie only).
- conftest: `client` fixture logs in as the env admin (`CO_ADMIN_EMAIL=admin@test.local`, `CO_ADMIN_PASSWORD=test-password-123`) and sets `client.headers["X-Requested-With"] = "co"`; new `anon_client` fixture (no cookie) and `member_client` fixture (admin creates a member via `/users` once Task 6 exists — until then create directly in DB).

- [ ] **Step 1: Write the failing tests**

```python
def test_every_route_requires_auth(anon_client):
    open_routes = {("GET", "/health"), ("POST", "/auth/login"),
                   ("GET", "/auth/setup-status"), ("POST", "/auth/setup"),
                   ("POST", "/webhooks/github")}
    for method, path in _all_http_routes(app):       # from app.routes, path params filled with "x"
        if (method, path_template) in open_routes: continue
        r = anon_client.request(method, path)
        assert r.status_code == 401, (method, path)

def test_cookie_post_without_csrf_header_is_403(client): ...
def test_ws_stream_rejects_anonymous(anon_client): ...      # websocket_connect raises / closes 4401
def test_member_cannot_write_mcp_or_skills(member_client): ...  # POST /mcp/servers, POST /skills → 403
def test_github_webhook_still_open(anon_client): ...        # signed payload → 2xx
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Register routers with `dependencies=[Depends(current_principal)]` in `main.py` (auth router and webhooks router without it; `/system/claude` gets it). Add `Depends(require_admin)` to write endpoints of `mcp.py`, `skills.py`, and to global-scope writes in `context_docs.py`. WebSocket: close with code `4401` when `ws_principal` is None. Existing tests keep passing via the logged-in `client` fixture.
- [ ] **Step 4: Run** full suite → PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(auth): require login on every route; CSRF header; admin-only global config"`.

---

### Task 5: Ownership (`created_by`) + scoped agent tokens

**Files:**
- Create: `backend/migrations/versions/0003_ownership.py`
- Modify: `backend/app/models.py`, `backend/app/task_service.py`, `backend/app/routers/{tasks,agents,templates,schedules,workflows,projects,context_docs,approvals,webhooks}.py`, `backend/app/scheduler.py`, `backend/app/workflow_runner.py`, `backend/app/worker.py`, `backend/app/schemas.py`
- Test: `backend/tests/test_ownership.py`

**Interfaces:**
- Consumes: `Principal` (`request.state.principal`), `mint_token`, `revoke_task_tokens`, `assign_orphans_to`.
- Produces: nullable `created_by: str` on `tasks, schedules, agents, workflows, workflow_runs, projects, templates, context_docs`; `decided_by: str` on `tasks`; `build_task(..., created_by: str | None = None)`; `TaskOut.created_by`, `TaskOut.decided_by`.
- Rules: API creations use the principal's user id; scheduler → schedule's `created_by`; workflow stages → run's `created_by`; dispatch children & follow-ups → parent task's `created_by`; webhook tasks → project's `created_by`. `assign_orphans_to(first_admin_id)` fills NULLs on startup.
- Worker: before spawning, `raw = mint_token(s, task.created_by, "agent-task", task_id, ttl_seconds=min(task_timeout, 86400))`; set `sub_env["CO_API_TOKEN"] = raw`; `_finish` calls `revoke_task_tokens`. Coordinator curl lines gain `-H "Authorization: Bearer $CO_API_TOKEN"`.

- [ ] **Step 1: Write the failing tests** — `test_created_by_set_on_api_create` (task, project, agent, schedule all show admin id), `test_dispatch_children_inherit_creator`, `test_orphans_assigned_to_first_admin`, `test_agent_token_scope` (mint for task T: `POST /agents/dispatch` with parent T → 201; `GET /tasks/T` → 200; `GET /tasks/<other>` → 403; `POST /approvals/<id>/approve` → 403; `GET /users` → 403), `test_agent_token_revoked_after_task` (run a task; afterwards no unrevoked token rows for it), `test_coordinator_prompt_uses_bearer` (`_coordinator_context` output contains `Authorization: Bearer $CO_API_TOKEN`), `test_approval_records_decider`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** full suite → PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(auth): record owners; scoped per-task agent tokens for dispatch"`.

---

### Task 6: Admin user management API

**Files:**
- Create: `backend/app/routers/users.py`
- Modify: `backend/app/main.py`, `backend/app/schemas.py`
- Test: `backend/tests/test_users_api.py`

**Interfaces:**
- Produces (all `require_admin`): `GET /users` → `list[UserOut]`; `POST /users {email, name, role, password}` → 201; `PATCH /users/{id} {name?, role?, active?}`; `POST /users/{id}/reset-password {password}` → 204 (revokes that user's sessions). Disabling revokes sessions. No hard delete (keeps ownership history).

- [ ] **Step 1: Write the failing tests** — `test_admin_creates_member_and_member_logs_in`, `test_duplicate_email_case_insensitive_409`, `test_member_gets_403_on_users`, `test_disable_user_kills_sessions`, `test_reset_password_kills_sessions`, `test_last_admin_cannot_demote_or_disable_self` (400), `test_password_too_short_400`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; "last admin" = count of active admins after the change would be 0.
- [ ] **Step 4: Run** full suite → PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(users): admin user management (create, role, disable, reset password)"`.

---

### Task 7: Frontend — login, setup, user menu, Users page

**Files:**
- Create: `frontend/src/auth/LoginPage.jsx`, `frontend/src/auth/SetupPage.jsx`, `frontend/src/components/UsersView.jsx`
- Modify: `frontend/src/api.js`, `frontend/src/App.jsx`, `frontend/vite.config.js` (proxy `/auth`, `/users`)

**Interfaces:**
- Consumes: Task 3 & 6 endpoints.
- Produces in `api.js`: every request sends `X-Requested-With: co` and `credentials: 'same-origin'`; a 401 calls a registered `onUnauthorized()` handler; methods `setupStatus, setup, login, logout, me, changePassword, listUsers, createUser, updateUser, resetUserPassword`.
- `App.jsx`: on load `setupStatus()` → SetupPage, else `me()` → LoginPage on 401, else the app. Header shows user name + role tag with a menu (Change password, Log out). "Users" nav item visible only to admins. Any 401 later returns to LoginPage with "Your session expired — please sign in again."

- [ ] **Step 1: Build check baseline** — `cd frontend && npm run build` → succeeds (before changes).
- [ ] **Step 2: Implement** the pages and wiring above (antd `Form`, `Card`, `Table`, `Modal`; same look as `SkillsView`).
- [ ] **Step 3: Build** — `npm run build` → succeeds.
- [ ] **Step 4: Browser check** (headless Chrome via Playwright `channel: 'chrome'`, as done for the Knowledge tab): fresh DB → setup page → create admin → app loads with no console errors; log out → login page; admin creates a member; member logs in and sees no Users nav; delete the member's session row in the DB (or wait out a forced short TTL) → next action shows the login page with the expiry message.
- [ ] **Step 5: Commit** `git commit -m "frontend: login, first-admin setup, user menu, admin Users page"`.

---

### Task 8: Deployment defaults, data migration script, docs

**Files:**
- Create: `backend/scripts/migrate_sqlite_to_postgres.py`
- Modify: `start.sh` (`--host "${CO_BIND_HOST:-127.0.0.1}"`), `backend/.env.example` (DB URL, `CO_ADMIN_EMAIL`, `CO_ADMIN_PASSWORD`, `CO_BIND_HOST`), `README.md` (Postgres setup, first login, HTTPS reverse-proxy note)
- Test: `backend/tests/test_migrate_script.py`

**Interfaces:**
- Produces: `python scripts/migrate_sqlite_to_postgres.py --from sqlite:///claude_orchestrator.db --to postgresql://…` — runs `alembic upgrade head` on the target, then copies every table in FK-safe order in batches; refuses if the target already has rows unless `--force`.

- [ ] **Step 1: Write the failing test** — `test_copy_sqlite_to_sqlite` (the copier is dialect-agnostic: create a source SQLite at head with one project + one task, copy to a fresh SQLite target, assert row counts and a field value match; second run without `--force` exits non-zero).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** full suite → PASS.
- [ ] **Step 5: Manual check** — copy the real `backend/claude_orchestrator.db` into a local Postgres; start the app against it; existing tasks/projects visible after logging in as the first admin.
- [ ] **Step 6: Commit** (`backend/` then root files separately) `git commit -m "feat(db): sqlite→postgres migration script"`, `git commit -m "chore: bind localhost by default; document Postgres + first login"`.
