# jspark-forge review — fit for the orchestrator (2026-11-20)

Source: https://github.com/harishgoswami23/jspark-forge (MIT, v0.4.2, created 2026-11-19). Read via the GitHub API; nothing installed or executed. Hook/script code read in full except the large blocklist/shell/schema/graph files (grepped for risky patterns).

**What it is:** a Claude Code plugin with hooks on 10 events (`node scripts/forge.js <Event>`):
- **Handoff/resume:** writes `<project>/.jspark-forge/handoff.md` (≤600 words: todo list → plan checkboxes → notes → last prompt) on tool events, PreCompact and usage thresholds; on SessionStart injects it and asks the user to confirm resuming.
- **Dangerous-command guard:** a real shell/PowerShell tokenizer (pipes, `$()`, `sh -c` wrappers) judges each stage against rules (`rules/default-blocklist.json`): rm -rf outside the project, force-push to main, reset --hard, clean -fdx, stash drop, destructive SQL / DB writes (incl. DB MCP servers), secret files, chmod wide, curl|sh, …). A hit is denied; the user lifts it by typing `CONFIRM <id>` (one-shot token, 5 min); "hard" hits can never be confirmed.

**Safety audit:** no network calls, no eval; only `git` (fixed argv) and its own node scripts are spawned (plus an opt-in `codebase-memory-mcp`). Reads `.env*` only to redact values from its logs. **Writes outside the project:** `.git/jspark-forge-own`, `<CLAUDE_CONFIG_DIR>/jspark-forge/…`, and it **auto-edits `<CLAUDE_CONFIG_DIR>/settings.json`** to install a status line unless `JSPARK_FORGE_AUTO_STATUSLINE=off`. Fails open on internal errors; interactive features gated on `CLAUDE_CODE_SESSION_ATTENDED=1`; the blocklist still enforces in headless/bypassPermissions runs.

**Fit as a skill pack for our agents: not as-is.** The guard would work headlessly, but: `.jspark-forge/` in every workspace, silent settings.json edits in each user's config dir, git-ownership-based "guest mode" for cloned repos, extra context injected every session (incl. "show the handoff and wait for the user" on resume — conflicts with unattended `--resume` follow-ups), and a blocked command can't be confirmed mid-run. Brand-new, AI-assisted, scope creep (build graphs, code map); headless path lightly tested by the author's own docs. Good test suite and 3-OS CI for its age.

## Borrow (rewrite in our code)
| # | Idea (their file) | Our place | Effort |
|---|---|---|---|
| 1 | **Server-side dangerous-command guard** — PreToolUse hook we ship ourselves, parsing pipes/`$()`/wrappers (`scripts/lib/blocklist.js`, `shell.js`) | Agent runs; deny reason tells the agent to request approval; approved tasks lift specific rule ids | M |
| 2 | **One-shot confirm token** bound to task + rule id, 5 min (`confirm.js`) | Approvals UI | S |
| 3 | **Resume after interruption** — progress ledger + re-spawn with `--resume <session>` and "continue from the ledger" (handoff template: task, step n/m, next, git state) | Reliability feature (what we did by hand after the offline trip) | M |
| 4 | **Redact `.env` values from logs** (`redact.js`) | Worker events/logs | S |
| 5 | Guest/own repo trust split | — (skip) | L |

Verdict: safe to read and borrow from; don't auto-deploy to a multi-user org without an isolated trial.
