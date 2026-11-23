# Forge Phase 5a — Command Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Workflow stages can be server-run command gates (real exit code, guard-checked, retry-then-full, pre-flight hold, parsed results, flaky names, live progress) or human-approval stages. Runs keep every attempt, keep a run log and show a cost line. Agent test commands get a result chip.

**Architecture:** A new package `backend/app/gates/` holds pure pieces:
- `test_results` (the parser);
- `spec` (stage validation);
- `preflight`, `codestate`;
- `executor` (one command in a subprocess);
- `totals`.

`workflow_runner.py` gains a stage-kind state machine with an in-memory table of live gate tasks (one uvicorn process). Per-stage state lives in `workflow_runs.stage_state`, the run's log in `workflow_runs.log`, and each command run is a row in `workflow_gate_runs`.

**Tech Stack:** FastAPI, async SQLAlchemy, Alembic, pytest (SQLite locally, CI also Postgres), React 18 + antd 5 + vitest.

**Spec:** `docs/superpowers/specs/2026-11-23-forge-gates-phase5a-design.md`

## Global Constraints
- **Commands:**
  - Backend tests: from `backend/`, `/Users/ashutosh/Projects/Work/Claude-Orchestrator/backend/.venv/bin/python -m pytest -o addopts="" -q <paths>`.
  - Frontend: from `frontend/`, `npx vitest run <files>` and `npm run build`.
  - The machine is heavily loaded. Run only your own test files plus the files you touched. A timeout in an untouched frontend file is environmental.
- Commit folder by folder (backend, frontend, docs separately). **No `Co-Authored-By` trailer.**
- Never run the real `claude`. Never touch the real DB. Scratch servers use ports 8336/5336, never 8200/5200.
- Migration id `0014_gates`; down_revision `0013_briefs`. Use `JSON(none_as_null=True)` for nullable JSON columns.
- **Shell:** `/bin/bash` if it exists, else `/bin/sh`, called as `[shell, "-c", cmd]`. Never `$SHELL` or a PATH lookup.
- **Command rules:**
  - 1–500 characters after `clean_text`;
  - none of `;` `&` `|` `>` `<` `` ` `` `$(`;
  - no character below 0x20 and no 0x7f.
  - The 422 reason names the character.
- **Gate fields:**
  - `run`: 1–10 commands;
  - `retry`: only with exactly one `run`, and different from it;
  - `before`: ≤ 5 lines;
  - `timeout_min`: int from 1 to 720, default 60;
  - `project`: an existing project with a directory.
- **Approval:** `message` ≤ 2000 characters. A reject `reason` is 1–500 characters.
- **Pre-flight forms** (case-insensitive keywords, single spaces after trim):
  - `free memory <n> GB|MB` (GB allows one decimal);
  - `port <n> open|free` (1–65535);
  - `container <name> running`, where the name matches `^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$`.
- **Caps:**
  - log file 20 MB, and writing stops past the cap;
  - parser input 20 MB, lines cut to 4,000 characters;
  - failing names ≤ 50 (≤ 160 characters each);
  - 5 log files kept per run stage, 50 gate-run rows kept per run stage;
  - run log: newest 200 lines;
  - live tail read: the last 256 KB, cached 2 s per run;
  - log endpoint: the last 200 lines, ≤ 64 KB;
  - code-state diff over 50 MB → null.
- **Log dir:** `settings.workspaces_dir / "_gates" / <run_id> / "<stage>-<n>.log"`. Dirs are 0700 and files 0600. The `_gates` dir is a guard protected path.
- **Timeout kill:** SIGTERM to the process group, then SIGKILL 5 s later.
- **Fixed words (exact):**
  - "not yet passed ({k} of {n} commands passed, none running)";
  - "running `{cmd}` ({k} of {n} commands passed)";
  - "Step {name} (gate) passed";
  - "Step {name} (gate) failed: {cmd}, exit {code}";
  - "Step {name} (gate) still needs: {cmd}";
  - "Step {name} (approval) approved by {user}";
  - "Step {name} (approval) rejected by {user}: {reason}";
  - "Step {name} held: {reason}";
  - "resumed after a server restart";
  - "blocked by the guard: {reason}";
  - "a flaky test is not a fixed test";
  - "failed then passed unchanged";
  - "So far: {d} of {n} steps done, {M} min, ${C}, {T} tokens (last: {stage}, {m} min, ${c})".

## Review Focus
1. **A server restart mid-gate:** the gate resumes from the unrecorded command. Passed commands are not rerun, and a late result from the old hand-out is dropped. (Task 6 test: a stale-hand-out record is refused, and a restart resumes.)
2. **Retry of a failed run:** it must not delete tasks or gate rows, and must give a new hand-out, so a still-running old gate cannot record into the new attempt. (Task 6.)
3. **Hostile log content:** a 20 MB single line, or a line of 100k `=` characters, must parse in well under a second, with no regex built from input. (Task 1 timing test.)
4. **A failed pre-flight never fails the run:** it holds, re-checks and starts once met. A check that cannot answer is "not met". (Tasks 2 and 6.)
5. **Old workflows without `kind`** keep working unchanged (the agent default). The live scene and board must not crash on gate or approval stages with no tasks. (Tasks 2, 7, 9.)

---

### Task 1: Test-result parser and progress line (F116, F119)

**Files:**
- Create: `backend/app/gates/__init__.py` (empty)
- Create: `backend/app/gates/test_results.py`
- Create: `backend/tests/fixtures/gate_results/*.txt` (one or more fixture outputs per runner)
- Test: `backend/tests/test_gates_test_results.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class TestResult: runner: str | None; passed: int | None; failed: int | None; skipped: int | None; total: int | None; failing: tuple[str, ...]`
  - `parse(text: str) -> TestResult`. It never raises. An unknown runner gives `TestResult(None, None, None, None, None, ())`.
  - `progress(text: str) -> tuple[int, int | None, int] | None`, meaning `(done, total_or_None, failed)`. It is None when nothing is recognised.
  - `progress_line(p) -> str`: "31 of 63 tests done, 0 failed", or "31 tests done, 0 failed" without a total.
  - `MAX_INPUT = 20 * 1024 * 1024`, `MAX_LINE = 4000`, `MAX_NAMES = 50`, `MAX_NAME = 160`.

- [ ] **Step 1:** Read forge's upstream parser and its tests, read-only. Use `gh api repos/harishgoswami23/jspark-forge/contents/scripts/lib/gate-results.js?ref=v0.7.0 -H "Accept: application/vnd.github.raw"`, and the same for `tests/gate-results.test.js`. Port the per-runner patterns and the summary-first rule to Python, with anchored `re` patterns compiled once at import. If the fetch fails, write the patterns from each runner's documented summary format and note it in the report.
- [ ] **Step 2:** Write failing tests. Write one fixture per runner (node:test, TAP, jest, vitest, pytest, Playwright, mocha, go test), each with a summary, and assert the exact counts and failing names. Also test:
  - a cut-off pytest run with no summary is counted from per-test lines;
  - an unknown runner gives all None;
  - the 51st failing name is dropped, and a 300-character name is cut to 160;
  - text over 20 MB parses only the last 20 MB;
  - **timing:** `parse("=" * 5_000_000)` and `parse(("a" * 100_000 + "\n") * 50)` each finish in under 1 s (`time.monotonic`);
  - `progress` on a half-done vitest/pytest log gives `(done, total, failed)`, and `progress_line((31, 63, 0)) == "31 of 63 tests done, 0 failed"`, `progress_line((31, None, 0)) == "31 tests done, 0 failed"`.
- [ ] **Step 3:** Run the tests and confirm they fail. Implement. Run them and confirm they pass.
- [ ] **Step 4:** Commit with `feat(gates): test-result parser and progress line`.

### Task 2: Stage kinds, validation and pre-flight (F66, F115)

**Files:**
- Create: `backend/app/gates/spec.py`, `backend/app/gates/preflight.py`
- Modify: `backend/app/schemas.py` (`WorkflowStage`, `WorkflowStageItem`). `items` becomes required only for agent stages, and the extra fields are allowed.
- Modify: `backend/app/routers/workflows.py`. `create_workflow`/`update_workflow` call `spec.validate_stages`, which gives a 422 with the plain reason. `_validate_agents` checks only agent stages. `workflow_projects` adds the gate/approval `project` ids.
- Test: `backend/tests/test_gates_spec.py`, `backend/tests/test_gates_preflight.py`

**Interfaces:**
- Produces:
  - `spec.KINDS = ("agent", "gate", "approval")`
  - `spec.stage_kind(stage: dict) -> str`. A missing or unknown kind gives `"agent"`. This is used everywhere a stage is read.
  - `spec.check_command(cmd: str) -> str`: the cleaned command, or ValueError.
  - `async spec.validate_stages(s, stages: list[dict]) -> list[dict]`. It returns the normalized stages, with `timeout_min` defaulted to 60, `retry` defaulted to None and `before` defaulted to `[]`. It raises ValueError with a plain reason.
  - `preflight.parse(line: str) -> Check` (frozen dataclass `kind: "memory"|"port"|"container"`, plus `value`, `want`), or ValueError.
  - `async preflight.check(lines: list[str]) -> str | None`. None means all are met. Otherwise it returns the first unmet reason in plain words, for example "free memory is 2.1 GB, needs 4 GB", "port 5432 is not open", "port 3000 is in use", "container db is not running", or "could not check free memory".
  - Probes are module functions so tests can monkeypatch them: `_free_bytes() -> int | None`, `_port_open(port: int) -> bool`, `_container_running(name: str) -> bool | None`.

- [ ] **Step 1:** Write failing tests:
  - Old stages with no `kind` validate unchanged.
  - Each command-rule reject: `;`, `&&`, `|`, `>`, `<`, a backtick, `$(`, a newline, an empty command, 501 characters.
  - `retry` with two `run` commands is rejected, and `retry == run[0]` is rejected.
  - `timeout_min` of 0 and of 721 is rejected.
  - An unknown project, and a project without a directory, are rejected.
  - An approval message of 2001 characters is rejected.
  - Bad pre-flight lines (`free memory 4.25 GB`, `port 70000 open`, `container -x running`, `disk 4 GB`) are rejected.
  - `workflow_projects` includes the gate project.
  - The REST create endpoint returns 422 with the reason.
  - Pre-flight with monkeypatched probes: met; unmet memory with the exact reason; probe None means "could not check…"; port open/free both ways; docker missing means unmet.
  - `_port_open` against a real listening socket on an ephemeral port returns True; after it closes, False.
- [ ] **Step 2:** Implement.
  - Memory reads `/proc/meminfo` `MemAvailable` on Linux, and on macOS `/usr/bin/vm_stat` run as argv with a 5 s timeout: (free + inactive + speculative) × page size, where the page size is parsed from its header. Anything else gives None.
  - Port tries a connect to 127.0.0.1 and ::1 with a 1 s timeout each.
  - Docker runs as argv with a 5 s timeout. Docker missing or failing gives None.
  - Run the blocking probes in `asyncio.to_thread`.
- [ ] **Step 3:** Run the tests and confirm they pass. Commit with `feat(gates): gate and approval stage validation and pre-flight checks`.

### Task 3: gitsafe additions and code state (F118)

**Files:**
- Modify: `backend/app/gitsafe.py`. Add exactly two entries: `diff` with exactly `--no-ext-diff --no-textconv --binary HEAD`, and `ls-files` with exactly `--others --exclude-standard -z`, each "any order, nothing else", like the existing extras. Update the docstring.
- Create: `backend/app/gates/codestate.py`
- Test: `backend/tests/test_gitsafe.py` (add cases), `backend/tests/test_gates_codestate.py`

**Interfaces:**
- Produces: `async codestate.code_state(directory: str) -> str | None`, a 64-hex sha256. It is None when the directory is not a git repo, git fails, or the diff is over 50 MB. It runs through `gitsafe.run_git` with a 30 s timeout per call.

- [ ] **Step 1:** Write failing tests:
  - gitsafe accepts both new forms and rejects `diff HEAD`, `diff --binary HEAD~1`, `diff --no-ext-diff --no-textconv --binary HEAD --output=x` and `ls-files --others`.
  - In a temp git repo, code_state is stable when nothing changes, changes after a tracked edit, changes after a new untracked file, and does not change for a new ignored file.
  - A non-repo gives None.
- [ ] **Step 2:** Implement and run the tests. Commit with `feat(gates): code-state hash with two exact gitsafe reads`.

### Task 4: Migration 0014, models, run log and totals (F120)

**Files:**
- Create: `backend/migrations/versions/0014_gates.py`
- Modify: `backend/app/models.py`:
  - `WorkflowRun.stage_state` (JSON dict, default dict) and `WorkflowRun.log` (JSON list, default list);
  - `Task.stage_attempt` (Integer, default 0, server_default "0");
  - new `WorkflowGateRun`.
- Create: `backend/app/gates/runlog.py`, `backend/app/gates/totals.py`
- Test: `backend/tests/test_gates_runlog_totals.py`, plus `backend/tests/test_migrations.py` if it lists heads.

**Interfaces:**
- Produces the `WorkflowGateRun` table, `workflow_gate_runs`. Columns:
  - `id` String(32) uuid hex;
  - `run_id` (FK workflow_runs.id ondelete CASCADE, index);
  - `stage_index` int, `attempt` int, `handout` String(32), `command` Text, `kind` String(8);
  - `started_at`, `finished_at` (tz DateTime, nullable);
  - `duration_ms` int nullable, `exit_code` int nullable, `outcome` String(16);
  - `runner` String(32) nullable;
  - `passed`/`failed`/`skipped` int nullable;
  - `failing` JSON list, `flaky` JSON list nullable;
  - `code_state` String(64) nullable, `log_name` String(200) nullable.
- `runlog.append(run, text: str) -> None`: adds `{"at": iso utc, "text": text}`, keeps the newest 200 and reassigns the list so SQLAlchemy sees the change.
- `async totals.compute(s, run) -> dict`, with keys:
  - `stages_done`, `stages`, `minutes` (one-decimal float), `cost_usd` (float), `tokens` (int);
  - `last`: `{"name", "minutes", "cost_usd", "tokens"}` or None.
  - Wall times come from `stage_state[str(i)].started_at/finished_at`. Cost and tokens are the sums of the run's tasks (every attempt) of `total_cost_usd` and `input_tokens + output_tokens`.
- `totals.line(t: dict) -> str`, the fixed "So far: …" words. Tokens print compact (1.2M, 41k, 900).

- [ ] **Step 1:** Write failing tests:
  - The migration upgrades and downgrades on SQLite (follow the existing migration tests' pattern).
  - runlog keeps the newest 200.
  - totals with two finished stages and one running gives the right sums, and `line` matches the fixed words exactly for a sample.
- [ ] **Step 2:** Implement and run the tests. Commit with `feat(gates): gate run records, run log and run totals`.

### Task 5: Gate executor (F66, F103, F116a)

**Files:**
- Create: `backend/app/gates/executor.py`
- Modify: `backend/app/guard/runtime.py` (add `workspaces/_gates` to `protected` in `build_policy`)
- Test: `backend/tests/test_gates_executor.py`

**Interfaces:**
- Consumes: `credentials.base_env()`, `agent_env._apply_project_env`, `guard.runtime.build_policy`, `guard.policy_inputs.gather`, `guard.rules.evaluate`, and Task 1's `parse`.
- Produces:
  - `shell_path() -> str`
  - `gate_env(project) -> dict[str, str]`: `base_env()`, then the project env through `_apply_project_env`, then `CI=1`. No credential and no `CO_*` key.
  - `async guard_verdict(s, project, cmd: str)`: the `rules.evaluate("Bash", {"command": cmd}, policy, project.directory)` Verdict. The policy comes from `build_policy(task_id="gate", project=…, workspace=Path(project.directory), cwd=Path(project.directory), claude_config_dir=None, mcp_tools=None, extra=gather(...))`. The stand-in task is `SimpleNamespace(id="gate", root_id="gate")`, and `owner_id=None`.
  - `log_file(run_id: str, stage_index: int, n: int) -> Path`. It creates the dirs at 0700.
  - `prune_logs(run_id, stage_index, keep=5) -> None`.
  - `@dataclass class Outcome: outcome: str; exit_code: int | None; started_at: datetime; finished_at: datetime; duration_ms: int; result: TestResult`.
  - `async run_command(cmd, *, cwd, env, log_path, timeout_s, on_spawn=None) -> Outcome`. The outcome is `passed` (exit 0), `failed`, `timed_out` or `cancelled`. `on_spawn(proc)` lets the runner hold the handle. Output streams to the log up to 20 MB. The parse runs in `asyncio.to_thread` on the log (read back up to 20 MB) after exit.
  - `async kill_group(proc) -> None`: SIGTERM to the group, waits 5 s, then SIGKILL.
  - `CancelledError` while running kills the group, then re-raises after recording nothing. The caller decides.

- [ ] **Step 1:** Write failing tests, using real subprocesses with harmless commands:
  - `true` passes, and `exit 3` fails with exit_code 3.
  - Output lands in the log at mode 0600.
  - A 20 MB cap is honoured (monkeypatch the cap down to 1 KB with `yes | head` replaced by `python3 -c` printing many lines; note `|` is a validator concern, not the executor's).
  - `sleep 5` with `timeout_s=0.5` is `timed_out` with exit None, and its child process is gone (start `sleep 30 & wait` through the shell, check the pid no longer exists).
  - Cancelling the asyncio task kills the group.
  - `gate_env` drops `ANTHROPIC_API_KEY` from the project env and has no `CO_` keys even if the server env has them (monkeypatch `os.environ`).
  - `shell_path` ignores `SHELL=/bin/true`.
  - `guard_verdict` on `rm -rf /` is deny, and on `pytest -q` is allow (temp project, own trust).
  - `build_policy` protected paths include the `_gates` dir.
  - `prune_logs` keeps 5.
- [ ] **Step 2:** Implement and run the tests. Commit with `feat(gates): server gate executor with fixed shell, clean env and guard check`.

### Task 6: Runner integration (F66, F103, F112, F115, F117, F118, F120)

**Files:**
- Modify: `backend/app/workflow_runner.py`
- Test: `backend/tests/test_gates_runner.py` (new). Keep `tests/test_workflow_start_race.py` and the other workflow tests green.

**Interfaces:**
- Consumes: Tasks 1–5.
- `stage_state[str(i)]` keys:
  - `attempt` int, `status`, `reason`;
  - `started_at`/`finished_at` (iso);
  - `handout` (hex), `passed_cmds` (list of run indices passed this hand-out), `round` (`full` | `retry` | `full2`);
  - `allowed` (bool, for this hand-out), `hits` (list of guard reasons);
  - `decided_by`, `decided_at`;
  - `summary` (text used as this stage's `{{prev}}` output).
- `status` values:
  - gate: `waiting`, `held`, `needs_approval`, `running`, `passed`, `failed`;
  - approval: `waiting_approval`, `approved`, `rejected`;
  - agent: `running`, `done`, `failed`, `held`.
- Produces (used by Task 7):
  - `WorkflowRunner.live_gate(run_id) -> dict | None`: `{stage_index, command, k, n}` while a child runs.
  - `async approve_stage(run_id, i, user) -> None`
  - `async reject_stage(run_id, i, user, reason) -> None`
  - `async allow_gate(run_id, i, user) -> None`
  - Each raises `ValueError` with a plain reason when the stage is not in the right state.
  - `retry_run` keeps tasks and rows: attempt + 1, new hand-out.
  - `cancel_run` also kills a live gate.
  - `_stage_outputs` uses only the current attempt's tasks per stage, plus the gate/approval `summary`.

- [ ] **Step 1:** Write failing tests. Drive the runner with `_advance` calls and a fake worker. Monkeypatch `executor.run_command` and `executor.guard_verdict` in most tests, and use one real `true`/`false` gate end-to-end.
  - An agent → gate → agent run passes through. The gate row is recorded, and the log has "Step Test (gate) passed" and a "So far:" line.
  - A failing gate fails the run with "Step Test (gate) failed: pytest -q, exit 1".
  - The retry round runs full fail, then retry pass, then full pass, which passes. Full fail, retry fail gives failed. Full fail, retry pass, full fail gives failed. A retry pass alone never passes the gate.
  - Multi-command: the second command fails, which stops the round. On a resume after a simulated restart (clear the runner's live table while `status == running`), the first command is not rerun and the log has "resumed after a server restart".
  - A stale hand-out: a result arriving after `retry_run` changed the hand-out is not recorded.
  - Guard deny fails the stage with "blocked by the guard: …". Guard approve puts it in `needs_approval`, and `allow_gate` runs it. `allow_gate` from the wrong state raises.
  - Unmet pre-flight holds with "Step Test held: …", and the run stays `running`. Once met, it starts.
  - Flaky: full fails (names a, b, state X), retry passes, full passes with state X, so the record's flaky is ["a", "b"] and the summary includes "a flaky test is not a fixed test". With state Y there is no flaky. With a None state there is no flaky.
  - An approval stage pauses the run. Approve moves on, and the log has "approved by <user name>". Reject fails the run with the reason.
  - `retry_run` on a failed agent stage keeps the old tasks (`stage_attempt` 0) and creates attempt-1 tasks, and the runner ignores attempt-0 failures.
  - `cancel_run` during a running gate kills it, and the outcome is `cancelled`.
- [ ] **Step 2:** Implement.
  - Live gates: `self._gates: dict[(run_id, i), asyncio.Task]` and `self._procs: dict[(run_id, i), (proc, cmd, k, n)]`.
  - Record a result in a fresh session. Re-read the run and check that the status is active, `current_stage == i` and the hand-out is equal. Only then insert the row, update `stage_state` (reassign the dict) and the log, then commit, then call `_advance`.
  - Prune rows to 50 and logs to 5 per run stage.
  - `_tick` resumes any `running` gate stage with no live task. Keep the pending-claim race guard unchanged.
- [ ] **Step 3:** Run the new tests and every existing `tests/test_*workflow*`, `test_run*`, and `test_api.py -k workflow`. Commit with `feat(gates): runner runs gate and approval stages, keeps attempts, names flaky tests`.

### Task 7: REST for gates, approvals, log tail and run detail

**Files:**
- Modify: `backend/app/routers/workflows.py`, `backend/app/schemas.py`
- Test: `backend/tests/test_gates_api.py`

**Interfaces:**
- `GET /workflows/runs/{id}` adds these keys:
  - `stage_state`;
  - `gate_runs` (rows for the run, newest first, ≤ 200, with no `log_name` path, only `has_log`);
  - `live`: `runner.live_gate()` plus `progress_line` (from the last 256 KB of the live log, cached 2 s), or None;
  - `totals`: `totals.compute`, plus `line`;
  - `log`.
  - `WorkflowRunOut` gains `log: list = []` and `stage_state: dict = {}`.
- `POST /workflows/runs/{id}/stages/{i}/approve` and `/reject` (`{reason}`): manage permission, as in `_require_run(manage=True)`.
- `POST /workflows/runs/{id}/stages/{i}/allow`: admin, or maintainer of that gate's project. Otherwise 403.
- `GET /workflows/runs/{id}/gate-runs/{rid}/log`: run visibility. It returns `{"lines": [...], "truncated": bool}`. It reads the last 200 lines (≤ 64 KB). The values of the project's env (len ≥ 6) are replaced by `[redacted]`, then `clean_text` is applied. A missing file gives `{"lines": [], "truncated": false}`.
- `_stage_state` for `/runs/live` reads `stage_state` for gate and approval stages:
  - `passed`/`approved` → `done`;
  - `failed`/`rejected` → `failed`;
  - `waiting_approval`/`needs_approval` → `waiting_approval`;
  - other states at the current index → `current`, and before it → `done`, after it → `next`.
- A runner `ValueError` → 409 with the reason.

- [ ] **Step 1:** Write failing tests:
  - Run detail has the new keys for a run with a gate row.
  - approve/reject check permissions (a viewer gets 403, a creator gets 200) and give 409 on the wrong state.
  - allow: a contributor gets 403, the gate-project maintainer 200, an admin 200.
  - The log tail redacts a project env value and caps at 200 lines.
  - The log endpoint is 404 for an invisible run.
  - `/runs/live` with a gate stage does not crash and shows `current`.
  - An old run without `stage_state` still renders.
- [ ] **Step 2:** Implement and run the tests plus `tests/test_runs_live.py` and `tests/test_run_scoping.py`. Commit with `feat(gates): run detail, approvals, gate allow and log tail endpoints`.

### Task 8: Workflow editor — stage kinds (frontend)

**Files:**
- Modify: `frontend/src/components/WorkflowsView.jsx`, `frontend/src/api.js` (new endpoints for Task 9)
- Create: `frontend/src/gates/StageFields.jsx` (gate and approval field groups), `frontend/src/gates/gates.js` (a client copy of the command check, giving an inline error before save)
- Test: `frontend/src/gates/StageFields.test.jsx`, `frontend/src/gates/gates.test.js`, `frontend/src/components/WorkflowsView.test.jsx` (extend)

**Interfaces:**
- Each stage gets a "Kind" select: Agents (default), Command gate, Approval.
- **Gate fields:**
  - Project (select of projects with a directory);
  - Commands (list, 1–10);
  - Retry command (enabled only with one command);
  - Before (list, ≤ 5, placeholder `free memory 4 GB`);
  - Timeout (minutes, 1–720, default 60).
- **Approval fields:** Message, and Project (optional).
- Saving an old workflow without `kind` sends no `kind` for agent stages.
- The server's 422 reason shows under the form.
- The workflow list shows a gate stage as `⚙ name` and an approval stage as `✋ name`.
- api.js gains `approveWorkflowStage(id, i)`, `rejectWorkflowStage(id, i, reason)`, `allowWorkflowGate(id, i)` and `workflowGateLog(id, rid)`.

- [ ] **Step 1:** Write failing tests:
  - Choosing Command gate shows the gate fields and hides the agent items.
  - Retry is disabled with two commands.
  - `gates.checkCommand('a && b')` returns the `&` reason, and `checkCommand('pytest -q')` returns null.
  - The saved payload for a gate stage has `kind: 'gate'`, `run`, `project`, `timeout_min` and no `items`.
  - An approval stage payload is right.
- [ ] **Step 2:** Implement. Run the new tests plus WorkflowsView's, then `npm run build`. Commit with `feat(gates): workflow editor for gate and approval stages`.

### Task 9: Run view — gate card, approval card, attempts, cost line, run log (frontend)

**Files:**
- Modify: `frontend/src/runs/RunView.jsx`, plus its helpers if any (`tasksOfStage` must filter by `stage_attempt` = the stage's current attempt, and show earlier attempts collapsed)
- Create: `frontend/src/gates/GateCard.jsx`, `frontend/src/gates/ApprovalCard.jsx`, `frontend/src/gates/RunLog.jsx`
- Test: `frontend/src/gates/GateCard.test.jsx`, `frontend/src/gates/ApprovalCard.test.jsx`, `frontend/src/runs/RunView.test.jsx` (extend)

**Interfaces:**
- **GateCard** shows:
  - the state words (for a gate in `running` without `live`, it shows the "not yet passed (k of n commands passed, none running)" words);
  - the live command and progress line;
  - a held or blocked reason;
  - `needs_approval` hits with an "Allow and run" button (shown to admins or maintainers of the gate project; the server decides);
  - the gate runs, newest first: command, kind, outcome, exit, time, counts, failing names, a flaky tag with "a flaky test is not a fixed test", and a "Show log" button (fetches the tail into a `<pre>`).
- **ApprovalCard** shows the message, plus Approve and Reject (reason required) for a `waiting_approval` stage.
- The header shows `totals.line`.
- **RunLog** lists `log` newest first.
- The Comets timeline still renders for gate and approval stages, which have task_count 0.

- [ ] **Step 1:** Write failing tests:
  - GateCard renders the "not yet passed" words when running with no live handle, and the progress line when live.
  - A flaky tag shows.
  - "Show log" fetches and renders lines.
  - Allow calls the api.
  - ApprovalCard: Reject without a reason is blocked, and Approve calls the api.
  - RunView with a gate stage selected renders GateCard, and shows the totals line.
  - Earlier attempts are collapsed under "Earlier attempts (1)".
- [ ] **Step 2:** Implement. Run the new tests plus RunView's, then `npm run build`. Commit with `feat(gates): run view gate and approval cards, attempts, cost line and log`.

### Task 10: Test-result chip on agent tasks (F116b) and README

**Files:**
- Modify: `backend/app/worker.py` (in `_handle_stream_event`: remember Bash `tool_use` ids whose command looks like a test run; on their `tool_result`, parse the full `flat` text in `asyncio.to_thread`, and emit `test_result` when `runner` is not None)
- Create: `backend/app/gates/testcmd.py` with `looks_like_test(command: str) -> bool`
- Modify: `frontend/src/components/TaskDetail.jsx` (render a `test_result` event as a chip: "Tests: 61 passed, 2 failed (a, b)")
- Modify: `README.md` (section "Command gates": gate and approval stages, command rules, guard check and Allow, retry round, pre-flight, results/flaky/progress, cost line, test chip, restart behaviour, what is not covered)
- Test: `backend/tests/test_gates_testcmd.py`, `backend/tests/test_worker_test_result.py` (feed the stream handler a tool_use + tool_result pair with pytest output, and assert the event), `frontend/src/components/TaskDetail.test.jsx` (chip)

**Interfaces:**
- `looks_like_test`:
  - It allows leading `VAR=value` words.
  - It is true for `pytest`, `python -m pytest`, `python3 -m pytest`, `py.test`, `npm test`, `npm run test…`, `yarn test`, `pnpm test`, `npx vitest|jest|playwright test`, `vitest`, `jest`, `mocha`, `node --test` and `go test`.
  - It is false for `cat pytest.ini`, `echo npm test` and `git commit -m "go test"`.
- The event payload is `{runner, passed, failed, skipped, failing: [≤10], command: ≤200 chars}`. The command and names go through the task's secret redaction (`_short`-style, with `ctx["secrets"]`).

- [ ] **Step 1:** Write the failing tests, implement, and run the tests (backend test files above, the TaskDetail test). Commit backend, frontend and docs separately: `feat(worker): test-result event for agent test commands`, `feat(ui): test-result chip`, `docs(readme): command gates`.
