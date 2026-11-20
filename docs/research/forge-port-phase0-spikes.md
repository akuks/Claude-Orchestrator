# Forge port: Phase 0 spike results

Date: 2026-11-20. Host: macOS (Darwin 25.3), `claude --version` = **2.1.329 (Claude Code)**, hook interpreter
`/opt/homebrew/bin/python3` 3.14.3. Plan context: `jspark-forge-feature-inventory.md` sections 4 and 5 (Phase 0, a–h).

Every run used `claude -p --output-format stream-json --verbose --permission-mode bypassPermissions
--plugin-dir <spike plugin> --model claude-haiku-4-5 --max-turns 3 --max-budget-usd 0.05`, with cwd set to a scratch
dir and `CO_TEST_VAR=1` in the environment. Some runs also added `--include-hook-events` or `--resume`.
`CLAUDE_CONFIG_DIR` was **not** set, so the user's normal login was used. As a result the user's own plugins
(superpowers and the built-ins) loaded beside the spike plugin, and Claude wrote session transcripts under
`~/.claude/projects/<scratch-path>/`, which it always does. Nothing in `~/.claude` was edited.

**Runs: 7 of the 12 allowed. Cost: about $0.30** (0.034 + 0.050 + 0.026 + 0.052 + 0.046 + 0.065 + 0.023).

| # | Purpose |
|---|---|
| 1 | Event coverage, deny with JSON output, nonces, env (`--include-hook-events`) |
| 2 | Subagent (Agent tool), run *without* `--include-hook-events` |
| 3 | PreToolUse that blocks for 60 s, then `ask` |
| 4 | Does `--permission-prompt-tool` exist (trivial prompt) |
| 5 | `--resume <run1>` with `/compact`: PreCompact and SessionStart `compact` |
| 6 | `--resume` after the compaction: which nonces does the model see? |
| 7 | Hook timeout, exit-code-2 deny, and a crashing hook (parallel tool calls) |

## Answers

| Q | Answer | Evidence |
|---|---|---|
| **a** Which events fire; do Subagent* fire? | **All 9 configured events fired.** Run 1: SessionStart(`startup`), UserPromptSubmit, PreToolUse, PostToolUse, Stop and SessionEnd. Run 2: SubagentStart and SubagentStop fire when the model uses the Agent tool. **The subagent's own tool calls also go through PreToolUse and PostToolUse**, and their payload carries `agent_id` and `agent_type`. Run 5: PreCompact (`trigger: "manual"`), SessionStart(`resume`) and SessionStart(`compact`). Compaction itself runs as an internal subagent, so it also fires SubagentStop. Other findings: UserPromptSubmit did **not** fire for the `/compact` slash prompt. Stop did **not** fire in run 2, which ended `error_max_budget_usd` (SessionEnd still fired). | Run 2 hook log: `PreToolUse Agent` → `SubagentStart agent_type=general-purpose agent_id=a761ff03d2a385083` → `PreToolUse Bash "echo from-subagent" agent_type=general-purpose` → `PostToolUse Bash` → `SubagentStop` → `PostToolUse Agent` → `SessionEnd`. Run 5: `PreCompact None manual`, `SessionStart compact`. |
| **b** Does a PreToolUse deny win under bypassPermissions? | **Yes.** The sentinel did not run (`sentinel.txt` was never created). The model receives the reason as an `is_error` tool_result. The `result` message lists the call in `permission_denials`. | `{"type":"tool_result","content":"PreToolUse:Bash hook error: CO-GUARD: blocked by spike rule forbidden-sentinel (reason nonce NONCE-DENY-7731)","is_error":true}`. The result has `"permission_denials":[{"tool_name":"Bash",...,"command":"echo FORBIDDEN-SENTINEL > sentinel.txt"}]`. Exit-code-2 also denies (run 7): `"PreToolUse:Bash hook error: [python3 -I \".../hook.py\" PreToolUse]: CO-GUARD exit2: blocked (nonce NONCE-EXIT2-3310)\n\nThis hook comes from the co-spike@inline plugin."` The JSON form is cleaner because it does not leak the command line. |
| **c** Can a hook block about 60 s (timeout 90)? | **Yes.** The hook slept 60,010 ms and then returned `allow`. The command ran and the task ended `success` (71.9 s wall time). While a hook runs, stream-json emits a **heartbeat every 30 s**. **When a hook passes its `timeout`, the call fails OPEN**: a 10 s-timeout hook that would have denied after 20 s was cancelled, and the command **ran** (`timeout.txt` was created). **A crashing hook (exit 1, traceback) also fails open**: `crash.txt` was created. | `{"type":"tool_progress","tool_name":"PreToolUse","parent_tool_use_id":"toolu_01Dp…","elapsed_time_seconds":30,"heartbeat":true}` (and again at 60). Timeout: `hook_response … "exit_code":1,"outcome":"cancelled"` then `{"subtype":"informational","content":"A hook of the \"co-spike\" plugin didn't answer in time, so it didn't check this action.","tag":"hook_no_verdict","hook_no_verdict":{"timed_out":true,…}}`. |
| **d** Does additionalContext reach the model in `-p`? | **Yes, from all three sources:** SessionStart (`startup`, `resume`, `compact`), PreToolUse and PostToolUse. The model repeated every nonce. After `/compact`, the SessionStart(`compact`) context is present in the next turn, which is what re-injection (F77) needs. Claude delivers it as a `<system-reminder>SessionStart hook additional context: …` in the transcript. | Run 1 answer: `NONCE-SS-4417 (SessionStart)`, `NONCE-PRE-5102 (PreToolUse:Bash hook)`, `NONCE-POST-8823 (PostToolUse:Bash hook)`, `NONCE-DENY-7731 (block reason)`. Run 6: `NONCE-SS-resume-4417 — system-reminder`, `NONCE-SS-compact-4417 — system-reminder (SessionStart hook additional context)`. |
| **e** What does `ask` do in `-p`; does `--permission-prompt-tool` exist? | **`ask` becomes an automatic deny** in `-p`, even under bypassPermissions. Claude emits a `permission_denied` system event, and the model sees the `ask` reason as an error. The command did not run (`asked.txt` was not created). `--permission-prompt-tool` **exists but is hidden**: the `--help` text does not list it as an option, only mentions it inside the new **`--permission-prompts host\|none`** option ("Who answers permission prompts with --print: "host" (the SDK host or --permission-prompt-tool) or "none""). Run 4 accepted `--permission-prompt-tool mcp__nonexistent__approve` without an error. Routing `ask` to an MCP tool was **not tested** (no MCP server was built). | `{"type":"system","subtype":"permission_denied","tool_name":"Bash","decision_reason_type":"hook","decision_reason":"CO-GUARD: needs approval (ask nonce NONCE-ASK-2290)"}` followed by `tool_result "CO-GUARD: needs approval (ask nonce NONCE-ASK-2290)", is_error: true`. |
| **f** Do hooks see custom env and `CLAUDE_CODE_SESSION_ID`? | **Yes.** Every hook, including those inside subagents and SessionEnd, saw `CO_TEST_VAR=1`, `CLAUDE_CODE_SESSION_ID` (equal to the payload's `session_id`), `CLAUDE_PROJECT_DIR` (the cwd) and `CLAUDE_PLUGIN_ROOT`. The PreToolUse payload includes `permission_mode: "bypassPermissions"`. | Log line: `{'ev':'PreToolUse','CO_TEST_VAR':'1','CLAUDE_CODE_SESSION_ID':'93c78431-…','CLAUDE_PROJECT_DIR':'…/spike0/work','permission_mode':'bypassPermissions'}` |
| **g** Does stream-json show hook activity? | **Only with `--include-hook-events`.** With the flag, each hook emits `system/hook_started` and `system/hook_response` (with `output`, `stdout`, `stderr`, `exit_code`, `outcome`, and `hook_name` such as `PreToolUse:Bash` or `SessionStart:compact`). **Without the flag, only the SessionStart hooks are shown.** Even without the flag you get the `tool_result` deny text, `permission_denied` (for `ask`), the `permission_denials` list on `result`, heartbeats, the `hook_no_verdict` warning, `task_started`, `task_progress` and `task_notification` for subagents, and `compact_boundary`. SessionEnd never appears in the stream because it runs after `result`. | Run 2 (no flag) contained only `hook_started` and `hook_response` for `SessionStart:startup`. |
| **h** Python hook latency | Measured offline (fork, exec, interpreter, JSON parse and log append; 30 runs, `python3 -I`). Homebrew **3.14.3: min 27.2 / median 28.7 / max 31.1 ms**. System `/usr/bin/python3` **3.9.6: min 79.0 / median 83.7 / max 90.0 ms**. In-process time after startup was below 0.1 ms. Within the run, consecutive PreToolUse hooks added no measurable delay at stream-json granularity. | Offline driver measuring `subprocess.run(['python3','-I','hook.py','PreToolUse'])` wall time. |

## Formats that worked

`.claude-plugin/plugin.json`:
```json
{"name": "co-spike", "version": "0.0.1", "description": "Phase 0 hook spike"}
```

`hooks/hooks.json`. The other events (UserPromptSubmit, PostToolUse, Stop, SubagentStart, SubagentStop, PreCompact
and SessionEnd) use the same shape. PostToolUse has the same matcher, and the rest have no matcher.
`${CLAUDE_PLUGIN_ROOT}` is expanded.
```json
{
  "hooks": {
    "SessionStart": [
      {"hooks": [{"type": "command",
                  "command": "python3 -I \"${CLAUDE_PLUGIN_ROOT}/hooks/hook.py\" SessionStart",
                  "timeout": 90}]}
    ],
    "PreToolUse": [
      {"matcher": "Bash|mcp__.*|Agent|Task",
       "hooks": [{"type": "command",
                  "command": "python3 -I \"${CLAUDE_PLUGIN_ROOT}/hooks/hook.py\" PreToolUse",
                  "timeout": 90}]}
    ]
  }
}
```
Several hooks under one matcher run **in parallel**, and any deny wins.

Hook stdout (exit 0):
```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
  "permissionDecisionReason": "CO-GUARD: blocked by … (rule id)"}}
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
  "permissionDecisionReason": "approved", "additionalContext": "…"}}
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "…"}}
{"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "…"}}
{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "…"}}
```
Alternatively, exit 2 with the reason on stderr denies too, but the message also includes the hook command line.

Payload keys as observed (stdin JSON):
- SessionStart: `cwd, hook_event_name, session_id, source, transcript_path`. The `resume` source adds
  `context_tokens, estimated_cache_write_usd, prompt_cache_likely_expired, seconds_since_last_response`. The
  `compact` source adds `model, prompt_id`.
- UserPromptSubmit: `+ permission_mode, prompt, prompt_id`.
- PreToolUse: `cwd, hook_event_name, permission_mode, prompt_id, session_id, tool_input, tool_name, tool_use_id,
  transcript_path`. Inside a subagent it adds `agent_id, agent_type`.
- PostToolUse: the PreToolUse keys plus `duration_ms, tool_response`.
- Stop: `background_tasks, last_assistant_message, permission_mode, prompt_id, session_crons, stop_hook_active, …`.
- SubagentStart: `agent_id, agent_type, …`. SubagentStop adds `agent_transcript_path, last_assistant_message`.
- PreCompact: `custom_instructions, trigger, prompt_id, …`. SessionEnd: `reason, prompt_id, …`.

## Design implications

1. **Fail-closed must be done in our code.** Claude Code fails **open** on a hook timeout and on a non-zero, non-2
   exit. co-guard needs these safeguards:
   - Wrap `main()` in `try/except BaseException`, and on any internal error print a JSON `deny`, which is
     "guard error, retry or ask a human".
   - Keep its own deadline well under the configured `timeout`: return `deny` at `timeout - 10 s`.
   - Never rely on the outer timeout.

   A broken policy file, for example, must deny rather than let the command through.
2. **Inline-wait approvals are viable.** A 60 s block caused no task failure, and the stream reports a heartbeat
   every 30 s, which the worker can show as "waiting for approval". Set the hook `timeout` to at least
   `guard_wait_seconds + 15`, and have the hook deny with "Request #id still pending, end your turn" just before
   that deadline. The 120 s and longer wait in the plan was not tested; 60 s was. Re-test 120 s on Linux in phase 1
   if that becomes the default. The cost of inline mode is one worker slot held, as the plan predicted.
3. **`ask` is useless in plain `-p`**, because it becomes an automatic deny. Approvals must therefore use either our
   own inline polling in the hook (point 2) or the deny-then-resume flow (plan §4.4 step 3). The hidden
   `--permission-prompt-tool` together with `--permission-prompts host` may route `ask` to an MCP approval tool.
   That is a possible later path, but it is unverified and it ties approvals to a hidden flag, so it should not be on
   the critical path.
4. **The server needs these, with `--include-hook-events` turned on in `worker.py`:**
   - The hook `tool_result` deny text, `permission_denials` and `permission_denied` are visible even without the flag.
   - The flag is needed so the Run view can show hook outcomes. It also exposes `hook_no_verdict` and timed-out
     warnings, which should become a red "guard did not check this action" event.

   SessionEnd output is never in the stream, so anything produced at session end must go through the spool file.
5. **Subagents are covered.** Their tool calls hit PreToolUse with `agent_id` and `agent_type`, so one guard covers
   helpers. Subagent start and stop (F17) can come from hooks or from stream-json `task_*` events.
6. **Compaction re-injection (F77) works** through SessionStart `compact` → `additionalContext`. PreCompact fires
   with `trigger`. UserPromptSubmit does not fire for slash prompts, so do not depend on it for `/compact`.
7. **Env plumbing works.** `CO_TASK_ID`, `CO_POLICY_FILE` and similar variables set on the claude process reach
   every hook, and `CLAUDE_CODE_SESSION_ID` is there for grants and resume.
8. **Interpreter choice matters.** Pin a modern `python3` (Homebrew 3.14 took about 29 ms against about 84 ms for
   macOS's 3.9). Use `sys.executable` from the server venv as the plan says. Both are well under the 300 ms budget.
9. **Budget caveat.** `--max-budget-usd` is checked after a turn. The first turn's cache write can exceed a small cap:
   $0.01 → $0.052 actual in run 4, and run 2 ended `error_max_budget_usd`, where **Stop did not fire**. Any
   end-of-task work must come from SessionEnd or the worker, never from Stop alone.
10. Other plugins' hooks (here, the user's superpowers SessionStart) run in parallel with co-guard. Under the
    production per-user `CLAUDE_CONFIG_DIR` this only matters if users install plugins, and co-guard's deny still
    wins.
11. The worker already sends the prompt on stdin (`worker.py:500`). For `--resume` runs that pass the prompt as an
    argument, use `stdin=DEVNULL`, or the CLI waits 3 s and warns "no stdin data received".

Not covered: the `mcp__.*` matcher (no MCP server was configured; the matcher syntax was accepted), a 120 s and
longer inline wait, Linux behaviour, and a per-user `CLAUDE_CONFIG_DIR`. Re-run runs 1, 3 and 7 on the Linux worker
at the start of phase 1.

Spike plugin (kept): `/private/tmp/claude-501/-Users-ashutosh-Projects-Work-Claude-Orchestrator/79861028-113a-4efa-8ee7-d9ad3f5a6dee/scratchpad/spike0/plugin/`
(`.claude-plugin/plugin.json`, `hooks/hooks.json`, `hooks/hook.py`). The raw logs were deleted after this summary
was written.
