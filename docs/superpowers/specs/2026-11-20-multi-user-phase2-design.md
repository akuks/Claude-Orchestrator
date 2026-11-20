# Multi-user Phase 2 — Teams, membership, scoping — design

Parent design: `docs/superpowers/specs/2026-11-19-multi-user-design.md`
(goals, trust model and anti-goals there still bind). Phase 1 (accounts,
login, Postgres, route lock-down, agent tokens, `created_by`) is merged.

## Intent
Let an organisation split work: people only see and act on the projects they
are assigned to, teams make assignment cheap, and risky work is approved by the
project's maintainers instead of the person who asked for it.

## Goals
- Teams; a project can be assigned to teams and/or individual users, each grant
  with a role **maintainer** or **contributor**.
- Members see everything on their projects (teammates' tasks, results, costs)
  and nothing on other projects.
- Approvals go to the project's maintainers and admins.
- Usage can be viewed per project and per user.

## Non-goals (this phase)
- Per-user Claude/GitHub credentials, worktree per task, PRs, the project
  "automation owner" and worktree copy-list — all Phase 3.
- Members creating their own agents, templates or workflows.
- Fine-grained ACLs below the project level.

## Anti-goals
- A member reaching any project-bound object of a project they're not on
  through *any* route (list, get, stream, artifact download, report, stats,
  usage, approvals, MCP observability) or through an agent token.
- A non-admin gaining server access: editing project `directory`, `env`,
  remote-ops servers, folder import, MCP servers or skill packs.
- Project `env` values (may hold secrets) shown to non-admins.
- Breaking delegation: coordinator agents must still dispatch within the
  projects their task's owner can use.

## Decisions (from the conversation)
- **Who creates agents, templates, workflows:** admins only (option a).
  Members use the shared list. (Loosening later is cheap.)
- **Enforcement approach:** one access module called explicitly by every
  route, plus a coverage test that walks every route (approach 1). Rejected:
  automatic ORM filtering (hidden, needs escape hatches, doesn't cover write
  roles); Postgres row-level security (breaks SQLite tests/dev).

## Approaches considered
| Approach | Trade-off | Outcome |
|---|---|---|
| 1. Access module + per-route calls + route-coverage test | explicit; one place for view and edit rules; must remember each route, the test catches misses | **chosen** |
| 2. ORM-level automatic filter (`with_loader_criteria`) | hard to forget; magic; worker/scheduler need bypass; write roles still per-route | rejected |
| 3. Postgres RLS | strongest; breaks SQLite; rules hidden in DB | rejected |

## Data model (migration 0004)
- `teams` (id, name unique, created_at).
- `team_members` (team_id, user_id; unique pair).
- `project_members` (id, project_id, subject_type `user`|`team`, subject_id,
  role `maintainer`|`contributor`; unique (project_id, subject_type,
  subject_id)).
- Deleting a team or user removes their membership rows. Deleting a project
  removes its grants.
- No backfill: existing projects start with no members (only admins see them,
  which matches today's single-admin reality).

## Access rules
`app/access.py` is the single source:
- `visible_project_ids(principal) -> set[str] | ALL` — admins get ALL.
- `project_role(principal, project_id) -> 'maintainer'|'contributor'|None` —
  effective role = highest of the direct grant and grants via any of the user's
  teams; admins are implicitly maintainers everywhere; disabled users have none.
- `require_project_role(principal, project_id, min_role)` raises **404** when
  the user can't see the project (don't leak existence) and **403** when they
  can see it but lack the role.
- `can_see_task(principal, task)` — project task: any role on its project;
  no-project (ad-hoc) task: its creator or an admin.
- Agent-token principals act with their **owner's** rights intersected with
  the token's existing scope (dispatch under its task; read its unit).

### Per object
| Object | View | Create / act | Edit / delete |
|---|---|---|---|
| Project | any role | admin (create, import, discover) | maintainer: name, instructions, default_model, memory (edit/regenerate), budget, github_repo, auto_review_prs; **admin only**: directory, env, archived, delete, remote-ops servers |
| Project `env` | admin sees values; others get keys masked (`has_env` + key names) | — | admin |
| Members of a project | any role | maintainer manages grants (users and teams, either role) | maintainer |
| Task, events, artifacts, report, thread, stream | `can_see_task` | contributor on the project (or no project = anyone) | cancel/retry/duplicate/follow-up/delete: creator, maintainer, admin |
| Approvals | queue lists only tasks the viewer can decide | approve/reject: maintainer of the task's project or admin; ad-hoc tasks: admin; a requester can't decide their own **critical** task unless admin | — |
| Findings | any role on project | — | triage (PATCH): contributor |
| Schedules | project: any role; no project: creator/admin | contributor on its project | creator, maintainer, admin; run-now: contributor |
| Agents / templates / workflows | everyone sees the list | run: contributor on every project the run would touch (agent's project or the override; each workflow stage's agent project); no-project agent = ad-hoc task | admin |
| Workflow runs | viewer can see every project its stages touch, or is creator/admin | (see run above) | cancel/retry: creator, maintainer of all touched projects, admin |
| Knowledge docs | global: everyone; project: any role | global: admin; project: maintainer | same as create |
| MCP servers, policies, skill packs | everyone (secrets already masked) | admin (unchanged) | admin |
| MCP observability | calls from tasks the viewer can see | — | — |
| Usage | aggregates over visible projects + own ad-hoc tasks; admins see all | — | — |
| Teams | everyone can list names/members | admin | admin |
| User directory | `GET /users/directory` → id, name, email of active users, for any logged-in user (needed for the Members picker) | — | — (full `/users` stays admin) |

Lists are filtered in SQL (`project_id IN visible` or the ad-hoc rule), never
fetched-then-filtered in Python where the list is paginated.

The background worker, scheduler, workflow runner and webhook are not
principals and are unaffected. A schedule whose owner has lost access to its
project is **skipped** with a logged reason (like Phase 1's disabled-owner
rule).

## API additions
- `/teams`: GET (all), POST/PATCH/DELETE (admin); `/teams/{id}/members`
  PUT replace list (admin).
- `/projects/{id}/members`: GET (any role), PUT replace list (maintainer).
  Body: `[{subject_type, subject_id, role}]`.
- `GET /users/directory`.
- `ProjectOut` gains `my_role` (`maintainer`|`contributor`, admin →
  `maintainer`).
- `/usage/by-user` (per user totals over the viewer's visible scope).

## UI
- **Teams page** (admin, next to Users): list teams, add/remove members.
- **Project "Members" tab**: users and teams with role; maintainers edit;
  contributors read-only.
- Project picker and every project dropdown show only visible projects
  (comes free from the filtered `/projects`).
- Admin-only fields/buttons hidden for non-admins (directory, env, archive,
  delete, remote servers, new project, import); maintainer-only controls hidden
  for contributors. The server enforces regardless.
- Approvals page shows only decidable items; Usage gets a "by user" view.

## Testing
- Coverage test: enumerate every route; for each project-bound route assert a
  user with no grant on the target project gets 404 (or a filtered list), the
  same way `test_lockdown.py` enumerates routes. A route with no project
  binding is listed explicitly in an allowlist with a reason.
- Role matrix tests: contributor vs maintainer vs admin on project edits,
  member management, approvals (incl. self-approval of a critical task),
  schedules, knowledge docs, workflow runs spanning two projects.
- Team grants: role via team; highest-of rule; removal from team revokes.
- Agent token: dispatch into a project the owner can't use → refused.
- `env` masking for non-admins.
- Runs on SQLite and Postgres (existing CI).

## Clarifications (from the builder check; marked "my call" where decided by the designer)
1. **"Remote-ops servers"** = the project's `servers.yaml` inventory behind
   `GET/PUT /projects/{id}/servers` (`ServerEntry`: host, user, pem path on
   the server). View: any role on the project; PUT: admin only.
   `allow_remote` on tasks/agents is unchanged this phase (trust-within-org;
   it already defaults to true) — my call.
2. **Project-scoped MCP servers, skill packs and knowledge docs** are
   project-bound: non-members don't see them in lists or by id (anti-goal
   wins). `McpServer.project` holds a project *name*: resolve it to the
   project id with a name lookup (and when the project is renamed, it is matched by its current name) — my call.
   Global/team/user-scoped ones stay visible to everyone.
3. **Self-approval** applies only to `risk == "critical"` (as the parent
   design says). A project whose only maintainer requested it needs an admin —
   intended. The "requester" is `task.created_by` (for agent-token tasks, the
   token owner). A requester later removed from the project doesn't change who
   may decide.
4. **Owner loses access mid-run:** running tasks continue (maintainers can
   cancel); the next dispatch into that project fails with 403 and a plain
   message `"<owner> is not a contributor on project <name>"` the coordinator
   can read — my call.
5. **Task visibility follows the thread:** a task with no project but a
   `parent_task_id` (a dispatched child) is visible to anyone who can see its
   parent, recursively. Follow-ups share the thread's `root_id`, and a thread
   is visible as a whole by its root's rule — my call.
6. **Workflow runs:** creating a run needs contributor on *every* touched
   project (AND). Viewing needs *any* touched project (ANY), or
   creator/admin. Cancel/retry: creator, a maintainer of any touched project,
   or admin. The run stores its touched project ids in a new
   `workflow_runs.project_ids` JSON column at creation time so lists filter
   without recomputing — my call.
7. **Usage by user:** members see per-user cost rows for the projects they can
   see (teammates' included, per the goal), plus their own ad-hoc tasks.
   Totals are over that same scope, so views reconcile within it.
8. **Existing data:** there are no non-admin users today, so no backfill is
   needed; only admins can create projects, so no creator auto-grant.
9. **Users are disabled, not deleted** (Phase 1 has no delete). Disabled users'
   grants stay in place but confer nothing. Removing the last maintainer is
   allowed — admins are implicit maintainers.

## Builder's latitude
Exact module layout, response shapes beyond the fields named, UI layout, error
copy, and test structure. Anything changing a row of the access table above
comes back for a decision.
