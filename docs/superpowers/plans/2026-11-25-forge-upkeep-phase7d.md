# Forge Port Phase 7d Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Closed handoffs stay closed; optional tidying of old run history; sharper code-map notes; decisions before shell edits; a re-clone warning.

**Architecture:** handoff.py + worker for closing; a `retention.py` module and a lifespan loop; worker computes code-map freshness into the policy and the hook shows it; `rules.evaluate` returns write targets; project root commit stored and checked; System health cards and screens.

**Tech Stack:** FastAPI, async SQLAlchemy, Alembic, pytest (SQLite), React 18 + antd 5 + vitest.

**Spec:** `docs/superpowers/specs/2026-11-25-forge-upkeep-phase7d-design.md` — binding.

## Global Constraints
- **Commands.** Backend from `backend/`: `env -u CO_DATABASE_URL /Users/ashutosh/Projects/Work/Claude-Orchestrator/backend/.venv/bin/python -m pytest -o addopts="" -q -p no:cacheprovider <paths>`. Frontend from `frontend/`: `npx vitest run <files>`, `npm run build`.
- **Hard rules.** No Postgres server (temp SQLite). Never run the real `claude`, `npx` with a package, MCP servers, or the app; not 8200/5200. Never `monkeypatch.setattr(app.state, …)`. Install nothing.
- **Migration.** `0022`, `down_revision = "0021"`; SQLite + Postgres; migration test inserts real booleans; downgrade reverses everything.
- **git.** Only through `backend/app/gitsafe.py`; extend its allow-list with exactly the spec's commands, with tests that other forms stay refused. co_hook.py stays stdlib-only and runs no git.
- **Data.** Nothing deleted except what spec §2 names, and only when the switch is on.
- **Commits.** No trailers; by path; never `.superpowers/`.
- **Proxy.** Any new top-level router prefix needs a `frontend/vite.config.js` proxy entry (a test checks this).

## Review Focus
1. **Tidying with the switch off** — touches nothing; **on** — never touches unfinished tasks, never deletes tasks/notes/decisions (Task 1).
2. **A closed handoff and a later heartbeat/notes/finish of the same run** — stays closed; a Continue reopens it (Task 1).
3. **A repository with a rewritten history, a shallow clone, no git, or a huge diff** — freshness says "different history"/"could not check", list capped at 10, never slow (Task 2).
4. **A shell command writing 500 files** — at most 50 looked up; the hook stays within its time budget (Task 2).
5. **A project folder that is not a git repository** — no root-commit warning (Task 3).

---

### Task 1: Handoffs stay closed; tidying job
**Files:** `backend/app/resume/handoff.py`, `backend/app/worker.py` (auto-close on success), new `backend/app/retention.py`, `backend/app/main.py` (loop), settings storage for the switch (find the org settings model used by System health / guard org settings), `backend/app/routers/health.py` (or a new `routers/retention.py`: GET preview, PATCH switch, POST run now — admin), `backend/migrations/versions/0022_upkeep.py` (index only in this task; Task 3 adds root_commit to the same migration — coordinate by having this task create the file with the index and Task 3 extend it). Tests: `tests/test_handoff_closed.py`, `tests/test_retention.py`, migration test.
- [ ] Failing tests for spec §1, §2 and Review Focus 1–2; implement; run them plus handoff/resume/worker tests and the full suite once. Commit `feat(upkeep): closed handoffs stay closed; optional tidying of old run history`.

### Task 2: Code-map notes and decisions before shell edits
**Files:** `backend/app/gitsafe.py`, `backend/app/codemap/status.py` (freshness helper), `backend/app/guard/policy_inputs.py`, `backend/app/guard/co_hook.py`, `backend/app/guard/rules.py` (`Verdict.writes`). Tests: `tests/test_codemap_freshness.py`, `tests/guard/test_shell_decisions.py`, gitsafe tests.
- [ ] Failing tests for spec §3, §4 and Review Focus 3–4 (real temp git repos built in the test, as existing gitsafe/codemap tests do); implement; run them plus ALL of `tests/guard` and `tests/test_guard_*.py`, codemap and gitsafe tests. Time one hook call before/after and report. Commit `feat(guard): sharper code-map notes; decisions before shell-command edits`.

### Task 3: Re-clone warning, screens, README
**Files:** `backend/app/models.py` (`Project.root_commit`), the 0022 migration (extend), `backend/app/routers/projects.py`, `backend/app/worker.py` (`_spawn_handoff` area), `backend/app/health/checks.py`, frontend health page cards (tidying switch + preview + "Run now"; re-clone warning + "This is expected"), run notice text, `README.md`. Tests alongside.
- [ ] Failing tests for spec §2 screens, §5 and Review Focus 5; implement; backend + frontend tests + `npm run build`. Commits `feat(upkeep): re-clone warning and health cards` and `docs(readme): upkeep`.
