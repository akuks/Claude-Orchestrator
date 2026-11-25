# Forge Port Phase 7f Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A UI workflow template with a Screens gate, a "Set up UI checks" agent task, and a Brand guide Context Store template.

**Architecture:** `workflow_templates.py` gains the stages; new `backend/app/ui_checks.py` (file bodies + task prompt) and a route that creates the task; a Context Store template route; the Recommended panel and Context Store screens.

**Tech Stack:** FastAPI, async SQLAlchemy, pytest (SQLite), React 18 + antd 5 + vitest.

**Spec:** `docs/superpowers/specs/2026-11-25-forge-ui-checks-phase7f-design.md`. Forge's original files: `docs/research/forge-f71/` (MIT).

## Global Constraints
- **Commands.** Backend from `backend/`: `env -u CO_DATABASE_URL /Users/ashutosh/Projects/Work/Claude-Orchestrator/backend/.venv/bin/python -m pytest -o addopts="" -q -p no:cacheprovider <paths>`. Frontend from `frontend/`: `npx vitest run <files>`, `npm run build`.
- **Hard rules.** No Postgres server. Never run the real `claude`, `npx`/`npm` with a package, browsers, or the app; not 8200/5200. Install nothing. No migration.
- **Commits.** No trailers; by path; never `.superpowers/` (docs/research/forge-f71 IS committed as reference).
- New top-level router prefixes need a vite proxy entry (a test checks).

## Review Focus
1. **The Screens gate commands** pass `check_command` and the guard (test with the real rules) (Task 1).
2. **The setup prompt** never tells the agent to overwrite, push, or run anything beyond installing the three dev dependencies (Task 1).
3. **Brand guide on a project that already has `brand/brand.md`** — 409, nothing changed (Task 1).
4. **A person without permission** cannot start the setup task or create the doc (Task 1).

---

### Task 1: Backend
**Files:** `backend/app/workflow_templates.py`, new `backend/app/ui_checks.py`, a route for "Set up UI checks" (find where the Recommended routes live — `routers/recommend.py`), a Context Store template route (`routers/context_docs.py`), `backend/app/recommend/panel.py` (UI-skills paragraph line). Tests alongside (template stages validate; prompt content; task created with the person's defaults; 409; permissions; guard verdict for the three npm commands).
- [ ] Test-first; run workflow-template, recommend, context-docs, gates-spec, guard tests and the full suite once. Commit `feat(ui-checks): UI workflow screens gate, set-up task, brand guide template`.

### Task 2: Screens and README
**Files:** Recommended tab ("Set up UI checks" button with a confirm and a link to the created task), Context Store ("Start from a template → Brand guide"), README (Workflow templates section, Recommended section, Context Store, Upgrade notes entry, "Check on a real run" items). Tests alongside.
- [ ] Test-first; tests + `npm run build`. Commits `feat(ui): set up UI checks; brand guide template` and `docs(readme): UI checks`.
