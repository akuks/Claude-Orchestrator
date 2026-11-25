# Forge port Phase 7a — workflow run control — design

Owner request (2026-11-24): "check the forge again and make sure nothing is left there". A coverage audit of forge
v0.4.2–v0.8.1 against the code found gaps; this phase takes the workflow-run ones that do not need a dependency
graph. Phase 7b (steps side by side: needs/after, parallel limit, cycle check, all-errors validation, canvas lanes,
F65) follows. Standing autonomy: the designer makes the calls and records them. Screens follow the owner's rule:
plain words, technical strings under "Details", verified with real screenshots.

Sources: `docs/research/jspark-forge-delta-v0.7-v0.8.md` (F123, F126–F129, F131) and the inventory rows F66, F16.

## 1. Approve ahead (F123)

- A person who may approve a run (`_require_run(manage=True)`: creator, admin, or maintainer of a project the run
  touches) may approve an **approval step that has not been reached yet**, and withdraw that early approval.
- A stage dict may carry `"ahead": false`. Such a step can never be approved ahead (409, "This step can only be
  approved when the run reaches it."). The templates set `ahead: false` on every release approval ("Approve
  release", and the ui template's final "Approve"); "Approve plan" stays allowed. Missing key = allowed.
- Stored in `stage_state[str(i)]["ahead"] = {"by": user_id, "by_name": str, "at": iso}`. Only for a future step
  (`i > current_stage`) whose kind is approval, while the run is `running` or `paused`.
- When the run reaches that step it is approved at once: same effect as Approve, `decided_by` = the early approver,
  summary "Approved ahead by <name>", one log line "Approve plan: approved ahead by <name>".
- An early approval is dropped (and a log line says so) when the run is cancelled, fails, or is restarted from an
  earlier step (§3). It survives a server restart (it is in the database).
- API: `POST /workflow-runs/{id}/stages/{i}/approve-ahead`, `DELETE /workflow-runs/{id}/stages/{i}/approve-ahead`.
  The approve-ahead POST must be idempotent and must refuse a step already reached (409).

## 2. One check at a time per project (F126)

- A gate stage that is about to run its commands in a project folder first takes an in-process lock keyed by the
  folder's real path. If another run holds it, the stage is **held** (existing `_hold`, never failed) with the
  reason "Waiting for checks from another run in this project to finish." and retried on the next tick.
- The lock is held from the first command of the gate stage until the stage ends (passed, failed, needs approval,
  waiting for approval of a flagged command, cancelled) — including its fix round. It is released on every exit
  path, including errors and cancel. After a server restart locks start empty; resumed gates take the lock again
  before running.
- Agent steps are not locked (tasks of different runs in one project already share the folder; that predates this
  phase and is documented).

## 3. Run again from a step (F66)

- On a run that is `completed` or `failed`, a person who may manage it can **run it again from step N** (N ≤ the
  last step reached). Steps N.. are reset to not started; step N starts as a new attempt (`_new_attempt`); the run
  becomes `running`; earlier steps keep their results, so `{{prev}}`/`{{stageN}}` still resolve.
- Old tasks and gate runs stay in the database and in the run's history; the run view shows the newest attempt.
- The spending limit still applies; a run held for budget stays held.
- API: `POST /workflow-runs/{id}/stages/{i}/rerun`. Refused (409) while the run is running/paused/pending.
- The existing failed-step Retry keeps working unchanged.

## 4. Time and spend per step (F66)

- The run API returns, per step, `usage: {cost_usd, input_tokens, output_tokens, seconds}` for the newest attempt:
  cost and tokens summed from the step's tasks (the `gates/totals.py` query), seconds from `started_at` →
  `finished_at` (or now while running) plus gate run durations where that is how the gate measured them. Computed on
  read; nothing new is stored.

## 5. Wording (F127, F128, F129, F131)

- **A step is named once** in run log lines (no "Step gate (gate)").
- **Status words** on the run view: a gate is "Running" only while its process is alive; "Waiting its turn"
  (held), "Waiting for approval", "Interrupted — will continue" for a gate whose process is gone and is about to be
  resumed. Failed stays "Failed".
- **Handoff file list:** "N files touched. Newest 5:" then 5, then "The full list is on the task page." (was 8 +
  "(and N more)").
- **Caution notes:** note lines of the form `Caution: … | measured: … | check again before acting` are taken out of
  the notes text and shown under a separate heading "Re-check before acting" in the handoff text and card, each as a
  plain item; they are shown as things to verify, never as instructions.

## Not covered / to know

- Steps still run one after another; side-by-side steps are Phase 7b.
- The project check lock is in memory (one server process, as today).
- An early approval is approval of the step as defined when the run started; nothing about later output is checked.
- Not run against the real `claude` CLI.

## Rulings (with costs)

- Gate-command re-approval by hash (F66) is **not built**: forge needs it because an agent can edit the graph file;
  here workflows are database records only admins can change, and the guard judges each command when it runs.
  Cost if wrong: an admin's edited command runs without a second person.
- Agent steps are not covered by the project lock. Cost if wrong: two runs' agents can still edit one checkout at
  once (as today).

## Anti-goals

Approving ahead a release step; a held step turning into a failure; losing earlier steps' results on "run again";
technical strings on the main view.
