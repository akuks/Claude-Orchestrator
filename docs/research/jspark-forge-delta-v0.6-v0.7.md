# jspark-forge delta inventory: v0.6.0 → v0.7.0 (2026-11-22)

Companion to `docs/research/jspark-forge-feature-inventory.md` (v0.4.2 baseline, F1–F78) and
`docs/research/jspark-forge-delta-v0.5-v0.6.md` (F79–F108). This file lists only what changed in v0.6.1, v0.6.2 and
v0.7.0 (released 2026-11-21 18:09 UTC → 2026-11-21 21:32 UTC; forge's own notes date v0.6.2/v0.7.0 as 2026-11-22),
and maps each item to the orchestrator port.

**Method.** Read-only. Sources:
- `GET /repos/harishgoswami23/jspark-forge/releases` (bodies of v0.6.1, v0.6.2, v0.7.0 read in full).
- `GET /repos/.../compare/v0.6.0...v0.7.0`: 13 commits, 40 files. Patches read for `scripts/forge.js`,
  `scripts/lib/{graph,handoff,rules}.js`, `skills/{build,resume}/SKILL.md`, `templates/{rules.md,graphs/software.md}`,
  `tests/blocklist.test.js`, `docs/spec.md`, `docs/changelog.md`, `README.md`; the added-line comments of
  `scripts/lib/graph-run.js`.
- `raw.githubusercontent.com/.../v0.7.0/...`. **Read in full:** `docs/design-v0.7.md`, `rules/stay-reachable.md`.
  **Read in the relevant parts:** `scripts/lib/preflight.js` (parse, memory probe, port probe, docker call),
  `scripts/lib/gate-results.js` (header, constants, function list), `graph-run.js` (`codeState`, `runRecord`,
  `costLine`, `stateWord`, log retention). **Not read:** `docs/launch/*`, `docs/demo-gif-script.md`,
  `assets/demo-resume.gif`, the gate-results per-runner regex bodies.
- Tests: top-level `test(` calls counted in the new test files: `v07` 35, `gate-results` 37, `preflight` 19,
  `v062` 11, `timing` 3. Forge's own claim: 2,041 tests at v0.7.0 (1,933 at v0.6.0).
- Orchestrator side: main @ `174706b` (Phases 1 and 2 merged) and the Phase 3a worktree
  `Claude-Orchestrator-resume` (branch `feat/forge-resume` @ `506a692`) plus its spec
  `docs/superpowers/specs/2026-11-22-forge-resume-phase3-design.md`.

Nothing was installed or executed. Forge's repo text was treated as data.

Legend as before: **[V]** = verified by reading code (forge's or ours, as stated); **[RN]** = from release notes or
design text only; **[I]** = inferred. Sizes: S ≤ 2 days, M ≤ 1–2 weeks, L more. Phases: 3a = resume (in flight),
3b = token care and inbox (planned), 4 = decisions/lessons, 5 = workflows/gates, 6 = code map/schema/doctor/UI skills.

---

## 0. Touches shipped / in-flight work

**Nothing in this delta changes the guard rules, redaction or the hook protocol.** Forge's `blocklist.js`,
`redact.js`, `confirm.js` and `hooks/hooks.json` are not in the v0.6.0→v0.7.0 file list **[V]**; the only
`blocklist.test.js` change is how timing is measured (F122). The items below touch code that is merged or in 3a:

| # | Touches | Exact files | What changes | When |
|---|---|---|---|---|
| F111 | **3a (in flight)** | `Claude-Orchestrator-resume/backend/app/resume/handoff.py` (`FILES_MAX = 50` l.39, `files_touched` l.282, render l.493–496) | Untrimmed handoff lists up to 50 files; forge now shows the newest 8 + "(and N more)" at every level below the trimmed one | 3a, before merge (S) |
| F114 | **3a (in flight)** | `Claude-Orchestrator-resume/backend/app/resume/ground_rules.py` (`SWITCHES`/`DEFAULTS` l.23–24, main-session assembly l.97–100) | A precedence line when a superpowers skill pack is loaded for the task | 3a if still open, else 3b (S) |
| F121 | **3a (in flight)**, only if adopted | same `ground_rules.py` | A `stay_reachable` switch would sit beside the others. Recommendation below: **do not** add forge's text as-is under `-p` | — |
| F118 | **Shipped (Phase 2) and touched by 3a** | `backend/app/gitsafe.py` (subcommand allowlist l.97–116; 3a adds status extras) | Flaky detection needs a code-state hash: `rev-parse HEAD` (allowed), `diff HEAD` and `ls-files --others --exclude-standard -z` (not allowed today: "no patches or diffs") | Phase 5, when gates land |
| F117 | **Shipped (Phase 2)**, no code change now | `backend/app/guard/rules.py` `evaluate` (l.4229) | A `retry:` command is a command: when server-run gates exist, it is evaluated by the guard exactly like `run:` (delta F103 mapping) | Phase 5 |
| F122 | **Shipped tests** | `backend/tests/test_redaction.py:444-447`, `backend/tests/guard/test_custom.py:263-265`, `:425-427` | Wall-clock `< 3.0 s` asserts; forge moved to "CPU time or wall time under the limit" after a busy machine stretched a 1 s check to 5.7 s | Any time (S) |

---

## 1. Summary per release

| Release | Date (UTC) | Theme | What is new, in plain words |
|---|---|---|---|
| **v0.6.1** | 2026-11-21 18:09 | Tidy-up | No behaviour change. Positioning becomes "a Claude Code based coding tool for non-technical founders". One more test (decide skill's migration-mapping text); `spec-coverage.md` updated. |
| **v0.6.2** | 2026-11-21 19:16 | "Six small fixes from real use" (a Claude session rated it 7/10) | A first message "continue/resume/go on/yes" is the go-ahead (no second "Continue?"). Handoff lists the newest 8 touched files + count. A handed-out gate nobody runs is "not yet passed (k of n passed, none running)"; "running" only while `graph gate` runs. Messages read "Step <id> (gate) …". Notes show their date when not today and say "re-check any caution or limit"; the notes nudge asks for what was measured behind a caution. One "who decides what" line when superpowers is installed. |
| **v0.7.0** | 2026-11-21 21:32 | "Better checks for long builds" | Gate steps get `before:` pre-flight lines (free memory, port open/free, container running; fixed forms, never commands) and a `retry:` line (rerun only what failed, then the full command once more). Every gate run is recorded with pass/fail/skip/not-run counts and failing names parsed from 8 runners; output goes to a log file (last 5 per step), Claude gets a summary and, on failure, the tail. Flaky = failed then passed with no git-visible code change; named every time, never "fixed". `graph status` shows live progress from the log. Every `graph next` ends with steps/minutes/tokens so far. A "stay reachable" rule (long commands in the background; answer a mid-task message first). |

Also in the window (process, not product): `docs/design-v0.7.md` with an independent review whose 7 findings were
fixed with tests; `tests/helpers/timing.js`; a generated demo GIF and launch posts.

---

## 2. Delta feature list

New ids continue from F108. "Changes Fx" marks a change to an earlier feature.

### 2A. Positioning (v0.6.1)

| # | Feature | What it does | Forge source | Maturity |
|---|---|---|---|---|
| **F109** | Positioning: non-technical founders (changes spec §2) | README/spec line now "a Claude Code based coding tool for non-technical founders"; coders welcome, product written for a non-coder. No behaviour change. | `README.md`, `docs/spec.md` §2, changelog Rev 18 | doc only **[V]** |

### 2B. Resume and handoff (v0.6.2)

| # | Feature | What it does | Forge source | Maturity |
|---|---|---|---|---|
| **F110** | First message is the go-ahead (changes F15) | `WAIT_LINE`: if the user's first message already says to carry on ("continue", "resume", "go on", "yes"), Claude says where the work stands in ≤ 4 lines, does not ask again, and carries on (crash check first). A question or the `/resume` command is not a go-ahead. Instruction text, not a hook; forge says a real session start must prove it. | `handoff.js` WAIT_LINE, `skills/resume/SKILL.md` step 4 | T v062(11) as text **[V]** |
| **F111** | Shorter file list in the handoff (changes F11, F83) | "Files touched in this task" shows the newest 8 and "(+N more)" (5 at trim level ≥ 2). Before, up to 50 names at every session start. The rest is in the log. | `handoff.js` `FILES_SHOWN = 8`, render | T handoff, v062 **[V]** |
| **F113** | Dated notes, measured cautions (changes F14) | The notes label shows `YYYY-MM-DD HH:MM` when the notes are not from today, and says "re-check any caution or limit before acting on it". The notes nudge adds: write a caution with what was measured ("free memory was 2 GB"); drop one that no longer holds. | `handoff.js` render, `notesLine` | T v062 **[V]** |
| **F114** | "Who decides what" with superpowers (changes F76/model map) | When superpowers is installed, SessionStart adds one line: superpowers sets the method (brainstorm, plan, TDD, review, verify); forge sets guard rails (resume, block list, decisions, grounding); at session start forge's lines come first; on conflict: user's instruction > forge's block list and grounding rule > superpowers. | `forge.js` `PRECEDENCE_LINE` | T v062 as text **[V]** |

### 2C. Build-graph gates (v0.6.2, v0.7.0)

| # | Feature | What it does | Forge source | Maturity |
|---|---|---|---|---|
| **F112** | Honest gate state words (changes F66, F103) | A gate is "running" in the record from hand-out, but is *shown* as "not yet passed (k of n commands passed, none running)" unless `graph gate` is running it; a handed-out human step reads as waiting for the user. Messages read "Step <id> (gate) passed / failed / still needs" (was "Gate <id> …", which gave "Gate gate"). Scripts that parse the old text must change; the state file is unchanged. | `graph-run.js` `stateWord` | T graph-gate, v062 **[V]** |
| **F115** | Gate pre-flight `before:` (new; replaces the graph-wide memory line cut in v0.4) | Any number of `- before:` lines on a **gate** only, fixed forms: `free memory <n> GB\|MB` (GB ≤ 1 decimal), `port <n> open\|free`, `container <name> running` (name `^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$`). Other forms are a `graph check` error. Checked when `graph next` would hand the gate out and again when `graph gate` starts, **before** the record lock. Not met → gate not started, nothing fails, plain words ("free memory is 2.1 GB, needs 4 GB"). Unanswerable check (docker silent) = not met. Memory = Linux `MemAvailable`, macOS free+inactive+speculative pages via `/usr/bin/vm_stat`, else `os.freemem()`. Port = a Node child tries 127.0.0.1 and ::1 (1 s timeout; open if either connects). Container = `docker ps --filter name=^<n>$ --filter status=running` as argv, never a shell. Not re-checked for commands Claude runs by hand afterwards. | `preflight.js`, `graph.js` validate, `graph-run.js` `preflightFor` | T preflight(19), v07(35) **[V]** |
| **F116** | Gate runs recorded, results without logs (new) | Every gate command run is recorded (last 10 per step): command, full/retry, time, ms, exit, passed/failed/skipped/not run, ≤ 50 failing names (≤ 160 chars each), code-state hash, log path. Parser knows node:test, TAP, jest, vitest, pytest, Playwright, mocha, go test; summary line wins, else per-test lines (cut-off run); lines cut to 4,000 chars, anchored patterns, input ≤ 20 MB (review: two patterns were slow on hostile lines; parsing moved outside the lock). Unknown runner → exit code only. `graph gate` writes output to `.jspark-forge/state/gatelog-<graph>-<step>-<n>.log` (last 5 kept), prints counts + failing names and, on failure, the last lines; a Bash-tool run is recorded from the (possibly cut) tool output. **Exit code alone decides pass/fail.** `graph status` lists runs one under another. | `gate-results.js`, `graph-run.js` `runRecord`/`runShell`/`readLog`/`pruneLogs` | T gate-results(37), v07 **[V]** |
| **F117** | `retry:` only what failed, then full (new) | At most one `- retry:` on a gate with exactly one `run:`; same command rules as `run:` (no `; && \| > < \` $(`, ≤ max length), must differ from `run:`; covered and shown by TRUST GRAPH (a gate without one keeps its old trust entry). Round: full fails → needs retry; retry fails → gate fails; retry passes → full once more; that full passes → done, else fails. One retry round per hand-out; `graph rerun` gives a new one. A retry alone never completes a gate. | `graph.js` validate, `graph-run.js` `settleGate` | T v07 **[V]** |
| **F118** | Flaky named, never "fixed" (new) | When a full run passes after earlier failing full runs (this or an earlier hand-out) **and** the code-state hash is equal (`rev-parse HEAD` + `diff HEAD` + untracked-not-ignored files by name/size/mtime, forge's own folder excluded, null over a cap), the earlier failing names are stored as flaky and printed in the pass message, `graph status` and at graph finish ("a flaky test is not a fixed test"). Code changed → nothing called flaky. A run with no exit code (timeout) never counts. Names unreadable → "failed then passed unchanged". | `graph-run.js` `codeState`/`noteFlaky` | T v07 **[V]** |
| **F119** | Live gate progress (new) | `graph status` on a gate that `graph gate` is running reads the log so far: "31 of 63 tests done, 0 failed" (or "31 tests done" without an announced total). The build skill tells Claude to use this, not read the log. | `gate-results.js` `progress`/`progressLine`, `skills/build` | T gate-results, v07 **[V]** |
| **F120** | Cost line per step (new) | Every `graph next` ends: "So far: d of n steps done, M min, T tokens (last: <step>, m min, t tokens). Tokens are counted for helper steps only." Gates now record minutes. | `graph-run.js` `costLine` | T v07 **[V]** |

### 2D. Instructions to Claude (v0.7.0)

| # | Feature | What it does | Forge source | Maturity |
|---|---|---|---|---|
| **F121** | Stay-reachable rule (new; principle 10 addendum) | At every SessionStart: a user message arrives only when the current command ends, so run anything > ~1 min in the background, carry on or end the turn, and answer a mid-task message first in a line or two. `- stay-reachable: off`. A rule, not a hook (nothing can interrupt a running command); README points to Claude Code's `/btw <question>` and Ctrl+Enter. A "side window" for questions was considered and dropped. | `rules/stay-reachable.md`, `handoff.reachableRule`, `forge.js` SessionStart, `rules.js` SETTINGS | T v07 as text **[V]** |

### 2E. Test infrastructure

| # | Feature | What it does | Forge source | Maturity |
|---|---|---|---|---|
| **F122** | Load-tolerant timing asserts (process) | `cost(fn)` measures wall and CPU time; `fastEnough(m, ms)` passes when either is under the limit; used for the block-list performance tests. Reason: a busy CI machine stretched wall time to 5.7 s while the work took ~1 s. | `tests/helpers/timing.js`, `tests/blocklist.test.js` | T timing(3) **[V]** |

**Count: 14 new or changed features (F109–F122).**

---

## 3. Orchestrator mapping

| # | How it translates | Orchestrator code it touches | Size | Phase |
|---|---|---|---|---|
| F109 | **Doesn't translate** (product positioning). Worth noting for UX copy: forge's target user is now a non-coder, which explains its plain-words output. | — | — | — |
| F110 | **Already built (different shape).** There is no show-and-wait turn: the Continue button is the go-ahead, and 3a's `CONTINUE_PROMPT` / `RECOVERED_CONTINUE` tell the agent to check the record and continue, never to ask **[V]** `resume/texts.py:29,40`; 3a spec non-goal "the show-and-wait Continue? turn" **[V]** spec l.308. Nothing to do. | `resume/texts.py` (3a) | — | 3a (done) |
| F111 | **Translates; touches 3a.** 3a renders up to `FILES_MAX = 50` files untrimmed (level 0/1) and 5 at level ≥ 2 **[V]** `handoff.py:39-40,493-496`. The handoff is re-injected into resumed prompts and at compaction, so 50 paths cost tokens on every resume. Port: show the newest 8 + "(and N more)" in the rendered text; keep the full ≤ 50 list in the stored content for the card's "show details". | `resume/handoff.py` render | S | **3a** (before merge) |
| F112 | **Translates in Phase 5.** Today a workflow has only `pending/running/paused/completed/failed`, and `paused` means a human-approval stage **[V]** `workflow_runner.py:25-29,266-267`; there are no command gates yet **[V]** (stages are agent tasks only, `models.py` `Workflow.stages`). When server-run gates land: show "not yet passed (k of n commands passed, none running)" vs "running" from a real process handle, and name events "Step <name> (gate) passed/failed/still needs". | `workflow_runner.py`, WorkflowsView | S | 5 |
| F113 | **Already built in 3a.** `NOTES_LINE` already asks to "write a caution or a limit with what was measured … drop one that no longer holds" **[V]** `guard/co_hook.py:133-137` (3a worktree); the render prints "written <ISO timestamp>; re-check any caution before acting on it" **[V]** `handoff.py:481` (an ISO stamp always carries the date). Optional polish: forge's "or limit" wording and a short local date instead of raw ISO. | `resume/handoff.py` (3a) | — (S polish) | 3a (done) |
| F114 | **Translates, conditionally.** We have no superpowers preset (`routers/skills.py` PRESETS lists cybersecurity-skills, diagram-design, scientific-agent-skills) **[V]**, but an admin can install it as a skill pack, and 3a already reads superpowers plans/ledgers for step progress **[V]** `handoff.py:64-65,214`. Port: when a task's loaded skill packs include one whose plugin name is `superpowers`, append the precedence line (co-orchestrator sets guard rails; the guard's approvals and the grounding rule outrank skill instructions; the user's prompt outranks both) to the main-session ground rules. | `resume/ground_rules.py` (3a), worker plugin-dir list | S | 3a if open, else 3b |
| F115 | **Translates, server-side; simpler and stricter.** The server runs gates (Phase 5) on the host that holds the workspace, so pre-flight runs there with no agent involvement: memory from `/proc/meminfo MemAvailable` (Linux) / `vm_stat` (macOS dev), port probe with a Python socket to 127.0.0.1 and ::1, container check with `docker ps` as argv. Keep forge's rules: fixed forms only (validated on workflow save, never a command), unmet = **not started, not failed**, plain-words reason, unanswerable = unmet, check again just before start. Multi-user caveat **[I]**: several runs share one host, so "port 3000 free" and "4 GB free" are host-wide; show which other runs hold the project directory (3a's "other runs" query). Also offer the pre-flight on plain tasks/schedules as a stage option, not only on gates. | `workflow_runner.py` (gate stage), new `app/workflows/preflight.py`, workflow schema validation, WorkflowsView | S–M | 5 |
| F116 | **Translates; two uses.** (a) **Gates (Phase 5):** server-run gate output goes to a per-run log file (keep the last 5 per step), parsed for counts and ≤ 50 failing names; the stage card shows runs side by side; the fix-it prompt for the next agent step gets counts + failing names + the last lines, not the whole log. Exit code decides. (b) **Any task (3b candidate):** the worker already sees every Bash `tool_result`; running the same parser over results of commands that look like test runs gives a "Tests: 61 passed, 2 failed (names)" chip on the Run view without the user reading the log **[I]** (needs the result length before `_short` cuts it, as delta F96 noted). Port the parser as a pure Python module with forge's safety limits (line cap, anchored regexes, input cap, never inside a DB lock) and forge's per-runner fixtures as tests. Consider JUnit XML as a ninth input for runners we configure ourselves **[I]**. | new `app/workflows/test_results.py`, `workflow_runner.py`, `worker.py` event path (b), Run view | M | 5 (a); 3b optional (b) |
| F117 | **Translates; changes our retry.** Today `retry_run` **deletes** the failed stage's tasks and starts the stage again **[V]** `workflow_runner.py:181-204`, so earlier attempts and their evidence are lost. Port: (1) keep every attempt as a run record; (2) a gate may declare one `retry` command (same validation as `run`; must differ; only with one `run`); (3) order full → retry → full, only a passing full run completes; one retry round per hand-out, a manual rerun starts a new round; (4) the retry command goes through `guard.rules.evaluate` like every gate command (delta F103) **[V]** `guard/rules.py:4229` exists. | `workflow_runner.py`, `WorkflowRun.stages` (attempt records), guard evaluate | M | 5 |
| F118 | **Translates; touches `gitsafe.py`.** Code state = hash of `rev-parse HEAD` + `diff HEAD` + untracked-not-ignored files (name/size/mtime), excluding our own task dir. `gitsafe` forbids diffs today **[V]** `gitsafe.py:29,88-116`; add exactly `diff --no-ext-diff --no-textconv --binary HEAD` (output hashed, never stored or shown) and `ls-files --others --exclude-standard -z`, both under the existing gpg/diff/attribute program switches. Flaky names on the gate card, the workflow's final summary and the next step's prompt ("flaky is not fixed"). A timeout or killed run never counts. | `gitsafe.py` allowlist (+tests), gate run records, WorkflowsView | S–M | 5 |
| F119 | **Translates (Phase 5) as a live gate card.** The server owns the gate process, so it can tail the log and push "31 of 63 tests done, 0 failed" over the existing live channel (Run view already streams) **[I]** for the transport. For agent-run background commands inside a task this does not translate: their output reaches us only when the tool call ends **[I]**. | `workflow_runner.py`, live events, WorkflowsView | S | 5 |
| F120 | **Translates; mostly data we have.** `Task` stores `total_cost_usd`, `duration_ms`, `input_tokens`, `num_turns` **[V]** `models.py:94-97`; `WorkflowRun` stores no totals **[V]** `models.py:521-538`. Port: a running total on the workflow run ("3 of 6 stages, 41 min, $2.10 / 1.2M tokens; last: Develop, 18 min, $1.40") shown on WorkflowsView and appended to the stage-advance event; the same "so far" line per thread on the Run view (sum over the thread's steps). Unlike forge, we know tokens and cost for **every** step, not only helpers. | `workflow_runner.py`, WorkflowsView, Run view, 3b token panel | S | 3b (thread) / 5 (workflow) |
| F121 | **Doesn't translate as written; the need warrants new work.** Our runs are `claude --print` with the prompt given once **[V]** `worker.py:493-525` (no `--input-format stream-json`); a user's message to a running thread is a follow-up that needs a finished session (`409 … must finish a run first`) and is queued as a new step **[V]** `routers/tasks.py:360-394`. So "answer the mid-task message first" has no channel today, and "end your turn while a command runs in the background" may end the `-p` run with work still going **[I]** (3a already captures Stop `background` for that case). Options: (a) **Live steering:** spawn with `--input-format stream-json` and let a person push a message into the running session (it lands between tool calls, like Ctrl+Enter) — L, needs a Phase-0-style spike on our CLI version; (b) **Ask about this run:** a side question answered by a separate cheap model call over the run's events and handoff (forge's `/btw` equivalent; sees the record, not the code) — S–M, no change to the running agent. Do not add forge's rule text to ground rules. | `worker.py` spawn and stdin, `routers/tasks.py`, Run view; (b) new endpoint over `TaskEvent`s | L (a) / S–M (b) | **new phase 3c** |
| F122 | **Translates (tests only).** Replace the three wall-clock `< 3.0` asserts with a helper that passes when wall **or** process CPU time (`time.process_time()`) is under the bound **[V]** files in §0. Keeps the linear-time guarantee for redaction and rule matching while ending CI flakes under load. | `backend/tests/test_redaction.py`, `backend/tests/guard/test_custom.py`, new `tests/helpers/timing.py` | S | any (with 3a or a test-only PR) |

### 3.1 By bucket

- **Translates:** F111, F114, F115, F116, F117, F118, F119, F120, F122.
- **Doesn't translate:** F109 (positioning); F121 as a rule (the need is real; see new phase 3c).
- **Already built:** F110 (Continue button and texts), F113 (measured cautions, dated notes) — both in 3a **[V]**.

---

## 4. Updated phasing

Additions to the baseline §5 and the v0.5–v0.6 delta §4 (existing contents unchanged):

| Phase | Add from this delta | Notes |
|---|---|---|
| **3a (in flight)** | F111 newest-8 file list; F114 precedence line if 3a is still open; F122 timing helper (test-only, optional) | Small edits to `resume/handoff.py` and `resume/ground_rules.py`. F110 and F113 are already covered. |
| **3b (token care, inbox)** | F120 thread "so far" line (steps, minutes, tokens, cost); F116(b) test-result chip for agent-run test commands; F114 if 3a closed | F116(b) shares the parser with Phase 5; build the parser once (pure module) and land it with whichever comes first. |
| **3c (new): reach a running agent** | F121: (b) "Ask about this run" side question first; (a) live steering via stream-json input after a spike | (b) is cheap and safe (reads the record only). (a) changes how the worker spawns and feeds `claude`; it interacts with co-guard approvals (a message arriving while a call is held) and needs its own spike. Could also sit after 5. |
| **4 (decisions)** | — | Nothing new. |
| **5 (workflows/gates)** | F112 state words; F115 pre-flight; F116(a) run records + log files + parser; F117 retry-then-full and keeping attempts; F118 flaky (with the `gitsafe` allowlist change); F119 live progress; F120 workflow totals | This turns Phase 5's "server-run gates" (F66/F103) into **gates v2** from day one. Suggest splitting Phase 5: **5a** command gates (F66, F103, F112, F115–F120); **5b** launch list, templates, lint (F61–F64, F68–F70, F104). 5a is the user-visible win. |
| **6 (code map/schema/doctor/UI)** | — | Doctor could show docker availability for pre-flight **[I]**, minor. |

### Top 3 by user value

1. **F117 retry only what failed, then one full run** — the hours forge saw lost went to rerunning whole suites; for us
   it also stops `retry_run` from throwing away earlier attempts.
2. **F115 gate pre-flight** — a long gate that would die on a missing database or low memory is held, not failed,
   with the reason in plain words.
3. **F116 + F118 results without logs, and flaky named** — counts and failing names on the card and in the fix-it
   prompt (fewer tokens, no log reading), and a pass after a flaky failure is never reported as a fix.

---

## 5. Verified vs inferred

**Verified by reading forge at v0.7.0:** F110–F122 from the patches and files listed in Method (WAIT_LINE text,
FILES_SHOWN, notes label and `notesLine`, `PRECEDENCE_LINE`, `stateWord` comment, graph.js `before`/`retry`
validation, preflight.js parse/memory/port/docker code, gate-results.js limits and runner list, `codeState`,
`runRecord` limits, `LOG_KEEP = 5`, `costLine` text, `reachableRule` and the rule file, `rules.js` SETTINGS, timing
helper use in `blocklist.test.js`). F109 from README/spec/changelog. The per-runner regex bodies, `settleGate` and
`noteFlaky` bodies were read via their comments and the design doc, not line by line (**[RN]** for their exact
edge cases). That forge's guard/redaction/hook files are unchanged: verified from the compare file list.

**Verified by reading orchestrator code:** main @ `174706b`: `workflow_runner.py` (statuses, paused = human
approval, `retry_run` deletes stage tasks), `models.py` (`Task` usage columns, `Workflow`/`WorkflowRun` shape, no
totals, no gate commands), `routers/tasks.py` follow-up (needs a finished session, queues a child), `worker.py`
spawn (`--print`, stream-json output only, prompt once), `routers/skills.py` PRESETS (no superpowers),
`gitsafe.py` allowlist (no diff, no ls-files), `guard/rules.py` `evaluate`, timing asserts in
`tests/test_redaction.py` and `tests/guard/test_custom.py`. 3a worktree @ `506a692`: `resume/handoff.py`
(FILES_MAX, render, notes label, superpowers plan parsing), `resume/ground_rules.py` (switches, texts),
`resume/texts.py` (Continue texts), `guard/co_hook.py` NOTES_LINE, and the 3a spec's scope and non-goals.

**Inferred / to verify:**
- That a `-p` run which ends its turn while a background Bash command is still running stops that command (F121).
- That `claude --print --input-format stream-json` accepts a user message mid-run on our CLI version and that it
  lands between tool calls (F121a).
- That the Run view's live channel can carry gate progress without a new transport (F119).
- That test-command detection on agent Bash results is reliable enough for a Run-view chip (F116b).
- Host-wide pre-flight semantics with several users' runs on one server (F115).
- Sizes.
