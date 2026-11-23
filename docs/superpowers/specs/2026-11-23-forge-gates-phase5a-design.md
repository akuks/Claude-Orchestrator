# Forge port — Phase 5a: command gates (gates v2) — design

Program: port every jspark-forge feature into the orchestrator (owner: "Copy each and every feature of forge").
Inventory: `docs/research/jspark-forge-feature-inventory.md` F66 (§3 row), `-delta-v0.5-v0.6.md` F103,
`-delta-v0.6-v0.7.md` F112, F115, F116 (a and b), F117, F118, F119, F120. Phase 5 is split: **5a** (this) is
server-run command gates and human-approval stages; **5b** is the launch list, templates and lint (F61–F64,
F68–F70, F104), and also takes typed stage outputs, automatic retry-once and a per-run budget cap (F66 parts
parked here).

## Intent
A workflow today is a chain of agent stages. "Run the tests" is itself an agent stage. The agent can say the
tests passed when they did not, can cut the output, and pays tokens to read a long log. Forge's gates are
commands whose **real exit code** decides. The server runs them with no agent involved, so no one can fake
the result. Forge's v0.7 lessons come with them:
- retry only what failed, then one full run;
- hold a gate whose machine is not ready (memory, port, container) instead of failing it;
- give counts and failing names, so no one has to read the log;
- name a flaky test, and never call it fixed;
- show live progress;
- keep a running cost line.

## Decisions (owner: proceed autonomously; these are the designer's calls)

### Stage kinds
A stage gets an optional `kind`. It is `agent` (default, today's shape), `gate` or `approval`.
- **Gate stage:** `{"name", "kind": "gate", "project": "<project id>", "run": ["cmd", …], "retry": "cmd" | null,
  "before": ["…"], "timeout_min": 60}`.
  - `run` holds 1–10 commands.
  - `retry` is allowed only when `run` has exactly one command, and must differ from it.
  - `before` holds 0–5 pre-flight lines (below).
  - `timeout_min` is an int from 1 to 720, per command.
  - The project must exist and have a directory.
- **Approval stage:** `{"name", "kind": "approval", "message": "…" (≤ 2000), "project": "<id>" | null,
  "before": []}`.
- **Agent stages** may also carry `before` (pre-flight on any stage, as delta F115 suggests).
- **Command rules** (forge F117/F103), checked on save with a 422 and a plain reason:
  - non-empty, ≤ 500 characters;
  - none of `;` `&` `|` `>` `<` `` ` `` `$(`, and no newline, NUL or other control character;
  - `clean_text`ed;
  - one command is one program call (e.g. `npm test -- --runInBand`, `pytest -q`).
- **Who may save:** workflows stay admin-only to create or edit, so an admin's save is the trust step (forge's
  TRUST GRAPH: a person approved the exact commands). The run's `project_ids` include gate and approval
  projects, so starting a run needs contributor on them as today.

### Running a gate (F66, F103)
- The server runs each command itself with `asyncio.create_subprocess_exec`:
  - The shell is fixed: `/bin/bash` if it exists, else `/bin/sh`, called as `[shell, "-c", cmd]`. Never
    `$SHELL` or a PATH lookup.
  - The working directory is the project directory, with a new session (process group) and stdin
    `/dev/null`.
- **Environment:** built from scratch. It holds the allowlisted server slice the agent env uses, then the
  project env (with the same blocked keys removed, `PATH_PREPEND` honoured), then `CI=1`. It never holds an
  agent or user credential, an org key, `CO_*` or a task token.
- **Guard check:** just before a command starts (every hand-out, and again after a restart), the command goes
  through `guard.rules.evaluate("Bash", {"command": cmd}, policy, cwd)`.
  - The policy is the gate project's, built the way a task's is (`build_policy` + `policy_inputs.gather` with
    a stand-in task that has no grants).
  - This runs even when the project's guard is off, because no person watches a gate.
  - `deny`: the gate fails without running, with "blocked by the guard: <reason>".
  - `approve`: the stage waits in `needs_approval` with the reasons listed. A maintainer of the gate's
    project, or an admin, clicks **Allow and run** (`POST …/stages/{i}/allow`). The allowance covers this
    hand-out only, and a rerun asks again.
- **One hand-out per stage start.** `stage_state[i].handout` is a uuid. Each start of the stage makes a new
  one, and so does a retry of the run. A result is recorded only if the run is still active, the stage is
  still current and the hand-out is unchanged. Cancel or retry drops a late result.
- **Pending and passed:**
  - Commands run in order, and the first failure stops the round.
  - Commands that already passed in this hand-out are not run again after a server restart.
  - If the server dies mid-command, nothing is recorded for that command. On the next tick the runner sees
    the stage "running" with no live process of its own, so it starts again from that command. The run log
    gets "resumed after a server restart".
- **Timeout:** past `timeout_min` the process group gets SIGTERM, then SIGKILL after 5 s. The outcome is
  `timed_out` with no exit code.
- **Cancel:** cancelling a run kills the gate's process group the same way, and the outcome is `cancelled`.
- **Exit code alone decides pass or fail.** Exit 0 passes. Anything else, a timeout or a kill is not a pass.
- **Single process:** `start.sh` runs one uvicorn process. Live process handles live in the runner's memory,
  and the design relies on that, as the runner already does.

### Retry only what failed, then full (F117)
- **Gate with a `retry` command:**
  1. Run the full command.
  2. If it fails, run the retry command.
  3. If the retry fails, the gate fails.
  4. If the retry passes, run the full command once more. The gate passes only if that full run passes.
- One retry round per hand-out. A retry never completes a gate on its own.
- **Agent stages keep their attempts:**
  - `retry_run` no longer deletes the failed stage's tasks.
  - Tasks get `stage_attempt`, and `stage_state[i].attempt` counts up.
  - The runner looks only at the current attempt's tasks. `{{prev}}`/`{{stageN}}` use the current attempt's
    completed tasks.
  - The board shows earlier attempts, collapsed.

### Pre-flight (F115)
- Fixed forms only, validated on save:
  - `free memory <n> GB|MB` (GB allows one decimal);
  - `port <n> open|free` (1–65535);
  - `container <name> running`, where the name matches `^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$`.
- **When checked:** when the runner would start the stage, and again just before a gate's first command.
- **Not met:** the stage is `held`, not started and not failed. The reason is in plain words ("free memory is
  2.1 GB, needs 4 GB"). The runner checks again on every tick, and the run stays `running`.
- **How each form is checked:**
  - **Memory:** Linux `/proc/meminfo` `MemAvailable`. macOS: `/usr/bin/vm_stat` (free + inactive +
    speculative pages × page size). Anything else cannot answer.
  - **Port:** a socket connect to 127.0.0.1 and ::1 with a 1 s timeout. It is open if either connects.
  - **Container:** `docker ps --filter name=^<n>$ --filter status=running --format {{.Names}}` as argv, with
    a 5 s timeout.
  - A check that cannot answer counts as not met.
- **Host-wide:** pre-flight checks the whole server. The held reason also names the other active runs whose
  stages use the same project (a count, plus run names the viewer may see).

### Results without logs (F116a, F119)
- **Log file:** each command's output (stdout and stderr together) goes to
  `workspaces/_gates/<run_id>/<stage>-<n>.log` (dirs 0700, files 0600). It is capped at 20 MB, and writing
  stops past the cap while the process keeps running. The last 5 logs per stage are kept. `_gates` is added
  to the guard's protected paths, so agents cannot read it.
- **Parser:** `app/gates/test_results.py`, a pure module and a port of forge `gate-results.js`.
  - Runners: node:test, TAP, jest, vitest, pytest, Playwright, mocha, go test.
  - The summary line wins. Without one it counts per-test lines (a cut-off run).
  - Input ≤ 20 MB, lines cut to 4,000 chars, anchored patterns, no catastrophic regex.
  - Failing names: at most 50, each at most 160 chars. Unknown runner → counts are null, and the exit code
    still decides.
  - It runs off the event loop (`asyncio.to_thread`) and never inside a DB transaction.
- **Record:** table `workflow_gate_runs`, one row per command run. Columns:
  - run id (FK, cascade), stage index, attempt, hand-out, command, `kind` (`full` | `retry`);
  - started/finished, ms, exit code (null when none);
  - outcome (`passed` | `failed` | `timed_out` | `cancelled` | `refused`);
  - passed/failed/skipped counts (nullable), failing names (JSON), flaky names (JSON), runner name;
  - code-state hash (nullable), log file name.
  - The last 50 rows per run and stage are kept.
- **Live progress (F119):** while a command runs, the runner reads the log tail (the last 256 KB) on each
  `GET /runs/{id}` (at most once per 2 s per run, cached). It shows "31 of 63 tests done, 0 failed", or "31
  tests done" when no total was announced. The Run view already polls the run.
- **Log tail:** `GET /workflows/runs/{id}/gate-runs/{rid}/log` returns the last 200 lines (≤ 64 KB). Known
  secrets (project env values) are redacted. It needs run visibility.

### Flaky named, never "fixed" (F118)
- **Code state:** a sha256 over three things, taken right before each command starts:
  - `rev-parse HEAD`;
  - `diff --no-ext-diff --no-textconv --binary HEAD`;
  - untracked-not-ignored files, as name, size and mtime from `ls-files --others --exclude-standard -z`.
  - It is null when the directory is not a git repo, git fails, or the diff is over 50 MB.
- **gitsafe gets exactly two new entries:** `diff` with exactly `--no-ext-diff --no-textconv --binary HEAD`,
  and `ls-files` with exactly `--others --exclude-standard -z`. Both come under the existing program
  switches. The diff output is hashed, never stored or shown.
- **When a test is flaky:** a full run passes, and an earlier full run of the same stage in this run (any
  attempt) failed with a real exit code and the same non-null code state. Then those runs' failing names are
  stored on the passing record as flaky. If the names were unreadable, the record says "failed then passed
  unchanged". A changed code state, or a run with no exit code, never counts.
- **Where it shows:** the gate card, the run log line and the next stage's `{{prev}}` text, as "flaky (a
  flaky test is not a fixed test): …".

### Honest state words (F112) and the run log
- **Gate stage states:**
  - `waiting` (not started);
  - `held` (pre-flight);
  - `needs_approval`;
  - `running`, only while this process has a live child. It reads "running `pytest -q` (1 of 3 commands
    passed)";
  - otherwise "not yet passed (k of n commands passed, none running)";
  - `passed` or `failed`.
- **Approval stage:** `waiting_approval`. The run is `paused` while it waits.
- **Run log:** a new `workflow_runs.log` JSON list of `{at, text}`, newest 200 kept. Lines read:
  - "Step <name> (gate) passed", "… failed: <first failing command>, exit 1, 2 failed: a, b" and "… still
    needs: <command>";
  - "Step <name> (approval) approved by <user>" and "… rejected by <user>: <reason>";
  - "Step <name> held: <reason>".
- **The live scene:** `_stage_state` maps gate and approval states to the existing scene states (`done`,
  `current`, `failed`, `waiting_approval`, `next`). A held stage is `current`.

### Approval stages (F66 human steps)
- The stage starts and the run goes `paused`.
- Anyone who may manage the run (creator, admin, maintainer of a touched project) can approve or reject:
  - `POST …/stages/{i}/approve`;
  - `POST …/stages/{i}/reject` with `{reason}` (≤ 500), which fails the run with that reason.
- Approve moves on to the next stage. The approver and the time are stored in `stage_state[i]` and in the
  log.

### Cost line (F120)
Each stage's start and end time go in `stage_state[i]`. `GET /runs/{id}` returns `totals`:
- `stages_done`, `stages`, `minutes` (sum of the finished stages' wall time plus the current one so far);
- `cost_usd` and `tokens` (the input + output sum over all the run's tasks, every attempt);
- `last`, the last finished stage: name, minutes, cost and tokens.

When a stage finishes, the log line ends "So far: 3 of 6 steps done, 41 min, $2.10, 1.2M tokens (last:
Develop, 18 min, $1.40)". The Run view shows the same line in the header.

### Test-result chip on agent tasks (F116b)
- When an agent's Bash `tool_use` command looks like a test run, the worker remembers its id. A test run
  means one of these is the first program word, after optional `cd x &&`-free env assignments:
  - `pytest`, `python -m pytest`, `py.test`;
  - `npm|yarn|pnpm test` and `npm run test…`;
  - `npx vitest|jest|playwright`, `vitest`, `jest`, `mocha`;
  - `node --test`, `go test`.
- Its `tool_result` is parsed with the same parser on the full text, before `_short`, in `to_thread`.
- If the parser finds a runner, a `test_result` event `{runner, passed, failed, skipped, failing (≤ 10),
  command (≤ 200)}` is emitted.
- TaskDetail shows it as a chip in the run log: "Tests: 61 passed, 2 failed (test_a, test_b)".

## Goals
1. Parser (with fixtures per runner) and progress line.
2. Stage schema and validation for gate and approval stages, command rules, pre-flight forms and checks.
3. Two gitsafe entries plus the code-state hash.
4. Migration 0014:
   - `workflow_runs.stage_state` JSON and `workflow_runs.log` JSON;
   - `tasks.stage_attempt` int, default 0;
   - the `workflow_gate_runs` table.
5. Gate executor: fixed shell, env, guard check, log file, timeout, kill, record.
6. Runner integration:
   - stage kinds, hand-out, resume after a restart, hold, needs-approval;
   - retry round, attempts kept, flaky;
   - log lines and totals.
7. REST: run detail with stage state, gate runs, progress and totals; approve, reject and allow; log tail.
8. Editor UI: a stage kind select, gate fields, pre-flight lines, approval message.
9. Run view: gate card, approval card, earlier attempts, cost line, run log.
10. F116b chip; README section "Command gates".

## Non-goals (parked)
- Typed stage outputs, automatic retry-once of agent stages, a per-run budget cap (5b).
- A fix-it loop back to an agent stage after a failing gate. Today a failed gate fails the run, and "Retry"
  runs it again.
- Windows server support (the shell rule is POSIX only).
- Live progress for commands an agent runs in the background.

## Anti-goals
- A gate that passes without a real exit code of 0, or a result recorded for a stale hand-out.
- A gate command that reaches the shell without a guard check, runs with a credential, or comes from `$SHELL`
  or PATH.
- A failed pre-flight that fails the run (it must hold).
- A flaky test called fixed, or a code change called flaky.
- A slow or hostile log hanging the server (caps, anchored patterns, parsing off the loop).
- Lost evidence: retry deletes nothing.

## Approaches considered
| Question | Options | Outcome |
|---|---|---|
| Who runs gate commands | an agent stage / **the server** | server: real exit code, no agent can fake it |
| Command trust | separate TRUST step / **admin save + guard check at run** | workflows are admin-only to edit, so the save is the approval; the guard still checks each run |
| Live state | DB polling of a pid / **in-memory handle in the single process** | single uvicorn process (start.sh), as the runner already assumes |
| Records | JSON on the run / **a table** | attempts and runs grow; a table prunes and queries cleanly |

## Builder's latitude
Module layout under `app/gates/`, the exact parser patterns (within forge's runner list and caps), the
stand-in task for the gate policy, UI layout and copy apart from the fixed words above, test structure.
