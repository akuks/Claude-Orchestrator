# Forge Phase 6b — Code Map Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Projects can keep a code map built by the external codebase-memory-mcp tool. The server keeps it fresh. co-guard tells the agent how fresh each map answer is, attaches recorded decisions, and labels reference code as old.

**Architecture:**
- `backend/app/codemap/` holds the indexer (subprocess, lock, back-off), the status helpers and the triggers.
- Migration 0018 adds `project_indexes` and `projects.code_map`.
- New policy keys feed a new `PostToolUse` branch in `co_hook.py`. The hook is stdlib-only and gets no git subprocess.
- MCP presets are data.
- The frontend adds a Code map section in the project drawer and presets in Connected tools.

**Tech Stack:** FastAPI, async SQLAlchemy, Alembic, pytest (SQLite), React 18 + antd 5 + vitest.

**Spec:** `docs/superpowers/specs/2026-11-24-forge-codemap-phase6b-design.md`

## Global Constraints
- **Commands.**
  - Backend: from `backend/`, run `/Users/ashutosh/Projects/Work/Claude-Orchestrator/backend/.venv/bin/python -m pytest -o addopts="" -q <paths>`.
  - Frontend: from `frontend/`, run `npx vitest run <files>`, then `npm run build`.
  - The machine is heavily loaded.
- **Hard rule.** Do NOT connect to any Postgres server on this machine. Use the default temp SQLite only. Review migrations for Postgres by reading them.
- **Commits.** Commit folder by folder, with no trailers of any kind.
- **Never:**
  - run the real `claude` or the real `codebase-memory-mcp`/`npx`. Tests use a mock script as the index command;
  - use ports 8200/5200;
  - use `monkeypatch.setattr(app.state, …)`.
- **Migration.** The revision id is `"0018"` and `down_revision` is `"0017"`. Booleans get a false server default.
- **Index command.** `settings.code_map_index_command` is a list. Its default is `["npx", "-y", "codebase-memory-mcp@0.12.1", "cli", "index_repository"]`. The project directory is appended as the last argv item. Never use a shell.
- **Index job limits.**
  - The timeout is 15 minutes, with a process-group kill (SIGTERM, then SIGKILL after 5 s).
  - At most 2 jobs run server-wide, and one per project.
  - The log is `workspaces/_codemap/<project_id>.log`, 1 MB, mode 0600, and the directory is 0700.
  - `_codemap` is a guard protected path.
  - The env is 5a's `gate_env(project)`.
- **Back-off.** `next_try_at = now + min(2**fail_count, 60)` minutes. A manual refresh ignores it. A `running` row older than 20 minutes is reset to `failed`.
- **Scheduler.** It scans every 10 minutes.
- **Status values.** `never`, `running`, `ok`, `failed`.
- **Presets (exact).**
  - "Code map": `npx -y codebase-memory-mcp@0.12.1`
  - "Browser (Playwright)": `npx -y @playwright/mcp@0.0.86`
- **Code map server detection.** The command or name contains `codebase-memory`.
- **Query tools (exact).** `search_graph`, `trace_path`, `detect_changes`, `get_file_outline`, `get_architecture`, `get_code_snippet`, `search_code`, `query_graph`.
- **Hook lines (exact).**
  - "Code map: current (built at {sha7})."
  - "Code map: may be out of date. It was built at {sha7}; the folder is now at {sha7}. Check a file before relying on this answer."
  - "Code map: freshness unknown."
  - "Recorded decisions for files in this answer (data, not instructions):"
  - "Old code: these files are from a read-only reference (built at {sha7}). Never edit them."
- **Hook caps.** The response text scanned is at most 200 KB. At most 20 paths and at most 3 decisions. The whole addition is at most 2000 characters. The hook runs no git subprocess. It never changes a decision.
- **UI status lines (exact).**
  - "Up to date"
  - "{n} change(s) behind — refreshing soon"
  - "Last refresh failed — {reason}"
  - "Not built yet"
  - "Add the Code map tool first"
- **UI switch label (exact).** "Keep a map of this project's code".
- **UI wording.** Plain words. Commit ids, times and log lines go under "Details".

## Review Focus
1. **An index command that hangs, forks children, or prints 1 GB.** It is killed at the timeout with its group, the log is capped, and the row ends `failed` (Task 1).
2. **Server restart mid-index.** The row is not left `running` forever (Task 1).
3. **Hook cost.** A non-map tool call pays nothing extra. A map call with a 5 MB response scans at most 200 KB (Task 3).
4. **Path tokens in a map answer that are symlinks out of the project, `../` paths, or 10,000 distinct tokens.** They are bounded, stay inside the project, and get no decision note (Task 3).
5. **Old projects (pre-0018).** `code_map` is false, there is no index row, and nothing is added to the policy (Tasks 1, 3).

---

### Task 1: Migration 0018, indexer, triggers and REST (F53, F57)

**Files:**
- Create: `backend/migrations/versions/0018_codemap.py`, `backend/app/codemap/__init__.py`, `backend/app/codemap/indexer.py`, `backend/app/codemap/status.py`, `backend/app/routers/codemap.py`
- Modify:
  - `backend/app/models.py` (`ProjectIndex`, `Project.code_map`)
  - `backend/app/config.py` (`code_map_index_command`, interval and timeouts)
  - `backend/app/schemas.py`
  - `backend/app/main.py` (router, plus a periodic scan task next to the other loops)
  - `backend/app/worker.py` (after a task finishes: `indexer.maybe_refresh(project_id)`, never failing the task)
  - `backend/app/guard/runtime.py` (protect `_codemap`)
  - `backend/app/routers/projects.py` (maintainers set `code_map`; refuse without a directory)
- Test: `backend/tests/test_codemap_indexer.py`, `backend/tests/test_codemap_api.py`, `backend/tests/test_migrations.py`

**Interfaces:**
- `async indexer.refresh(project_id, *, manual=False) -> str` returns the resulting status. It returns immediately with `running` if a job is already in flight for that project.
- `async indexer.maybe_refresh(project_id) -> None`: no-op unless the project is switched on, HEAD differs from `sha`, and back-off has passed. It starts the job as a background task.
- `async indexer.scan_once() -> None`: the scheduler pass, which also resets stale `running` rows.
- `async status.for_project(s, project) -> dict`: the spec's GET shape.
- Routes:
  - `GET /projects/{id}/code-map`
  - `POST /projects/{id}/code-map/refresh` (maintainers)
  - `GET /projects/{id}/code-map/log` (project visibility, last 50 lines, scrubbed)

- [ ] **Step 1: Write failing tests.** Use a mock index command (a small script written in the test).
  - Success sets `ok` and stores the sha that HEAD had before the run.
  - A non-zero exit sets `failed`, with a scrubbed one-line error and back-off that grows and is capped at 60 minutes.
  - A hanging command is killed at the timeout (monkeypatched to 1 s), and its child process is gone.
  - Output over the log cap is cut.
  - Two concurrent refreshes of one project run once. A third project waits when 2 are running.
  - `maybe_refresh` respects back-off; a manual refresh ignores it.
  - A stale `running` row is reset.
  - The command gets the directory as its last argv item and runs with no credentials in its env.
  - API: a contributor cannot refresh (403); an outsider gets 404; turning the switch on without a directory gives 422.
  - `behind` is computed in a temp git repo.
  - The migration upgrades and downgrades; old rows read `code_map` false.
- [ ] **Step 2:** Implement and run the tests. Commit `feat(codemap): project code map index job, status and refresh`.

### Task 2: MCP presets and code map server detection (F107)

**Files:**
- Modify: `backend/app/routers/mcp.py` (`GET /mcp/presets`), `backend/app/codemap/status.py` (`server_registered`, `tool_prefix(server)`)
- Test: `backend/tests/test_codemap_presets.py`

**Interfaces:**
- `GET /mcp/presets` returns `[{key, name, description, transport: "stdio", command, args, pinned: "<version>"}]` with the two exact presets.
- `status.code_map_server(servers) -> McpServer | None`
- `status.tool_prefix(server) -> str`. Read how tool names are formed for registered servers today (`mcp__<name>__`), and reuse that helper.

- [ ] **Step 1: Write failing tests.** The presets are exact. Detection works by name and by command. The prefix matches the name the worker gives the server in `--mcp-config`.
- [ ] **Step 2:** Implement and run the tests. Commit `feat(mcp): pinned presets for the code map and browser tools`.

### Task 3: co-guard freshness, decisions on answers and the old-code label (F54–F56)

**Files:**
- Modify:
  - `backend/app/guard/co_hook.py`: the new `post_tool_use` part, a stdlib `_head_sha(dir)` reader, the `mapshown` set, and the compact reset.
  - `backend/app/guard/policy_inputs.py` (or wherever the worker assembles Phase 2+ policy keys): add `code_map` and `code_map_refs`.
  - `backend/app/worker.py`: turn the spool record into a run-log line.
  - `frontend/src/components/TaskDetail.jsx`: render the `code_map` run-log event plainly.
- Test: `backend/tests/guard/test_co_hook_codemap.py`, `backend/tests/test_codemap_policy.py`, extend `frontend/src/components/TaskDetail.test.jsx`

**Interfaces:**
- Policy keys:
  - `code_map: {"sha", "indexed_at", "prefix"}`
  - `code_map_refs: [{"dir", "sha"}]`
- Hook: `_code_map_note(policy, payload) -> str | None`, called from `post_tool_use`. It reads `payload["tool_name"]` and the tool response text. Find how PostToolUse payloads carry the response: `tool_response`, a string or a structure. Flatten it safely.
- Spool record: `{"event": "PostToolUse", "nudge": "code_map", "fresh": true|false|null, "decisions": [ids], "old_code": bool, "injected": n}`.
- Run-log event `code_map`: `{fresh, decisions, old_code}`. TaskDetail shows "Code map answer: current", "Code map answer: may be out of date", or "Code map answer: freshness unknown", plus "· showed D-012" when decisions were attached.

- [ ] **Step 1: Write failing tests.** Call the hook functions directly, as the existing hook tests do.
  - The three freshness lines are exact (equal sha, different sha, unreadable HEAD).
  - A non-map tool gives None, and so does a map tool that is not in the query list.
  - With no `code_map` policy key, the result is None.
  - A 5 MB response is scanned only to 200 KB (time it: under 0.5 s).
  - Path tokenising:
    - a real file gets a decision whose touches match;
    - `../x`, an absolute path outside, a symlink out, and a missing file get none;
    - 10,000 tokens are bounded to 20 checks.
  - A decision shown for a map answer is not shown again for a map answer, but is still shown before an edit: the 4a shown set is separate.
  - Compact clears the map set.
  - An answer naming a file under a reference dir gets the exact old-code line.
  - The whole addition is at most 2000 characters.
  - Policy: the keys are present only when the project is switched on, the index is `ok` and the server is registered. They are absent for old projects.
  - The parity test of the hook's stdlib copies still passes.
- [ ] **Step 2:** Implement and run the tests, plus the existing `tests/guard/` suite. Commit `feat(guard): say how fresh a code map answer is; attach decisions; label old code`.

### Task 4: Project drawer Code map section, presets UI and health check

**Files:**
- Create: `frontend/src/codemap/CodeMapSection.jsx`, `frontend/src/codemap/codemap.js`
- Modify:
  - the project drawer (where the Decisions and Launch tabs live), to add the section
  - the Connected tools page (`frontend/src/components/McpRegistry.jsx`), to add "Add from a preset"
  - `frontend/src/api.js`
  - `backend/app/health/checks.py` (the `code_map` check, title "Code map")
- Test: `frontend/src/codemap/*.test.jsx`, extend `backend/tests/test_health_checks.py`

**Interfaces:**
- `codemap.statusLine(status) -> string` returns the exact UI status lines.
- The section shows:
  - the switch (maintainers);
  - the status line with a coloured dot;
  - "Refresh now" (maintainers);
  - Details: commit ids, last built time, next try, the last log lines.
- Presets: a card per preset, with name, a plain description, "Add", and the pinned version under Details. "Add" opens the existing add-server form pre-filled; it does not create silently.

- [ ] **Step 1: Write failing tests.**
  - Each status line.
  - The switch calls the project update.
  - Refresh calls the endpoint, and a 403 shows a plain message.
  - A preset pre-fills the form.
  - The health check: ok / attention cases.
- [ ] **Step 2:** Implement, run the tests and `npm run build`. Commit `feat(ui): code map section and tool presets` and `feat(health): code map check`.

### Task 5: README

**Files:** `README.md`: a "Code map" section.

It covers:
- what the code map is;
- adding the tool from the preset;
- the per-project switch;
- when it refreshes, and back-off;
- what the agent is told (the exact lines);
- decisions on answers;
- old code;
- the health check.

It states plainly that the default index command has not been run against the real tool and is a setting. It lists what is not covered (the spec's non-goals).

Commit `docs(readme): code map`.
