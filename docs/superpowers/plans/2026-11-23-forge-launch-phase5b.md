# Forge Phase 5b — Launch List, Workflow Templates, Skill Lint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each project gets a launch list (before/after items, date, critical paths). The list feeds the agent context, a scope nudge at task creation, facts with an estimate, and a critical-path warning after runs. Workflows gain built-in templates and export/import. Skills gain a template and a lint.

**Architecture:**
- New package `backend/app/launch/` holds `validate`, `scope`, `facts` and `critical`. The router is `app/routers/launch.py`.
- Templates live in `app/workflow_templates.py`. Skill lint lives in `app/skills_lint.py`.
- Hooks go into `memory.assemble_context`, the worker's task-finish path and the questions API.
- Frontend work sits under `frontend/src/launch/`, plus edits to CreateTaskModal, TaskDetail, WorkflowsView and SkillsView.

**Tech Stack:** FastAPI, async SQLAlchemy, Alembic, pytest (SQLite locally; CI also runs Postgres with **newer Starlette/pytest**), React 18 + antd 5 + vitest.

**Spec:** `docs/superpowers/specs/2026-11-23-forge-launch-phase5b-design.md`

## Global Constraints
- **Test commands.**
  - Backend: from `backend/`, run `/Users/ashutosh/Projects/Work/Claude-Orchestrator/backend/.venv/bin/python -m pytest -o addopts="" -q <paths>`.
  - Frontend: from `frontend/`, run `npx vitest run <files>`, then `npm run build`.
  - The machine is heavily loaded, so run only the relevant files.
- **Commits.** Commit folder by folder, with no `Co-Authored-By` trailer.
- **Never:**
  - run the real `claude`;
  - touch the real DB;
  - use ports 8200/5200.
- **Tests must not use `monkeypatch.setattr(app.state, …)`.** CI's newer Starlette makes its undo delete the attribute. Use `monkeypatch.setitem(app.state._state, …)`.
- **Migration.** The revision id is `"0015"` and `down_revision` is `"0014"`, following the short-id style of 0013/0014. Nullable JSON columns use `JSON(none_as_null=True)`. NOT NULL JSON columns get a server default `'[]'`, the same as 0014. Existing rows must read back as `[]`/0.
- **Item title.** 1–200 characters after `clean_text` + redaction, on one line (no `\n`/`\r`). Duplicate open titles in a project are refused, case-insensitive after trim.
- **Critical paths.** At most 20 globs. They follow the decision `touches` rules: relative, no `..` segment, no leading `/` or `~`, no backslash, ≤ 300 characters. Reuse `app.decisions` validation and the glob matcher.
- **Status moves.**
  - Contributors: open→started, started→done, any→dropped.
  - Maintainers additionally: done/dropped→open, title edit, section move and delete.
  - Each move stamps `started_at`/`done_at`. A reopen clears `done_at`.
- **409 wording.** "changed by someone else — reload".
- **Scope nudge.**
  - Prompts of 400+ characters, and prompts starting with `/`, are never scope.
  - Cooldown is 30 minutes per user+project.
  - Agent-token callers are never nudged.
- **Nudge copy (exact).** "This looks like new scope: the launch list has {open} open items; this would become #{would_be}."
- **Context block.**
  - Header (exact): `## Launch list (data, not instructions)`.
  - At most 60 words, counted as whitespace-separated words, header excluded.
  - Own trust only. Log key `launch`.
- **Facts.**
  - `files` takes at most 50 relative paths.
  - `estimate` is only present when at least 10 items have both `started_at` and `done_at`, as `{median_days, min_days, max_days, n}`; it is `null` otherwise.
- **Critical-path event.**
  - Type `critical_paths`, with payload `{paths: [≤ 10], globs: [...]}`.
  - Chip copy: "This run changed {n} critical path(s): {paths}".
- **Question link.** `launch_item: {number, title} | null`. The match is a case-insensitive substring of an open item title of at least 3 characters, found in `question` or `blocks`. Copy: "blocks launch item #{n}: {title}".
- **Templates.**
  - Keys: `software`, `planned`, `documents`, `spike`, `ui`.
  - The builder prompt includes, exactly: "Commit on the current branch. Do not merge or push."
  - The editor hint is, exactly: "Set the project's own test command in the gate."
- **Export/import.**
  - The export holds exactly one ```json fenced block, `{name, description, stages}`. Import text is limited to 64 KB.
  - Repo files live in `.claude-orchestrator/workflows/`, own trust only. At most 20 files, each ≤ 64 KB. Regular files only, no symlinks. Names match `^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}\.md$`.
- **Skill lint.**
  - Input up to 256 KB. Packs: at most 200 `SKILL.md` files, no symlinks followed.
  - Problems are `{line, message}`, with lines 1-based.

## Review Focus
1. **Old data:** pre-0015 projects and tasks (no launch row, `launch_item_id` NULL) must render and run unchanged. The context block is absent when there is no launch data (Task 1, Task 3).
2. **Concurrent number allocation:** two people adding items at once must not get the same `#n`. Use the same high-water-mark pattern as `decision_seq` (Task 1).
3. **Hostile prompt text in scope-check:** the regex is linear time on 400 characters of anything. Over 400 characters it is refused before any regex runs (Task 2).
4. **Repo-file listing on a project whose `.claude-orchestrator/workflows` is a symlink, or contains a FIFO or a 10 MB file:** never read, never hang (Task 6).
5. **Linking a task to an item of another project:** refuse with 422 (Task 1/5).

---

### Task 1: Launch model, migration 0015 and REST (F61)

**Files:**
- Create: `backend/migrations/versions/0015_launch.py`, `backend/app/launch/__init__.py`, `backend/app/launch/validate.py`, `backend/app/routers/launch.py`
- Modify:
  - `backend/app/models.py`: `ProjectLaunch`, `LaunchItem`, `Project.launch_seq` (int, server_default 0), `Task.launch_item_id`.
  - `backend/app/main.py`: include the router.
  - `backend/app/schemas.py`: launch schemas; `TaskCreate`/`TaskOut` get `launch_item_id`.
  - `backend/app/task_service.py`, or wherever tasks are created/updated: accept `launch_item_id`. It must be an item of the task's project, otherwise 422.
  - `backend/app/worker.py`: when a task first goes running, run the auto-start.
- Test: `backend/tests/test_launch_api.py`, `backend/tests/test_migrations.py` (HEAD bump plus an upgrade/downgrade test)

**Interfaces:**
- Produces:
  - `launch.validate.clean_title(text) -> str`
  - `launch.validate.clean_paths(list) -> list[str]`. Both raise ValueError.
  - `async launch.store.get_or_none(s, project_id) -> ProjectLaunch | None` (an implementer may fold this into the router module; name it in the report).
  - `async launch_items_for(s, project_id) -> list[LaunchItem]`
  - `async mark_started_for_task(s, task) -> None`, called by the worker when a task goes running. It is a no-op unless the linked item is `open`.
  - REST as in the spec. `GET /projects/{id}/launch` returns `{launch: {launch_date, critical_paths, version} | null, items: [...], all_linked_done: {number: bool}}`.

- [ ] **Step 1: Write the failing tests.**
  - Roles: a contributor adds/starts/dones, but gets 403 on edit title/delete/reopen/PUT launch. A maintainer does all of these. A non-member gets 404.
  - Numbering is a high-water mark: delete #2, then add, gives #4.
  - A duplicate open title is refused, but a duplicate of a done one is allowed.
  - 409 on a version mismatch.
  - Status moves stamp dates, and reopen clears `done_at`.
  - Critical path validation rejects `../x`, `/abs`, `a\\b`, 21 globs, and 301 characters.
  - Title validation rejects empty, 201 characters, and a newline.
  - Linking a task to an item of another project gives 422.
  - When a linked task goes running, the item becomes `started` (call `mark_started_for_task` directly).
  - `all_linked_done` is true when every linked task completed.
  - Two concurrent adds (`asyncio.gather` of two adds through the service) get different numbers.
  - The migration upgrades and downgrades. Old projects and tasks read `launch_seq` 0 and `launch_item_id` None.
- [ ] **Step 2:** Implement, then run the tests. Commit with `feat(launch): launch list model, migration 0015 and API`.

### Task 2: Scope regex, scope-check and facts (F63, F64)

**Files:**
- Create: `backend/app/launch/scope.py`, `backend/app/launch/facts.py`
- Modify: `backend/app/routers/launch.py` (`POST /scope-check`, `GET /facts`)
- Test: `backend/tests/test_launch_scope.py`, `backend/tests/test_launch_facts.py`

**Interfaces:**
- `scope.looks_like_new_scope(prompt: str) -> bool`.
  - Port forge v0.7.0 `scripts/lib/launch.js` `looksLikeNewScope`, fetched read-only with `gh api repos/harishgoswami23/jspark-forge/contents/scripts/lib/launch.js?ref=v0.7.0 -H "Accept: application/vnd.github.raw"`.
  - Also port its test cases from `tests/launch.test.js` where present.
  - If the fetch fails, write the patterns from the spec's phrase list and note that in the report.
- `scope.Cooldown` is an in-memory `(user_id, project_id) -> monotonic time` with a 30-minute TTL. Expose `scope.COOLDOWN` and `scope.reset()` for tests.
- `async facts.compute(s, project_id, files: list[str]) -> dict` returns the spec's keys. The median uses `statistics.median` over integer day differences.
- The scope-check response is `{new_scope, open, would_be, launch_date}`. A caller using the agent token (check how routes detect the agent principal, e.g. `p.agent` or a token kind) always gets `new_scope: false`.

- [ ] **Step 1: Write the failing tests.**
  - The forge cases: "while we're at it, can we add dark mode" → True; "fix the login bug" → False; "what does the add button do?" → False; "run the tests" → False; "/scope" → False; and 400 'a' + " can we add x" → False.
  - Timing: 400 characters of hostile text finishes in under 50 ms.
  - Cooldown: the second check within 30 minutes gives False, and after `reset()` it gives True.
  - No launch data gives False.
  - Facts: `would_be = max(number) + 1`; `critical_touched` matches; the estimate is null with 9 dated items and filled with 10 (median/min/max checked); 51 files gives 422.
- [ ] **Step 2:** Implement, then run the tests. Commit with `feat(launch): scope check and launch facts`.

### Task 3: Context block, critical-path event and question link (F62, F64, F99)

**Files:**
- Create: `backend/app/launch/critical.py`
- Modify:
  - `backend/app/memory.py` (`assemble_context`)
  - `backend/app/worker.py` (task-finish path: emit `critical_paths`)
  - The questions router/serializer (find where open questions are listed; it is phase 3b): add `launch_item`
- Test: `backend/tests/test_launch_context.py`, `backend/tests/test_launch_critical.py`, `backend/tests/test_launch_questions.py`

**Interfaces:**
- `launch.context.block(launch, items) -> str | None`: the fixed header plus at most 60 words; None when there is nothing. The helper may live in `launch/__init__` or `launch/context.py`; say which in the report.
- `critical.changed_paths(events: list[dict], project_dir: str) -> list[str]`: relative paths from `tool_use` events of `Edit`/`Write`/`MultiEdit`/`NotebookEdit`. Read `file_path`, or `notebook_path` for NotebookEdit. Drop anything outside the project dir.
- `critical.hits(paths, globs) -> (list[str], list[str])` uses the decision matcher.
- Worker: after the final status is stored, if the task's project has critical paths, read the task's `tool_use` events from the DB or from the ctx the worker already keeps. Emit at most one `critical_paths` event, never failing the task on error (log at debug).

- [ ] **Step 1: Write the failing tests.**
  - The context block for an own project has the exact header and ≤ 60 words, with `#n title` items.
  - External trust has no block. No launch data has no block. `launch` appears in `log["included"]`.
  - `changed_paths` handles absolute, relative and outside-project paths, and NotebookEdit.
  - The worker path emits `critical_paths` with the right payload. Drive the finish helper directly with fake events and check the event row.
  - A question whose `blocks` names an open item title gets `launch_item`. A done item and a 2-character title get none.
- [ ] **Step 2:** Implement, then run the tests plus `tests/test_memory*.py` and the questions tests. Commit with `feat(launch): launch context, critical-path warning and question link`.

### Task 4: Launch tab, CreateTaskModal nudge, TaskDetail chip and question line (frontend)

**Files:**
- Create: `frontend/src/launch/LaunchTab.jsx`, `frontend/src/launch/ScopeNudge.jsx`, `frontend/src/launch/launch.js` (pure helpers)
- Modify:
  - `frontend/src/api.js`: launch endpoints.
  - The project drawer: add a **Launch** tab after Decisions. Find where the Decisions tab is added (`frontend/src/decisions/` / ProjectsView).
  - `frontend/src/components/CreateTaskModal.jsx`: the nudge on submit, plus a "Launch item" select of open items for the chosen project, sending `launch_item_id`.
  - `frontend/src/components/TaskDetail.jsx`: the `critical_paths` chip.
  - The Questions inbox component (find it under `frontend/src/questions/`): the "blocks launch item #n: title" line.
- Test: `frontend/src/launch/LaunchTab.test.jsx`, `frontend/src/launch/ScopeNudge.test.jsx`, `frontend/src/launch/launch.test.js`, plus extensions of `CreateTaskModal.test.jsx` and `TaskDetail.test.jsx`

**Interfaces:**
- **LaunchTab:**
  - Date and critical paths, editable by maintainers.
  - "Before launch" and "After launch" lists. Each row shows `#n`, title, status tag, dates and the buttons allowed for the role (Start, Done, Drop, Reopen, Edit, Move, Delete).
  - An add box per section.
  - A "linked tasks done — mark done?" hint when `all_linked_done[number]`.
  - On 409, reload and show "changed by someone else — reload".
- **ScopeNudge** shows the exact copy and three buttons:
  - **Add to launch list**: POST a `before` item titled from the prompt's first line (≤ 200), then create the task linked to it.
  - **After launch**: the same, with an `after` item.
  - **Run anyway**: create the task.

  The nudge never blocks; closing the modal does nothing.

- [ ] **Step 1: Write the failing tests.**
  - The tab renders both sections. A contributor doesn't see Delete. Add calls the api, and a 409 reload shows the message.
  - The nudge copy is exact. "Add to launch list" calls the item create first, then the task create with `launch_item_id`. "Run anyway" creates the task only.
  - When scope-check says false, no nudge appears and the task is created directly.
  - The TaskDetail chip copy is exact.
  - The question line is exact.
- [ ] **Step 2:** Implement, then run the new tests plus CreateTaskModal and TaskDetail tests and `npm run build`. Commit with `feat(launch): launch tab, scope nudge, critical-path chip and question link`.

### Task 5: Workflow templates, export/import and repo files (F70, F104) — backend

**Files:**
- Create: `backend/app/workflow_templates.py`
- Modify: `backend/app/routers/workflows.py`. Declare the new routes `/templates`, `/repo-files` and `/import` before any `/{workflow_id}` route that would capture them.
- Test: `backend/tests/test_workflow_templates.py`, `backend/tests/test_workflow_import.py`

**Interfaces:**
- `TEMPLATES: dict[str, dict]`, keyed `software`, `planned`, `documents`, `spike`, `ui`. Each value is `{name, description, stages}`.
  - Stages use 5a kinds. Agent items reference 4c preset names exactly as `app/tiers.py`/the agents presets define them; check the real preset names in the presets route.
  - Every gate stage carries `run: ["npm test"]` and no `project`.
  - Builder prompts include the exact sentence.
- `export_markdown(wf) -> str` and `parse_import(text) -> dict` (ValueError on 0 or 2+ json blocks, or text over 64 KB).
- `async list_repo_files(project) -> {"files": [...], "reason": str | None}`. Use `os.lstat` and `stat.S_ISREG`, and open each file with `O_NOFOLLOW|O_NONBLOCK`. Refuse a symlinked dir. Cap the count and the size.
- Routes:
  - `GET /workflows/templates` returns `[{key, name, description, stages, hint}]`.
  - `GET /workflows/{id}/export` returns `text/markdown`.
  - `POST /workflows/import` takes `{text}` or `{project_id, file}`. Admin only; it creates through `spec.validate_stages` and `_validate_agents`, and gives 422 with the reason.
  - `GET /workflows/repo-files?project_id=` requires visibility.

- [ ] **Step 1: Write the failing tests.**
  - Every template's stages pass `spec.validate_stages` once a project is set on its gates (fill project in the test). Each template's agent names are real preset names.
  - Builder prompts contain the exact sentence.
  - Export then import round-trips.
  - Import is refused with 2 json blocks, with none, and over 64 KB. A non-admin gets 403.
  - Repo files: an own project lists its `.md` files.
    - These are skipped: a symlinked file, a FIFO (`os.mkfifo`), a 65 KB file and a bad name.
    - A symlinked `workflows` dir gives an empty list with a reason.
    - An external-trust project gives an empty list with a reason.
  - Importing a repo file works.
- [ ] **Step 2:** Implement, then run the tests plus `tests/test_gates_spec.py` and `tests/test_api.py -k workflow`. Commit with `feat(workflows): built-in templates, export/import and repo workflow files`.

### Task 6: Workflow templates and import UI (frontend)

**Files:**
- Modify: `frontend/src/components/WorkflowsView.jsx`, `frontend/src/api.js`
- Create: `frontend/src/gates/TemplatePicker.jsx` (or `frontend/src/workflows/TemplatePicker.jsx`), `frontend/src/gates/ImportWorkflow.jsx`
- Test: the new component tests, plus an extension of `WorkflowsView.test.jsx`

**Interfaces:**
- **New from template:** a picker of the templates. Choosing one opens the existing editor pre-filled, with the exact hint visible above the gate stages.
  - Missing agents are listed: "Add these helpers from Agents → presets first: …". Compute this against `api.listAgents`.
- **Export:** each workflow row gets an Export action that downloads `<name>.md` as a Blob.
- **Import:** a modal with a paste box, or a project select plus a repo-file select. It parses through the server, then opens the editor pre-filled; the admin still has to press Save.

- [ ] **Step 1: Write the failing tests.**
  - The picker lists 5 templates, and choosing one pre-fills the editor stages.
  - The hint text is exact.
  - Missing agents are listed.
  - Import with pasted text, when the server returns 422, shows the reason.
  - Export calls the api.
- [ ] **Step 2:** Implement, then run the tests and `npm run build`. Commit with `feat(workflows): templates, export and import in the workflows view`.

### Task 7: Skill template and lint (F69), plus the README

**Files:**
- Create: `backend/app/skills_lint.py`, `backend/app/templates/skill.md` (or keep the template as a constant in `skills_lint.py`), `frontend/src/skills/SkillLint.jsx`
- Modify:
  - `backend/app/routers/skills.py`: `POST /skills/lint`, `GET /skills/template` and `GET /skills/{id}/lint`. Declare the static routes before `/{id}`.
  - `frontend/src/components/SkillsView.jsx`: the panel, plus a per-pack Lint action.
  - `frontend/src/api.js`.
  - `README.md`: the sections "Launch list", "Workflow templates" and "Skill lint", accurate against the code. List what is not covered, as in the spec's non-goals.
- Test: `backend/tests/test_skills_lint.py`, `frontend/src/skills/SkillLint.test.jsx`

**Interfaces:**
- `lint(text: str) -> list[dict]`.
  - Port forge v0.7.0 `scripts/lib/lint.js` `lintSkill` and its cases from `tests/lint.test.js`, fetched read-only with `gh api` the same way as Task 2.
  - HTML comments are blanked out with line numbers preserved.
  - The template itself must lint clean.
- `GET /skills/{id}/lint` returns `[{file, problems}]` for every `SKILL.md` under the pack's `local_path`. Walk without following symlinks. Cap at 200 files and 256 KB per file; skip files over the cap with a problem "file too large".

- [ ] **Step 1: Write the failing tests.**
  - The template lints clean.
  - Each of these gives a problem at the right line: missing name; a description that doesn't start with "Use when"; a part missing Output; a last part with no Verify/Evidence.
  - A comment hiding a bad part is ignored, and line numbers stay right.
  - A 300 KB input gives 422.
  - Pack lint walks a temp pack and skips a symlink.
  - The UI shows problems by line.
- [ ] **Step 2:** Implement, then run the tests and `npm run build`. Commit by folder: `feat(skills): skill template and lint` (backend), `feat(ui): skill lint panel` (frontend), `docs(readme): launch list, workflow templates, skill lint`.
