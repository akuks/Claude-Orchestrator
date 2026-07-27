# Claude-Orchestrator

A web-based team orchestration layer for Claude Code — run, stream, and manage
Claude Code tasks from a dashboard instead of one-off terminal sessions.

> **Status: Phases 1–4 complete.**
> **Phase 1 (Core Engine):** task management, an isolated Claude Code worker
> pool, live WebSocket streaming, a REST API, and a React dashboard.
> **Phase 2 (MCP Management):** MCP server registry with scopes, an AES-256-GCM
> credential vault, connection testing + tool discovery, per-tool policies
> (auto-approve / require-approval / block), per-task config injection, and call
> observability.
> **Phase 3 (Project Memory):** projects mapped to directories with per-project
> instructions & default model, tasks that run in the project's repo, a
> context-assembly pipeline (instructions + living memory + relevant past-task
> summaries within a token budget), and auto-updated living memory.
> **Phase 4 (Scheduling & Automation):** cron schedules with an in-process
> scheduler loop, next-run preview, pause/resume, run-now and run history;
> reusable task templates + pre-built automation presets; and Slack-webhook
> notifications per schedule. Later phases (approvals, team/auth, CLI) are in
> `claude-orchestrator-features.md`.

---

## Architecture

```
frontend/  React + Vite + Ant Design + ECharts dashboard
backend/   FastAPI + async SQLAlchemy (SQLite) + in-process asyncio worker pool
```

- **Worker manager** spawns `claude --print --output-format stream-json` in an
  isolated per-task workspace, parses the JSONL event stream, enforces a
  concurrency limit / per-task timeout / max-turns, and collects output files.
- **Streaming** is an in-memory pub/sub broker over WebSocket, with every event
  also persisted to the DB so a reconnecting client can replay what it missed.
- **No external services** for Phase 1 — SQLite + an in-memory queue (the
  spec's single-user fallback). Redis/Postgres/Celery come with team mode later.

---

## Quickstart

### 1. Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # optional — defaults work out of the box
uvicorn app.main:app --reload # serves on http://localhost:8000
```

The `claude` CLI must be installed and on your `PATH` (the worker shells out to
it). Interactive API docs are at http://localhost:8000/docs.

### 2. Frontend

```bash
cd frontend
npm install
npm run dev                   # serves on http://localhost:5173, proxies to :8000
```

Open http://localhost:5173 and click **New Task**.

> Point the dashboard at a backend on a different port with
> `VITE_PROXY_TARGET=http://localhost:8077 npm run dev`.

---

## Configuration (backend `.env`, prefix `CO_`)

| Variable | Default | Purpose |
|---|---|---|
| `CO_DATABASE_URL` | `sqlite+aiosqlite:///./claude_orchestrator.db` | Task/event store |
| `CO_WORKSPACES_DIR` | `./workspaces` | Isolated per-task working dirs |
| `CO_CLAUDE_BIN` | `claude` | Claude Code CLI to spawn |
| `CO_DEFAULT_MODEL` | `sonnet` | Default model |
| `CO_DEFAULT_MAX_TURNS` | `25` | Per-task turn cap |
| `CO_WORKER_CONCURRENCY` | `3` | Max simultaneous tasks |
| `CO_TASK_TIMEOUT_SECONDS` | `1800` | Kill tasks exceeding this |
| `CO_CLAUDE_PERMISSION_MODE` | `bypassPermissions` | Permission mode passed to the CLI |

> **Note on `bypassPermissions`:** tasks run autonomously in an isolated
> workspace directory, so no human is present to answer permission prompts.
> Tighten to `acceptEdits` or `default` for more guardrails — a full approval
> workflow arrives in Phase 5.

---

## REST API

| Method | Path | Description |
|---|---|---|
| `POST` | `/tasks` | Create + queue a task |
| `GET` | `/tasks` | List tasks (`?status=`, `?project=`, `?limit=`, `?offset=`) |
| `GET` | `/tasks/{id}` | Task detail |
| `GET` | `/tasks/{id}/events` | Full event log (`?after_seq=`) |
| `POST` | `/tasks/{id}/cancel` | Cancel a queued/running task |
| `POST` | `/tasks/{id}/retry` | Re-run a finished task |
| `POST` | `/tasks/{id}/duplicate` | Clone a task into a fresh run |
| `POST` | `/tasks/{id}/followup` | Continue the task's Claude session with a new prompt |
| `GET` | `/tasks/{id}/artifacts` | List output files |
| `GET` | `/tasks/{id}/artifacts/{path}` | Download an output file |
| `GET` | `/tasks/stats` | Dashboard stats |
| `WS` | `/tasks/{id}/stream` | Live event stream (`?last_seq=` to resume) |

### Create a task

```bash
curl -X POST localhost:8000/tasks -H 'Content-Type: application/json' -d '{
  "prompt": "Write a haiku about orchestration to haiku.txt",
  "model": "sonnet",
  "priority": "high",
  "max_turns": 10
}'
```

Streamed events are normalized to: `started`, `mcp`, `system`, `text_output`,
`tool_use`, `tool_result`, `log`, `error`, `completed`.

### MCP management API (Phase 2)

| Method | Path | Description |
|---|---|---|
| `POST` | `/mcp/servers` | Register a server (stdio/http; `env`/`headers` encrypted) |
| `GET` | `/mcp/servers` | List servers (secrets never returned) |
| `PATCH` | `/mcp/servers/{id}` | Update config / secrets / enabled |
| `DELETE` | `/mcp/servers/{id}` | Remove a server |
| `POST` | `/mcp/servers/{id}/test` | Probe connection, discover tools, seed policies |
| `GET`/`PUT` | `/mcp/servers/{id}/policies` | Read / set per-tool policies |
| `GET` | `/mcp/observability` | Call counts, failure rate, top tools (`?days=`) |

MCP servers are matched to a task by **scope** (team/user always; project-scoped
only when the task's project matches), written to a per-task `.mcp-config.json`
with decrypted credentials, and passed to the CLI via `--mcp-config`
`--strict-mcp-config`, with `--allowedTools`/`--disallowedTools` derived from
policy. Secrets are stored only as **AES-256-GCM** blobs; the vault key lives in
`.secret.key` (gitignored) or `CO_SECRET_KEY`.

### Scheduling & templates API (Phase 4)

| Method | Path | Description |
|---|---|---|
| `GET` | `/schedules/preview?cron=` | Next run times for a cron expression |
| `POST`/`GET` | `/schedules` | Create / list schedules |
| `PATCH`/`DELETE` | `/schedules/{id}` | Edit (pause/resume via `enabled`) / delete |
| `POST` | `/schedules/{id}/run` | Fire a schedule immediately |
| `GET` | `/schedules/{id}/runs` | Run history for a schedule |
| `GET` | `/templates/presets` | Pre-built automation templates |
| `POST`/`GET` | `/templates` | Create / list templates |
| `POST` | `/templates/{id}/run` | Run a template as a task |

---

## Phase 1 feature coverage

- **Task management** — create, statuses (`queued → running → completed/failed/cancelled`),
  priority (low/normal/high/urgent), tags, cancel, retry, duplicate, attach input files.
- **Follow-up threads** — continue a finished task with a new prompt; the follow-up
  resumes the parent's Claude session (`--resume`) in the same workspace, so full
  context (files, prior reasoning) carries across steps. A thread shows as a single
  row in the feed (with a step count); its steps are navigable in the detail drawer.
- **Worker manager** — isolated workspaces, per-task model/max-turns, concurrency
  limit, timeout, CLAUDE.md injection, stdout/stderr/exit capture, artifact collection.
- **Live streaming** — WebSocket stream + terminal viewer, reconnect with history replay.
- **REST API** — full task lifecycle + artifacts + stats.
- **Dashboard** — task feed, create-task form, live detail drawer, artifact browser, stats.

## Phase 2 feature coverage

- **MCP registry** — stdio + HTTP servers, three scopes (team / user / project),
  add/edit/remove/enable, matched into tasks by scope.
- **Credential vault** — env vars & HTTP headers stored only as AES-256-GCM blobs;
  never returned by the API or written in plaintext.
- **Connection testing** — real MCP stdio handshake, tool discovery, live status
  (healthy / disconnected) plus a periodic health-check loop.
- **Tool policies** — auto read/write classification with defaults (auto-approve
  reads, require-approval writes, block dangerous); editable per tool and enforced
  at task time via allow/deny tool lists.
- **Observability** — MCP calls recorded from the task stream with error
  attribution; call counts, failure rate, and top tools in the dashboard.

## Phase 3 feature coverage

- **Projects** — CRUD, mapped to a directory on disk; per-project instructions
  (injected, not written into the repo), default model, archive; a project
  switcher scopes the feed and new tasks.
- **Discover** — scan `~/.claude/projects` (reading each session's real `cwd`) to
  find every directory you've run Claude Code in, and bulk-add them as projects;
  the orchestrator's own sandbox/temp dirs are filtered out.
- **Repo-backed tasks** — a project task runs in the project's directory (acting on
  the real codebase) and inherits its default model.
- **Context assembly** — before each project task, a preamble of project
  instructions + living memory + the most relevant past-task summaries (keyword +
  recency scored) is prepended within a token budget; the assembly is logged as a
  `context` event.
- **Living memory** — after each successful project task, a cheap background Claude
  call writes a one-paragraph task summary and folds it into the project's
  `memory.md`; editable and regenerable from the dashboard.

## Phase 4 feature coverage

- **Cron schedules** — create recurring tasks (raw cron or common presets), with a
  next-run preview, pause/resume, edit, delete, run-now, and per-schedule run
  history. An in-process loop fires due schedules and advances their next run.
- **Task templates** — save reusable task configs (prompt + model + project +
  settings), run them on demand, plus six pre-built automation presets (morning
  brief, PR digest, standup, code-quality, dependency check, changelog).
- **Notifications** — per-schedule Slack incoming-webhook alerts (never /
  on-failure / always).

> **Scope notes.** Phase 2 injects a generated MCP config per task (stateless)
> rather than running long-lived shared daemons; HTTP-transport health checks
> are not yet validated (config is still injected); full approval routing for
> `require_approval` tools lands in Phase 5. Phase 3 implements project management,
> context assembly, and living memory; auto-extracted `decisions.md`, generated
> codebase overviews, and full-text conversation search are deferred. Concurrent
> tasks in the same project directory are not yet serialized.
