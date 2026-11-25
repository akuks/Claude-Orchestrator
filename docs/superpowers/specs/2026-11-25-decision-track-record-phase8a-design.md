# Phase 8a — decision track record — design

Owner-approved addition (2026-11-25, from the Cognition comparison): score each recorded decision by what happened
after agents were shown it, and flag decisions that keep being followed by trouble. Plain screens.

## What is recorded

- New table `decision_showings` (migration **0024**): `id, project_id, decision_number, task_id, shown_at, outcome
  (null|"passed"|"completed"|"fixed"|"failed"), retried (bool), settled_at`; unique (`task_id`, `decision_number`);
  index (`project_id`, `decision_number`).
- Written where the worker already turns the hook's decision-note spool line into a `decision_note` task event: one
  row per task and decision number (upsert; first showing time kept). Raise the 3-id cap on stored numbers to 20.
  The hook does not change.

## Outcomes

- Settled when the task finishes (a small job next to the existing after-run job, also for failed tasks; ad-hoc
  steps of a project thread count under the thread's project) and again when its workflow stage settles. A
  workflow step's "gate" is the gate step that waits for it directly:
  - `failed` — the task failed, or its gate finally failed on a real check;
  - `fixed` — its gate needed a fix-it round (gate `fix_round` ≥ 1, or a `fix`-tagged task for the same run, gate
    stage and attempt);
  - `passed` — its gate passed with no fix round;
  - `completed` — a task outside a workflow (or a workflow step with no gate after it) that completed.
  - `retried` — the task had automatic retries.
- A fix task (tagged `fix`) is judged by its gate's final result (design ruling, 2026-11-25): `passed` if the gate finally
  passed (the fix worked), `failed` if it finally failed; never `fixed`.
- No outcome (not counted): cancelled tasks; a gate not settled yet; a gate stopped by cancel or by its own
  infrastructure (internal error, guard refusal, missing command or folder, fixer not available); tasks of an
  older attempt of their step.

## Score (computed on read)

- The decisions list adds `track: {shown, passed, completed, fixed, failed, last_shown_at, trouble: bool}` per
  decision from one grouped query over the last 90 days.
- Trouble = (fixed + failed) / showings with an outcome. `trouble` true when ≥ 5 showings with an outcome AND trouble
  rate ≥ 50% AND the rate is ≥ 1.5× the project's overall rate over the same window.
- Never changes a decision's status or stale flag.

## Screens

- Decisions list: a small grey tag "Shown 12× · 7 needed fixes" next to the stale tag (active decisions only, when
  shown ≥ 1); orange "Often followed by trouble" when `trouble`. Tooltip: "Tasks that were shown this note later
  needed fixes or failed in 7 of 12 cases. This doesn't mean the note caused it — worth checking it is still right and
  clear."
- Admin action on the Decisions page: "Build from past runs" — fills `decision_showings` from existing
  `decision_note` task events (only those still present; tidied history is gone) and settles their outcomes;
  idempotent; shows "Added N showings from past runs."

## Not covered / to know

- Counts are "at least": showings during crash recovery, beyond the per-session repeat filter, or in tidied history
  aren't counted.
- Correlation, not cause; risky files attract both notes and trouble.
- A superseded decision's record doesn't carry over to its replacement.

## Rulings

- Separate table (survives history tidying) rather than computing from task events.
- Backfill only on an admin's click (stored data is not changed without asking).
