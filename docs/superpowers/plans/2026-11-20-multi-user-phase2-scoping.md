# Multi-user Phase 2 — Teams, membership & scoping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teams and per-project membership (maintainer/contributor) so members only see and act on projects they're assigned to, with approvals routed to maintainers.

**Architecture:** One access module (`app/access.py`) answers "which projects can this principal see" and "what is their role on project X", and returns SQLAlchemy filter clauses for tasks. Every router calls it explicitly. A route-enumeration test (like `tests/test_lockdown.py`) proves no project-bound route skips it.

**Tech Stack:** FastAPI, async SQLAlchemy 2, Alembic, pytest (SQLite + mock claude; Postgres in CI), React 18 + antd 5.

**Spec:** `docs/superpowers/specs/2026-11-20-multi-user-phase2-design.md` (parent: `docs/superpowers/specs/2026-11-19-multi-user-design.md`). The access table and "Clarifications" section of the spec are binding.

## Global Constraints
- Not visible → **404** (never reveal existence); visible but role too low → **403**.
- Roles: `maintainer` > `contributor`. Effective role = highest of direct grant and every team grant. Admins (`user.role == "admin"`) are implicit maintainers everywhere. Inactive users (`user.active == False`) have no role anywhere.
- Agent-token principals (`Principal.is_agent`) act with their token owner's (`principal.user`) rights; Phase 1 token-scope checks still apply on top.
- Admin-only: project create/import/discover/delete/archive, `directory`, `env`, `PUT /projects/{id}/servers`, teams writes, agents/templates/workflows writes, MCP servers & skill packs writes (unchanged), global knowledge docs.
- Non-admins receive project `env` with values masked: `{key: "••••"}`.
- Lists filter in SQL, not in Python after `LIMIT`.
- Background worker, scheduler, workflow runner, webhook are not principals — no access checks there except the scheduler skip in Task 5.
- Migrations: new revision `0004_scoping` after `0003`; works on SQLite and Postgres; `DateTime(timezone=True)`.
- Every change keeps `cd backend && CO_DB_NULL_POOL=1 .venv/bin/python -m pytest -q -p no:warnings` green.
- Commits: folder-wise, conventional prefixes, **no Co-Authored-By trailer**.
- Never run real `claude`; tests use the mock binary already configured in `tests/conftest.py`. Don't touch ports 8200/5200 or `backend/claude_orchestrator.db` / the `claude_orchestrator` Postgres DB.

## Review Focus
1. A user in two teams granting different roles on one project, plus a direct grant → highest wins; removing them from the higher team drops them to the lower role (Task 1 test).
2. An inactive user who still holds grants, and an agent token whose owner was disabled → sees nothing (Task 1 test).
3. A contributor PATCHing a project with an allowed field *and* an admin-only field in one body → 403 and nothing saved (Task 3 test).
4. A project-scoped MCP server after its project is renamed → still matched to that project for visibility (Task 6 test).
5. A member with zero grants → every list is empty (not an error) and usage totals are 0 (Task 6 test).

---

### Task 1: Data model, migration 0004, access module

**Files:**
- Modify: `backend/app/models.py` (add `Team`, `TeamMember`, `ProjectMember`; `WorkflowRun.project_ids`)
- Create: `backend/migrations/versions/0004_scoping.py`
- Create: `backend/app/access.py`
- Test: `backend/tests/test_access.py`, extend `backend/tests/test_migrations.py`

**Interfaces:**
- Consumes: `auth.Principal` (`.user`, `.is_admin`, `.is_agent`), `models.Task`, `models.Project`.
- Produces (used by every later task):
  - `ALL` sentinel; `async def visible_project_ids(s, p: Principal) -> set[str] | ALL`
  - `async def project_role(s, p: Principal, project_id: str) -> str | None`
  - `async def require_project_role(s, p: Principal, project_id: str | None, min_role: str = "contributor") -> str` — raises 404 (no role) / 403 (role below `min_role`); `project_id=None` returns `"maintainer"` for admins, else raises 403 when `min_role == "maintainer"`, else returns `"contributor"`.
  - `async def task_visibility_clause(s, p: Principal)` → SQLAlchemy boolean clause over `Task` (spec Clarification 5): `project_id IN visible` OR (`project_id IS NULL` AND (`created_by == user.id` OR `root_id IN (SELECT id FROM tasks WHERE project_id IN visible)`)); admins → `true()`.
  - `async def can_see_task(s, p: Principal, task: Task) -> bool` (same rule, single row; walks `root_id`).
  - `async def require_task(s, p: Principal, task_id: str) -> Task` — 404 if missing or invisible.
  - `async def task_role(s, p, task) -> str | None` — role on the task's project; for no-project tasks: `"maintainer"` if admin or creator, else `"contributor"` if visible, else None.
  - Models: `Team(id, name unique, created_at)`, `TeamMember(team_id, user_id)` unique pair, `ProjectMember(id, project_id, subject_type, subject_id, role)` unique `(project_id, subject_type, subject_id)`, `WorkflowRun.project_ids: list` JSON default `[]`.

- [ ] **Step 1: Write failing tests** in `tests/test_access.py` (build users/teams/grants directly in the DB like `member_client` in conftest; call the functions with `Principal(user=...)`):
  - `test_admin_sees_all` — `visible_project_ids` is `ALL`; `project_role` == `"maintainer"` on a project with no grants.
  - `test_no_grant_no_role` — member: `project_role is None`; `require_project_role` raises HTTPException 404.
  - `test_direct_contributor` — role `"contributor"`; `require_project_role(..., "maintainer")` raises 403.
  - `test_highest_of_team_and_direct` — direct contributor + team A contributor + team B maintainer → `"maintainer"`; delete the team-B `TeamMember` → `"contributor"`.
  - `test_inactive_user_has_nothing` — maintainer grant, `active=False` → `visible_project_ids == set()`, role None.
  - `test_agent_principal_uses_owner` — `Principal(user=member, token=<agent-task ApiToken>)` gets the member's role.
  - `test_task_visibility_clause` — tasks: in visible project (seen), in other project (hidden), own ad-hoc (seen), someone else's ad-hoc (hidden), someone else's ad-hoc child whose `root_id` is a task in a visible project (seen); assert via `select(Task.id).where(clause)` and match `can_see_task` for each.
  - `test_migration_0004` in `test_migrations.py` — upgrade to head creates `teams`, `team_members`, `project_members`, and column `workflow_runs.project_ids`; downgrade to `0003` removes them.
- [ ] **Step 2: Run** `CO_DB_NULL_POOL=1 .venv/bin/python -m pytest tests/test_access.py tests/test_migrations.py -q` → FAIL (import errors).
- [ ] **Step 3: Implement** models, migration `0004_scoping` (down_revision `"0003"`), and `app/access.py`. Role resolution is one query: union of direct `ProjectMember(subject_type="user", subject_id=user.id)` and `ProjectMember(subject_type="team")` joined to `TeamMember.user_id == user.id`. Deleting a `Team` must also delete its `TeamMember` and `ProjectMember(subject_type="team")` rows (do it in the API in Task 2; add no DB cascade).
- [ ] **Step 4: Run** the same command → PASS; run the full suite → green.
- [ ] **Step 5: Commit** — `git add backend/app/models.py backend/migrations/versions/0004_scoping.py && git commit -m "feat(db): teams, team members, project members; workflow run project ids"`; `git add backend/app/access.py && git commit -m "feat(access): project visibility and role resolution"`; `git add backend/tests && git commit -m "tests: access resolution and migration 0004"`.

### Task 2: Teams, project members, user directory APIs

**Files:**
- Create: `backend/app/routers/teams.py`
- Modify: `backend/app/routers/projects.py` (members endpoints), `backend/app/routers/users.py` (`/users/directory`), `backend/app/schemas.py`, `backend/app/main.py` (register `teams.router` in the `_authed` list)
- Test: `backend/tests/test_teams_members.py`; add a tiny helper in `tests/conftest.py`: `def login_as(email: str, role: str = "member") -> TestClient` that creates the user via the admin `/users` API (password = `ADMIN_PASSWORD`) and returns a logged-in client with the CSRF header.

**Interfaces:**
- Consumes: Task 1 `require_project_role`, models.
- Produces:
  - `GET /teams` → `[TeamOut{id, name, members: [UserBrief{id, name, email}]}]` (any user); `POST /teams {name}` → 201 (admin; 409 on duplicate name); `PATCH /teams/{id} {name}` (admin); `DELETE /teams/{id}` → 204 (admin; also removes its members and its project grants); `PUT /teams/{id}/members {user_ids: [...]}` → TeamOut (admin; replaces list; unknown ids → 400).
  - `GET /projects/{id}/members` → `[MemberOut{subject_type, subject_id, role, label}]` (any role; `label` = user name/email or team name); `PUT /projects/{id}/members [MemberIn{subject_type, subject_id, role}]` (maintainer; replaces; validates subject exists and role in {maintainer, contributor}; duplicates → 400).
  - `GET /users/directory` → `[UserBrief]` of active users (any logged-in user).
- [ ] **Step 1: Write failing tests**: `test_teams_admin_only` (member POST /teams → 403); `test_team_crud_and_members`; `test_delete_team_removes_grants` (team grant on project → member loses visibility after delete); `test_members_put_by_maintainer` (contributor PUT → 403, maintainer PUT → 200, outsider GET → 404); `test_members_put_rejects_bad_role_and_unknown_subject` (400); `test_directory_any_user` (member gets list, no `role`/`password_hash` fields).
- [ ] **Step 2: Run** `pytest tests/test_teams_members.py -q` → FAIL.
- [ ] **Step 3: Implement** the routers and schemas above.
- [ ] **Step 4: Run** → PASS; full suite green.
- [ ] **Step 5: Commit** — routers+schemas+main as `feat(api): teams, project members, user directory`; tests as `tests: teams and project membership API`.

### Task 3: Project scoping

**Files:**
- Modify: `backend/app/routers/projects.py`, `backend/app/schemas.py` (`ProjectOut.my_role`)
- Test: `backend/tests/test_project_scoping.py`

**Interfaces:**
- Consumes: Task 1 access functions; Task 2 `login_as`, members PUT for setup.
- Produces: `ProjectOut.my_role: str | None`; `MAINTAINER_FIELDS = {"name","instructions","default_model","memory_enabled","memory","budget_usd","github_repo","auto_review_prs"}`, `ADMIN_FIELDS = {"directory","env","archived"}` in `projects.py`.
- Rules (spec table, Project row): list filtered by `visible_project_ids`; GET/memory/summaries/stats/servers-GET need any role; memory regenerate needs contributor; PATCH needs maintainer for `MAINTAINER_FIELDS` and admin for any `ADMIN_FIELDS` key present (403, nothing saved); POST, `/discover`, `/import`, DELETE, `PUT /servers` admin-only; `env` values masked to `"••••"` for non-admins in every `ProjectOut`.
- [ ] **Step 1: Write failing tests**: `test_list_only_visible`; `test_get_invisible_404`; `test_my_role_values` (admin→maintainer, contributor, maintainer); `test_env_masked_for_member` (admin sees value, member sees `"••••"`); `test_contributor_cannot_patch` (403); `test_maintainer_patch_allowed_fields`; `test_mixed_patch_rejected_nothing_saved` (Review Focus 3: `{"instructions": "x", "directory": "/tmp"}` by maintainer → 403, instructions unchanged); `test_admin_only_routes` (member: POST /projects, GET /projects/discover, POST /projects/import, DELETE, PUT servers → 403 or 404 for invisible); `test_regenerate_needs_contributor`.
- [ ] **Step 2: Run** `pytest tests/test_project_scoping.py -q` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS; full suite green (existing tests use the admin client, so they keep passing).
- [ ] **Step 5: Commit** `feat(projects): membership-scoped project access; admin-only server fields`; tests separately.

### Task 4: Tasks, stream, approvals

**Files:**
- Modify: `backend/app/routers/tasks.py`, `backend/app/routers/stream.py`, `backend/app/routers/approvals.py`
- Test: `backend/tests/test_task_scoping.py`

**Interfaces:**
- Consumes: `task_visibility_clause`, `require_task`, `task_role`, `require_project_role`.
- Rules: create needs contributor on `project_id` (none → allowed); list/stats filter with `task_visibility_clause`; get/thread/events/report/artifacts(+download) use `require_task`; cancel/retry/duplicate/followup/delete need creator, `task_role == "maintainer"`, or admin, else 403 (follow-up/duplicate additionally need contributor on the project); WebSocket closes **4404** when the task is invisible. Approvals: list = `AWAITING_APPROVAL` tasks the viewer may decide; may decide iff admin, or (task has a project and viewer is its maintainer and not (`task.risk == "critical"` and `task.created_by == viewer`)). Ad-hoc tasks: admin only. Bulk approve skips undecidable ids (returns only decided).
- [ ] **Step 1: Write failing tests**: `test_task_list_scoped`; `test_task_get_events_artifacts_report_404_for_outsider`; `test_create_in_project_needs_contributor`; `test_contributor_cannot_cancel_others_task` / `test_maintainer_can_cancel`; `test_child_of_visible_root_visible` (ad-hoc child with `root_id` of a project task); `test_ws_invisible_task_4404`; `test_approvals_routed_to_maintainers` (contributor list empty, maintainer sees it); `test_cannot_self_approve_critical` (maintainer-requested critical task: their approve → 403, another maintainer → 200, admin → 200); `test_adhoc_approval_admin_only`.
- [ ] **Step 2: Run** `pytest tests/test_task_scoping.py -q` → FAIL.
- [ ] **Step 3: Implement.** Use `requires_approval=True` in the create payload to put tasks in the queue; set `risk` to critical with a prompt `classify_risk` marks critical (check `task_service.classify_risk` for a trigger word) or set it in the DB.
- [ ] **Step 4: Run** → PASS; full suite green.
- [ ] **Step 5: Commit** `feat(tasks): scope tasks, stream and approvals by project membership`; tests separately.

### Task 5: Agents, templates, workflows, schedules

**Files:**
- Modify: `backend/app/routers/agents.py`, `templates.py`, `workflows.py`, `schedules.py`, `backend/app/scheduler.py`, `backend/app/workflow_runner.py` (only if needed to read stage projects)
- Test: `backend/tests/test_run_scoping.py`

**Interfaces:**
- Consumes: Task 1 functions; `WorkflowRun.project_ids`.
- Produces: `async def workflow_projects(s, wf: Workflow) -> list[str]` in `workflows.py` — distinct non-null `project_id`s of the agents its stages reference (stage `agent` is id or exact name).
- Rules: agents/templates/workflows POST/PATCH/DELETE admin-only; lists visible to all. Run (agent run/bulk, template run, workflow run): contributor on the target project(s) — agent: `payload.project_id or agent.project_id`; workflow: every id in `workflow_projects` (AND) — 404/403 per access rules. `POST /agents/dispatch` (agent tokens and users): for each target project, owner must be contributor, else **403** with detail exactly `f"{user.email} is not a contributor on project {project.name}"`. Workflow run stores `project_ids` at creation. Runs list: admin all; else `created_by == me` OR any overlap with visible ids (compute in SQL on Postgres is awkward with JSON → filter in Python is allowed here because the runs list is not paginated; say so in a comment). Run get/cancel/retry: view = creator/admin/any overlap; cancel/retry = creator, admin, or maintainer of any touched project. Schedules: list/get filter (project: any role; none: creator/admin); create/run-now need contributor; PATCH/DELETE need creator, maintainer, or admin. Scheduler: skip firing (log `"Skipping schedule %s (%s): owner lost access to project"`, advance `next_run_at`) when the owner has no contributor role on `sch.project_id` (admin owners always pass).
- [ ] **Step 1: Write failing tests**: `test_agent_template_workflow_writes_admin_only`; `test_run_agent_needs_contributor_on_target`; `test_dispatch_refused_message` (agent token of a member without access → 403 and exact detail); `test_workflow_run_needs_all_projects` (contributor on 1 of 2 → 403; both → 201 and `project_ids` stored); `test_workflow_run_visible_with_any_overlap`; `test_schedule_scoping`; `test_scheduler_skips_owner_without_access` (call the scheduler's tick function directly, assert no task created and `next_run_at` advanced).
- [ ] **Step 2: Run** `pytest tests/test_run_scoping.py -q` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS; full suite green.
- [ ] **Step 5: Commit** `feat(automation): admin-managed agents/templates/workflows; membership-checked runs and schedules`; tests separately.

### Task 6: Findings, usage, MCP observability, knowledge docs, project-scoped MCP & skills

**Files:**
- Modify: `backend/app/routers/findings.py`, `usage.py`, `mcp.py`, `context_docs.py`, `skills.py`
- Test: `backend/tests/test_misc_scoping.py`

**Interfaces:**
- Consumes: Task 1 functions.
- Produces: `GET /usage/by-user` → `[{user_id, name, email, cost_usd, tokens, tasks}]` over the viewer's visible tasks (tasks with `created_by` null grouped under `user_id: null`, name `"(unassigned)"`).
- Rules: findings list/summary filter by visible projects; PATCH needs contributor. Usage summary/timeseries/by-project/by-model/by-user all filter tasks with `task_visibility_clause`. MCP `/observability` counts only `McpCall` rows whose `task_id` is a visible task (admins: all, including `task_id` null). MCP servers list/get and skill packs list hide `scope == "project"` rows whose project isn't visible (MCP: resolve `McpServer.project` name → `Project.id` by current name; unresolvable → admin-only). Policies GET follows server visibility. Knowledge docs: list/get/preview hide project docs of invisible projects (`preview?project_id=` of an invisible project → 404); create/patch/delete: global → admin, project → maintainer.
- [ ] **Step 1: Write failing tests**: `test_findings_scoped_and_patch_needs_contributor`; `test_usage_scoped` (member sees only their projects' cost); `test_usage_by_user`; `test_zero_grants_empty_everywhere` (Review Focus 5: GET /projects, /tasks, /findings, /schedules, /workflows/runs, /approvals, /context-docs all `[]`-or-only-global, `/usage/summary` totals 0); `test_mcp_observability_scoped`; `test_project_scoped_mcp_and_skill_hidden`; `test_mcp_project_match_after_rename` (Review Focus 4); `test_context_docs_roles` (contributor create project doc → 403, maintainer → 201, member create global → 403).
- [ ] **Step 2: Run** `pytest tests/test_misc_scoping.py -q` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS; full suite green.
- [ ] **Step 5: Commit** `feat(scoping): findings, usage (+by-user), MCP and knowledge docs follow membership`; tests separately.

### Task 7: Route-coverage test

**Files:**
- Create: `backend/tests/test_scoping_coverage.py`

**Interfaces:**
- Consumes: `_flat_routes`/`_all_http_routes` from `tests/test_lockdown.py` (import them), Tasks 2–6 behaviour.
- [ ] **Step 1: Write the test.** Fixture: as admin create project P (no grants for the outsider), and in P: a task (with an artifact if the mock produces one), a schedule, a finding (insert in DB), a project knowledge doc, a project-scoped skill pack and MCP server, a workflow + run touching P (agent with `project_id=P`). Map each path parameter name to the matching id (`project_id`, `task_id`, `schedule_id`, `finding_id`, `doc_id`, `pack_id`, `server_id`, `run_id`, `workflow_id`, `agent_id`, `template_id`, `filename`). For every HTTP route not in an explicit `NOT_PROJECT_BOUND = {(method, path): "reason", ...}` dict, call it as the outsider (logged-in member with no grants) and assert: routes with a P-bound path param → status in {403, 404}; list routes (no path param) → 200 and the response JSON contains none of P's ids (search the serialized body). Also `test_not_project_bound_is_exact`: every entry in `NOT_PROJECT_BOUND` exists as a route (stale entries fail). WebSocket: outsider on P's task → close 4404.
- [ ] **Step 2: Run** `pytest tests/test_scoping_coverage.py -q`. Expected PASS. If any route fails, fix that route (it missed a check) — never widen `NOT_PROJECT_BOUND` without a reason a reviewer would accept.
- [ ] **Step 3: Full suite green; commit** `tests: every project-bound route enforces membership`.

### Task 8: Frontend — Teams, Members tab, role-aware UI, usage by user

**Files:**
- Create: `frontend/src/components/TeamsView.jsx`, `frontend/src/components/ProjectMembers.jsx`
- Modify: `frontend/src/api.js`, `frontend/src/App.jsx` (Teams nav for admins next to Users), `frontend/src/components/ProjectsView.jsx` (Members tab; hide admin-only fields/buttons unless `me.role === "admin"`; hide maintainer controls unless `project.my_role === "maintainer"`), `frontend/src/components/UsageView.jsx` (by-user table), `frontend/vite.config.js` (`/teams` proxy)
- [ ] **Step 1:** api.js: `listTeams, createTeam, updateTeam, deleteTeam, setTeamMembers, getProjectMembers, setProjectMembers, userDirectory, usageByUser`.
- [ ] **Step 2:** TeamsView (admin): table of teams, create/rename/delete, member multi-select from `userDirectory`. ProjectMembers: list rows (label, type tag, role); maintainers get add (user or team picker + role) / change role / remove, saved via one PUT.
- [ ] **Step 3:** Role-aware hiding in ProjectsView and automation views (agents/templates/workflows create/edit buttons admin-only; knowledge docs global add admin-only). UsageView: "By user" table.
- [ ] **Step 4: Verify** `cd frontend && npm run build` succeeds; then, against a scratch backend on spare ports (not 8200/5200) with a temp SQLite DB, log in as admin and as a member and click through Teams, Members, project list filtering (headless Chrome via Playwright `channel: 'chrome'` as in Phase 1). Do not create real tasks.
- [ ] **Step 5: Commit** `frontend: teams page, project members tab, role-aware controls, usage by user`.
