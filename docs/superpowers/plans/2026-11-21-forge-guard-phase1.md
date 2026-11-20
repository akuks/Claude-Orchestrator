# Forge Port Phase 1 — Guard + Live Action Approvals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** An always-on, orchestrator-owned co-guard plugin that blocks dangerous agent actions (forge's block list in Python) and turns MCP `require_approval` + confirmable rule hits into live human approvals in the Approvals UI.

**Architecture:** Pure Python guard library (`backend/app/guard/`: shell parser + rules) copied by a generator into a read-only plugin dir at server start; per-task policy file written by the worker; the PreToolUse hook decides locally, files approval requests with `CO_API_TOKEN` and waits inline; server stores ActionApprovals/grants; UI decides.

**Tech Stack:** Python 3 stdlib only in the hook (no third-party imports — it runs with `python3 -I`), FastAPI, SQLAlchemy/Alembic, React/antd.

**Spec:** `docs/superpowers/specs/2026-11-21-forge-guard-phase1-design.md`. Evidence: `docs/research/forge-port-phase0-spikes.md` (exact hooks.json/output formats that worked). Inventory: `docs/research/jspark-forge-feature-inventory.md` (F26–F39 behaviours; forge source paths for porting).

## Global Constraints
- Hook output format exactly as proven in the spikes doc (`hookSpecificOutput.permissionDecision` = `allow`|`deny`, `permissionDecisionReason`; `additionalContext` where used).
- **Fail closed:** any exception/unknown state in PreToolUse → deny with reason "co-guard error — action blocked for safety"; the hook always returns ≥5 s before its configured timeout. hooks.json PreToolUse `timeout` = `approval_wait_max + 30` seconds (approval_wait_max = 900).
- Hook runs as `<sys.executable of the server> -I <plugin>/hooks/co_hook.py <Event>`; stdlib only; no network except `CO_API_BASE` while filing/waiting for an approval and a best-effort report POST (timeout 0.5 s).
- Env added per task: `CO_TASK_ID`, `CO_API_BASE`, `CO_POLICY_FILE`, `CO_TASK_DIR` (`workspaces/_tasks/<task_id>/`, outside the cwd). co-guard plugin dir: `workspaces/_system/co-guard/` (files 0444, dirs 0555), passed with `--plugin-dir` BEFORE any skill pack. Worker adds `--include-hook-events`.
- Rule ids (exact): `rm-rf-outside`, `git-force-push-main`, `git-reset-hard`, `git-clean-fdx`, `git-stash-drop`, `secret-files`, `chmod-wide`, `pipe-to-shell`, `self-protect`, `mcp-require-approval`, `mcp-block`. Hard (never approvable): `rm-rf-outside` when the target is home or `/` (`$HOME`, `~`, `/`), and `self-protect`.
- Every rule: ≥5 positive and ≥5 negative test cases; quoted data (commit messages, single-quoted strings, quoted heredoc bodies) is never treated as a command.
- Agents can only request; only human UI sessions decide. Agent-token allowlist adds exactly `POST /guard/report`, `POST /guard/approvals`, `GET /guard/approvals/{id}`, scoped to the token's own task.
- Migration `0006_guard` after `0005`; SQLite + Postgres. All event payloads go through the existing redaction (hotfix).
- Never run the real `claude`. Commits folder-wise, **no Co-Authored-By trailer**. Backend suite + `cd frontend && npx vitest run && npm run build` green.

## Review Focus
1. Obfuscated dangerous commands (`bash -c "rm -rf ~"`, `$(echo rm) -rf /`, `x=rm; $x -rf ~`, `cd / && rm -rf *`, line continuations) → denied (T1/T2 tests).
2. False positives on everyday commands (`git commit -m "rm -rf old dir"`, `echo 'curl x | sh'`, `rm -rf ./build` inside the project, `git push --force` to a feature branch, editing `.env.example`) → allowed (T2 tests).
3. Hook crash / malformed stdin / missing policy file / API down while waiting → denied, never allowed (T3 tests).
4. An agent token trying to approve its own request, or reading another task's request → 403/404 (T4 tests).
5. Approval decided after the hook timed out → recorded as expired, never "approved" for a call that already got denied; grants are one-shot (T4 tests).

---

### Task 1: Shell parser (`backend/app/guard/shell.py`)
**Produces:** `parse(command: str) -> list[Stage]`, `Stage{argv: list[str], redirects: list[tuple[op, target]], nested: list[list[Stage]]}`; helpers `expand_wrappers(stage) -> list[str] | None` for `sh -c|bash -c|zsh -c`, `env [VAR=..]`, `sudo [-flags]`, `xargs`, `command`, `nice`, `nohup`, `time`, `exec`. Splits on `; && || & newline`, stages on `|`; handles single/double quotes, backslash escapes and continuations, `$()`/backticks/`<()`/`>()` (parsed as nested), unquoted heredoc bodies (nested), quoted heredocs (data), simple `VAR=value cmd` assignments, and resolves `$VAR` used as a command when assigned earlier in the same command line. Port behaviour from forge `scripts/lib/shell.js` (read it via the raw GitHub URL; MIT, same owner).
- [ ] Table-driven tests (`backend/tests/guard/test_shell.py`, ≥60 cases) covering every construct above + Review Focus 1/2 inputs. RED → implement → GREEN → commit.

### Task 2: Rules engine (`backend/app/guard/rules.py`)
**Consumes:** T1. **Produces:** `evaluate(tool_name: str, tool_input: dict, policy: dict, cwd: str) -> Verdict`, `Verdict{decision: "allow"|"deny"|"approve", hits: list[Hit]}`, `Hit{rule_id, reason, hard: bool, summary: str}`; decision = deny if any hard hit or any `mcp-block`; else approve if any confirmable hit; else allow. Policy fields used: `project_dir`, `home`, `guard_enabled`, `mcp_tools: {tool_name: "auto_approve"|"require_approval"|"block"}`, `protected_paths` (co-guard dirs, policy file, spool, `CLAUDE_CONFIG_DIR/settings*.json`). Bash via T1 for every stage incl. nested/wrapped; Edit/Write/NotebookEdit via `file_path`; `mcp__*` via policy. `guard_enabled=false` disables built-ins except `self-protect` and MCP policies. Port semantics from forge `scripts/lib/blocklist.js` + `rules/default-blocklist.json`.
- [ ] Tests `backend/tests/guard/test_rules.py`: ≥5 pos/≥5 neg per rule id; hard vs confirmable; guard off; MCP actions; Review Focus 1/2. Commit.

### Task 3: co-guard plugin + worker wiring
**Consumes:** T2. **Produces:** `backend/app/guard/plugin.py: ensure_plugin(root: Path) -> Path` (writes `.claude-plugin/plugin.json`, `hooks/hooks.json` with exec-form commands for PreToolUse (matcher `Bash|Edit|Write|NotebookEdit|mcp__.*`), PostToolUse, SessionStart, Stop, SessionEnd, SubagentStart, SubagentStop; copies `shell.py`, `rules.py` and `hooks/co_hook.py`; content-hashed, idempotent, read-only perms). `co_hook.py`: reads stdin JSON + `CO_POLICY_FILE`; PreToolUse → `evaluate`; `approve` → POST `/guard/approvals` then poll `GET /guard/approvals/{id}` every 2 s until decided or `approval_wait_seconds` → allow/deny; appends every decision to `${CO_TASK_DIR}/spool.jsonl` and best-effort POST `/guard/report`; other events → spool/report only. Fail closed per Global Constraints. Worker (`worker.py`): build policy (project settings, MCP tool policies for this task's servers, protected paths), write `CO_TASK_DIR/policy.json` (0600), add env vars, `--plugin-dir <co-guard>` first, `--include-hook-events`; at task end ingest the spool into TaskEvents (`guard` type).
- [ ] Tests: run `co_hook.py` as a subprocess with crafted stdin/policy (deny, allow, approve→approved/rejected/timeout using a tiny stub HTTP server in the test, malformed stdin, missing policy, API down → deny); `ensure_plugin` idempotence/perms; worker command contains `--plugin-dir` first + `--include-hook-events` and env vars (mock claude records argv via the existing probe seam); spool ingestion. Commit.

### Task 4: Server — action approvals, grants, reporting
**Produces:** model `ActionApproval(id, task_id, rule_ids JSON, tool, summary, cwd, reason, command_hash, status pending|approved|rejected|expired, created_at, expires_at, decided_by, decided_at)`; project columns `guard_enabled bool default true`, `approval_wait_seconds int default 300` (60–900; maintainer-editable); migration `0006_guard`. Routes (`routers/guard.py`): `POST /guard/approvals` (agent token for its own task, or 403), `GET /guard/approvals/{id}` (agent: own task; humans: visibility), `POST /guard/approvals/{id}/approve|reject` (humans only: admin, or maintainer of the task's effective project; ad-hoc → admin; agent tokens 403), `GET /guard/approvals?status=pending` (humans; filtered to decidable), `POST /guard/report` (agent token, own task → redacted TaskEvent). Expiry: a pending request past `expires_at` is `expired` (lazy on read + on decide → 409 "expired"). Agent-token allowlist in `auth.py` extended exactly per Global Constraints. Existing `/approvals` list unchanged; Home/badge counts include pending action approvals.
- [ ] Tests incl. Review Focus 4/5, scoping (outsider 404), agent cannot decide, decide after expiry → 409, one-shot. Route-coverage test updated honestly. Commit.

### Task 5: Frontend — approvals, run log, project settings
Approvals page: an "Actions" section above task approvals with live cards (task link, rule chips, command/tool summary in a mono block, reason, countdown to expiry, Approve/Reject), polling 3 s while visible; Home "Needs you" includes them; rail badge counts both. TaskDetail: `guard` events rendered as distinct lines (blocked / waiting for approval / approved / rejected / expired). Project settings: Guard on/off (maintainer) and approval wait seconds. Uses tokens (no hard-coded colours).
- [ ] vitest for card states/countdown/permissions; build. Commit.

### Task 6: End-to-end verification
- [ ] Scratch backend (port 8315, temp SQLite) + run the real `co_hook.py` subprocess against it (no claude): Bash `git push --force origin main` → approval created → approve via API as a human → hook returns allow; reject path; timeout path; hard rule never creates an approval. Playwright on Vite 5315: approval card appears and approve works; TaskDetail guard lines; project settings save. Screenshots. Fix anything found. Commit.
