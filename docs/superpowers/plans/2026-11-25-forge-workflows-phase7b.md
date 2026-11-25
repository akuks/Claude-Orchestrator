# Forge Port Phase 7b Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Workflow steps can wait for several earlier steps and run side by side, with a parallel limit, all-errors validation, and a canvas that shows the branches.

**Architecture:** `after` and `max_parallel` validated in `gates/spec.py`; a pure dependency module `backend/app/flowgraph.py` (ready set, dependants, depth); the runner schedules from the ready set instead of `current_stage`; the frontend lays out columns by depth and edits `after`.

**Tech Stack:** FastAPI, async SQLAlchemy, pytest (SQLite), React 18 + antd 5 + vitest.

**Spec:** `docs/superpowers/specs/2026-11-25-forge-workflows-phase7b-design.md` — binding (texts, defaults, rules).

## Global Constraints
- **Commands.** Backend, from `backend/`: `env -u CO_DATABASE_URL /Users/ashutosh/Projects/Work/Claude-Orchestrator/backend/.venv/bin/python -m pytest -o addopts="" -q -p no:cacheprovider <paths>`. Frontend, from `frontend/`: `npx vitest run <files>`, then `npm run build`.
- **Hard rules.** No Postgres server (temp SQLite only). Never run the real `claude`, `npx` with a package, or any MCP server. Not ports 8200/5200; never touch the running app. Never `monkeypatch.setattr(app.state, …)`. Install nothing. No migration (stages are JSON).
- **Commits.** No trailers of any kind; commit only your own paths (`git commit -m … -- <files>`); never `.superpowers/`.
- **Compatibility.** A workflow or run snapshot without `after`/`max_parallel` must behave exactly as before — every existing runner test must pass unchanged (a test may only change if it asserted something this spec deliberately changes; say which and why).
- **Runner safety.** Every scheduling decision under the existing per-run lock; budget checked before each start; never more than `max_parallel` running.

## Review Focus
1. **An old linear workflow / old run snapshot** — same order, same `{{prev}}`, same pause/fail behaviour (Tasks 1, 2).
2. **Two branches finishing at the same moment, and the join step** — the join starts exactly once, after both (Task 2).
3. **A failure while another branch is running, and Retry after it** — the other branch is cancelled, Retry continues correctly (Task 2).
4. **An approval waiting on one branch while another branch runs** — the run is not paused until nothing else can run (Task 2).
5. **Moving or deleting a step in the editor that others wait for** — references re-numbered or removed, never pointing later or nowhere (Task 3).

---

### Task 1: Model, validation, `{{prev}}`, dependency helpers

**Files:** Create `backend/app/flowgraph.py`; modify `backend/app/gates/spec.py`, `backend/app/routers/workflows.py` (422 with the list of sentences), `backend/app/workflow_runner.py` (`substitute` only), `backend/app/workflow_templates.py` (export/import keep the keys). Tests: `backend/tests/test_flowgraph.py`, `backend/tests/test_stages_after_spec.py`, `backend/tests/test_substitute_after.py`.

**Interfaces (flowgraph.py, pure functions over the stages list):** `deps(stages, i) -> list[int]` (missing `after` → `[i-1]`, stage 0 → `[]`); `ready(stages, done: set[int], started: set[int]) -> list[int]`; `dependants(stages, i) -> set[int]` (transitive, includes nothing before i); `depth(stages) -> list[int]`; `validate(stages, max_parallel) -> list[str]` (every sentence from the spec).

- [ ] Failing tests for every rule and sentence in the spec's Model section and `{{prev}}` rules; then implement; run the new tests plus `tests/test_stages_*.py`, `tests/test_gates_spec.py`, `tests/test_workflow*.py`. Commit `feat(workflows): steps can wait for several earlier steps`.

### Task 2: Runner schedules from the ready set

**Files:** `backend/app/workflow_runner.py`, `backend/app/routers/workflows.py` (run response: nothing new beyond what the UI needs — say what you add). Tests: `backend/tests/test_runs_parallel.py`.

- [ ] Failing tests for every rule in the spec's Running section and each Review Focus line 1–4 (use the existing fake task/gate machinery); then implement; run ALL runner tests (`tests/test_gates_*.py`, `test_stages_*.py`, `test_run*.py`, `test_workflow*.py`) — unchanged tests must pass. Commit `feat(workflows): run steps side by side`.

### Task 3: Canvas, editor, README

**Files:** `frontend/src/flow/flow.js`, `FlowCanvas.jsx`, the workflow editor (`frontend/src/components/WorkflowsView.jsx`, `frontend/src/gates/gates.js` `formStage`/`stagePayload`), `frontend/src/runs/RunView.jsx`, `README.md`. Tests: `flow.test.js`, `FlowCanvas.test.jsx`, `WorkflowsView.test.jsx`, `RunView.test.jsx`.

- [ ] Failing tests: depth layout (a linear workflow lays out exactly as before), connectors from each waited-for step, "Waits for" multi-select, "Run beside the step before", "Steps at the same time", re-numbering on move/delete (Review Focus 5), all validation sentences shown, several running steps on the run view; then implement; run the tests + `npm run build`. README: workflow section text + "To know". Commits `feat(ui): steps side by side` and `docs(readme): steps side by side`.
