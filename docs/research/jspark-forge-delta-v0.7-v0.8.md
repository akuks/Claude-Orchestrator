# jspark-forge delta inventory: v0.7.0 → v0.8.1 (2026-11-25)

Companion to `docs/research/jspark-forge-feature-inventory.md` (v0.4.2 baseline, F1–F78),
`docs/research/jspark-forge-delta-v0.5-v0.6.md` (F79–F108) and `docs/research/jspark-forge-delta-v0.6-v0.7.md`
(F109–F122). This file lists only what changed in v0.8.0 and v0.8.1 (both released 2026-11-22) and maps each item to
the orchestrator as it stands on main @ `a1fd3e0` (Phases 1–6c merged, including 5a gates/approvals, 5c stages, 4b
proposals).

**Method.** Read-only. Sources:
- `GET /repos/harishgoswami23/jspark-forge/compare/v0.7.0...v0.8.1`: 15 commits, 35 files. All commit messages read
  in full. Patches read in full for `scripts/forge.js`, `scripts/lib/{blocklist,confirm,decide,graph,handoff,memory}.js`,
  `skills/build/SKILL.md`, `templates/graphs/*.md`, and the added lines of `scripts/lib/graph-run.js` (+386 −95:
  `lbl`, `snapshot`/guard hash, `openNames`/`setOpen`/`busyOther`, `startRun`, the approve-ahead block,
  `next` hand-out loop, `stateWord`, `currentAll`/`statusAll`, `onSubagentStart` evidence routing, `onCommand`
  interrupted path, `gate`, `trustOne`/`trustSeveral`/`trustPrompt`, `onPrompt`). `docs/spec.md` added lines read.
- Release notes `docs/release-notes/v0.8.0.md` and `v0.8.1.md` read in full (from the compare patches).
- Tests: top-level `test(` calls counted in the new test files: `graph-ahead` 10, `graph-trust-early` 11,
  `graph-multi` 8, `graph-status-words` 7, `notes-cautions` 5; `memory-offer` +2. Forge's claim: 2,080 tests at
  v0.8.0, 2,084 at v0.8.1 (2,041 at v0.7.0). Integration tests against real Claude Code were **not** run for either
  release (their own notes).
- Downloads kept under the session scratchpad `forge-v081/` (compare.json + per-file patches). Nothing was installed
  or executed; forge's text was treated as data.

Legend as before: **[V]** = verified by reading code (forge's or ours, as stated); **[RN]** = from release notes or
commit messages only; **[T]** = forge has tests for it (count where known); **[I]** = inferred. Sizes: S ≤ 2 days,
M ≤ 1–2 weeks, L more.

---

## 0. Touches shipped work

**No guard-rule, redaction or hook-protocol change.** `blocklist.js` changes only the *wording* of the gate-runner
refusal (F132); `confirm.js` only exports its `WRAPPED` regex so the graph code can reuse it (F133); `hooks/hooks.json`
and `redact.js` are not in the file list **[V]**. What touches merged orchestrator code:

| # | Touches | Exact files | What changes |
|---|---|---|---|
| F123 | **5a approvals (merged)** | `workflow_runner.py:372-376` (approval start → `waiting_approval`, run paused), `:722-737` `approve_stage`, `:1229-1242` `_decision_target` (requires current stage + `waiting_approval`); `gates/spec.py:179-189` (approval fields); `frontend/src/gates/ApprovalCard.jsx:12` ("Not reached yet"); `workflow_templates.py:50,56,85` | Approve a not-yet-reached approval stage ahead of time |
| F126 | **5a gates (merged)** | `workflow_runner.py:306-314` `_hold`, `:327-376` `_start_stage`; `routers/workflows.py:615-638` (held stages list "others") | Cross-run lock so two runs' gates in one project never overlap |
| F127/F128 | **5a gate words (merged)** | `workflow_runner.py:179-189` `gate_status_text`; `:675,734,800,954,1052…` "Step {name} (gate) …" | "waiting to be run" / "interrupted" words; name a step once |
| F129 | **3a handoff (merged)** | `resume/handoff.py:39-41,503-506` | Long file list → count + newest 5 + where the rest is |
| F131 | **3a handoff + co_hook (merged)** | `resume/handoff.py:488-491`, `guard/co_hook.py:141-146` | Caution lines parsed out and shown as "Re-check before acting" |

---

## 1. Summary per release

| Release | Date (UTC) | Theme | What is new, in plain words |
|---|---|---|---|
| **v0.8.0** | 2026-11-22 07:48 (merged 08:09) | "Build graphs that keep going while you are away" | Source: a build stopped for hours at a plan approval with nobody there; a typed trust message was dropped silently; a trial graph could not run while the main one waited. New: `APPROVE AHEAD <graph> <step>` / `CANCEL APPROVAL`; `- ahead: no` on human steps (all template release steps); `TRUST GRAPH` sent early is answered, near-misses told the exact text; several graphs open at once with a `- lock: <name>` header; `graph status` with no name; `DONE <graph> <step>` when ambiguous. Smaller: status words running / waiting to be run / interrupted / handed to a helper; step named once; file list as count + newest 5 + log path; decision offer shown once and `{"none":[sha]}`; `Caution: … \| measured: … \| check again before acting` note lines; clearer gate-runner refusal. |
| **v0.8.1** | 2026-11-22 14:03 (merged 14:10) | "Trust several build graphs in one message" | `TRUST GRAPH a b c` (≤ 12 names), all or nothing. |

---

## 2. Delta feature list

New ids continue from F122.

### 2A. Build graphs: unattended progress (v0.8.0, v0.8.1)

| # | Feature | What it does | Forge source | Verified |
|---|---|---|---|---|
| **F123** | Approve a human step ahead (new; changes F66) | The person's own whole message `APPROVE AHEAD <graph> <step>` (regex `^approve[ \t]+ahead[ \t]+<id>[ \t]+<id>$`, ids `[a-z][a-z0-9-]{0,39}`). Accepted only when the graph is valid **and trusted** and the step is a human step not yet reached; refusals in plain words: not a human step; already done; "waiting for you now — send DONE <id>"; marked `- ahead: no`. Stored with time and the step's `say:` text (before a run: in the trust file with a hash of all steps; during a run: in the run record). When `graph next` reaches the step with its dependencies done, it is completed by itself: state `done`, `note = "approved ahead by the person at YYYY-MM-DD HH:MM"`, output prints the instruction and tells Claude to tell the user (loop repeats, one may free the next). Cancelled — and the user told (`AHEAD_LOST`) — when the steps' hash changed before start, the graph file changed during the run, the run is stopped, or the `say:` text differs. `CANCEL APPROVAL <graph> <step>` withdraws. `graph status` shows approvals (pending ones "go with the run when the graph starts"). `DONE <step>` sent too early now says how to approve ahead. New step field `- ahead: yes\|no` (human steps only; validated); `noAhead` list is part of the TRUST GRAPH hash. Templates mark every `release` step `- ahead: no` (software, planned; documents/spike/ui templates changed too). Build skill: Claude must never type, suggest sending for the user, or imitate the message. Only in attended sessions (`CLAUDE_CODE_SESSION_ATTENDED=1`). | `graph-run.js` approveAhead/cancelAhead/takeAhead/dropChangedAhead/next, `graph.js` parse+validate, `templates/graphs/*.md`, `skills/build/SKILL.md` | [V] [T graph-ahead 10] |
| **F124** | `TRUST GRAPH` never dropped silently (changes F66) | Before: sent before `graph start` had shown the commands, it was an ordinary prompt (nothing recorded, nothing said — seen 4 times). Now: valid graph → trusted as it is now, answer lists commands, gates, human steps, never-ahead steps, lock. Missing / invalid → one line. Already trusted and unchanged → "already trusted… Nothing changed." Commands changed since `start` showed them → still refused. Near miss (one line ≤ 80 chars, ≤ 6 words, containing "trust graph" with extras, or edit distance ≤ 2 to "trustgraph"/"trust graph") → nothing trusted, told the exact text (and the graph names present). Surrounding spaces accepted. | `graph-run.js` trustOne/trustGraph/trustPrompt/editDistance | [V] [T graph-trust-early 11] |
| **F125** | `TRUST GRAPH` several names (v0.8.1; changes F124) | `TRUST GRAPH a b c`, 1–12 names, duplicates count once. Dry-run all first; if any cannot be trusted none is, and the answer lists each failure and "Send exactly this: TRUST GRAPH <the good ones>". | `graph-run.js` trustSeveral | [V] [T graph-trust-early, +4 tests per notes] |
| **F126** | Several graphs open + `- lock:` (new; changes F66 "one graph per repo") | `active` file now `{names:[…]}` (old `{name}` still read). A graph waiting at a human step holds nothing. Before handing out an agent or gate step, `busyOther` (under a project-wide `state/graph-handout` lock) looks for another open graph with an agent/gate step in state running; it is skipped only when **both** graphs have a `lock` and the names differ. Blocked step: "Step X is not started: build graph Y has Z under way … (both carry the lock "db" / a graph with no lock line runs alone). Nothing failed." `- lock: <name>` header (ID-validated), covered by TRUST GRAPH; skill: Claude must not add/change it to get past a wait. `graph status` with no name lists every open graph (+ lock); SessionStart and the handoff name every open graph. `DONE <step>` ambiguous across graphs → asks for `DONE <graph> <step>`; bare `done` when exactly one human step waits anywhere. Helper-start evidence goes to the graph (owned by this session) that is waiting for that helper kind; a gate command's exit code goes to the graph whose gate lists it. | `graph-run.js` openNames/setOpen/busyOther/startRun/next/statusAll/currentAll/onSubagentStart/onCommand/onPrompt, `graph.js`, `forge.js` SessionStart + `graph status`, `handoff.js` save | [V] [T graph-multi 8] |

### 2B. Build graphs: words (v0.8.0)

| # | Feature | What it does | Forge source | Verified |
|---|---|---|---|---|
| **F127** | Status words kept apart; interrupted ≠ failed (changes F112) | "running" only while a process forge started is alive (`graph gate`); a handed-out gate nobody runs is "waiting to be run (k of n commands passed / the retry of what failed is next / the full command must pass once more)" (was "not yet passed … none running"); a gate whose command was stopped before it ended is "interrupted (… nothing was recorded as failed; run it again)". Behaviour change: a Bash gate call that the tool reports `interrupted`, or that failed with **no** "exit code N" in the error (stopped by hand, tool time limit), is recorded with exit `?` and marks the step interrupted instead of failing the run; `graph gate` killed by a signal does the same. "failed" only on a real exit code. Agent step handed out: "handed to a helper". | `graph-run.js` stateWord, onCommand (`cut`), gate | [V] [T graph-status-words 7] |
| **F128** | A step named once (changes F112) | `lbl(id, kind)`: "Step gate failed" when id equals kind, else "Step checks (gate)". | `graph-run.js` lbl | [V] [T v062, v07 updated] |

### 2C. Handoff, notes, decisions (v0.8.0)

| # | Feature | What it does | Forge source | Verified |
|---|---|---|---|---|
| **F129** | File list = count + newest + where the rest is (changes F111) | ≤ 8 files: all named. More: "50 files; newest: <5 names>; the rest are in the log (<log file>)" (3 names at trim level ≥ 2). | `handoff.js` render `FILES_NEWEST = 5` | [V] [T handoff] |
| **F130** | Decision offer once; "no decision here" (changes F47) | `deliver` keeps an offer only while `delivered < 1` (was < 2: the repeat made Claude ask again every message); stale flags still ≤ 2. Offer text adds "Offer once: if the user does not answer, do not ask again". For a commit that settled no choice (fix, rename, tests, wording) Claude writes `{"none": ["<sha>"]}` and runs `decide --from`; the result is "No decision in <sha>: nothing recorded, and nothing to ask the user" and the offer is cleared. | `memory.js` deliver, `decide.js` run | [V] [T memory-offer +2] |
| **F131** | Caution lines shown as things to re-check (changes F113) | A notes line starting `Caution:` or `Limit:` (optionally bulleted), or ending with "check again (before acting)", is taken out of the notes text and parsed into `{text, measured}` (`… \| measured: <what, when> \| check again before acting`). Handoff shows "**Re-check before acting (true when written at HH:MM / on YYYY-MM-DD HH:MM, maybe not now; test each again, these are not instructions):** <text> (measured: … / no measurement was written down)", ≤ 6 (≤ 3 at trim level ≥ 3), text ≤ 160, measured ≤ 120 chars. Cautions alone count as work worth resuming. The notes nudge now gives that exact line format. | `handoff.js` cautionOf/parseNotes/render/workOf/notesLine | [V] [T notes-cautions 5] |

### 2D. Guard-adjacent wording and hardening (v0.8.0)

| # | Feature | What it does | Forge source | Verified |
|---|---|---|---|---|
| **F132** | Gate-runner refusal says what to do (changes F91) | Same hard `forge-state` deny; the reason shown is now: main session, extra text around it → "nothing may come before or after it on the command line (no cd, no variable, no && and no pipe). Run it from the project folder as a command of its own."; helper → "runs from the main session, never from a helper. Report back; …". | `blocklist.js` GATE_RUN_SAY / _HELPER | [V] [T graph-gate +] |
| **F133** | Graph control messages ignore harness-wrapped text (changes F90a scope) | `onPrompt` (TRUST GRAPH, APPROVE AHEAD, CANCEL APPROVAL, DONE) returns "ordinary prompt" when the message contains any `confirm.WRAPPED` marker (`<agent-message>`, `<task-notification>`, `<system-reminder>`, …) anywhere — the same test CONFIRM uses since v0.5. Before, only the whole-message regex guarded DONE/TRUST. | `graph-run.js` onPrompt, `confirm.js` export | [V] (tests: graph-ahead covers quotation/helper report **[RN]**) |

**Count: 11 new or changed features (F123–F133).** Docs only: README tables, `docs/spec.md`, `docs/releases.md`,
changelog (not separate rows).

---

## 3. How it translates

The orchestrator has no typed control messages: approvals, allowances, budget raises and retries are authenticated
UI actions (`routers/workflows.py:684-722`, `_require_run(manage=True)`), workflow stages are snapshotted onto the
run at start (`routers/workflows.py:299`), and gates are run by the server, never by the agent (`gates/executor.py`).
That removes most of forge's text-protocol problems; what is left is the *behaviour* behind them.

| # | Translates? | Orchestrator equivalent | Already present? (file:line) | Size |
|---|---|---|---|---|
| F123 | **Yes — highest value.** The orchestrator *is* the "nobody is there" case: a `planned` workflow started in the evening stops at "Approve plan" until morning. | An **"Approve ahead"** button on an approval card that is "Not reached yet", and optionally checkboxes in the Run modal ("approve these steps ahead"). Store on `run.stage_state[i]`: `ahead = {by, at, message}`. In `_start_stage` for `kind == "approval"` (`workflow_runner.py:372`): if `ahead` is set and `message` equals the snapshot, set `status="approved"`, `decided_by=<who> (approved ahead at <time>)`, runlog "Step X (approval) approved ahead by Y at T; nobody read it at this point. Its message: …", and advance instead of pausing. "Withdraw" button while not reached. New stage field `ahead: false` validated in `gates/spec.py` (approval only); set it on "Approve release" (`workflow_templates.py:56`) and "Approve" (ui, `:85`), leave "Approve plan" (`:50`) allowed, as forge does. Same permission as approve (`manage=True`). Cancellation triggers are mostly free: stages are a run snapshot so they cannot change under it; cancel_run ends the run. Decide: should a Retry of an *earlier* stage drop it? (Today Retry only re-runs the current failed stage, `workflow_runner.py:693-710`, so not needed yet.) Notifications: tell the run creator when an ahead approval was used. | **No.** `_decision_target` requires the current stage in `waiting_approval` (`workflow_runner.py:1237-1241`); ApprovalCard has no action for `waiting` (`frontend/src/gates/ApprovalCard.jsx:12`). | S–M |
| F124 | **No.** Gate commands are trusted by an admin saving the workflow (`routers/workflows.py:236,258` `ensure_admin`) and each command is checked by the guard at run time, with "Allow and run" for flagged ones (`workflow_runner.py:1446-1465`, `allow_gate` `:787-805`). No typed trust message exists to be lost. | — | n/a (different design) | — |
| F125 | **No.** Same as F124. A bulk "allow" for several runs' flagged gates is not needed today. | — | n/a | — |
| F126 | **Partly — the lock half matters.** Several runs open at once is already normal (no single-run restriction at `routers/workflows.py:285-315`; `/runs/live` at `:388` lists them), and an approval-paused run holds nothing. **Missing:** nothing stops two runs' gates (or a gate and another run's agent stage) from working in the same project directory at the same moment — e.g. two `pytest` suites on one test database, or one run's builder editing files while another's gate tests them. Port: a project-level gate lock: before spawning a gate (`_spawn_gate` / `_start_stage`), if another active run has a live gate (or a running agent stage) in the same project, `_hold` the stage with a plain reason "waiting for <run> step <name> in the same project; nothing failed" (reuse the held path `workflow_runner.py:306-314`, re-checked every tick, and the "others" list already shown at `routers/workflows.py:615-638`). Optional workflow field `lock: <name>` (forge semantics: differ only if both set). Approval stages never hold the lock. | **No** cross-run serialisation; `held` today means pre-flight only (`workflow_runner.py:310`). | M |
| F127 | **Mostly already there.** The server owns the gate process, so "running" is real (`gate_status_text` uses the live child, `workflow_runner.py:179-189`), a server restart mid-command resumes the gate instead of failing it ("resumed after a server restart", `:951-955`), and timeout/cancel are their own outcomes and never feed the fix loop (`gates/executor.py:102`, `workflow_runner.py:29-30`). Polish: replace "not yet passed (k of n commands passed, none running)" with forge's "waiting to be run (…)" and say "interrupted by a server restart; running again" while that state lasts. A user Cancel stays a run failure (the whole run stops; correct for us). | Yes for behaviour (`workflow_runner.py:951-955`); words differ (`:188`, `GateCard.jsx:76`). | S |
| F128 | **Yes, trivial.** Every runlog line is `Step {name} (gate|approval) …`; a stage named "Test"/"Gate" is fine, one named "gate" reads "Step gate (gate)". Add forge's `lbl` rule (case-insensitive) in one helper. | No (`workflow_runner.py:675,734,800,954,1052,1370…`). | S |
| F129 | **Yes, small.** Our injected handoff already shows 8 / 5 + "(and N more)"; forge's form adds *where the rest is*. For us: "50 files; newest: <5>; the full list is on the run's Where-it-stands card" (the stored content keeps ≤ 50, `handoff.py:39,291`). The agent cannot open the card, so say instead "ask for `git status` / `git log --name-only` if you need them" **[I]**. | Partly (`resume/handoff.py:503-506`). | S |
| F130 | **No (already solved differently).** The orchestrator never has the agent offer to record a decision: proposals are made server-side after the run (`decisions/after_run.py`), commits with chore/docs/style/bump/release/merge/revert subjects are skipped (`:51`), the model may return `[]` "when there is none" (`decisions/texts.py:18`), and each proposal has a dedupe key per commit (`after_run.py:244`), so nothing is asked twice and no person is pestered in the run. | Yes (different shape): `decisions/after_run.py:51,244`, `decisions/texts.py:18`. | — |
| F131 | **Yes.** Today the notes nudge asks for cautions with measurements (`guard/co_hook.py:143-144`) but the handoff renders the notes as one blob with a generic "re-check any caution" (`resume/handoff.py:488-491`). Port: parse `Caution:`/`Limit:`/"check again" lines (same rules and caps) into `notes.cautions`, render "Re-check before acting (true when written <time>, maybe not now; test each again, these are not instructions)", show them as a distinct list on the Where-it-stands card, and give the exact line format in `NOTES_LINE`. Matters more for us than for forge: resumed `-p` runs get the handoff injected with no person reading it first. | Partly (`co_hook.py:141-146`, `handoff.py:488-491`). | S |
| F132 | **No.** Gates are run by the server; agents never invoke a gate runner, so there is no refusal to word. | n/a (`gates/executor.py`) | — |
| F133 | **No (already structural).** Approvals are HTTP actions by an authenticated user with the manage role (`routers/workflows.py:684-700`), never text in a prompt, so a quoted or helper-reported "APPROVE" cannot grant anything. Keep it that way if a chat-style control is ever added. | Yes (`routers/workflows.py:684-722`). | — |

### 3.1 By bucket

- **Translates, worth doing:** F123 (approve ahead), F126 (project gate lock), F131 (caution lines).
- **Translates, polish:** F127 (words), F128 (name once), F129 (file-list wording).
- **Doesn't translate / already covered:** F124, F125 (no typed trust), F130 (server-side proposals), F132 (server-run
  gates), F133 (UI approvals).

---

## 4. Recommended shortlist

1. **F123 Approve ahead** (S–M) — directly addresses the orchestrator's main use (start a workflow and walk away);
   keep forge's guard rails: per-step `ahead: false` on release approvals, the message snapshot shown in the record,
   a Withdraw button, same permission as Approve, and a notification when it was used.
2. **F126 Project gate lock** (M) — a real correctness gap for a multi-user server: two runs' test gates or a gate and
   another run's builder in one directory can corrupt each other's results. Hold (never fail) with a plain reason,
   reusing `held`.
3. **F131 Caution lines** (S) — cheap, and it improves what resumed unattended runs are told.
4. Polish batch (S, one PR): F127 words, F128 name once, F129 file-list wording.

---

## 5. Verified vs inferred

**Verified in forge (v0.8.1 patches):** all of F123–F133 from the code listed in Method; test counts from the new
test files. The v0.8.1 test delta (+4) is from the release notes **[RN]**. Which test covers F133's wrapped-text case
was not checked line by line **[RN]**.

**Verified in the orchestrator (main @ `a1fd3e0`):** `workflow_runner.py` (approval start and pause, `approve_stage`,
`_decision_target`, `_hold`, `gate_status_text`, restart resume, retry scope, runlog wording), `gates/spec.py`
(kinds, approval fields), `gates/executor.py` (outcomes), `routers/workflows.py` (admin-only save, run snapshot, no
single-run restriction, approve/allow routes, held "others"), `workflow_templates.py` (approval stages),
`frontend/src/gates/{ApprovalCard,GateCard}.jsx` (status words), `resume/handoff.py` (file list, notes render),
`guard/co_hook.py` (NOTES_LINE), `decisions/{after_run,texts}.py` (skip prefixes, dedupe, empty answer); no project
lock found by grep in `workflow_runner.py`, `gates/`, `worker.py` (only the global `worker_concurrency` semaphore,
`worker.py:220`).

**Inferred / to check before building:**
- That agent stages of different runs in the same project directory also need the F126 lock (tasks run without
  worktrees today; this predates the delta).
- Whether a pre-approved stage should be dropped on Retry once Retry can re-run earlier stages (F123).
- The right wording to point a resumed agent at the full file list (F129).
- Sizes.
