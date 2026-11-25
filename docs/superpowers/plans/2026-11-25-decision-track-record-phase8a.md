# Phase 8a Implementation Plan — decision track record

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record which decisions were shown to which tasks, settle each showing's outcome, and show a track record per decision.

**Architecture:** `decision_showings` table (migration 0024); writes in the worker's spool ingest; a settle job beside `after_run`; workflow stage settle hook; grouped query in the decisions list; a backfill route; DecisionsPane tag.

**Tech Stack:** FastAPI, async SQLAlchemy, Alembic, pytest (SQLite), React 18 + antd 5 + vitest.

**Spec:** `docs/superpowers/specs/2026-11-25-decision-track-record-phase8a-design.md` — binding.

## Global Constraints
- **Commands.** Backend from `backend/`: `env -u CO_DATABASE_URL /Users/ashutosh/Projects/Work/Claude-Orchestrator/backend/.venv/bin/python -m pytest -o addopts="" -q -p no:cacheprovider <paths>`. Frontend: `npx vitest run <files>`, `npm run build`.
- **Hard rules.** No Postgres server. Never run the real `claude`, npm/npx with a package, or the app; not 8200/5200. Install nothing. Migration `0024` (`down_revision "0023"`), one transaction, real booleans in tests. Tests that hold locks across sessions must work on Postgres (use `fail_after_60s`).
- **Commits.** No trailers; by path; never `.superpowers/`; never `git stash`.

## Review Focus
1. **A task shown the same decision 50 times** — one row (Task 1).
2. **A cancelled task, a retried task, a multi-task stage** — outcomes as the spec says (Task 1).
3. **Backfill run twice** — no duplicates (Task 1).
4. **A project where everything fails** — no decision flagged (baseline rule) (Task 1).

### Task 1: Backend
**Files:** `backend/app/models.py`, `backend/migrations/versions/0024_decision_showings.py`, `backend/app/worker.py` (spool ingest write; settle on finish), new `backend/app/decisions/track.py` (settle, score, backfill), `backend/app/workflow_runner.py` (settle when a stage settles), `backend/app/routers/decisions.py` (track in list; admin backfill route). Tests alongside.
- [ ] Test-first; run decisions, worker, workflow-runner, migration tests + full suite once. Commit `feat(decisions): track record of what happened after each decision was shown`.

### Task 2: Screens and README
**Files:** `frontend/src/decisions/DecisionsPane.jsx` (+ test), the Decisions page admin action, `README.md` (Decision memory section, upgrade note, Cognition credit not needed).
- [ ] Test-first; tests + `npm run build`. Commits `feat(ui): decision track record` and `docs(readme): decision track record`.
