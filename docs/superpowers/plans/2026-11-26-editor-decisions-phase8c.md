# Phase 8c Implementation Plan — team decisions in your own editor

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only MCP endpoint with personal keys so people's own editors can read decisions of projects they can see.

**Architecture:** `editor_keys` table (0026); `app/editor_mcp/` (keys, protocol, tools); `/me/editor-keys`, `/admin/editor-keys`, `POST /mcp` mounted outside the cookie-auth dependency with its own key check; org switch `editor_connections` (default off); a "Connect my editor" modal.

**Tech Stack:** FastAPI, async SQLAlchemy, Alembic, pytest (SQLite), React 18 + antd 5 + vitest.

**Spec:** `docs/superpowers/specs/2026-11-26-editor-decisions-phase8c-design.md` — binding (exact texts, limits, shapes).

## Global Constraints
- **Commands.** Backend from `backend/`: `env -u CO_DATABASE_URL /Users/ashutosh/Projects/Work/Claude-Orchestrator/backend/.venv/bin/python -m pytest -o addopts="" -q -p no:cacheprovider <paths>`. Frontend: `npx vitest run <files>`, `npm run build`.
- **Hard rules.** No Postgres server. Never run the real `claude`, npm/npx with a package, or the app; not 8200/5200. Install nothing (no MCP library). Migration `0026` (`down_revision "0025"`), idempotent like 0025, real booleans in tests. Lockdown and scoping-coverage tests must stay exact (add the new routes with reasons, never loosen).
- **Commits.** No trailers; by path; never `.superpowers/`; never `git stash`.

## Review Focus
1. **A key never shows a project its owner can't see** — including after the owner loses access, a key limited to project A asked about project B, a disabled owner, an expired/revoked key, switch off (Task 1).
2. **Nothing writes** — no tool or method changes data; cookies alone never reach `/mcp`; a browser page (Origin header) is refused (Task 1).
3. **Hostile decision text** (instructions to the editor's agent, secrets, huge text) — redacted, framed as data, capped (Task 1).
4. **Malformed JSON-RPC** (batch, wrong types, unknown method, missing id, huge body) — correct errors, no 500 (Task 1).
5. **Key text never appears** after creation — not in list responses, admin views, logs or errors (Task 1, Task 2).

### Task 1: Backend
**Files:** `backend/app/models.py`, `backend/migrations/versions/0026_editor_keys.py`, new `backend/app/editor_mcp/` (`keys.py`, `protocol.py`, `tools.py`), new routers (`/me/editor-keys`, `/admin/editor-keys`, `/mcp`), `backend/app/main.py`, org switch in the ground-rules/org settings, `backend/tests/test_lockdown.py` + `test_scoping_coverage.py` entries. Tests alongside.
- [ ] Test-first; run the new tests, lockdown, scoping, decisions, migration tests + full suite once. Commit `feat(editor): read-only team decisions for your own editor`.

### Task 2: Screens and README
**Files:** new `frontend/src/components/ConnectEditor.jsx` (+ test), TopBar/App/commands/api.js, admin switch + "Editor keys" list, dev proxy prefix for `/mcp` only if the UI calls it (it shouldn't), `README.md`.
- [ ] Test-first; tests + `npm run build`. Commits `feat(ui): connect my editor` and `docs(readme): editor connections`.
