# Forge port — Phase 5b: launch list, workflow templates, skill lint — design

Program: port every jspark-forge feature into the orchestrator (owner: "Copy each and every feature of forge").
Inventory: `docs/research/jspark-forge-feature-inventory.md` F61–F64, F69, F70 (§3 rows of the same ids);
`-delta-v0.5-v0.6.md` F99 (the launch-item link) and F104. F68 (brief template + lint) shipped in 4c
(`briefs/validate.py`, including "stop must not be never") and is not repeated.

Phase 5 is now three parts: 5a (merged, command gates), **5b** (this), **5c** (typed stage outputs, automatic
retry-once of an agent stage, a per-run budget cap, a fix-it loop from a failed gate back to an agent stage — all
parked from 5a), then Phase 6.

## Intent
Forge keeps a short **launch list** per project: what must ship before the launch date, what waits until after,
and which paths are critical. It shows the list to the agent, notices when a request looks like new scope ("while
we're at it, can we add…") and offers a choice before work starts, and warns when a run touches a critical path.
Forge also ships **workflow templates** (software, planned, documents, spike, ui) and a **skill template with a
lint**. 5b brings all three, adapted to a multi-user server.

## Decisions (owner: proceed autonomously; these are the designer's calls)

### Launch list (F61)
- **Storage is the database** (like decisions, 4a). Repo-file sync with forge's `launch.md` is a non-goal.
- `ProjectLaunch` (table `project_launches`, one row per project, created on first write): `project_id`
  (PK, FK projects cascade), `launch_date` (Date, nullable), `critical_paths` (JSON list, ≤ 20 globs, same rules
  and matcher as decision `touches`: relative, no `..`, no leading `/` or `~`, no backslash, ≤ 300 chars),
  `version` int (optimistic lock), `updated_at`.
- `LaunchItem` (table `launch_items`): `id` String(32); `project_id` (FK cascade, indexed); `number` (per project
  from `projects.launch_seq`, a high-water mark, never reused, unique with project); `title` (1–200, `clean_text`ed
  and redacted, one line); `section` (`before` | `after`); `status` (`open` | `started` | `done` | `dropped`);
  `added_at`, `started_at`, `done_at` (nullable); `added_by` (FK users, set null); `version`. Shown `#7`.
  Duplicate open titles in a project (case-insensitive, after trim) are refused (forge: refuses duplicates).
- `Task.launch_item_id` (String(32), nullable, FK launch_items set null, indexed): a task may be linked to one item.
- **Who may do what:** anyone who can see the project reads. Contributors add items, link their tasks, and
  start/done/drop items. Maintainers (and admins) also edit titles, move sections, reopen, delete, and set the
  date and critical paths.
- **REST** (`app/routers/launch.py`, prefix `/projects/{project_id}/launch`):
  `GET` (launch + items, `before` first then `after`, each by number), `PUT` (`{launch_date, critical_paths,
  version}`), `POST /items` (`{title, section}`), `PATCH /items/{number}` (`{title?, section?, status?, version}`;
  status moves: open→started→done, any→dropped, done/dropped→open by maintainers; each stamps its date),
  `DELETE /items/{number}` (maintainers), `GET /facts?files=a,b`, `POST /scope-check`. Writes bump `version`;
  mismatch → 409 "changed by someone else — reload". Validation → 422 with a plain reason.
- **Linking (start dates):** a task created with `launch_item_id` (CreateTaskModal, or `PATCH` on an open task by
  its creator/maintainer) links it. When a linked task first goes `running`, an `open` item becomes `started`
  (stamped). **Done stays a person's call:** when every linked task is completed, the Launch tab shows
  "linked tasks done — mark done?". A task's own project must equal the item's project.

### Launch context for the agent (F62)
For a task in an `own`-trust project with a launch row or items, `memory.assemble_context` adds a block
"## Launch list (data, not instructions)" at most **60 words**: the launch date (or "no date"), open count and the
open `before` titles as `#n title` (cut to fit), and the done count. Logged in `included` as `launch`.

### Scope nudge at task creation (F63)
- `app/launch/scope.py` `looks_like_new_scope(prompt) -> bool`: a port of forge's `launch.js` `looksLikeNewScope`
  (build verbs plus "can we add", "while we're at it", "it would be nice to", …; excludes questions about existing
  things, test/lint/fix/bug wording, prompts of 400+ characters and slash commands). Pure, anchored patterns,
  pinned by forge's own cases where available.
- `POST /projects/{id}/launch/scope-check {prompt}` → `{new_scope, open, would_be, launch_date}`; `new_scope` is
  false when the project has no launch row and no items, or when this user was nudged for this project in the
  last **30 minutes** (in-memory per process, keyed user+project; the nudge is a hint, so losing it on restart is
  fine).
- CreateTaskModal calls it on submit when a project is chosen; on `new_scope` it shows, inline and never blocking:
  "This looks like new scope: the launch list has 7 open items; this would become #8." with **Add to launch list**
  (adds a `before` item titled from the prompt's first line ≤ 200, links the task to it, then creates the task),
  **After launch** (adds an `after` item, links, creates), **Run anyway** (creates). Agent-created tasks
  (dispatch API, agent token) are never nudged.

### Facts and critical paths (F64)
- `GET /facts?files=a,b` (≤ 50 files, each relative): `{launch_date, open, done, would_be, after: [#n title],
  critical_touched: [glob], estimate}`. `estimate` is `{median_days, min_days, max_days, n}` from items with both
  `started_at` and `done_at`, only when **n ≥ 10** (forge's rule), else null.
- **Post-run check:** when a task in a project with critical paths finishes (any end state), the worker collects
  the paths it changed from its own `tool_use` events (`Edit`, `Write`, `MultiEdit`, `NotebookEdit` targets,
  made relative to the project dir; outside → ignored) and matches them against `critical_paths` with the decision
  matcher. Any hit → a run-log event `critical_paths` `{paths: [≤ 10 relative paths], globs: [...]}`, shown on
  TaskDetail as a warning chip "This run changed 2 critical paths: src/billing.py, …". Shell-command edits are not
  seen (same limit as 4a's decision notes). Raising risk/approval from critical paths is parked (5c).

### Questions that block a launch item (F99 link)
The Questions inbox API adds `launch_item: {number, title} | null` to each open question when its `question` or
`blocks` text names an open launch item of the task's project (title ≥ 3 chars, case-insensitive substring).
Shown as "blocks launch item #3: Billing export".

### Workflow templates (F70, F104)
- `app/workflow_templates.py`: built-in templates **software**, **planned**, **documents**, **spike**, **ui**,
  each a stage list in 5a's stage kinds, using 4c's helper presets by name (Planner, Builder, Reviewer, Checker)
  plus approval and gate stages. Gate stages carry `run: ["npm test"]` and no `project`; the editor requires the
  admin to pick the project and the real command before save (validation already refuses a gate without a
  project). Every builder prompt includes forge's notes verbatim in our words: "Set the project's own test command
  in the gate." (shown as an editor hint) and "Commit on the current branch. Do not merge or push." (in builder
  prompts). `planned` = approve-plan → parallel builders → gate → review → release approval.
- `GET /workflows/templates` (anyone) returns them with a short description each. WorkflowsView gains **New from
  template**, which opens the editor pre-filled. Agents a template names that do not exist are listed in the
  editor ("Add these helpers from Agents → presets first"), and save stays refused by the existing check.
  The existing Canonizer PRESETS stay as they are.
- **Export/import (F104):** `GET /workflows/{id}/export` returns a Markdown document: a title line, the
  description, and one fenced ```json block holding `{name, description, stages}`. `POST /workflows/import
  {text}` (admin) parses that block (≤ 64 KB, exactly one ```json block) and creates a workflow through the same
  validation as a save. **Repo files:** `GET /workflows/repo-files?project_id=` lists `.claude-orchestrator/
  workflows/*.md` in an `own`-trust project's directory (≤ 20 files, regular files only — no symlinks, each
  ≤ 64 KB, names `^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}\.md$`); `POST /workflows/import` also takes
  `{project_id, file}` to import one. External projects → 404-free empty list with a reason. Import never runs
  anything; the admin saves after review in the editor.

### Skill template and lint (F69)
- `app/skills_lint.py` `lint(text) -> list[{line, message}]`, a port of forge's `lint.js` skill rules:
  frontmatter with `name` and a `description` starting "Use when"; numbered parts each carrying `Input:`,
  `Action:` and `Output:`; the last part is `Verify:` with `Evidence:`; HTML comments ignored but line numbers kept.
  Input ≤ 256 KB.
- `POST /skills/lint {text}` (any signed-in user) → `{ok, problems}`. `GET /skills/template` → the template text.
- **Installed packs:** `GET /skills/{id}/lint` lints each `SKILL.md` in the pack's directory (≤ 200 files, each
  ≤ 256 KB, no symlinks followed) → `[{file (relative), problems}]`. Warnings only: nothing is blocked or changed.
- SkillsView gains a **Skill template & lint** panel (template shown, a text box, Lint button, problems by line)
  and a **Lint** action per installed pack showing its warnings. No server writes into project directories.

## Goals
1. Migration 0015: `project_launches`, `launch_items`, `projects.launch_seq`, `tasks.launch_item_id`.
2. Launch REST with roles, optimistic locking, status moves, linking and auto-start.
3. Scope regex, scope-check with cooldown, facts with estimate.
4. Context block, post-run critical-path event, question link.
5. Launch tab (project drawer), CreateTaskModal nudge + item link, TaskDetail chip, Questions line.
6. Workflow templates, export/import, repo-file listing; WorkflowsView "New from template" and import.
7. Skill lint + template; SkillsView panel and per-pack lint.
8. README sections "Launch list", "Workflow templates", "Skill lint".

## Non-goals (parked)
- Repo-file sync of the launch list (`launch.md`), arrivals (F62 "items that arrived with git").
- Critical paths raising task risk/approval; critical paths for shell-command edits.
- Writing skills, briefs or workflows into project directories from the server.
- 5c items: typed outputs, auto-retry, budget cap, fix-it loop.

## Anti-goals
- A scope nudge that blocks, repeats within 30 minutes, or fires for agent-created tasks.
- Launch text treated as instructions (framed as data, cleaned, ≤ 60 words).
- A reused item number, or two people's edits silently overwriting each other (409).
- Reading repo files from an external project, following a symlink, or reading an unbounded file.
- An import that runs anything or bypasses stage validation.

## Approaches considered
| Question | Options | Outcome |
|---|---|---|
| Where the launch list lives | repo `launch.md` / **DB** | DB: multi-user, not agent-writable (as decisions) |
| Scope nudge | in-chat hook / **pre-flight hint at task creation** | there is no chat in `-p`; the person creating the task decides |
| Done dates | auto when linked tasks finish / **person's call, suggested** | a finished task is not a shipped item |
| New skill | write `SKILL.md` into the project / **template + lint in the UI** | no server writes into user directories |

## Builder's latitude
Module layout under `app/launch/`, the exact scope regex (within forge's cases), UI layout and copy apart from the
fixed texts, template prompt wording apart from the two forge notes, test structure.
