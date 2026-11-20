# Forge port — Phase 1: Guard + live action approvals — design

Program: port every jspark-forge feature into the orchestrator (owner's request:
"Copy each and every feature of forge"). Inventory and phasing:
`docs/research/jspark-forge-feature-inventory.md`. Spike evidence (real `claude -p`,
CLI 2.1.329): `docs/research/forge-port-phase0-spikes.md`. This doc is Phase 1.

## Intent
Agents run with `--permission-mode bypassPermissions`, so nothing stops a dangerous
command, and MCP tools configured `require_approval` (45 today) silently run. Phase 1
adds an orchestrator-owned agent-side guard (forge's block list, ported to Python) and
turns "require approval" into a real, live human approval in the Approvals UI.

## Decisions (owner: proceed autonomously; these are designer's calls)
- **Architecture** (inventory §4): an always-on, server-generated plugin **co-guard**
  (Python hooks, `python3 -I`, read-only files) passed via `--plugin-dir` before any
  skill pack. The worker writes a per-task **policy file** outside the workspace; hooks
  decide locally (no network on the hot path) and report via a spool file + best-effort
  POST with `CO_API_TOKEN`.
- **Live approvals are inline** (spike c: a hook can wait ≥60 s; stream heartbeats
  every 30 s): the PreToolUse hook files a request and polls until decided or timeout.
  Default wait 300 s, configurable per project (60–900). Timeout/reject → deny with a
  clear reason. (Deny-then-resume fallback is Phase 3.)
- **Fail closed** (spike c: a hook timeout/crash lets the call run): the hook wraps all
  logic, denies on any internal error, and always returns at least 5 s before its own
  timeout (hook `timeout` = wait + 30 s).
- **Hard rules** are never approvable: deleting home/root, writes to co-guard's own
  plugin/policy/spool dirs or `settings*.json` in `CLAUDE_CONFIG_DIR`, running the hooks
  by hand.
- Hooks can only *request* approval with `CO_API_TOKEN`; only a human UI session can
  decide. Agent-token allowlist gains exactly: `POST /guard/report`,
  `POST /guard/approvals`, `GET /guard/approvals/{id}` (own task only).
- Worker adds `--include-hook-events` (spike g) so guard activity reaches the run log.

## Goals (Phase 1 scope)
1. co-guard plugin generation at startup; policy file + env (`CO_TASK_ID`,
   `CO_API_BASE`, `CO_POLICY_FILE`, `CO_TASK_DIR`) per task.
2. Bash shell parser (forge F26: pipelines, `; && || & newline`, `|` stages, quoting,
   `$()`/backticks/`<()`, unquoted heredocs, line continuations, `sh -c`/`bash -c`/
   `env`/`sudo`/`xargs` wrappers) with a large table-driven test suite. PowerShell/cmd
   (F27) is out of scope (servers are macOS/Linux) — documented.
3. Built-in rules (forge F28–F31, F34–F36, F38): `rm-rf-outside` (hard when home/root),
   `git-force-push-main`, `git-reset-hard`, `git-clean-fdx`, `git-stash-drop`,
   `secret-files` (Bash writes + Edit/Write/NotebookEdit tools), `chmod-wide`,
   `pipe-to-shell`, `self-protect` (hard).
4. MCP tools with `require_approval` → live approval; `block` → deny (in addition to
   today's `--disallowedTools`); `auto_approve` → allow.
5. Action approvals: `ActionApproval` rows (task, rule ids, tool, normalised command or
   MCP tool+input summary, cwd, reason, status pending/approved/rejected/expired,
   decided_by/at). Who may decide: same as task approvals (Phase 2 rules: project
   maintainers/admins; ad-hoc tasks → admins; requester can't approve their own *hard*…
   n/a — hard rules are never approvable). Approvals page shows action approvals next to
   task approvals, live (poll), with the command, rule and reason; Home "Needs you"
   counts them.
6. Grants: approving creates a one-shot grant bound to (task, rule ids, command hash),
   consumed by the waiting hook; expires with the request.
7. Guard events in the run log (blocked / requested / approved / rejected / expired),
   redacted (hotfix PR #9 redaction applies), shown in TaskDetail as distinct lines.
8. Project setting: guard on (default) / off for built-in rules (MCP approvals always
   on); approval wait seconds.

## Non-goals (later phases, per inventory §5)
SQL rules + DB environment tags, readonly sources, trust/quarantine, org/project custom
rules UI (Phase 2); handoff/resume, notes, budget bands, compaction re-injection
(Phase 3); decision memory (4); workflows/launch (5); code map, schema, doctor (6).

## Anti-goals
- A guard bug that lets a dangerous call through silently (fail closed; tested).
- A guard that blocks normal work (false positives) — every rule has negative tests
  (≥5 per rule, forge's bar); quoted data (commit messages, heredoc bodies) is not a command.
- Any path where an agent approves its own request.
- Network on the PreToolUse hot path except while waiting for a human.

## Approaches considered
| Approach | Trade-off | Outcome |
|---|---|---|
| Install forge itself as a skill pack | its own state dirs, settings edits, wait-for-human prompts; no link to our approvals | rejected |
| Server-side only (parse stream-json, kill on bad call) | too late — the call has already run | rejected |
| **Orchestrator-owned co-guard plugin (Python hooks) + server approvals** | in-process deny (proven by spike b), our UI/approvals, Python like the backend | **chosen** |
| `--permission-prompt-tool` → our MCP approval tool | hidden flag, untested routing | revisit later |

## Builder's latitude
Module layout under `backend/app/guard/`, exact table names, UI layout of approval cards,
copy, test structure, polling intervals.
