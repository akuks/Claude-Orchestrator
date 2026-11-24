# Forge Phase 5c — Typed Outputs, Auto-Retry, Budget Cap, Fix-It Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Workflows get four additions:
- agent stages can declare typed outputs, which are checked and passed on by name;
- agent stages can auto-retry once;
- runs get a budget cap;
- failed gates can hand off to a fixer agent.

Critical paths can raise task risk, and tasks can be linked to launch items after creation.

**Architecture:** All new runner behaviour goes into `backend/app/workflow_runner.py`, following its per-run lock pattern from 5a. The pure helpers live in `backend/app/gates/outputs.py` and `backend/app/gates/fixloop.py`. Validation goes in `backend/app/gates/spec.py`. Migration 0016 carries the new columns. The frontend changes are in WorkflowsView, RunView, gates cards, LaunchTab and TaskDetail.

**Tech Stack:** FastAPI, async SQLAlchemy, Alembic, pytest (SQLite local; CI also Postgres with newer Starlette/pytest), React 18 + antd 5 + vitest.

**Spec:** `docs/superpowers/specs/2026-11-24-forge-stages-phase5c-design.md`

## Global Constraints
- **Test commands.**
  - Backend: from `backend/`, run `/Users/ashutosh/Projects/Work/Claude-Orchestrator/backend/.venv/bin/python -m pytest -o addopts="" -q <paths>`.
  - Frontend: from `frontend/`, run `npx vitest run <files>`, then `npm run build`.
  - The machine is heavily loaded.
- **Commits.** Commit folder by folder, with no `Co-Authored-By` trailer.
- **Never:**
  - run the real `claude`;
  - touch the real DB;
  - use ports 8200/5200;
  - write `monkeypatch.setattr(app.state, …)` in tests. Use `monkeypatch.setitem(app.state._state, …)` instead.
- **Migration.** Revision id `"0016"`, down_revision `"0015"`. Nullable JSON columns use `JSON(none_as_null=True)`. Booleans get a server default of false, and old rows must read back unchanged.
- **Output names.** At most 10, unique, each matching `^[a-z][a-z0-9_]{0,39}$`.
- **Output value checks.**
  - Each value is a string of at most 2000 characters after `clean_text`.
  - `*_path` must be relative, contain no `..`, and exist inside the project directory after realpath.
  - `*_commit` must be lowercase hex, 7–40 characters, and resolve through gitsafe `rev-parse --verify --quiet <hex>^{commit}`, the one new exact entry.
- **Output parsing.** Take the last ```json fenced block of `result_text`, scanning at most the final 64 KB with `rfind`.
- **Output prompt suffix (exact).** "When you finish, end your final message with a fenced ```json block holding exactly these keys, each a short string: {names}. A key ending in _path is a path relative to the project folder that exists; a key ending in _commit is a commit id in this repository." Here `{names}` is the comma-joined names.
- **Output failure.** The task's error is "outputs invalid: {reason}".
- **Placeholders.**
  - `{{stageN.name}}` is 1-based and reads the current attempt. When several items produce a value it is joined with a blank line.
  - `{{prev.name}}` reads the previous stage. An unknown name gives "".
  - Substitution is single-pass and never re-expands.
- **auto_retry.** A bool, default false. It fires at most once per stage per run and never after a `cancelled` task.
- **Budget.**
  - `budget_usd` must be between 0.01 and 10000. The raise must be greater than the amount spent.
  - The per-task cap is `min(agent cap, remaining)`, and the run pauses when the remainder is below 0.01.
  - Status `budget`.
- **Fix loop.**
  - The `fix` value is `{"agent": "<name|id>", "rounds": 1..3}`, and the agent must belong to the gate's project.
  - The fix prompt head (exact) is "A check failed. Fix the cause, commit on the current branch, do not merge or push. Then stop."
  - The fix prompt then carries, as data: up to 20 failing names and the last 50 log lines (redacted, then `clean_text`).
  - Stage status while fixing: `fixing`.
- **Fixed log words (exact).**
  - "Step {name} retried automatically"
  - "Step {name} held: budget reached (${spent} of ${budget})", with 2-decimal amounts
  - "Budget raised to ${new} by {user}"
  - "Step {name} (gate) failed, fixing (round {k} of {n})"
  - "Step {name} (gate) passed after {k} fix round(s)"
- **Critical-path risk.**
  - `project_launches.raise_risk`, default false.
  - The prefix is the glob up to its first wildcard, with any trailing `/` stripped, and must be at least 3 characters. Matching is case-insensitive.
  - A hit returns "critical", and the event carries `risk_reason: "critical path <prefix>"`.
- **Task link PATCH.**
  - `PATCH /tasks/{id}/launch-item {launch_item_id | null}`, callable by the creator or a maintainer/admin.
  - The item must be in the same project and open or started. Otherwise return 422.

## Review Focus
1. **Old workflows and runs.** Stages without `outputs`, `auto_retry` or `fix`, and runs without `budget_usd`, must behave exactly as in 5a and 5b (Tasks 1–4).
2. **Output values with placeholder text.** An output value containing `{{stage1}}` or `{{input}}` must appear literally in later prompts (Task 2).
3. **A budget raise racing the tick, or cancel racing a fix task's start.** Use the 5a per-run lock, so the cancel wins and no fix task is created on a failed run (Tasks 3–4).
4. **The fix prompt for a gate whose log holds secrets.** Project env values are redacted, and the fixer agent's project equals the gate project (Task 4).
5. **A `result_text` of 5 MB with no json block, or with an unterminated fence.** It parses fast and fails cleanly with the reason (Task 2).

---

### Task 1: Migration 0016 and validation of the new fields

**Files:**
- Create: `backend/migrations/versions/0016_stages.py`.
- Modify:
  - `backend/app/models.py`: `Task.outputs`, `Workflow.budget_usd`, `WorkflowRun.budget_usd`, `ProjectLaunch.raise_risk`.
  - `backend/app/gates/spec.py`: accept and validate the item `outputs`, the agent-stage `auto_retry`, and the gate `fix`, which needs an async agent lookup and the project check.
  - `backend/app/schemas.py`: `WorkflowCreate`, `WorkflowUpdate` and `WorkflowOut` gain `budget_usd`; `WorkflowRunOut` gains `budget_usd`; `TaskOut` gains `outputs`; the launch schemas gain `raise_risk`.
  - `backend/app/routers/workflows.py`: snapshot `budget_usd` when a run starts.
  - `backend/app/routers/launch.py`: maintainers can set `raise_risk` on PUT.
- Test:
  - `backend/tests/test_stages_spec.py`;
  - `backend/tests/test_migrations.py`, with a HEAD bump and an upgrade/downgrade test.

**Interfaces:**
- `spec.validate_stages` keeps its signature and returns normalized stages.
  - An agent stage gets `auto_retry` only when true. An item keeps `outputs` only when non-empty. A gate keeps `fix` only when given.
  - Old stages round-trip byte-identical.

- [ ] **Step 1: write the failing tests.**
  - Output names that must be rejected: `Bad`, `a-b`, 41 chars, a duplicate, 11 names.
  - `auto_retry` must be a bool.
  - `fix` rejects `rounds` of 0 or 4, an unknown agent, an agent with no project, and an agent of another project.
  - `budget_usd` rejects 0, 10001 and a non-number.
  - `raise_risk` can be set by a maintainer; a contributor gets 403.
  - A run snapshots `budget_usd`.
  - Old stages round-trip unchanged.
  - The migration upgrades and downgrades.
- [ ] **Step 2: implement, run the tests, and commit** with `feat(stages): migration 0016 and validation for outputs, auto-retry, fix and budget`.

### Task 2: Typed outputs

**Files:**
- Create: `backend/app/gates/outputs.py`.
- Modify:
  - `backend/app/gitsafe.py`: the one new exact entry, plus a docstring line.
  - `backend/app/workflow_runner.py`: add the prompt suffix in `_start_stage`; validate outputs when a stage's tasks complete; extend `substitute` and `_stage_outputs`.
  - `backend/app/routers/workflows.py`: run detail includes `outputs` per visible task. This is already true through `TaskOut`; confirm it.
- Test:
  - `backend/tests/test_stages_outputs.py`;
  - `backend/tests/test_gitsafe.py`, with the new entry accepted and its near-misses rejected.

**Interfaces:**
- `outputs.suffix(names: list[str]) -> str` returns the exact suffix.
- `outputs.parse(result_text: str | None, names: list[str]) -> dict` raises `ValueError(reason)`.
- `async outputs.verify(values: dict, project_dir: str) -> None` raises `ValueError(reason)` for `_path` and `_commit` failures, and runs blocking I/O in `to_thread`.
- `substitute(prompt, run_input, stage_outputs, named_outputs: dict[int, dict[str, str]])`, where `named_outputs` holds the current-attempt values per stage index. Keep backward compatibility: `named_outputs` defaults to `{}`.
- Runner: when every task of an agent stage has completed, verify the declared outputs. On failure, set that task to failed with error "outputs invalid: …" and let the normal failure path run, including auto-retry in Task 3.

- [ ] **Step 1: write the failing tests.**
  - Parsing:
    - The last block wins.
    - An extra key, a missing key and a non-string value are each refused with a reason.
    - A 2001-char value is refused.
    - A 5 MB text with no block takes under 1 s and is refused. An unterminated fence is refused.
  - Verification, in a temp git repo:
    - `_path` passes for an existing file. It fails for `../x`, an absolute path, a missing file, and a symlink pointing outside.
    - `_commit` passes for HEAD's sha and fails for a bogus value.
  - Placeholders:
    - `{{stage1.plan_path}}` and `{{prev.summary}}` substitute.
    - A value containing `{{input}}` stays literal.
    - An unknown name gives "".
  - Runner integration: invalid outputs fail the stage with the exact error, and valid ones advance.
- [ ] **Step 2: implement, run the tests (plus the gates and workflow suites), and commit** with `feat(stages): typed stage outputs checked and passed by name`.

### Task 3: Auto-retry once and the budget cap

**Files:**
- Modify:
  - `backend/app/workflow_runner.py`;
  - `backend/app/routers/workflows.py`: add `POST /runs/{id}/budget`; run detail gets `budget: {spent, budget_usd}`.
  - `backend/app/schemas.py`.
- Test: `backend/tests/test_stages_retry_budget.py`.

**Interfaces:**
- Runner method `async raise_budget(run_id, budget_usd: float, user) -> None` raises `ValueError` when the value is not above spent or the run is not active.
- The auto-retry state is `stage_state[str(i)].auto_retried = True` once it has been used.

- [ ] **Step 1: write the failing tests.**
  - Auto-retry:
    - A failed task with `auto_retry` gets a new attempt and the log line; a second failure fails the run.
    - A `cancelled` task is not retried.
    - Without `auto_retry`, the 5a behaviour is unchanged.
  - Budget:
    - With spent ≥ budget before a stage, the run goes `paused` with status `budget` and the exact log line.
    - The new task's `max_budget_usd` is min(agent, remaining).
    - A remainder under 0.01 pauses the run.
    - A raise resumes on the next `_advance`. Its permission is manage, and a raise not above spent gives 409.
  - Race: a budget raise racing cancel ends with cancel winning.
- [ ] **Step 2: implement and run the tests**, plus `tests/test_gates_runner.py`, `tests/test_gates_api.py`, `tests/test_workflow_start_race.py` and `tests/test_run_scoping.py`. Commit with `feat(stages): auto-retry once and a per-run budget cap`.

### Task 4: Fix-it loop for failed gates

**Files:**
- Create: `backend/app/gates/fixloop.py`, which builds the fix prompt.
- Modify: `backend/app/workflow_runner.py`.
- Test: `backend/tests/test_stages_fixloop.py`.

**Interfaces:**
- `fixloop.prompt(cmd: str, exit_code: int | None, result: TestResult, log_tail: str, secrets: list[str]) -> str` returns the exact head plus the redacted data block.
- Runner, on a gate failure with an exit code (not a guard refusal or a timeout) when rounds remain:
  - set `stage_state.status = "fixing"` and `fix_round = k`;
  - create the fixer task at the gate's stage_index and current attempt, with tags `["workflow", "fix"]`, in the gate's project, under the budget rules from Task 3;
  - when it completes, make a new hand-out and run the gate again;
  - if it fails, fail the run.
- `_advance` must not treat the fix task as an agent-stage task of the gate stage. Branch on the `fix` tag.

- [ ] **Step 1: write the failing tests.**
  - Gate fail, then fix, then gate pass: the log has both exact lines, and the round counter is right.
  - Gate fail with rounds exhausted fails the run.
  - A fix task that fails fails the run.
  - A guard refusal gets no fix. A timeout gets no fix.
  - The fix prompt redacts a project env value and includes at most 20 names and 50 lines.
  - Cancel during `fixing` cancels the fix task, and no new hand-out happens.
  - Each fix round's gate gets a new hand-out, and stale results are dropped (the 5a rule).
- [ ] **Step 2: implement, run the tests plus the gates suites, and commit** with `feat(stages): fix-it loop from a failed gate to a fixer agent`.

### Task 5: Critical-path risk and linking a task after creation

**Files:**
- Modify:
  - `backend/app/task_service.py`: `classify_risk(prompt, project_id, tags=None, critical_prefixes: list[str] | None = None)`, called with prefixes loaded in `build_task` when the launch has `raise_risk`.
  - `backend/app/routers/tasks.py`: the PATCH route.
  - The launch helpers: `prefixes(critical_paths) -> list[str]`.
- Test: `backend/tests/test_stages_risk_link.py`.

- [ ] **Step 1: write the failing tests.**
  - Risk:
    - With `raise_risk` on, a prompt mentioning `src/billing` (critical path `src/billing/**`) is critical, and the event has `risk_reason`.
    - With it off, there is no change.
    - A prefix shorter than 3 chars is ignored, and matching is case-insensitive.
    - Existing `classify_risk` callers are unchanged.
  - PATCH link:
    - The creator links, a maintainer links, and another contributor gets 403.
    - Another project's item gives 422, and a done item gives 422.
    - Unlinking works.
    - Linking a running task to an open item starts the item.
- [ ] **Step 2: implement, run the tests plus the launch and task-creation tests, and commit** with `feat(launch): critical paths can raise task risk; link tasks after creation`.

### Task 6: Frontend

**Files:**
- Modify:
  - `frontend/src/gates/StageFields.jsx` and `WorkflowsView.jsx`: an outputs list per agent item, an Auto-retry switch per agent stage, and gate Fix fields (agent select filtered to the gate's project, rounds 1–3). Add a workflow Budget field.
  - `frontend/src/runs/RunView.jsx` and `frontend/src/gates/GateCard.jsx`:
    - outputs as a key/value table per stage, text only;
    - a `fixing` state showing the round;
    - the header line "Budget: $spent of $budget";
    - when status is `budget`, a "Raise budget" input plus a button for managers.
  - `frontend/src/launch/LaunchTab.jsx`: a "Critical paths raise task risk" switch for maintainers.
  - `frontend/src/components/TaskDetail.jsx`: a launch-item select that PATCHes the link.
  - `frontend/src/api.js`.
- Test: extend the matching `*.test.jsx` files.

- [ ] **Step 1: write the failing tests.**
  - The editor payload carries `outputs`, `auto_retry` and `fix` only when set, and old stages are unchanged.
  - RunView shows outputs, the fixing round, the budget line, and calls Raise.
  - The LaunchTab switch calls PUT.
  - The TaskDetail select calls PATCH.
- [ ] **Step 2: implement, run the tests and `npm run build`, and commit** with `feat(ui): outputs, auto-retry, fix loop, budget and task launch link`.

### Task 7: README

**Files:** `README.md`.

Update "Command gates" (the fix loop) and "Launch list" (raise-risk and linking after creation). Add a section "Typed outputs, retry and budget". Each claim must be accurate against the code, and each section lists what is not covered (the spec's non-goals). Commit with `docs(readme): typed outputs, auto-retry, budget cap, fix loop`.
