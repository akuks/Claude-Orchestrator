# Forge Port Phase 6c Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Database tab read from SQL migration files, a Recommended panel with UI-skills text for front-end projects, and a "Published outside the project" list on the task page.

**Architecture:** Three small backend packages (`app/schema/`, `app/recommend/`, hook + spool for outputs), each with one read endpoint; migration 0020 adds two project columns; the frontend adds two project tabs and one task card.

**Tech Stack:** FastAPI, async SQLAlchemy, Alembic, pytest (SQLite), sqlglot, React 18 + antd 5 + vitest.

**Spec:** `docs/superpowers/specs/2026-11-24-forge-extras-phase6c-design.md` — read it first; every exact value (roots, limits, texts, preset versions, regexes, answer shapes) is there and binding.

## Global Constraints
- **Commands.** Backend, from `backend/`: `env -u CO_DATABASE_URL /Users/ashutosh/Projects/Work/Claude-Orchestrator/backend/.venv/bin/python -m pytest -o addopts="" -q <paths>`. Frontend, from `frontend/`: `npx vitest run <files>`, then `npm run build`.
- **Hard rules.**
  - Do NOT connect to any Postgres server on this machine. Default temp SQLite only.
  - Never run the real `claude`, `npx` with a package, or any real MCP server.
  - Do not use ports 8200/5200. Never restart or touch the running app.
  - Never `monkeypatch.setattr(app.state, …)`; use `monkeypatch.setitem(app.state._state, …)`.
  - The only package you may install is `sqlglot` (Task 1), with `/Users/ashutosh/Projects/Work/Claude-Orchestrator/backend/.venv/bin/pip install "sqlglot>=25"`.
- **Commits.** Folder by folder, no trailers of any kind.
- **Migration.** Revision `"0020"`, `down_revision = "0019"`; booleans get a server default.
- **Reading project files.** Always: relative path, `lstat` regular file, `O_NOFOLLOW`, real path inside the project folder, capped size. Reuse `routers/skills.py:_read_capped` / `gates/outputs.py:_inside` style; nothing from the project is ever executed or imported.
- **Wording.** Screens use the spec's plain texts exactly; file:line, shas and raw names go under a collapsed "Details".
- **Permissions.** Read endpoints: `require_project_role(s, principal, project_id)`; settings changes: `"maintainer"`; adding a tool: the existing admin-only `POST /mcp/servers`.

## Review Focus
1. **A migration file sqlglot cannot parse, or a 4 MB single file** — counted as not understood / limit named; the endpoint still answers within the budget (Task 1).
2. **A link inside `migrations/` pointing outside the project, or `migrations` itself a link** — skipped, never read (Task 1).
3. **A project with no folder, or a folder that vanished** — all three endpoints answer plainly (`found=false` / `has_ui=false`), no 500 (Tasks 1, 2).
4. **A publish link with a token in its query string** — never stored or shown (Task 3).
5. **`brand_skill` holding prompt text (spaces, newlines, quotes)** — refused with 422 (Task 2).

---

### Task 1: Schema reader and endpoint

**Files:**
- Create: `backend/app/schema/__init__.py`, `backend/app/schema/files.py` (find + safe read + order + limits), `backend/app/schema/replay.py` (sqlglot replay), `backend/app/routers/schema.py`
- Modify: `backend/requirements.txt` (`sqlglot>=25`), `backend/app/main.py` (router), the task-token allowlist (find where `CO_API_TOKEN` routes are allowed; add GET schema for the task's own project)
- Test: `backend/tests/test_schema_files.py`, `backend/tests/test_schema_replay.py`, `backend/tests/test_schema_api.py`

**Interfaces:**
- `files.collect(project_dir: str) -> Collected` (`roots: list[RootFiles]`, `limits: list[str]`); each file `(rel_path, text)`.
- `replay.replay(files: list[tuple[str, str]]) -> RootSchema` (tables, `not_understood`).
- `GET /projects/{id}/schema[?table=]` → the spec's answer shape.

- [ ] **Step 1: Failing tests.**
  - Files: numeric-aware order (`2_x.sql` before `10_x.sql`); down/undo skipped (each pattern in the spec); prisma layout; the four roots kept separate; 5,000-file and 5 MB limits named in `limits`; a link file and a linked root skipped; a file outside the project never read.
  - Replay: CREATE with primary key and foreign key (inline and table-level); ALTER add/drop/rename column; add/drop constraint; DROP and RENAME TABLE; `public.`/`dbo.` treated as default; Postgres `$tag$` bodies, MySQL backticks, T-SQL brackets; garbage statement → `not_understood.count == 1` with `file:line`; a CREATE inside a `DO $$ … $$` block marks the table uncertain; `defined_at` is `file:line`.
  - API: member only (403/404 as the code-map router does); `?table=` filter; no folder → `found=false` with the spec's note; 60/200/50 caps named; a task token for its own project is allowed and for another project refused; a parser that sleeps past the budget gives the "took too long" answer (shrink the budget with monkeypatch).
- [ ] **Step 2: Implement; run the three test files plus `tests/test_codemap_api.py` (or the nearest router test). Commit `feat(schema): read a project's tables from its SQL migration files`.**

### Task 2: Recommended panel backend and UI-skills text

**Files:**
- Create: `backend/app/recommend/__init__.py`, `backend/app/recommend/detect.py`, `backend/app/recommend/panel.py`, `backend/app/routers/recommend.py`, `backend/migrations/versions/0020_ui_skills.py`
- Modify: `backend/app/codemap/presets.py` (two new pinned presets, keys `browser-devtools`, `ui-shadcn`), `backend/app/models.py`, `backend/app/schemas.py`, `backend/app/routers/projects.py` (accept `ui_skills`, `brand_skill`, maintainer), `backend/app/guard/policy_inputs.py` + `backend/app/worker.py` (paragraph into main ground rules and helper start text), `backend/app/main.py`
- Test: `backend/tests/test_recommend_detect.py`, `backend/tests/test_recommend_api.py`, `backend/tests/test_ui_skills_text.py`, `backend/tests/test_migrations.py`

**Interfaces:**
- `detect.detect(project_dir: str | None) -> Detected(has_ui: bool, react: bool, brand_skill_found: bool)` — never raises.
- `panel.ui_skills_text(project, detected) -> str | None` — the spec's paragraph, exact.
- `GET /projects/{id}/recommended` → the spec's shape.

- [ ] **Step 1: Failing tests.**
  - Detect: each dependency name; each file ending in root, `src/`, `frontend/`, `frontend/src/`; `frontend/package.json`; a 1 MB or invalid package.json → not detected, no error; a linked package.json not followed; missing folder → all false.
  - Panel: the table in the spec row by row (`ui-shadcn` only with react); `present` true when a matching enabled tool is registered for the project and false when disabled or for another project; `frontend-design` present from an enabled pack.
  - Text: the three `{brand}` variants exactly; nothing added when `ui_skills` is off or no UI detected; the paragraph appears in the main ground rules event and in the policy's helper text.
  - API: `brand_skill` with a space, newline or quote → 422; member cannot change settings, maintainer can; migration up/down, old rows read `ui_skills=true`.
- [ ] **Step 2: Implement; run these tests plus the guard policy-input tests, `tests/test_mcp*` and project API tests. Commit `feat(recommend): suggested tools and design-skill text for projects with a user interface`.**

### Task 3: Published links

**Files:**
- Modify: `backend/app/guard/co_hook.py` (PostToolUse: spool an `Output` record), `backend/app/guard/runtime.py` / `backend/app/worker.py` (spool reader → task event type `output`), `backend/app/resume/ground_rules.py` (switch `local_first`, default on, exact text in the spec)
- Test: `backend/tests/guard/test_outputs_hook.py`, `backend/tests/test_outputs_ingest.py`, the ground-rules tests

**Interfaces:**
- Spool record: `{"event":"Output","link","tool","title","local_file"}`.
- Task event: `type="output"`, `payload={link, tool, title, local_file, kept}`.

- [ ] **Step 1: Failing tests.**
  - Hook: each tool-name word; each `gh … create` form; no link → nothing; query string, fragment and `user:pass@` removed; link over 500 chars dropped; `title` picked and capped; `local_file` found only for a real regular file inside the project or workspace (a link, an outside path and `..` give null); 21st record and a repeated link not spooled; the hook adds no network call and stays within its existing time budget (time one call before and after, report both).
  - Ingest: record → event with `kept` true/false; a malformed record ignored; an `output` event is never produced from a record whose link is not http(s).
  - Ground rules: `local_first` on by default, text exact, switch off removes it, old stored settings without the key read as on.
- [ ] **Step 2: Implement; run the whole `tests/guard` suite and `tests/test_guard_worker.py`. Commit `feat(outputs): list what a run published outside the project`.**

### Task 4: Screens and README

**Files:**
- Create: `frontend/src/schema/DatabaseSection.jsx` (+ test, css), `frontend/src/recommend/RecommendedSection.jsx` (+ test), `frontend/src/runs/PublishedLinks.jsx` (+ test)
- Modify: `frontend/src/api.js`, `frontend/src/components/ProjectsView.jsx` (tabs "Database" and "Recommended"), `frontend/src/components/TaskDetail.jsx` (card after `HandoffCard`), `frontend/src/codemap/CodeMapSection.jsx` (readable times via `when()` from `time.js`), `backend/app/routers/codemap.py` (log: project real path → `<project folder>`), `README.md`
- Test: the three new test files, `frontend/src/codemap/CodeMapSection.test.jsx`, `backend/tests/test_codemap_api.py`

**Interfaces:** consumes the three endpoints and `output` task events exactly as specified.

- [ ] **Step 1: Failing tests.**
  - Database: empty state shows the spec's note; a table card shows columns with a key mark and "links to <table>"; uncertain tables carry "May differ from what is shown"; file:line and the sha only under "Details"; "N statements were not understood" line; a limits line.
  - Recommended: tab hidden when `has_ui` is false; each item shows "Added" or an "Add" button (admin) / "Ask an admin to add this"; Add posts the preset scoped to the project; the switch and brand field save through `updateProject`; a 422 text is shown under the field.
  - Published links: hidden with none; shows the link, the file, the amber "No copy in the project" tag.
  - Code map: no raw ISO string is rendered; the log endpoint hides the folder path.
- [ ] **Step 2: Implement; run the tests and `npm run build`.** README: three short sections ("Database tab", "Recommended tools for projects with a user interface", "Published outside the project"), each with what it does and the spec's "Not covered / to know" lines. Commit `feat(ui): database tab, recommended panel, published links` and `docs(readme): database tab, recommended panel, published links`.
