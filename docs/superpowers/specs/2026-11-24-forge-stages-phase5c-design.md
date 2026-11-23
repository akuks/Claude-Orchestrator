# Forge port — Phase 5c: typed outputs, auto-retry, budget cap, fix-it loop — design

Program: port every jspark-forge feature into the orchestrator (owner: "Copy each and every feature of forge").
Inventory: `docs/research/jspark-forge-feature-inventory.md` F66 (§3 row: typed outputs, retry once then fail,
budget cap per run) and F64 (critical paths → risk); parked items from 5a (fix-it loop) and 5b (link a task to a
launch item after creation). This closes Phase 5; Phase 6 follows.

## Intent
5a made gates trustworthy; 5c makes multi-stage workflows hand work forward reliably and stay inside limits:
an agent stage can declare **typed outputs** that later stages read by name and the runner checks (a path that
exists, a commit that resolves); a flaky agent stage can **retry once** on its own; a run can have a **budget cap**
that pauses it instead of overspending; a failing gate can hand its failure to a **fixer agent** and run again;
**critical paths** can raise a task's risk; and a task can be **linked to a launch item after it was created**.

## Decisions (owner: proceed autonomously; these are the designer's calls)

### Typed stage outputs (F66)
- An agent stage item may declare `outputs: ["plan_path", "summary", "fix_commit"]` (≤ 10 names, each
  `^[a-z][a-z0-9_]{0,39}$`, unique). Saved through `spec.validate_stages` (422 with a plain reason).
- The runner appends to that task's prompt (exact): "When you finish, end your final message with a fenced ```json
  block holding exactly these keys, each a short string: {names}. A key ending in _path is a path relative to the
  project folder that exists; a key ending in _commit is a commit id in this repository."
- On task completion the runner parses the **last** ```json fenced block of `result_text` (≤ 64 KB scanned from
  the end; linear `rfind`, no regex over the whole text). Valid when: a JSON object; exactly the declared keys;
  each value a string ≤ 2000 chars after `clean_text`; `*_path` relative, no `..`, exists in the task's project
  directory (lstat, symlinks allowed only when they resolve inside the project); `*_commit` resolves through
  gitsafe (one new exact entry: `rev-parse --verify --quiet <hex>^{commit}`, hex 7–40 lowercase) to a commit.
- Valid → stored on `tasks.outputs` (JSON, nullable) and the stage continues. Invalid or missing → the task is
  marked **failed** with error "outputs invalid: <first reason>" (the agent's work is kept; auto-retry below
  applies). A stage with several items: every item with outputs must be valid.
- Later prompts read them: `{{stageN.name}}` (1-based, the stage's current attempt; several items with the same
  name → joined with a blank line) and `{{prev.name}}`. Unknown name → empty string (as `{{stageN}}` today).
  Substitution is single-pass: an output value containing `{{…}}` is never expanded again.
- The run view shows each stage's outputs as a small key/value table (text only); values from a stage whose tasks
  the viewer cannot see are not shown (same rule as stage tasks).

### Automatic retry once (F66)
- An agent stage may set `auto_retry: true` (default false). When any of its tasks of the current attempt fails or
  is cancelled **by the system** (not a person's cancel, not a run cancel), the runner starts the next attempt once
  (same mechanism as a manual Retry: new `stage_attempt`, earlier tasks kept) and logs "Step {name} retried
  automatically". At most once per stage per run; a second failure fails the run as today. A person's Retry later
  is still allowed.
- "Cancelled by a person" is known from the task's existing cancel path; the runner treats `cancelled` as
  person-cancelled and never auto-retries it.

### Per-run budget cap (F66 "plan-usage cap becomes a budget cap per run")
- A workflow may set `budget_usd` (0.01–10000, optional); a run snapshots it on start (`workflow_runs.budget_usd`).
- Spent = sum of `total_cost_usd` over the run's tasks (every attempt). Before the runner starts any stage (agent
  stage, fix task, auto-retry) it checks spent ≥ budget → the run goes `paused` with `stage_state[i].status =
  "budget"` and log "Step {name} held: budget reached (${spent} of ${budget})". Nothing in flight is stopped.
- Each new agent task of a capped run gets `max_budget_usd = min(agent's own, budget − spent)` (never below 0.01;
  if the remainder is under 0.01 the run pauses instead).
- `POST /workflows/runs/{id}/budget {budget_usd}` (manage permission; must be > spent) raises it; the run resumes
  on the next tick; log "Budget raised to ${new} by {user}". The run header shows "Budget: $spent of $budget".

### Fix-it loop for a failed gate (parked from 5a)
- A gate stage may set `fix: {"agent": "<name|id>", "rounds": 1..3}`. The agent must exist and belong to the gate's
  project (validated on save; an agent without a project is refused, since the fix prompt carries gate output).
- When the gate fails (exit code, not a guard refusal, not a timeout of the runner itself) and rounds remain: the
  stage goes `fixing`, the runner creates one task for the fixer agent in the gate's project (stage_index = the
  gate, current attempt, tagged `fix`) whose prompt is (exact head): "A check failed. Fix the cause, commit on the
  current branch, do not merge or push. Then stop." followed by the command, exit code, counts, up to 20 failing
  names and the last 50 log lines (redacted with the project's env values, `clean_text`ed), framed as data.
- Fix task completed → a new gate hand-out runs the gate again (the retry round of 5a applies inside each
  hand-out). Fix task failed or cancelled → the run fails. Rounds exhausted → the run fails as today. Log lines:
  "Step {name} (gate) failed, fixing (round {k} of {n})", "Step {name} (gate) passed after {k} fix round(s)".
- Flaky detection keeps working across fix rounds (code state differs after a fix commit, so nothing is called
  flaky across a fix, as intended).

### Critical paths raise risk (F64)
- `project_launches.raise_risk` (bool, default false), set by maintainers on the Launch tab.
- When on, `classify_risk` returns "critical" for a task in that project whose prompt contains a critical path's
  literal prefix (the glob up to its first wildcard, trailing `/` stripped, ≥ 3 chars), case-insensitive. The
  existing `gate_critical_approval` setting then decides approval, as for keyword hits. The task's events record
  `risk_reason: "critical path src/billing"`.
- `classify_risk` stays synchronous; the caller loads the launch row first and passes the prefixes in.

### Link a task to a launch item after creation (parked from 5b)
- `PATCH /tasks/{id}/launch-item {launch_item_id | null}` — the task's creator or a project maintainer/admin;
  item must be in the task's project and open or started; unlinking allowed. If the task is already running and
  the item is open, the item becomes started (same helper as creation). TaskDetail shows the link with a select.

## Goals
1. Migration 0016: `tasks.outputs`, `workflows.budget_usd`, `workflow_runs.budget_usd`,
   `project_launches.raise_risk`; stage validation for `outputs`, `auto_retry`, `fix`; workflow `budget_usd`.
2. Typed outputs: prompt suffix, parser/validator, gitsafe entry, placeholders, run view table.
3. Auto-retry once and the budget cap (+ raise endpoint).
4. Fix-it loop.
5. Critical paths → risk; link a task to a launch item after creation.
6. Frontend: editor fields, run view (outputs, budget, fixing state), Launch tab switch, TaskDetail link.
7. README sections updated ("Command gates", "Launch list", new "Typed outputs, retry and budget").

## Non-goals
- `--json-schema` for long agent runs (unverified against the real CLI for multi-turn runs; the fenced block is
  enough and testable).
- Non-string output types.
- Fixing a gate refused by the guard (a person must look).

## Anti-goals
- Output text expanded as placeholders a second time, or shown to viewers who cannot see the stage's tasks.
- A budget cap that kills running work (it only stops new work).
- A fix loop that runs without a round limit, or sends gate output to an agent outside the gate's project.
- An auto-retry after a person cancelled.

## Approaches considered
| Question | Options | Outcome |
|---|---|---|
| How outputs come back | `--json-schema` / **fenced json block in the final message** | block: works with any agent run, testable; schema mode unverified for agentic runs |
| Budget enforcement | kill in-flight tasks / **stop new work + per-task cap from the remainder** | nothing half-done is killed; spend stays bounded |
| Fix loop shape | loop back to an earlier agent stage / **a named fixer agent inside the gate stage** | no backwards jumps in the stage list; simple attempt accounting |
| Critical-path risk | always on / **per-project switch** | avoids flooding approvals on projects that list many paths |

## Builder's latitude
Module layout (`app/gates/outputs.py`, `app/gates/fixloop.py` suggested), UI layout and copy apart from the fixed
texts, test structure.
