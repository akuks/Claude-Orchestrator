# Forge Port Phase 7g Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A capacity readout on System health and a project "Activity" tab showing what was built and by which task.

**Architecture:** `backend/app/health/capacity.py` + route; `backend/app/resume/activity.py` (git log parser through gitsafe) + route; frontend card and tab.

**Tech Stack:** FastAPI, async SQLAlchemy, pytest (SQLite), React 18 + antd 5 + vitest.

**Spec:** `docs/superpowers/specs/2026-11-25-forge-activity-phase7g-design.md` — binding texts.

## Global Constraints
- **Commands.** Backend from `backend/`: `env -u CO_DATABASE_URL /Users/ashutosh/Projects/Work/Claude-Orchestrator/backend/.venv/bin/python -m pytest -o addopts="" -q -p no:cacheprovider <paths>`. Frontend from `frontend/`: `npx vitest run <files>`, `npm run build`.
- **Hard rules.** No Postgres server. Never run the real `claude`, npm/npx with a package, or the app; not 8200/5200. Install nothing (no psutil). No migration. git only through `backend/app/gitsafe.py` (extend its allow-list minimally with tests that other forms stay refused).
- **Commits.** No trailers; by path; never `.superpowers/`. New top-level router prefixes need a vite proxy entry (a test checks).

## Review Focus
1. **A repo with 100,000 commits, a shallow clone, no git, or commit titles with control characters / HTML** — bounded, plain text (Task 1).
2. **A hostile repo's git config** — the new git form runs no external programs (Task 1).
3. **A machine where memory can't be read** — null fields, no error (Task 1).

---

### Task 1: Backend
**Files:** new `backend/app/health/capacity.py`, `backend/app/resume/activity.py`, routes (health router; projects router), `backend/app/gitsafe.py` (allow `log --no-merges --numstat --format=<fixed> --since=<date> -n <N>` exactly as needed). Tests alongside (temp git repos; fake /proc and sysctl readers).
- [ ] Test-first; run health, projects, gitsafe, gitfacts tests + full suite once. Commit `feat(activity): capacity readout and what-was-built history`.

### Task 2: Screens and README
**Files:** System health Capacity card; project "Activity" tab (chart with the app's existing chart approach or plain CSS bars — no new libraries); README sections + upgrade note.
- [ ] Test-first; tests + `npm run build`. Commits `feat(ui): capacity card and project activity tab` and `docs(readme): capacity and activity`.
