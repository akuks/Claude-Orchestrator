# Forge port — Phase 4c: helper tiers, presets, tier lint, enforced briefs — design

Program: port every jspark-forge feature into the orchestrator (owner: "Copy each and every feature of forge").
Inventory: `docs/research/jspark-forge-feature-inventory.md` F67, F68 (forge table and orchestrator mapping §3), and
`-delta-v0.5-v0.6.md` F98 (helper habits), F102 (tier lint). Builds on Phase 2 (co-guard policy, `readonly_paths`,
the rules engine's readonly-source checks), 3a (ground rules, `USAGE_CARE_HELPER`), 4a (`app/decisions/validate.py`
path rules, the glob matcher copy in `co_hook.py`).

**Phase split (designer's ruling): not split.** Eight tasks: tiers/presets/lint (Tasks 1, 2, 6) and briefs (Tasks
3, 4, 5, 7) touch disjoint files apart from one migration, so a reviewer can take them task by task. The
enforcement code (Tasks 4, 5) is the part to review hardest.

## Intent
Forge ships four helpers (planner, reviewer, builder, checker) bound to model tiers, lints that each helper names a
tier model and says when and why to use it, and asks every helper to work from a brief (Goal, CAN change, CANNOT
change, Done, Stop …). Forge only *asks* a helper to stay inside the brief. We carry the brief on the task, put it in
the run's system prompt, and **enforce** CAN / CANNOT change with co-guard, so a run cannot edit outside its brief.

## Decisions (owner: proceed autonomously; these are the designer's calls)

### Tiers (F67)
- **Org setting `model_tiers`** `{strongest, standard, light}` → CLI model names, defaults
  `{"strongest": "opus", "standard": "sonnet", "light": "haiku"}`. Stored in a new JSON column
  `guard_org_settings.model_tiers` (migration `0013_briefs`), read through `policy_store.org_settings` (always all
  three keys; stored value of the wrong type ignored), changed with the existing `PUT /guard/org-settings`
  (admins only; the object is replaced whole, a key left out takes its default; unknown key or bad value → 400).
  A value is one of `VALID_MODELS` (`opus`, `sonnet`, `haiku`) or a full model id matching
  `^claude-[a-z0-9][a-z0-9.-]{1,60}(\[1m\])?$` (it reaches `--model` as one argv item, never a shell).
- **`Agent.tier`** String(16) null (`strongest` | `standard` | `light`); `AgentCreate/Update/Out` gain `tier`
  (bad value → 400 "Invalid tier. Use one of strongest, standard, light"). Agent `model` validation widens from
  `VALID_MODELS` to `VALID_MODELS ∪ the org's tier models`.
- **Resolution** (`app/tiers.py agent_model(s, agent) -> str | None`): the agent's own `model` when set, else its
  tier's model from the org map, else None (the project default, then Claude Code's, as today). Used at every place
  a task is built from an agent: `routers/agents.py` run / dispatch / bulk, `scheduler.build_from_schedule`,
  `workflow_runner` (all five use `agent.model or None` today). The resolved name is stored on the task's `model`,
  so the run log's `started` line shows it.

### Presets (F67, F98)
- Presets gain `tier` and lose `model` where a tier stands for it (haiku → `light`, sonnet → `standard`): Morning
  Brief, Weekly Review, Code Reviewer, QA / Tester → light; Tech Lead, Frontend/Backend/Fullstack Developer, DevOps
  Engineer → standard. Security Auditor stays without tier or model. With the default map nothing changes.
  Every preset description is reworded to pass the lint ("Use for …, because …"); the wording is the builder's.
- Code Reviewer and Security Auditor are already `read_only` (`read_only` adds Edit/Write/MultiEdit/NotebookEdit and
  git writes to `--disallowedTools`, `worker.py:611`); QA / Tester writes tests, so it stays writable.
- **Four new presets, category "Helpers"** (exact bodies below; `max_turns`/budget are the builder's, near the dev
  presets):

| Name | Tier | read_only | Description (exact) |
|---|---|---|---|
| Planner | strongest | no | Designs a change and writes the plan and briefs. Use for turning a goal into a step-by-step plan with briefs, because a plan checked against recorded decisions saves rework. |
| Reviewer | strongest | yes | Reviews a finished change and ranks findings. Use for a review before merge, because ranked findings with file:line and evidence are quick to act on. |
| Builder | standard | no | Implements one brief, test first. Use for a scoped change with clear CAN change files, because a tight brief keeps the diff small and the review short. |
| Checker | light | yes | Runs checks and reports facts only. Use for running test suites, builds and lints, because a cheap model can read long output and report the totals. |

  System prompts (exact; `{HABITS}` is `ground_rules.USAGE_CARE_HELPER`, imported, so Builder and Checker carry
  forge's helper habits permanently, F98):
  - **Planner:** "You are the planner. You design a change and write the plan; you do not implement it. Work from
    your brief (Goal, CAN change, CANNOT change, Done, Stop). Read the code you plan against and cite path:line.
    Never plan against a recorded project decision: when the goal conflicts with one, stop and file a question with
    your recommendation instead of planning around it. Split the work into small steps, each with its own brief:
    Goal, CAN change, CANNOT change, Done, Stop, and the tests that prove it. End with Outputs (the plan and the
    briefs, by path) and the evidence you relied on."
  - **Reviewer:** "You are the reviewer. Read-only: never edit, commit, push or merge; describe a fix, do not make
    it. Work from your brief. Review the change for correctness, edge cases, error handling, tests and the
    project's recorded decisions. Rank every finding H (must fix), M (should fix) or L (nice to have), and give for
    each the file:line, what is wrong, and the evidence (the code you read or the command you ran and its output).
    Say \"no findings\" when there are none; never invent one. End with Outputs (the findings by rank) and the
    evidence."
  - **Builder:** "You are the builder. Work from your brief and change only files it lets you change (CAN change);
    never touch CANNOT change (co-guard blocks it). Test first: write the failing test, see it fail, make it pass.
    Keep the diff small and in the project's style. Stop when Done is met, or when Stop says so, and report;
    do not widen the scope. End with Outputs (what changed, by path) and the evidence (the tests you ran and their
    results).\n\n{HABITS}"
  - **Checker:** "You are the checker. Read-only: run the checks your brief names (tests, builds, lints) and report
    facts only: commands run, totals, failing tests with their first error lines. No opinions, no fixes, no
    guesses; say \"not run\" for anything you could not run. End with Outputs (each check: pass or fail, with
    totals) and the evidence.\n\n{HABITS}"
  - Default prompts (exact): Planner "Plan the change described in the brief.", Reviewer "Review the change on this
    branch against the brief.", Builder "Implement the brief.", Checker "Run the checks in the brief and report.".

### Tier lint (F102)
- On agent create and update (and on `GET /agents` for each row) the response's `lint: [str]` holds non-blocking
  warnings; a save is never refused for them. Exact texts:
  - `model "<m>" is not one of the org's tier models (<opus, sonnet, haiku>)` — the agent's own model is set and not
    a value of the org map (list in strongest, standard, light order, deduplicated).
  - `the description should say when to use this agent ("Use for …")` — no case-insensitive `use for`.
  - `the description should say why ("… because …")` — no case-insensitive `because`.
- An agent with no model and no tier gets no model warning (it uses the project's default).

### Briefs (F68)
- **`Task.brief`** JSON null and **`Task.brief_bounds`** JSON null (migration `0013_briefs`). `brief` is the
  step's own brief: `{goal, can_change, cannot_change, done, stop, setup?, log?, retries?, tie_break?, outputs?}`.
  `brief_bounds` is server-set: the CAN/CANNOT scopes of enclosing briefs (below).
- **Validation** (`app/briefs/validate.py clean_brief(raw) -> dict`, raises `ValueError` with a plain reason →
  422 `detail` string):
  - Not an object → "the brief must be an object"; unknown key → "unknown brief field: <k>".
  - Required: `goal`, `done`, `stop` (text, non-empty after cleaning), `can_change`, `cannot_change` (lists; may be
    empty). Missing → "the brief needs <field>".
  - `stop` is not "never" (case-insensitive, after trimming spaces and a final `.`/`!`) → "stop must say when to stop,
    not \"never\"".
  - `retries` absent/null or an int 0..10 (a bool, float or text is refused) → "retries must be a whole number from
    0 to 10".
  - Globs: each entry through the 4a path rules (`validate._rel_path`: relative, no leading `/` or `~`, no
    backslash, no `..`, normalised), cleaned and redacted like touches, ≤ 300 chars, at most 30 `*`/`?` (the
    matcher's `GLOB_WILDCARD_MAX`, so a pattern can never silently fail to match), ≤ 20 entries per list,
    deduplicated. 4a's `_touches` is refactored into `path_list(value, what, max_items)` used by both.
  - Texts cleaned and redacted (`clean_text` + `redact`): `goal` ≤ 2000, `done`, `stop`, `setup` ≤ 1000 each,
    `log`, `tie_break` ≤ 500; `outputs` ≤ 10 names, each one line ≤ 60 chars.
  - `null` / absent brief → no brief. An empty object is "the brief needs goal".
- **Where a brief comes from:** `POST /tasks` (`TaskCreate.brief`), `POST /agents/{id}/run` (`AgentRun.brief`),
  `POST /agents/dispatch` (`DispatchItem.brief`, per item). Bulk runs, schedules, templates and workflows take
  none (non-goal).
- **Inheritance:** every thread step built from a parent carries the parent's `brief` and `brief_bounds`:
  `_step_from` (follow-up, Continue, answer follow-up), co-guard's deny-and-resume follow-up (`grants._spawn`) and
  Duplicate. A task created with a `parent_task_id` (agent run, dispatch) whose parent has a brief: no brief of its
  own → it takes the parent's brief and bounds; its own brief → `brief_bounds = parent.brief_bounds +
  [{"task_id": parent.id, "can_change": …, "cannot_change": …}]` (identical scopes once). More than 4 bounds → 422
  "briefs nest at most 5 deep". So a brief binds the rest of its thread and everything dispatched under it; a
  dispatched child can narrow it, never widen it.
- **Prompt:** the brief goes into the run's `--append-system-prompt` (after the ground rules and decision index), so
  it survives compaction and is re-sent to every step without growing the conversation; the run log's
  `ground_rules` line lists `brief`. Rendered by `app/briefs/render.py brief_text(brief, bounds)` (exact frame):

  ```
  co-orchestrator brief for this task (set by whoever started it). co-guard blocks Edit, Write, MultiEdit and NotebookEdit outside CAN change and inside CANNOT change, and shell commands that write into a CANNOT change folder.
  Goal: <goal>
  CAN change: <globs, comma-separated | anything not under CANNOT change>
  CANNOT change: <globs | nothing listed>
  Done when: <done>
  Stop and report when: <stop>
  Setup: <setup>            (each optional line only when set)
  Log: <log>
  Retries: <n>
  Tie-break: <tie_break>
  Outputs: <names>
  Also bound by an enclosing brief (task <id>): CAN change <…>; CANNOT change <…>    (one line per bound)
  End your report with Outputs (each name with its value or path) and the evidence (commands run and their results).
  ```
- **Coordinator registry:** the dispatch example in `_coordinator_context` gains an optional `"brief": {…}` per item
  and one line: "Give a builder a brief (goal, can_change, cannot_change, done, stop); co-guard enforces its
  CAN/CANNOT change."

### Enforcement (F68 upgrade)
- **Policy key** `brief` (written by the worker; only when the task has a brief): `{"layers": [{"task": id, "can":
  [globs], "cannot": [globs]}, …]}` — the task's own scope first, then its bounds. A change must pass **every**
  layer.
- **File tools (co_hook.py PreToolUse):** before `rules.evaluate`, for `Edit`, `Write`, `MultiEdit`, `NotebookEdit`:
  the target (`file_path` / `notebook_path`, relative paths against the payload's `cwd`) is made relative to the
  policy's `project_dir` twice — its real path (symlinks resolved) and its normalised as-given path. A target outside
  the project (temp folders, `CO_TASK_DIR`, uploads, other folders) is **never** checked by the brief; everything
  else of co-guard still applies to it. Inside the project, per layer: either form matching a CANNOT glob → deny;
  CAN non-empty and the real form matching no CAN glob → deny (CANNOT wins over CAN). Matching uses the existing
  4a matcher copy in `co_hook.py` (`matches`, platform case rule). Rule id `brief-scope`; always on (also with the
  project's guard off), hard (never filed for approval, no grant covers it). Deny reasons (exact):
  - `this changes <rel>, which the task's brief says CANNOT change (<glob>)`
  - `this changes <rel>, which is outside what the task's brief says CAN change (<globs, comma-separated, cut to 200>)`
  - for a bound layer, "the task's brief" reads "an enclosing brief (task <id>)".
  The agent sees `CO-GUARD: blocked — <reason> (brief-scope). Stay inside the brief; if the change is needed, stop
  and report why.`; the spool/report record is the usual PreToolUse deny (`rule_ids: ["brief-scope"]`), so the run
  log shows it like every guard denial (TaskDetail names the rule "Outside the task brief").
- **Fail closed:** a `brief` key that is not `{"layers": [ {task: str, can: [str], cannot: [str]} … ]}` → every
  file-tool call is denied with "the task's brief could not be read, so file changes are blocked"; an exception in
  the check takes the hook's existing error path (deny).
- **Shell commands — what Phase 2 already gives us, verified:** the rules engine has no list of a command's write
  targets to hand out (4a's ruling stands: `evaluate` returns hits only), but it checks every write target it
  resolves — redirects, `tee`, `cp/mv/rsync/ln` destinations, `sed -i`, `touch`, deletes, `git` work-tree changes,
  formatters, builds run in the folder — against **readonly roots** (`readonly_paths`, rule `readonly-source`,
  hard, always on; `rules.py` docstring l.153-218, roots built l.974-983, `readonly_root` l.1068, `readonly_write`
  l.2930). Roots are path prefixes, not globs. So:
  - Each CANNOT glob that is a **literal path** (`migrations`, `src/config.py`) or a **literal folder + `/**`**
    (`migrations/**`) is also written as a readonly root `{"path": <real abs path under project_dir>, "reason":
    "the task's brief says CANNOT change <glob>", "source": "brief"}` (`_readonly_entry_ok` accepts
    `source: "brief"`). Shell writes into it are denied by `readonly-source` with "this changes <path>, which is
    read-only (the task's brief says CANNOT change <glob>)".
  - Other CANNOT globs (`**/*.lock`, `src/*.gen.ts`) are enforced for file tools only; a `guard` run-log event
    `{"event": "brief_scope", "shell_roots": n, "file_tools_only": [globs]}` says so at start.
  - **Outside-CAN is not enforced for shell commands.** Builds, tests, package managers and git write caches,
    `node_modules`, `.git` and build output everywhere; treating each as "outside CAN" would deny the builder's own
    test runs (the engine also treats a build *run in* a root as writing it). The prompt and README say so.
  - Side effect, accepted: with a CANNOT root present, Phase 2's Ruling P17 holds a change after a `cd` the engine
    cannot follow for approval ("the folder this runs in is unknown, and read-only folders exist").
- **What agents must still write** (checked): `handoff.md` lives in `CO_TASK_DIR` (`resume/handoff.py:59,593`),
  notes / questions / proposals go through the API, context snapshots, MCP configs and uploads live under the
  workspaces dir outside the cwd, temp files under `/tmp`/`$TMPDIR`: all outside the project, so never in a brief's
  reach. The brief globs are always project-relative (validation forbids `/`, `~`, `..`). For an ad-hoc (sandbox)
  task the "project" is its workspace, so its artifacts must be under CAN (or CAN left empty).

### UI
- **Org guard settings:** a "Model tiers" card (admins edit, others read): three rows (Strongest, Standard, Light),
  each a select of opus / sonnet / haiku that also accepts a typed `claude-…` id; Save → `PUT` with `model_tiers`.
- **Agent editor:** a Tier select (Strongest / Standard / Light, clearable) next to Model, help "A model set here
  overrides the tier."; after a save, the response's `lint` shows as a warning list under the form (the modal stays
  open when there are warnings, with a "Saved with warnings" note; closed when none). The agent list shows a tier
  tag and a warning icon with the lint texts. Presets seed `tier`.
- **CreateTaskModal and the agent Run modal:** a collapsible "Brief" section (`BriefFields`, shared): Goal, CAN change
  and CANNOT change (tag inputs, `Select mode="tags"`), Done, Stop (required once any brief field is filled), and
  under "More" Setup, Log, Retries, Tie-break, Outputs (tags). Empty section → no `brief` sent. A 422 shows its
  plain reason.
- **TaskDetail:** a "Brief" panel (fields as labelled lines, CAN/CANNOT as tags, bounds as "Also bound by an
  enclosing brief"); the guard run-log line for `brief-scope` uses the rule name "Outside the task brief"; the
  `brief_scope` start event renders as "Brief: N CANNOT folders also guarded for shell commands; file tools only:
  <globs>".

## Goals
1. Tier map, `Agent.tier`, resolution at all five agent call sites, migration 0013.
2. Presets (four helpers + tiers + lint-clean descriptions) and the non-blocking lint.
3. `Task.brief` / `brief_bounds`: validation, create / agent run / dispatch, inheritance and bounds.
4. Brief in the system prompt; policy `brief` key; CANNOT folders as readonly roots for shell commands.
5. co-guard PreToolUse enforcement for file tools, fail closed.
6. UI for all of the above; README section "Helper tiers and briefs".

## Non-goals
- Briefs on bulk runs, schedules, templates and workflow stages (Phase 5 brings typed workflow inputs/outputs).
- Outside-CAN for shell commands; CANNOT globs with wildcards for shell commands (stated limits).
- Showing the tier when a Claude Code helper (SubagentStart) starts (F102's run-view line): our presets run as tasks,
  whose `started` line already shows the resolved model.
- Brief files in the repo (`/new-brief` copying a template into `.jspark-forge/briefs/`): the brief lives on the task.
- Editing a brief after the task is created.
- Fixing that follow-ups do not carry `read_only` / `system_prompt` (seen while reading `_step_from`; separate fix).

## Anti-goals
- A brief that a run can step out of with a file tool, through `..`, a symlink, an absolute path, a case variant
  on macOS, a helper (subagents share the policy) or a dispatched child.
- The brief blocking files the orchestrator needs written (all outside the project).
- A malformed brief or policy failing open.
- Lint that blocks a save; tier resolution that changes today's behaviour with the default map.
- Claiming shell enforcement we do not have.

## Approaches considered
| Question | Options | Outcome |
|---|---|---|
| Where the brief check lives | rules engine (new rule over every resolved path) / **hook, file tools + readonly roots for CANNOT folders** | hook: the engine's path checks are prefix roots and also fire for builds *run in* a folder, so outside-CAN there would deny test runs; CANNOT folders reuse readonly-source unchanged |
| Brief in the prompt | user prompt block via `prompting.compose` / **system prompt** | system prompt: survives compaction, every step gets it, `compose` rewrites the stored prompt |
| Inheritance for dispatched children | none / copy only / **copy or narrow with bounds** | bounds: a child can never widen its parent's scope |
| Tier → model | at run time in the worker / **when the task is built** | built: the task row shows the model it will use; the worker is unchanged |
| Tier storage | ground-rules object / **own column on guard_org_settings** | own column: ground rules are switches + text with their own validation |

## Builder's latitude
Module layout inside `app/briefs/`, preset turn/budget numbers and existing-preset wording (must pass the lint),
event wording apart from fixed texts, UI layout, test structure.
