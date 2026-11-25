# Forge Port Phase 7c Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record Claude's plan-limit signals from the headless stream, pause work until the reset time when the limit is reached, and show it plainly.

**Architecture:** Stream parser in `backend/app/worker.py` handles `rate_limit_event` and keeps `api_retry` fields; a new `plan_limit_status` table (migration 0021) holds the latest status per credential owner; the worker's retry path and the queue/workflow start checks consult it; a small router serves it; screens show banners and cards.

**Tech Stack:** FastAPI, async SQLAlchemy, Alembic, pytest (SQLite), React 18 + antd 5 + vitest.

**Spec:** `docs/superpowers/specs/2026-11-25-forge-plan-limits-phase7c-design.md` — binding.

## Global Constraints
- **Commands.** Backend from `backend/`: `env -u CO_DATABASE_URL /Users/ashutosh/Projects/Work/Claude-Orchestrator/backend/.venv/bin/python -m pytest -o addopts="" -q -p no:cacheprovider <paths>`. Frontend from `frontend/`: `npx vitest run <files>`, `npm run build`.
- **Hard rules.** No Postgres server (temp SQLite). Never run the real `claude`, `npx` with a package, MCP servers, or the app; not ports 8200/5200. Never `monkeypatch.setattr(app.state, …)`. Install nothing.
- **Migration.** `0021`, `down_revision = "0020"`; works on SQLite and Postgres; downgrade drops the table. Migration test must insert real booleans/values (Postgres CI is strict).
- **Commits.** No trailers; commit by path; never `.superpowers/`.
- **Safety.** Never store credentials or tokens; `raw` capped at 2 KB; owner is a user id or `"org"`.

## Review Focus
1. **A stream with a `rate_limit_event` whose `resetsAt` is in ms, in s, absurd, or missing** — normalised as the spec says (Task 1).
2. **A limit hit seen only in the `result` text (nothing on stderr)** — recognised; requeued to the reset time (Task 1).
3. **A stale `rejected` status after its reset time** — no longer holds work (Task 1).
4. **A workflow step whose task hits the limit** — held with the message, never failed (Task 1).
5. **An API-key user** — no plan card noise; work never held by another person's status (Tasks 1, 2).

---

### Task 1: Backend
**Files:** `backend/app/worker.py`, new `backend/app/planlimits.py` (normalise, record, is_held, message texts), `backend/app/models.py`, `backend/migrations/versions/0021_plan_limits.py`, new `backend/app/routers/plan.py` (`GET /plan/status` for me; `GET /plan/status/all` admin; `DELETE /plan/status/{owner}` admin "Try now"), the queue/start path, `backend/app/workflow_runner.py` (held, not failed), `backend/app/main.py`. Tests: `tests/test_planlimits.py`, `tests/test_worker_plan_limits.py`, `tests/test_plan_api.py`, `tests/test_migrations.py`.
- [ ] Failing tests for every rule in spec §1–§2 and Review Focus 1–5 (fake stream lines through the existing stream-parsing test helpers); implement; run them plus the worker, queue, workflow and migration tests and the full suite once. Commit `feat(plan): notice Claude plan limits and wait for the reset`.

### Task 2: Screens and README
**Files:** `frontend/src/api.js`, top bar / home banner component, the Usage page, `TaskDetail.jsx` (paused message), the Users page tag, `README.md`. Tests alongside.
- [ ] Failing tests for spec §3 texts and visibility; implement; tests + `npm run build`. README: "Claude plan limits" section with "To know" and the owner's verification list from the spec. Commits `feat(ui): Claude plan limits` and `docs(readme): Claude plan limits`.
