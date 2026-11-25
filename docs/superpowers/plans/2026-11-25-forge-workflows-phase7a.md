# Forge Port Phase 7a Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Approve ahead, one check at a time per project, run again from a step, time and spend per step, and clearer run/handoff wording.

**Architecture:** Runner changes in `backend/app/workflow_runner.py` (stage_state keys, a module-level project lock, a rerun entry point), three routes in `routers/workflows.py`, a computed `usage` per step in the run API, handoff text changes in `resume/handoff.py`; then the run view and approval card on the frontend.

**Tech Stack:** FastAPI, async SQLAlchemy, pytest (SQLite), React 18 + antd 5 + vitest.

**Spec:** `docs/superpowers/specs/2026-11-25-forge-workflows-phase7a-design.md` — read it first; texts, status names, routes and rules there are binding.

## Global Constraints
- **Commands.** Backend, from `backend/`: `env -u CO_DATABASE_URL /Users/ashutosh/Projects/Work/Claude-Orchestrator/backend/.venv/bin/python -m pytest -o addopts="" -q -p no:cacheprovider <paths>`. Frontend, from `frontend/`: `npx vitest run <files>`, then `npm run build`.
- **Hard rules.** Do NOT connect to any Postgres server (temp SQLite only). Never run the real `claude`, `npx` with a package, or any MCP server. Do not use ports 8200/5200; never touch the running app. Never `monkeypatch.setattr(app.state, …)`; use `monkeypatch.setitem(app.state._state, …)`. Install nothing. No migration in this phase (stage_state is JSON).
- **Commits.** Folder by folder, no trailers of any kind. Commit only your own paths (`git commit -m … -- <files>`); `.superpowers/` is never committed.
- **Runner safety.** Every change to a run's state happens under the existing per-run lock; the project lock is always released on every exit path (use try/finally); no new background task is started per run.
- **Wording.** Screen texts exactly as in the spec; technical strings under "Details".

## Review Focus
1. **Approve ahead on a release step, or on a step already reached** — refused (Task 1).
2. **Two runs in one project reaching a gate together** — one runs, the other is held with the spec's reason, then runs; the lock is never left held after a failure, cancel or exception (Task 1).
3. **Run again from a step while the run is still running, or from a step never reached** — refused; earlier steps' results still feed `{{prev}}` (Task 1).
4. **An early approval when the run is cancelled, fails, or is restarted from an earlier step** — dropped with a log line, never applied (Task 1).
5. **A server restart with an early approval stored and a gate held by the lock** — the approval still applies; the held gate takes the lock again (Task 1).

---

### Task 1: Runner and API

**Files:**
- Modify: `backend/app/workflow_runner.py`, `backend/app/routers/workflows.py`, `backend/app/workflow_templates.py` (`ahead: False` on release approvals), `backend/app/gates/spec.py` (accept the `ahead` key), `backend/app/gates/totals.py` (reuse for per-step usage), `backend/app/resume/handoff.py` (file list, caution notes), `backend/app/schemas.py` if run responses are typed
- Test: `backend/tests/test_runs_ahead.py`, `backend/tests/test_runs_project_lock.py`, `backend/tests/test_runs_rerun.py`, `backend/tests/test_runs_step_usage.py`, the handoff tests, `backend/tests/test_workflow_templates.py`

**Interfaces:**
- `POST /workflow-runs/{id}/stages/{i}/approve-ahead` → run; `DELETE` same path → run; `POST /workflow-runs/{id}/stages/{i}/rerun` → run.
- Run responses: `stages_state[i].ahead = {by_name, at} | absent`; `step_usage[i] = {cost_usd, input_tokens, output_tokens, seconds}`; held steps keep `status: "held"` with `reason`.

- [ ] **Step 1: Failing tests** for every rule in spec §1–§5 and each Review Focus line (check the real route prefix and the run response shape in routers/workflows.py first and use them).
- [ ] **Step 2: Implement; run the new tests plus every `tests/test_gates_*.py`, `tests/test_stages_*.py`, `tests/test_run*.py`, `tests/test_workflow*.py` and the handoff tests. Commit `feat(workflows): approve ahead, one check at a time per project, run again from a step`.**

### Task 2: Screens and README

**Files:**
- Modify: `frontend/src/gates/ApprovalCard.jsx`, `frontend/src/runs/RunView.jsx`, `frontend/src/flow/FlowCanvas.jsx` / `flow.js` (per-step time and spend on run boxes, status words), `frontend/src/api.js`, the handoff card component (caution list), `README.md`
- Test: the matching `*.test.jsx` / `flow.test.js`

- [ ] **Step 1: Failing tests:** an "Approve ahead" action on a not-yet-reached approval box for people who may manage the run, hidden for `ahead:false` steps and for others; "Approved ahead by <name>" with a Withdraw action; "Run again from here" in a finished step's details on completed/failed runs (Popconfirm "Run again from <step>? Later steps start over."); per-step "4 min · $0.12" on run boxes (tokens under Details); status words from the spec; "Re-check before acting" list on the handoff card.
- [ ] **Step 2: Implement; run the tests and `npm run build`.** README: update the workflow section (approve ahead, one check at a time, run again from a step, per-step spend) with a short "To know" from the spec. Commit `feat(ui): approve ahead, run again from a step, time and spend per step` and `docs(readme): workflow run control`.
