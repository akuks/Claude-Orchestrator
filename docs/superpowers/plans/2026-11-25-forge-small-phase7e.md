# Forge Port Phase 7e Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the small forge gaps: health additions, Node floor, usage tips, temp-doc copy, helper tier, redaction extras, PostToolUseFailure, security-review prompts.

**Architecture:** health/checks.py + mcp_client handshake (migration 0023); usage tracker/report; hook + plugin registration; redaction module + its hook copy; a copy route; security prompt builder and a config scanner; screens.

**Tech Stack:** FastAPI, async SQLAlchemy, Alembic, pytest (SQLite), React 18 + antd 5 + vitest.

**Spec:** `docs/superpowers/specs/2026-11-25-forge-small-phase7e-design.md` — binding texts.

## Global Constraints
- **Commands.** Backend from `backend/`: `env -u CO_DATABASE_URL /Users/ashutosh/Projects/Work/Claude-Orchestrator/backend/.venv/bin/python -m pytest -o addopts="" -q -p no:cacheprovider <paths>`. Frontend from `frontend/`: `npx vitest run <files>`, `npm run build`.
- **Hard rules.** No Postgres server (temp SQLite). Never run the real `claude`, `npx` with a package, MCP servers, or the app; not 8200/5200. Never `monkeypatch.setattr(app.state, …)`. Install nothing. Tests that hold DB locks across sessions must work on Postgres too (use the `fail_after_60s` fixture in conftest.py for anything that could block).
- **Migration.** `0023`, `down_revision = "0022"`, one transaction, real booleans in tests.
- **Commits.** No trailers; by path; never `.superpowers/`. New top-level router prefixes need a vite proxy entry (a test checks).

## Review Focus
1. **A copy request for a path not in the task's recorded docs, a symlink, a 6 MB file, `../x`, or an existing dest** — refused (Task 3).
2. **The redaction copies drifting** — the equality test covers the new functions (Task 2).
3. **A connected tool whose handshake has no serverInfo** — no version, no warning (Task 1).
4. **The plugin validate command missing in this CLI version, or hanging** — OK with a note / bounded (Task 1).
5. **A project `.claude/settings.json` with a 10 MB file or a symlink** — skipped safely (Task 3).

---

### Task 1: Health additions
**Files:** `backend/app/health/checks.py`, `backend/app/mcp_client.py`, `backend/app/models.py`, `backend/migrations/versions/0023_small.py`, `backend/app/recommend/panel.py` (Node mark), `backend/app/codemap/status.py` (two engines). Tests alongside.
- [ ] Spec §1 + Review Focus 3–4, test-first; run health/mcp/recommend/codemap/migration tests + full suite once. Commit `feat(health): tool versions, code-map errors, Node floor, self-tests`.

### Task 2: Usage tips, helper tier, redaction extras, PostToolUseFailure
**Files:** `backend/app/usage/tracker.py`, `backend/app/usage/report.py`, `backend/app/resume/logfmt.py` (+ wherever the tool_use event is built), `backend/app/redaction.py`, `backend/app/guard/co_hook.py`, `backend/app/guard/plugin.py`, `backend/app/worker.py` (.env values, applying redact_head/strip_query). Tests alongside.
- [ ] Spec §2, §4, §5, §6 + Review Focus 2, test-first; run usage, resume, redaction, the whole tests/guard suite and worker tests + full suite once. Commit `feat(usage,guard): per-step re-read tips, helper tier, redaction extras, failed-call records`.

### Task 3: Temp-doc copy, security reviews, screens, README
**Files:** `backend/app/routers/tasks.py` (copy route), `backend/app/security.py` (+ a new `backend/app/security_config.py`), `frontend/src/resume/HandoffCard.jsx`, `frontend/src/usage/TokensPanel.jsx`, health page cards, Recommended panel, `README.md`. Tests alongside.
- [ ] Spec §3, §7, screens for §1/§2 + Review Focus 1, 5, test-first; run tests + `npm run build`. Commits `feat(upkeep): copy temp documents into the project; safer security reviews` and `docs(readme): small gaps`.
