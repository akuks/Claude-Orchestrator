# Forge port Phase 7e — small gaps — design

From the 2026-11-25 audit: F74/F108 (health additions), F107 (Node ≥ 20), F96 (usage tips), F106 (copy a temp-folder
document into the project), F102 (helper tier in the run log), F90b (redaction extras), F80 (PostToolUseFailure),
P2 (security reviews: scanners, never "clean" unscanned, unsafe Claude-config scan). Standing autonomy; plain screens.

## 1. System health (F74, F108, F107)

- **Connected tool versions:** the MCP handshake keeps `serverInfo.name` and `serverInfo.version` (new
  `mcp_servers.server_version`, migration **0023**), refreshed on every probe. The "Connected tools" card shows each
  tool's version; when a tool came from a pinned preset and the version differs from the pin: attention, "<tool> is
  at <v>, expected <pin>."
- **Code map:** each project row shows the engine's last error in plain words (from `ProjectIndex.error`, cut to 200
  characters, under Details); a warning when more than one enabled connected tool is a code-map engine: "Two code-map
  engines are set up (<a>, <b>); only <a> is used."
- **Node:** parse the version; below 20 → attention "Node <v> is too old for the browser tools (they need 20 or
  newer)."; the Recommended panel marks Playwright and DevTools "Needs Node 20" when Node is older.
- **Self-tests:** "Agent tokens" — mint a throwaway task token for a fake task id, check it is accepted for that task
  only, revoke it, check it is refused; "Guard plugin" — `claude plugin validate <plugin dir>` (bounded 20 s; when
  the CLI has no such command, OK with "not available in this Claude version").

## 2. Usage tips (F96)

- Per step, the usage snapshot adds per bucket (main and each helper kind) `reread_per_step = cache_read /
  messages` and per helper kind the number of helper starts.
- Tips (exact): "Each step re-reads about N tokens of earlier conversation." (always shown when N ≥ 20k); "Helpers of
  type <kind> average <n> steps each — give them a narrower brief." (when > 40); the fresh-session tip gains "A fresh
  session would save about M tokens over the next 50 steps." with M = (reread − 30k) × 50 when reread > 150k.

## 3. Copy a temp-folder document into the project (F106)

- The handoff card's "may vanish" files get a **Copy into project** action (people who can edit the task):
  `POST /tasks/{id}/outside-docs/copy {path, dest}` — `path` must be one of the task's recorded outside docs and still
  in a temp folder with an allowed extension; read with no links followed, regular file, ≤ 5 MB; `dest` is a relative
  path inside the task's workspace/project (no `..`, parent exists, real path inside); never overwrites (409 "A file
  with that name already exists."); a task event records the copy. Default dest: `docs/<file name>`.

## 4. Helper tier in the run log (F102)

- When a helper starts, its model (the Agent call's `model` input, else the helper's first message's model) is
  mapped to the org's tier names; the log line reads "AGENT+ <kind> [<tier>]: <description>". Unknown → no tier.

## 5. Redaction extras (F90b)

- `strip_query(text)`: URLs keep scheme, host and path; query and fragment are dropped. `redact_head(text, secrets,
  n=4000)`: redact then cut to n characters. Both in `redaction.py` and the hook's copy (the equality test covers
  them). Applied to stored tool results, guard summaries, handoff texts. Not to the command shown on an approval card.
- Values from the workspace's top-level `.env*` files (read with no links followed, ≤ 64 KB each, values of 6+
  characters) are added to the run's secrets.

## 6. PostToolUseFailure (F80)

- The hook registers `PostToolUseFailure` and writes the same "done" line as PostToolUse, so a failed call never
  stays "in flight" after a crash. Harmless if the CLI never sends it.

## 7. Security reviews (P2)

- Scanner detection at prompt build: `gitleaks`, `osv-scanner`, `trivy`, `semgrep`, `bandit`, `pip-audit` on PATH, and
  `npm audit` when a lockfile exists. The prompt lists them and adds: "Run the scanners listed. If none could run,
  say 'Not scanned' — never call the code clean." The current "If none, say so" line is replaced.
- Unsafe Claude config scan: the project's `.claude/settings*.json` and `.mcp.json` (read safely, capped) are checked
  for hooks, wide allows (`Bash(*)`, `*`), MCP servers and token-looking values; findings go into the review prompt and
  the findings list ("Claude settings: …").

## Not covered / to know

- Not run against the real `claude` CLI (including `claude plugin validate`).

## Anti-goals

Running scanners on the server itself; copying arbitrary paths; showing raw secrets anywhere.
