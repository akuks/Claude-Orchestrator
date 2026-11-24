# Connected Tools Neutral Folder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every stdio connected tool a task starts runs from a private, empty folder, not from the project folder. A per-tool switch restores the old behaviour.

**Architecture:** A stdlib launcher, `backend/app/mcp_launch.py`, does `chdir` + `exec`. `mcp_manager.build_config` wraps stdio entries with it. `prepare_for_task` creates the per-task, per-tool folder. Migration 0019 adds `mcp_servers.start_in_project`.

**Tech Stack:** FastAPI, async SQLAlchemy, Alembic, pytest (SQLite), React 18 + antd 5 + vitest.

**Spec:** `docs/superpowers/specs/2026-11-24-mcp-neutral-folder-design.md`

## Global Constraints
- **Commands.**
  - Backend: from `backend/`, run `/Users/ashutosh/Projects/Work/Claude-Orchestrator/backend/.venv/bin/python -m pytest -o addopts="" -q <paths>`.
  - Frontend: from `frontend/`, run `npx vitest run <files>`, then `npm run build`.
- **Hard rules.**
  - Do NOT connect to any Postgres server on this machine. Use the default temp SQLite only.
  - Never run the real `claude`, `npx` or any real MCP server. Tests use small scripts they write themselves.
  - Do not use ports 8200/5200.
  - Never use `monkeypatch.setattr(app.state, …)`.
- **Commits.** Commit folder by folder, with no trailers of any kind.
- **Migration.** The revision id is `"0019"` and `down_revision` is `"0018"`. The boolean gets a false server default.
- **Launcher argv (exact).** `[sys.executable, "-I", <abs path of app/mcp_launch.py>, <neutral_dir>, "--", <command>, *args]`.
- **Launcher behaviour.**
  - It runs `chdir`, sets `PWD` to the neutral folder, removes `INIT_CWD` and `OLDPWD`, then calls `os.execvp`.
  - On any failure it prints one plain line to stderr and exits with code 127.
  - It is stdlib-only.
  - It never opens a file in the project.
- **Neutral folder.** `settings.workspaces_dir / "_mcp_configs" / <task_id> / "cwd" / <server prefix>`, with mode 0700. It is created empty for each task.
- **Env.** The existing env is kept, plus `CO_PROJECT_DIR` when the task has a project folder.
- **Switch.**
  - Column: `start_in_project`, default false.
  - Label (exact): "Start in the project folder".
  - Help text (exact): "Only for tools that must start inside the project. The project's own files can then change what the tool runs."
- **Relative command refusal (exact).** "A command that starts with ./ needs 'Start in the project folder'".
- **Unchanged.** `http` servers and the server-side probe stay as they are.

## Review Focus
1. **A tool whose command is not on PATH, or whose neutral folder vanished.** It must fail with a clear one-line error and exit code 127. It must not hang (Task 1).
2. **Server names with odd characters used as folder names.** Use the same sanitised prefix as tool names, with no path traversal (Task 1).
3. **Old rows (pre-0019).** They read as false, so they get the launcher (Task 1).
4. **Per-task folders.** They must not leak. They are removed wherever the task's MCP config folder is removed today (Task 1).
5. **The switch.** When it is on, the config entry is byte-identical to today's (Task 1).

---

### Task 1: Launcher, config wrapping, per-task folders, migration 0019 and validation

**Files:**
- Create: `backend/app/mcp_launch.py`, `backend/migrations/versions/0019_mcp_cwd.py`
- Modify:
  - `backend/app/mcp_manager.py` (`build_config(servers, *, task_cwd_root: Path | None, project_dir: str | None)`, `prepare_for_task`)
  - `backend/app/models.py`
  - `backend/app/schemas.py`
  - `backend/app/routers/mcp.py` (accept and validate the switch, and refuse relative commands)
  - `backend/app/worker.py` (pass the project folder; make sure the per-task folder is cleaned where `_mcp_configs/<task_id>` is cleaned)
  - `backend/app/health/checks.py` (count in the "Connected tools" details and summary)
- Test: `backend/tests/test_mcp_launch.py`, `backend/tests/test_mcp_neutral_cwd.py`, `backend/tests/test_migrations.py`

**Interfaces:**
- `mcp_launch.main(argv) -> NoReturn`. It is also runnable as a script.
- `mcp_manager.launcher_path() -> str`.
- `mcp_manager.neutral_dir(task_cwd_root, server) -> Path`.
- `build_config` keeps working when called with the old single argument, as the probe and the tests call it. In that case it does no wrapping.

- [ ] **Step 1: Write failing tests.**
  - **Launcher**, run as a real subprocess with `sys.executable -I`. Use a helper script that prints `os.getcwd()` and the env.
    - The cwd is the neutral folder.
    - `PWD` is set. `INIT_CWD` and `OLDPWD` are gone.
    - The args are passed through unchanged, including ones with spaces and `--`.
    - A missing folder exits with 127 and one stderr line.
    - A missing command exits with 127.
    - A `sitecustomize.py` or a same-named module in the PROJECT folder is not imported.
  - **Config.**
    - A stdio server with the switch off gives the exact launcher argv and the env with `CO_PROJECT_DIR`.
    - With the switch on, the entry is byte-identical to the old shape.
    - An http server is unchanged.
    - The folder is created with mode 0700 and is empty.
    - A server name with `../` or spaces gives a safe folder name inside the root.
    - Two tasks get different folders.
  - **Cleanup.** The folder is removed with the task's config folder.
  - **API.**
    - Only an admin can set the switch.
    - A `./bin/x` command is refused with the exact text unless the switch is on.
    - A bare `npx` is fine.
  - **Health.** The count appears in the summary.
  - **Migration.** It upgrades and downgrades, and old rows read false.
- [ ] **Step 2: Implement and run the tests,** plus the existing MCP tests (grep for `mcp` in the tests) and `tests/test_health_checks.py`. Commit with `fix(mcp): start connected tools from a private empty folder, not the project`.

### Task 2: The switch in the tool form, and the README

**Files:**
- Modify: `frontend/src/components/McpServerModal.jsx`, `frontend/src/components/McpRegistry.jsx` (a small "Starts in the project folder" tag on tools with the switch on), `frontend/src/api.js` if needed, `README.md`
- Test: `frontend/src/components/McpServerModal.test.jsx`

**Interfaces:**
- The switch sits under a collapsed "More options" in the form, for stdio tools only. It carries the exact label and help text.
- The server's refusal text shows under the command field.

- [ ] **Step 1: Write failing tests.**
  - The switch is hidden for http tools.
  - The payload carries `start_in_project` only when it is on.
  - The refusal text is shown.
  - The tag is shown on the list.
- [ ] **Step 2: Implement, then run the tests and `npm run build`.** README: a short section, "Where connected tools start". It covers:
  - the problem in plain words;
  - what changed;
  - the switch;
  - what is not covered (the spec's list);
  - a statement that this has not been run against the real CLI.

  Also correct the Code map section's "To know" line about task-started tools.

  Commit with `feat(ui): start-in-project switch for connected tools` and `docs(readme): where connected tools start`.
