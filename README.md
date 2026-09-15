# Claude-Orchestrator

A self-hosted, **team orchestration layer for Claude Code** — run, stream, govern,
and audit Claude Code tasks from a web dashboard instead of one-off terminal
sessions. Where `claude` runs one agent locally, this runs *many* — with a queue,
isolation, project memory, scheduling, human approvals, MCP credential governance,
cost caps, security reviews, and reusable agent roles.

> **Status:** Phases 1–5 complete, plus agents, security reviews, cost tracking,
> reports, headless deploy, and CI. Remaining: Team & Auth. Full roadmap in
> `claude-orchestrator-features.md`.

---

## What it does

| Area | Capabilities |
|---|---|
| **Tasks** | Queue + isolated worker pool, live WebSocket streaming, cancel / retry / duplicate / delete, **follow-up threads** (resume a Claude session), input files, artifacts |
| **Projects & memory** | Projects mapped to repos, **Discover** existing Claude Code dirs, repo-backed runs, **context assembly** (instructions + living memory + relevant past summaries), auto-updated `memory.md` |
| **MCP governance** | Server registry (3 scopes), **AES-256-GCM credential vault**, tool policies (auto-approve / require-approval / **block**), connection testing + tool discovery, call observability |
| **Scheduling** | Cron schedules (presets + raw), next-run preview, pause/resume, run-now, run history, reusable **templates** + 6 automation presets, Slack notifications |
| **Approvals** | Human-in-the-loop **inbox**, **auto-gate critical-risk** tasks, blast-radius context, approve/reject + reason, bulk approve, timeout auto-reject |
| **Agents** | Reusable, **governed roles** — system prompt + model + budget + optional project — run under all guardrails |
| **Security** | **VAPT/STQC security reviews** (read-only), a **findings tracker** (severity, CWE, status lifecycle, recurrence), and **GitHub PR → auto-review** webhooks |
| **Cost & reliability** | Token/cost capture, **Usage dashboard**, per-project budgets + over-budget flags, **hard spend caps** (`--max-budget-usd`), **auto-retry** transient failures, fallback model |
| **Reports** | Download any task's result as **PDF or DOCX** |
| **Ops** | Headless auth (`ANTHROPIC_API_KEY`), readiness endpoint, `start.sh`, pytest suite + GitHub Actions CI |

---

## Architecture

```
frontend/  React + Vite + Ant Design + ECharts dashboard
backend/   FastAPI + async SQLAlchemy (SQLite) + in-process asyncio worker pool
```

- **Worker manager** spawns `claude --print --output-format stream-json` per task
  (in the project's repo, or an isolated sandbox), parses the JSONL event stream,
  and enforces concurrency / timeout / max-turns / spend caps. Transient failures
  (rate limit / overload / network) are auto-retried with exponential backoff.
- **Streaming** is an in-memory pub/sub broker over WebSocket; every event is also
  persisted so a reconnecting client replays what it missed.
- **No external services** — SQLite + an in-process queue/scheduler. Everything
  (worker pool, cron, MCP config, approvals) runs in one process.

---

## Quickstart

The simplest way to run the whole stack:

```bash
./start.sh          # sets up deps on first run, then backend :8200 + frontend :5200
```

`start.sh` frees the ports first (so stale processes can't pile up) and stops both
on Ctrl+C. Then open **http://localhost:5200**. Override ports with
`CO_BACKEND_PORT` / `CO_FRONTEND_PORT`.

<details>
<summary>Or run the two services manually</summary>

```bash
# Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                  # optional; defaults work
uvicorn app.main:app --host 0.0.0.0 --port 8200

# Frontend (new terminal)
cd frontend
npm install
VITE_PROXY_TARGET=http://localhost:8200 VITE_PORT=5200 npm run dev
```
</details>

**Prerequisites:** the `claude` CLI on your `PATH` (the worker shells out to it),
Python 3.10+, and Node 18+. Interactive API docs at http://localhost:8200/docs;
the dashboard header shows a live **Claude readiness** tag (`GET /system/claude`).

---

## Configuration (`backend/.env`, prefix `CO_`)

| Variable | Default | Purpose |
|---|---|---|
| `CO_DATABASE_URL` | `sqlite+aiosqlite:///./claude_orchestrator.db` | Task/event store |
| `CO_WORKSPACES_DIR` | `./workspaces` | Isolated per-task working dirs (resolved absolute) |
| `CO_CLAUDE_BIN` | `claude` | Claude Code CLI to spawn |
| `CO_DEFAULT_MODEL` | `sonnet` | Fallback default model (blank ⇒ CLI default) |
| `CO_WORKER_CONCURRENCY` | `3` | Max simultaneous tasks |
| `CO_TASK_TIMEOUT_SECONDS` | `1800` | Kill tasks exceeding this |
| `CO_CLAUDE_PERMISSION_MODE` | `bypassPermissions` | Permission mode passed to the CLI |
| `CO_DEFAULT_TASK_BUDGET_USD` | *(unset)* | Default per-task spend cap (`--max-budget-usd`) |
| `CO_FALLBACK_MODEL` | *(unset)* | Fallback model if the primary is unavailable |
| `CO_RETRY_MAX_ATTEMPTS` / `CO_RETRY_BACKOFF_SECONDS` | `2` / `30` | Auto-retry transient failures |
| `CO_GATE_CRITICAL_APPROVAL` | `true` | Force approval for critical-risk tasks (merge/deploy/delete) |
| `CO_GITHUB_WEBHOOK_SECRET` | *(unset)* | HMAC secret for the GitHub PR webhook |
| `CO_SECRET_KEY` | *(auto)* | AES-256 vault key (else generated to `.secret.key`) |
| `ANTHROPIC_API_KEY` | *(unset)* | Headless auth for the spawned `claude` |

**Headless auth.** The worker inherits the server's environment, so set
`ANTHROPIC_API_KEY` (or run `claude setup-token`) to authenticate `claude` with no
interactive login — check readiness at `GET /system/claude`.

**`bypassPermissions`.** Tasks run unattended (no human to answer CLI prompts), so
the default is `bypassPermissions`. Governance comes from the **approval inbox**
(critical tasks are auto-gated) and **MCP tool policies** (e.g. `merge_pull_request`
= Block for review-only). Tighten to `default`/`acceptEdits` for more CLI-level
guardrails.

---

## REST API

Full interactive docs at `/docs`. Highlights:

**Tasks**

| Method | Path | Description |
|---|---|---|
| `POST` / `GET` | `/tasks` | Create + queue / list (`?status=`, `?project_id=`, `?roots_only=`) |
| `GET` | `/tasks/{id}` · `/events` | Detail / full event log (`?after_seq=`) |
| `POST` | `/tasks/{id}/cancel` · `/retry` · `/duplicate` · `/followup` | Lifecycle actions |
| `DELETE` | `/tasks/{id}` | Delete a task (or thread); cleans sandbox workspace |
| `GET` | `/tasks/{id}/report?format=pdf\|docx` | Download the result as PDF / DOCX |
| `GET` | `/tasks/{id}/artifacts[/{path}]` | List / download output files |
| `GET` | `/tasks/stats` | Dashboard stats |
| `WS` | `/tasks/{id}/stream` | Live event stream (`?last_seq=` to resume) |

**Projects & memory** — `/projects` CRUD · `/projects/discover` · `/projects/import`
· `/projects/{id}/memory[/regenerate]` · `/projects/{id}/summaries` · `/projects/{id}/stats`

**MCP** — `/mcp/servers` CRUD · `/mcp/servers/{id}/test` · `/mcp/servers/{id}/policies`
· `/mcp/observability`

**Scheduling / templates / approvals** — `/schedules` CRUD + `/run` + `/runs` +
`/preview` · `/templates` + `/presets` + `/run` · `/approvals` + `/{id}/approve|reject`

**Agents** — `/agents` CRUD · `/agents/{id}/run` (with optional project/prompt override)

**Security & usage** — `/findings` + `/summary` + `PATCH /{id}` · `/usage/summary`
· `/usage/timeseries` · `/usage/by-project` · `/usage/by-model`

**Webhooks / system** — `POST /webhooks/github` · `GET /system/claude` · `GET /health`

```bash
curl -X POST localhost:8200/tasks -H 'Content-Type: application/json' -d '{
  "prompt": "Write a haiku about orchestration to haiku.txt",
  "model": "sonnet", "priority": "high", "max_turns": 10, "max_budget_usd": 0.5
}'
```

Streamed events are normalized to: `started`, `context`, `mcp`, `system`,
`text_output`, `tool_use`, `tool_result`, `retry`, `log`, `error`, `completed`.

---

## Security reviews (VAPT / STQC)

The **Security Review** button (Tasks tab) runs a **read-only** audit of a
project's branch — OWASP Top 10 / CWE Top 25 — and produces a findings report.
After each scan, findings are extracted (via `--json-schema` for reliable output)
into the **Security** tab: severity, CWE, `file:line`, status lifecycle
(open / fixed / accepted / false-positive), and **new-vs-recurring** tracking
across scans. Set a project's **GitHub repo** + **Auto-review PRs**, add a repo
webhook to `/webhooks/github`, and every PR triggers a review whose findings land
in the tracker automatically. (Needs the server reachable by GitHub — deploy it or
tunnel with smee.io / ngrok.)

---

## Agents

An **agent** is a reusable, governed role: a **system prompt**
(`--append-system-prompt`) + model + budget + max-turns + optional **default
project** (overridable at run time). Running one creates a task under all the usual
guardrails (auto-gating, cost caps, tool policies from the project's MCP scope). So
"run the *Security Auditor* on *Hydra*" is one action, and the same role works on
any project. Manage them in **Automation → Agents**.

**Starter agents** — one-click presets (`GET /agents/presets`): *Morning Brief*,
*Weekly Review*, *Security Auditor*. Click one under "Starter agents" to drop a
ready-made role into your workspace, then edit it.

**Schedules run agents.** A schedule can carry its own prompt *or* target an
agent (`agent_id`). When it targets an agent, each fire builds a task from the
agent's role, model, budget, and default project — so "every morning at 8am, run
the *Morning Brief* agent" is a single, governed automation. Set it up in
**Automation → Schedules** by picking an agent instead of typing a prompt.

---

## Personal automations (Life Dashboard)

The engine is general-purpose, so the same primitives — agents + schedules +
MCP connectors — power personal automations, not just dev work. The built-in
**Morning Brief** preset is a chief-of-staff role that summarizes your day; on a
daily schedule it becomes a Life Dashboard.

**Wire it up:**

1. **Add the agent** — Automation → Agents → *Morning Brief* (starter). Give it a
   **Personal** project and a small **budget cap** (e.g. `$0.50`) so a daily run
   can't run away.
2. **Connect data sources (MCP).** The brief needs read access to your calendar
   and mail. In **MCP**, add a server per source. Gmail and Google Calendar are
   available in this Claude session as managed connectors — for the orchestrator's
   own `claude` subprocess, register an MCP server (stdio command or HTTP URL) and
   store any token in the vault. Set tool policies to **auto-approve** the
   *read* tools (`list_events`, `search_email`…) and **block** anything that
   sends or deletes. Use **scope = user** to make a connector available to every
   task, or **scope = project** to limit it to *Personal*.
3. **Schedule it** — Automation → Schedules → New, cron `0 8 * * *`, and pick the
   *Morning Brief* **agent** (no prompt needed). Keep it **disabled** until the
   connectors test green, then enable.

Because it runs as a normal task, you get the brief as an artifact/report, cost
tracking, and (if you flag it) an approval gate — same guardrails as everything
else.

---

## Remote Ops (SSH troubleshooting)

Point an agent at a server to diagnose it over SSH. Because a project task runs
with its **cwd set to the project directory**, an `Infra` project holds a
`servers.yaml` inventory the agents read at run time to resolve a target.

**The safe split** (approvals are task-level — the CLI can't pause mid-run at one
command, so read-only and mutating work are separate agents):

- **Remote Diagnostics** — read-only. Resolves the named server from
  `servers.yaml`, SSHes in (`ssh -o BatchMode=yes -o ConnectTimeout=10 -o
  StrictHostKeyChecking=accept-new -i <pem> …`), runs only observational
  commands, and reports *symptoms → evidence → root cause → proposed fix*.
- **Remote Remediation** — applies an **approved** fix. Marked
  **`requires_approval`**, so *every* run lands in Approvals first; you review the
  exact plan before it touches the server, then approve.

`requires_approval` is an agent-level flag (Agents form → "Approval-gate every
run") — any agent that changes state can use it.

**Setup:** create an `Infra` project; drop `docs/infra/servers.example.yaml` into
its directory as `servers.yaml` and fill in real hosts; put PEM keys on the host
(`~/.ssh/…`, `chmod 600`) — only their paths go in the inventory, never the keys.
See [`docs/infra/RUNBOOK.md`](docs/infra/RUNBOOK.md) for the diagnostic playbook.
Read-only is enforced by the agent's prompt; for a hard guarantee use a
dedicated read-only SSH user with no sudo on the server.

---

## Testing

```bash
cd backend && python -m pytest -q      # 20 tests: units + API integration (mock claude)
```

Unit tests cover the vault, risk classification, transient-failure detection,
finding fingerprints, prompts, webhook signatures, and report generation.
Integration tests exercise the task lifecycle, approval auto-gate/approve/reject,
reports, usage, agents, schedules-run-agents, and the GitHub webhook — all against a mock `claude`
binary. **GitHub Actions** (`.github/workflows/ci.yml`) runs the backend suite and
the frontend build on every push/PR.

---

## Scope notes & limitations

- **Approval granularity** is at the **task level** (approve before the run
  starts). True per-tool-call approval (pause at each `merge`) needs the Claude
  **Agent SDK**'s `canUseTool`, which the `claude --print` CLI doesn't expose — so
  for per-action safety, gate write tools via MCP policy (e.g. block merge).
- **MCP** is injected per task (stateless), not run as long-lived shared daemons;
  HTTP-transport health checks aren't validated yet.
- **Deferred:** Team & Auth (login/roles/audit), `docker-compose`, `decisions.md`,
  auto codebase overviews, full-text search, and per-project task serialization.

---

## Operations

Run with **`./start.sh`** — it kills whatever is on the ports first, so background
processes can't accumulate (a stack of stale schedulers is the usual cause of
duplicate scheduled runs and API rate-limit contention). For a durable deployment,
run the backend under a process manager (systemd) or a container with
`ANTHROPIC_API_KEY` set.
