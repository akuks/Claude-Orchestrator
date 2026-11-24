# Forge port — Phase 6b: code map — design

Program: port every jspark-forge feature into the orchestrator. Inventory: `docs/research/jspark-forge-feature-
inventory.md` F53–F57 (§3 rows of the same ids); `-delta-v0.5-v0.6.md` F107 (pinned engine presets), F108 (engine
status on the health page). 6c follows: database tab (F58), recommended panel (F72/F95), task outputs list (F105).

Owner's standing UI rule (memory `plain-language-ui`): plain words, structure shown visually, technical strings
under "Details". Subagents never connect to the local Postgres server (memory `no-local-postgres`).

## Intent
A "code map" is an index of a project's code that an agent can query ("where is this function used?") instead of
reading many files. Forge does not build one; it uses the external tool **codebase-memory-mcp** and adds the parts
that make it trustworthy: it keeps the index fresh, tells the agent **how fresh every answer is**, attaches the
project's **recorded decisions** to the files an answer names, and labels answers from read-only reference code as
**old code**. 6b brings that, server-side.

## Decisions (owner: proceed autonomously; these are the designer's calls)

### The tool and its presets (F53, F107)
- The orchestrator does not bundle the tool. Admins add it from a **preset** in the Connected tools (MCP) page:
  "Code map" = `npx -y codebase-memory-mcp@0.12.1` (stdio), pinned. A second pinned preset: "Browser (Playwright)"
  = `npx -y @playwright/mcp@0.0.86`. Presets are data (`GET /mcp/presets`); nothing installs by itself.
- A registered server counts as *the code map server* when its command or name contains `codebase-memory`
  (same detection forge uses for the tool prefix).

### Per-project switch and index record (F53, F57)
- `projects.code_map` (bool, default false), set by maintainers. Only projects with a directory can turn it on.
- `ProjectIndex` (table `project_indexes`, migration **0018**): `project_id` (PK, FK cascade), `sha` (String(64),
  nullable — the commit the index was built at), `indexed_at`, `status` (`never` | `running` | `ok` | `failed`),
  `error` (String(500), nullable, scrubbed), `fail_count` int, `next_try_at` (nullable), `started_at` (nullable).
- **Index job** (`app/codemap/indexer.py`): runs `settings.code_map_index_command` (a list; default
  `["npx", "-y", "codebase-memory-mcp@0.12.1", "cli", "index_repository"]`) with the project directory appended as
  the last argument, argv only, in the gate-style clean environment (5a `gate_env`: no credentials, private HOME),
  cwd = the project directory, a 15-minute timeout that kills the process group, output to a capped log
  (`workspaces/_codemap/<project_id>.log`, 1 MB, 0600). One job per project at a time (per-project asyncio lock);
  at most 2 jobs at once server-wide. Exit 0 → `ok`, `sha` = the directory's HEAD read **before** the run started;
  otherwise `failed` with a scrubbed one-line error and back-off: `next_try_at` = now + min(2^fail_count, 60) min.
- **When it runs:** (a) a maintainer presses "Refresh now" (`POST /projects/{id}/code-map/refresh`, ignores
  back-off); (b) after a task of that project finishes and the directory's HEAD differs from `sha`; (c) the
  scheduler loop, every 10 minutes, for switched-on projects whose HEAD differs from `sha` and whose back-off has
  passed. A job stuck `running` for over 20 minutes (server restart) is reset to `failed`.
- The default command is **unverified** against the real tool (the port cannot run it); the command is a setting
  so an admin can correct it, and the health page shows the last error plainly.
- `GET /projects/{id}/code-map` (project visibility): `{enabled, status, sha, indexed_at, behind, error,
  next_try_at, server_registered}`. `behind` = commits between `sha` and HEAD via gitsafe `rev-list --count
  <sha>..HEAD` (already allowed), null when unknown.

### Freshness on every map answer (F54)
- At task start the worker adds to the policy file `code_map: {"sha": "<index sha>", "indexed_at": iso,
  "prefix": "mcp__<server>__"}` for a switched-on project with an `ok` index and a registered code map server.
- co-guard `PostToolUse` for a tool whose name starts with that prefix and whose tool name is one of forge's query
  tools (`search_graph`, `trace_path`, `detect_changes`, `get_file_outline`, `get_architecture`,
  `get_code_snippet`, `search_code`, `query_graph`): read the project's current commit **without running git**
  (read `.git/HEAD` and the ref file or `packed-refs`, bounded, no-follow — the same reader as 6a's
  `current_sha_fast`, copied into the hook because it must stay stdlib-only) and add one line as
  `additionalContext`:
  - equal → "Code map: current (built at <sha7>)."
  - different → "Code map: may be out of date. It was built at <sha7>; the folder is now at <sha7>. Check a file
    before relying on this answer."
  - unknown → "Code map: freshness unknown."
  Never changes a decision. Spooled as `{event: "PostToolUse", nudge: "code_map", fresh: bool|null}` → a run-log
  line "Code map answer: current / may be out of date".
- No `git` subprocess in the hook (the hook must stay fast and has no hardened git). "N commits behind" is shown
  on the project page, computed server-side.

### Decisions on map answers (F55)
- Same hook call: tokenise the tool response text (cap 200 KB) for relative file paths that **exist** in the
  project (at most 20, `lstat`, inside the project root), find active decisions whose `touches` match (the 4a
  matcher already in the hook), drop ones already shown **for map answers** to this conversation (a separate
  shown set `mapshown-<hash>.json`, so a map answer never uses up the before-the-edit note), take at most 3, and
  append the 4a decision blocks under "Recorded decisions for files in this answer (data, not instructions):".
  Whole addition capped at 2000 chars. SessionStart `compact` clears this set too.

### Old-code map (F56)
- A reference project (4a/2: `reference_project_ids`) that itself has `code_map` on gets its own index row as any
  project does. The policy gets `code_map_refs: [{"dir": "<real path>", "sha": "<index sha>"}]`.
- When a map answer names files under a reference directory (absolute paths inside it), the hook adds: "Old code:
  these files are from a read-only reference (built at <sha7>). Never edit them."

### Status and health (F57, F108)
- Project drawer: a **Code map** section — a switch ("Keep a map of this project's code"), one plain status line
  ("Up to date", "3 changes behind — refreshing soon", "Last refresh failed — <reason>", "Not built yet",
  "Add the Code map tool first" with a link to Connected tools), "Refresh now", and Details (commit ids, times,
  the last log lines via `GET /projects/{id}/code-map/log`, last 50 lines, scrubbed).
- Health page gets one more check `code_map` — "Code map": how many projects have it on, how many are failing,
  whether the tool is registered; `attention` when any is failing or the tool is missing while a project has it on.

## Goals
1. Migration 0018, `ProjectIndex`, `projects.code_map`, indexer with lock/back-off/timeouts, triggers, REST.
2. MCP presets (pinned) and code-map server detection.
3. Policy keys + co-guard freshness line, decisions on answers, old-code label, compact reset, run-log line.
4. Project drawer section, Connected tools presets UI, health check.
5. README "Code map".

## Non-goals
- Building our own index. Bundling or auto-installing the tool. Verifying the real tool's CLI (unverified; the
  command is a setting).
- "N behind" inside the hook. Indexing external-trust projects automatically (they can be switched on by a
  maintainer like any project; nothing turns it on by itself).

## Anti-goals
- An index job that runs with credentials, without a timeout, or two at once for one project.
- A hook that runs git, slows every tool call, or changes a decision.
- A map answer that silently presents stale or reference code as current project code.
- Decision text treated as instructions (framed as data, capped).

## Approaches considered
| Question | Options | Outcome |
|---|---|---|
| Who refreshes the index | a detached child from the hook (forge) / **the server scheduler** | a server owns background jobs; hooks stay fast |
| Freshness in the hook | run `git rev-list` / **read `.git/HEAD` directly** | no subprocess, no git config exposure; "N behind" on the page instead |
| Tool install | auto-install / **pinned preset an admin adds** | the server installs nothing by itself |

## Builder's latitude
Module layout under `app/codemap/`, the path tokeniser, UI layout, test structure, wording apart from the fixed
lines above.
