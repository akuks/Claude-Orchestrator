# Forge port — Phase 3a: Resume and handoff — design

Program: port every jspark-forge feature into the orchestrator. Inventory and phasing:
`docs/research/jspark-forge-feature-inventory.md` (§1B F11–F25, §2 rows F11–F25 and F77, §4, §5 Phase 3) and the
v0.5–v0.6 delta `docs/research/jspark-forge-delta-v0.5-v0.6.md` (rows F79–F87, F93, F96–F99, F106; §4). Phase 1
(co-guard, block list, live approvals; `2026-11-21-forge-guard-phase1-design.md`) and Phase 2 (policy, grants,
deny-and-resume, redaction; `2026-11-21-forge-policy-phase2-design.md`) are merged on main (174706b). Their
rulings R1–R29 and the Phase 2 decisions are binding precedent here.

Phase 3 is too large for one reviewable plan, so it is split:
- **3a (this spec):** the handoff, notes, the Continue button, crash recovery, log replay, git facts (outside
  commits, no commits), the credentials note, ground rules, compaction re-injection, and files outside the
  workspace.
- **3b (next spec):** token care and the Questions inbox. See Non-goals.

## Intent
Today a stopped run is a dead end. A crash restarts the task from its original prompt
(`worker._requeue_pending` sets RUNNING back to QUEUED, and the task runs again from `task.prompt`). A failed or
cancelled run can only be followed up by a person who writes a new prompt from memory. Forge's answer is a
one-page handoff that is always current, notes the agent keeps, and a resume step that says what was half done.

3a ports these to a headless, multi-user server. The server already sees every stream event, so it builds the
handoff itself and keeps one per thread. A person sees it as a "Where it stands" card and continues with one
button. A run cut off by a server restart resumes its own session with a record of what was in flight. Every
continuation that cannot resume its session starts fresh with the handoff instead of starting bare.

## Verified against the code (and where the research docs are out of date)
- `_requeue_pending` (`worker.py:240`) re-runs orphaned RUNNING tasks from `task.prompt` with their *parent's*
  `resume_session_id`, never their own `session_id`. Confirmed (inventory §0.3).
- The claude child runs with `start_new_session=True` (`worker.py:621`), so it can **outlive a killed server**. A
  requeue without stopping it would put two agents in one workspace. The docs do not mention this.
- **Contradiction (delta F80/§4 quick win 5):** the spool is already fsynced. `co_hook._spool_strict` fsyncs every
  line, and the folder on create (Phase 2 final wave). No fsync work is left for 3a.
- **Contradiction (delta F81):** the last unanswered `tool_use` is **not** derivable from `TaskEvent`s today.
  `tool_use` events store `{name, input}` and `tool_result` events store `{content}` (`worker.py:1112, 1120`), and
  neither has an id. 3a adds the ids.
- **Contradiction (delta F80):** "seen line plus PostToolUse records = the in-flight set" does not hold. PostToolUse
  spools a line only for approved or granted calls (`co_hook.other_event`), and the `seen` line carries no tool or
  command. 3a adds both.
- **Contradiction (delta F83 mapping):** `frontend/src/runs/runStages.js` is the workflow-stage helper. Nothing in
  the backend or frontend parses TodoWrite, TaskCreate or TaskUpdate (only `guard/rules.py` names TodoWrite as a
  safe tool). The step parser is new code.
- `read_spool` drops `seen` lines (`guard/runtime.py:395`), and `make_task_dir` wipes the task dir at the next
  start (`runtime.py:200`). So recovery must read the raw spool **before** the requeued run starts. Today a
  crashed run's spool records are never ingested, because ingestion happens only at the end of a run.
- Usage capture is result-only (`worker.py:1128-1141`). Confirmed; 3b handles it.
- Migrations live in `backend/migrations/versions/` (latest `0007_policy.py`), not `backend/alembic/versions/`.
- `gitsafe.check_args` allows `rev-parse`, `remote`, `for-each-ref`, `config --get` and `log`/`rev-list` with
  options only. There is no `status`, no `merge-base` and no revision arguments, so 3a extends the allowlist.
  "Refs decide no-commits" (bf751a7) is confirmed in `guard/trust.py`.
- Single process: the README says to run uvicorn without `--workers`, and boot-time requeue relies on it. 3a keeps
  that assumption and records it.

## Decisions (owner: proceed autonomously; these are the designer's calls)
- **One handoff per thread (F11).** Table `TaskHandoff`, unique on `root_id`. Its columns:
  - `task_id`: the step it describes;
  - `version`, `status` (`open` | `closed`), `saved_at`;
  - `reason`, one of: `run ended`, `cut off`, `working`, `notes`, `continue`, `recovered`, `manual`;
  - `content` (structured JSON for the card) and `text` (the rendered form that is injected, ≤ 600 words);
  - `prev_content` / `prev_text`, the previous good copy (F82). The DB transaction is the durable write, so forge's
    file fsync and the prev-file dance reduce to keeping one previous version.

  The step described is the thread's latest **main-line** step, meaning a step whose `origin` is not `agent`.
  Dispatched children appear only as counts ("child tasks: 2 running, 1 failed"). Deleting a thread deletes its
  handoff.
- **`Task.origin` (F83, F90a).** Values: `user` (default), `agent` (a coordinator dispatch), `guard` (a co-guard
  approval follow-up), `continue` (the Continue button) and `recovery` (reserved). The handoff's **Task** line is
  the latest `user` prompt of the thread, plus up to 4 earlier `user` prompts. Prompts we inject (guard follow-ups,
  Continue, recovery) are never "the user's last request". The migration backfills `guard` for titles starting
  `co-guard follow-up:`. Older dispatched children stay `user`, which is a known, harmless gap.
- **Handoff content (F11, F12, F17, F18, F60, F83, F84, F86, F106).** The builder is a pure function of:
  - the thread's main-line steps;
  - the described step's events (the last `EVENTS_SCAN_MAX = 2000`);
  - git facts and the project's readonly/reference sources;
  - the thread's pending co-guard approvals ("Waiting on you");
  - other active runs in the same directory.

  It renders forge's layout:
  - **Header:** as of, saved because, branch @ sha, files not committed (capped "2000+"), and a session-ended or
    cut-off note. For external projects it adds "review of someone else's repo". It also adds a source line per
    readonly/reference source with its HEAD.
  - **Body fields:** Task, Step n of m, Done, Next action, Still running at cutoff, Waiting on you, Your last
    request, Requests before it, Claude's last reply (the last 70 words), Also worked in, and Claude's notes (with
    their time: "re-check any caution before acting on it").
  - **Lists:** Recent actions (the last 8 log lines), Files touched (≤ 50, newest first), Files outside the
    workspace that may vanish, Commits made outside this run, and Also active (≤ 2).
  - **More detail:** points at `GET /tasks/{id}/events`.

  Fields are cut at 200 characters. The cap is 600 words, trimmed in forge's 4 levels: drop recent actions, then
  files to 5, then notes to 60 words, then drop "also active".

  Step derivation, in priority order:
  1. the latest TodoWrite `todos` (the in-progress item, n of m);
  2. TaskCreate/TaskUpdate. The i-th TaskCreate of the step is id `str(i)`, and TaskUpdate `{taskId, status}`
     updates it. This is best-effort, because the CLI assigns the ids;
  3. a checkbox plan file the step wrote (≥ 2 `- [ ]` boxes, re-read from disk, ≤ 256 KB);
  4. a superpowers plan written under `docs/superpowers/plans/` (`### Task N:` headings), with its
     `.superpowers/sdd/<plan>/progress.md` "Task N: complete" lines;
  5. the notes header lines `Task:` / `Step: n of m: …` / `Next:`;
  6. the last user prompt.

  If the notes are newer than the plan file, the notes win (forge v0.5). Done also gets the last commit of the step
  (sha and subject).
- **Snapshots (F13).** A snapshot is taken:
  - when a step finishes (`_finish`: `run ended`, or `cut off` for a timeout or cancel);
  - at most every `HANDOFF_EVERY_SECONDS = 60` during a run, from the heartbeat loop and only when new events
    arrived (`working`);
  - on a notes update (`notes`);
  - before a Continue (`continue`) and on recovery (`recovered`);
  - on a person's refresh (`manual`).

  forge's "only when there is work worth resuming" rule becomes: a snapshot whose content is equal to the stored
  one apart from `as_of` does not bump the version. During a live run the worker also writes the rendered text to
  `${CO_TASK_DIR}/handoff.md` (0600), for compaction re-injection. It writes the file at run start, from the stored
  handoff, and again after each snapshot, so an early compaction finds it.
- **The heartbeat loop.** One loop per run sets `heartbeat_at` and drives the `working` snapshots. The handoff
  content key `notes` is `{"text", "at"}` (iso-8601) or null.
- **Closing (F16).** A person can mark a thread's handoff done (`POST /tasks/{id}/handoff/close`). The card then
  collapses to "closed", and the next run of the thread reopens it. There is no archive pruning: rows are small,
  and a thread delete removes them.
- **Notes (F14).** These are columns on `Task`: `notes` (≤ 120 words and ≤ 1000 chars, after stripping hidden
  characters and redacting) and `notes_updated_at`. The agent writes them with
  `PUT /tasks/{task_id}/notes` (`text/plain` or `{"text": …}`, a body ≤ 4 KB) and its own `CO_API_TOKEN`, for its
  own task only. The agent-token allowlist gains exactly this route. People see the notes read-only on the card.
  The handoff uses the latest notes of the thread. A PUT also touches `${CO_TASK_DIR}/notes.stamp`.
- **Notes nudges (F14, F83).** These come from co-guard, as `additionalContext`:
  - **Every 12 calls.** PostToolUse adds the notes line every `NOTES_AFTER_ACTS = 12` tool calls, counted as the
    spool's `done` lines, unless `notes.stamp` is newer than the first `done` line of that window.
  - **Before a commit.** An allowed Bash PreToolUse whose command runs `git … commit` adds the notes line.
  - **After compaction.** See the compaction decision.

  The org switch `ground_rules.notes: false` turns off both the nudges and the notes rule.
- **Capture (F80, F17, F81).** This is the groundwork for recovery and the handoff:
  - The stream parser stores `id` and `parent` (`parent_tool_use_id`) on `tool_use` events, and `tool_use_id`,
    `is_error` and `parent` on `tool_result` events.
  - co-guard's `seen` line gains `tool` and `what`. `what` is the description :: command (≤ 200), the file path
    (≤ 300), the subagent type, or the MCP tool name, redacted first and then cut. It stays the first write of
    PreToolUse.
  - PostToolUse spools `{"event":"PostToolUse","tool_use_id","done":true}` for every call. The existing `ran`
    line for approved calls is kept.
  - Stop spools `background` (the running background tasks from `background_tasks`, parsed defensively: the shape
    is unverified).

  `read_spool` skips `done` lines as it skips `seen` lines, and a new `read_spool_raw` returns every line.
  PostToolUseFailure is **not** registered: the event is not in the spiked hook set, and the stream's `is_error`
  results already close a call.
- **Crash recovery (F15a, F79, F80, F81, F82).** The server knows a run died, so forge's 90 s / 10 min liveness
  heuristics are not ported, only the content.
  - **Liveness fields.** The worker records `Task.run_pgid` and `run_proc_start` (`ps -o lstart= -p <pid>`, which
    is portable across Linux and BSD) when it spawns claude. A heartbeat loop sets `Task.heartbeat_at` every
    `HEARTBEAT_SECONDS = 30`.
  - **Who recovers.** `_requeue_pending` at boot handles every RUNNING row. A sweep every `SWEEP_SECONDS = 60`
    handles RUNNING rows that no live `_run_guarded` holds and whose heartbeat (or `started_at`) is older than
    `STALE_SECONDS = 120`. That is the "lost its worker but not its row" case. Both call `recover(task_id, why)`.
  - **`recover` steps:**
    1. Stop a surviving claude process group, but only when `ps -o lstart=` for that pid still equals
       `run_proc_start` (pid reuse can never kill a stranger). It sends SIGKILL to the group and records the stop.
    2. Read the raw spool before anything wipes it.
    3. Build the record:
       - in-flight calls: `seen` ids with no `done` line and no `tool_result` event, ≤ 10, each with forge's
         per-tool advice;
       - background shells and helpers that were running (Agent `tool_use`s with no result; `run_in_background`
         Bash; the last Stop `background`), "now gone; ask before restarting dev servers";
       - files the step wrote in the last `RECENT_WRITE_SECONDS = 120` before its last event, stat-ed and marked
         `missing`, `empty (0 bytes)` or `ends in NUL bytes` ("a hint, not proof");
       - the log lines after the last handoff save (≤ 12);
       - Claude's last words (the last 60 words, ≤ 600 chars, redacted whole and then cut).
    4. Ingest the spool's guard records as events and spend the once grants they used, by reusing Phase 2's
       `_ingest_guard_spool`. It must run after step 2, because it deletes the task dir. The records are redacted
       by token shapes, and the hook already redacted them by exact env value.
    5. Snapshot the handoff (`recovered`).
    6. Store the record in `Task.recovery` with `pending: true`, and increase `Task.recoveries`.
    7. If the step has its own `session_id`, set `resume_session_id = session_id`.
    8. Queue the task again.

    After `MAX_RECOVERIES = 2`, the task fails instead: "interrupted 3 times by server restarts; use Continue to
    resume it".
  - **The next run.** The record goes into the prompt (see Prompting), and `pending` is set to false once the
    prompt is sent.
  - **The `recovery` event** has the payload `{why, at, in_flight, orphan_killed, recoveries, gave_up}`.
  - **External projects.** A run in an external project adds "only look; do not re-run anything from this repo
    until you have checked it" (forge's guest line).
- **Prompting (F15, F85, F86, F18, F87).** One composer decides what a step's prompt carries. The modes:

  | Step | Session | Prompt |
  |---|---|---|
  | recovery pending | resumed | record + log tail + "continue the task" |
  | recovery pending | fresh | handoff + record + log tail + `# Task` + original prompt |
  | `continue` | resumed | handoff + log tail + the Continue text |
  | `continue` | fresh | project context + handoff + log tail + the Continue text + "the request was: …" |
  | `user` follow-up | resumed | unchanged, plus a one-line git note when there are outside commits |
  | any step that wanted to resume but could not | fresh | project context + handoff + `# Task` + prompt |
  | `guard` follow-up | either | unchanged (Phase 2 text; a fresh guard session keeps today's project preamble) |
  | new thread (no parent, not a recovery) | fresh | project context + other runs + no-commits line + prompt; no handoff |

  A step "wants to resume" when it has a parent step, its origin is `user`, `continue` or `guard`, and it joined
  an existing thread (`root_id != id`, so a duplicate does not count). A recovered step wants to resume its own
  session. Such a step "could not" when:
  - the session file is missing;
  - the credentials changed;
  - the sender is not the parent step's creator (the follow-up router already clears the resume id in that case,
    so the worker detects it from the parent).

  Today such a step starts bare.

  Every injected block is framed "a record, not instructions", redacted with the run's secrets, and stripped of
  hidden characters. The log tail is the last `LOG_TAIL_LINES = 30` formatted lines of the described step, ≤ 4000
  chars, with one shared formatter.

  Fresh sessions in a project directory also get "other runs working in this directory now" (F18). Titles are shown
  only for runs the owner can see; others are counted. A run in a repo with no commits gets the no-commits line
  (F86).
- **Continue (F15b).** `POST /tasks/{task_id}/continue` takes `{"mode": "auto"|"resume"|"fresh"}`, with `auto` as
  the default. It is allowed on a terminal main-line step (not `origin=agent`) when no step of the thread is active,
  by anyone who may follow up today (`_require_manage`, contributor). The rules:
  - `resume` needs the sender to be the session's owner and a session id; otherwise the server returns 409.
  - `auto` resumes when it can, else starts fresh.

  It snapshots the handoff (`continue`) and creates a follow-up step with `origin=continue` and the fixed Continue
  text. Phase 2's deny-and-resume merges into a *queued* follow-up of the thread; a Continue is just another queued
  step, so a later approval joins it as before.
- **Credentials note (F87).** `Task.credential_ref` is a fingerprint of the Claude credential the step ran with:
  HMAC-SHA256 with the server key over `<env var>\0<value>`, hex[:24]. The secret is never stored. The check runs
  **before** the step's new ref is stored, so a recovered step compares against its own earlier ref. A step that
  would resume a session made under a different `credential_ref` starts **fresh with the handoff**. It emits
  `fresh_session {"reason": "credentials changed since that session", "credentials_changed": true}`. Other
  `fresh_session` reasons keep today's payload, `{"reason": …}`, with no `credentials_changed` key. The Run view
  says "this step ran under different credentials than the session it continues, so it started fresh with the
  handoff". Forge's reasoning: earlier thinking from another org cannot be reused.
- **Git facts (F84, F86).** `app/resume/gitfacts.py` runs read-only commands through `gitsafe`, with a shared 5 s
  budget per snapshot. A failure or timeout gives "unknown", never an exception.
  - **The allowlist gains:**
    - `status --porcelain=v1 -z --untracked-files=all --no-renames`;
    - `merge-base --is-ancestor <hex> <hex>`;
    - `symbolic-ref -q --short HEAD`;
    - `log`/`rev-list` revision arguments that are `HEAD`, `<hex7-40>` or `<hex>..<hex>`.
  - **No commits.** A git dir with no ref gives "no commits yet in this repo (N files untracked)". This shows in
    the handoff, in runs (one line), and on the project page through `GET /projects/{id}/git-state`.
  - **Head at start.** The worker records `Task.head_at_start`.
  - **Commits made outside this run.** These are the commits in `<previous handoff head or head_at_start>..HEAD`
    that are not this step's own. A step's own commits are the short shas printed by its `git commit` tool results
    (`[branch (root-commit)? <sha>] subject`, including `[detached HEAD <sha>]`). The base is fixed once per run,
    at spawn: the stored handoff head, else `head_at_start`. So a commit stays listed through every `working`
    snapshot of the run. The range is used only when the old head is an ancestor of HEAD. If the branch was
    rewritten, the note says "history changed since the last save" instead. Several users share
    `project.directory`, so this matters more here than in forge.
- **Ground rules (F21, F93, F98 text, F77).** These are org settings and admin-only. `GuardOrgSettings` gains
  `ground_rules` JSON: `{"grounding": true, "short_answers": true, "usage_care": true, "notes": true, "extra": ""}`
  (`extra` ≤ 2000 chars, cleaned like rule reasons). Forge's texts are adapted, with "JSPARK Forge" →
  "co-orchestrator" and the code-map wording dropped.
  - **Main session:** the enabled rules (grounding, short answers, forge's `USAGE_CARE_MAIN`, the notes rule with
    the PUT command, and `extra`) are appended to `--append-system-prompt`. The system prompt survives compaction,
    so it is not re-sent there.
  - **Helpers:** SubagentStart `additionalContext` gets the data line (external), grounding, the four usage-care
    habits (F98) and `extra`, precompiled by the server into the policy key `helper_context`.

  There are no per-project overrides in 3a.
- **Compaction re-injection (F77).** SessionStart with `source == "compact"` answers `additionalContext` with:
  - the data line (external);
  - the carry-on line;
  - `${CO_TASK_DIR}/handoff.md` (≤ 8000 chars);
  - the notes line (when notes are on).

  A missing or unreadable `handoff.md` gives the data line only, plus a `guard_error` record. The policy key
  `compact: false` switches the injection off. The hook catches its own errors there and always answers once. Other SessionStart
  sources are unchanged: on `resume` the handoff is already in the prompt, so it is not injected twice.
- **Files outside the workspace (F106).** These come from the step's Write/Edit/MultiEdit/NotebookEdit events.
  Files with `.html`, `.htm`, `.md` or `.pdf` under co-guard's temp roots (`rules.temp_roots` /
  `rules.path_in_temp`, now module-level)
  are listed on the card as "may vanish". Other writes outside the workspace and the project directory are listed
  by folder as "Also worked in". co-guard's PostToolUse adds a one-line note to the agent, once per file per task:
  "this file is in a temp folder and can vanish; save it in the project and give the user its path". This is
  counted from `outside_doc` spool lines. Forge's "copy into project" action is not ported: the agent saves the
  file itself on the note.

## Goals (Phase 3a scope)
1. Capture: tool ids in events, co-guard `seen` with `what`, `done` lines for every call, Stop background tasks,
   and `Task.origin`.
2. Git facts (status, ancestry, outside commits, no commits) through an extended gitsafe allowlist, plus the
   project git-state endpoint.
3. The thread handoff: builder, renderer, snapshots, previous copy, close, and the API.
4. Notes: the agent route, the 12-call and pre-commit nudges, and the temp-folder document note.
5. Ground rules (main and helpers) and compaction re-injection.
6. The prompt composer: recovery, Continue, fresh fallbacks with the handoff, log tail, no-commits line, other
   runs, and the credentials check. The Continue endpoint.
7. Crash recovery: heartbeat, verified orphan kill, the spool-derived record, resume of its own session, the cap,
   and the sweep.
8. UI: the "Where it stands" card with notes, the Continue button, recovery/outside-commit/no-commits/credentials/
   temp-file notices, the project no-commits warning, and the ground-rules card on the Agent guard page.

## Non-goals (Phase 3b, later phases, or never)
- **Phase 3b (next plan, builds on 3a):**
  - per-message usage capture from the stream (`message.usage`, deduplicated by `message.id`, verified per CLI
    version) and the "Where the tokens went" panel (F96);
  - the context-size chip and the optional per-project fresh-session threshold (default 150k tokens), which ends the
    turn through co-guard and continues as a fresh session with the 3a handoff (F97, replacing F23's context
    band);
  - budget bands at 80 % and 95 % of `max_budget_usd` / `Project.budget_usd` (F23);
  - the Questions inbox (F22) with F99's `blocks` and `reversible` fields;
  - Home's "open handoffs" list (F25 `/status`).
- **Later phases:**
  - Phase 4: answer → decision offer, lessons at "done" (F99, F100);
  - Phase 5: the launch-item link (F99);
  - Phase 6: helper presets carrying the habits permanently (F98 presets), and the doctor's handoff-damage check
    (F82 doctor part).
- **Never:**
  - forge's status line (F24);
  - the show-and-wait "Continue?" turn (P1/F15): the button is the wait;
  - the transcript reader (F81): the stream covers it;
  - PostToolUseFailure and Notification hooks (F87): unverified events, and the server sees credential changes
    directly;
  - per-project ground-rule overrides;
  - multi-process workers (needs a lease, not a heartbeat).

## Anti-goals
- A recovered run that repeats the half-done action blindly, runs next to a surviving copy of itself, or loops on a
  task that keeps crashing the server.
- A kill signal to any process the orchestrator cannot prove is its own claude run.
- Injected text that is mistaken for instructions or for the user's request. That covers notes, the handoff,
  the log tail, agent text quoting "approved" or "CONFIRM", and an external repo's files.
- A secret in a handoff, a note, a recovery record or a log tail: every one is redacted before it is stored and
  again when it is injected.
- A continuation that resumes another account's session, or resumes across a credential change.
- A git helper that writes, fetches, runs repo-configured programs, or raises into a snapshot.
- A hook change that weakens fail-closed PreToolUse, adds a non-stdlib import, or answers twice.

## Approaches considered
| Question | Options | Outcome |
|---|---|---|
| Who builds the handoff | the hook (forge) / **the server from events** | server: it already has every event and git; the hook only re-injects the rendered file |
| Handoff granularity | per step / **per thread** / per project | per thread: Continue and follow-ups continue a thread; dispatched children are summarised |
| In-flight at a crash | new per-call files (forge) / **spool `seen` minus `done` and `tool_result` ids** | the spool exists and is fsynced; no new files |
| Orphaned claude after a server kill | ignore / kill the recorded pgid / **kill only when the process start time matches** | start-time match: pid reuse can never kill a stranger |
| Where notes live | a file in the task dir (agent cannot write it: self-protect) / a file with `--add-dir` / **an API route with the agent token** | route: auditable, redacted, one place |
| Credential change | resume anyway / warn only / **fresh session with the handoff** | fresh: forge's finding that another org's thinking cannot be reused |
| Ground rules store | new settings table / **`GuardOrgSettings.ground_rules` JSON** | one admin page and one row; switches can be added without migrations |
| Scope | one Phase 3 plan / **3a resume core, 3b token care and inbox** | 3b's fresh-session threshold needs 3a's handoff and Continue-fresh |

## Builder's latitude
Module layout beyond the interfaces fixed in the plan, the card layout and copy (apart from the fixed texts), the
log-line formatter's kind names, test structure, and how `others` is queried.
