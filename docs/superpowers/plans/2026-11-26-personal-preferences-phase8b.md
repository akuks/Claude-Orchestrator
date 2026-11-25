# Phase 8b Implementation Plan — personal preferences

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each person keep a short self-written preference list that is added to every task they start.

**Architecture:** `user_preferences` table (migration 0025); `/me/preferences` router; a `personal_prefs` ground-rule switch; injection in the worker's system-prompt assembly after ground rules; a "My preferences" modal; a ground-rules log line.

**Tech Stack:** FastAPI, async SQLAlchemy, Alembic, pytest (SQLite), React 18 + antd 5 + vitest.

**Spec:** `docs/superpowers/specs/2026-11-26-personal-preferences-phase8b-design.md` — binding (exact texts there).

## Global Constraints
- **Commands.** Backend from `backend/`: `env -u CO_DATABASE_URL /Users/ashutosh/Projects/Work/Claude-Orchestrator/backend/.venv/bin/python -m pytest -o addopts="" -q -p no:cacheprovider <paths>`. Frontend: `npx vitest run <files>`, `npm run build`.
- **Hard rules.** No Postgres server. Never run the real `claude`, npm/npx with a package, or the app; not 8200/5200. Install nothing. Migration `0025` (`down_revision "0024"`), idempotent like 0024, real booleans in tests.
- **Commits.** No trailers; by path; never `.superpowers/`; never `git stash`.

## Review Focus
1. **User A's list never reaches user B's task** (including a follow-up step B sends in A's thread) (Task 1).
2. **A secret pasted into a preference** is redacted in what the agent receives (Task 1).
3. **Hostile text** (newlines, hidden characters, a fake "# Task" heading, 10,000 characters) — rejected or cleaned; can't break out of the list (Task 1).
4. **Agent tokens and other users** can't read or write someone's list (Task 1).
5. **Org switch off / person's master switch off / all items off** — nothing added, no empty heading (Task 1).

### Task 1: Backend
**Files:** `backend/app/models.py`, `backend/migrations/versions/0025_user_preferences.py`, new `backend/app/preferences.py` (load, validate, render paragraph), new `backend/app/routers/preferences.py` (+ register in main), `backend/app/resume/ground_rules.py` (switch), `backend/app/worker.py` (injection + event). Tests alongside.
- [ ] Test-first; run preference, ground-rules, worker, migration, scoping/lockdown tests + full suite once. Commit `feat(prefs): personal preferences added to the tasks a person starts`.

### Task 2: Screens and README
**Files:** new `frontend/src/components/MyPreferences.jsx` (+ test), `frontend/src/shell/TopBar.jsx` (menu entry), `frontend/src/App.jsx` (mount, palette entry), `frontend/src/api.js`, TaskDetail ground-rules line, ground-rules settings switch, `README.md` (section + upgrade note for 0025).
- [ ] Test-first; tests + `npm run build`. Commits `feat(ui): my preferences` and `docs(readme): personal preferences`.
