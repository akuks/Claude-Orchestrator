# Forge port Phase 7b — steps side by side — design

Second half of the workflow gaps found by the 2026-11-25 code audit (forge F65, and the parts of F66 that depend on
it). Phase 7a (approve ahead, project check lock, run again from a step, per-step spend) is merged. Standing
autonomy: the designer makes the calls and records them. Screens: plain words, technical strings under "Details",
verified with real screenshots.

## What changes for people

Today a workflow is a single line of steps. After this phase a step can say which earlier steps it waits for, so two
or more steps can run at the same time (for example "Write tests" and "Write docs" both after "Build", and "Review"
after both). Existing workflows keep running exactly as before.

## Model

- A stage dict may carry `"after": [i, j, …]` — indexes of **earlier** stages in the list (0-based) that must finish
  first. Missing `after` means "the stage just before me" (`[i-1]`; stage 0 waits for nothing), so every existing
  workflow and every run snapshot behaves exactly as today. `"after": []` means "starts at once".
- Because `after` may only point to earlier stages, a loop is impossible by construction; the editor keeps the list
  order consistent (moving a step re-numbers the references, and a step cannot be moved above a step it waits for).
- A workflow may carry `"max_parallel": n` (1–4, default 2): at most n steps of one run are running at the same
  time. Approvals waiting for a person do not count.
- **Validation lists every problem at once**, each as a plain sentence with the step number, e.g. "Step 4 waits for
  step 6, which comes later.", "Step 3 waits for step 9, which does not exist.", "Step 2 lists step 1 twice.",
  "Step 5 waits for itself.", "Run at most 1 to 4 steps at once." (422 with `detail` as a list of sentences).

## Running

- A step is **ready** when every step in its `after` is finished successfully (agent done; gate passed; approval
  approved). The runner starts ready steps, lowest number first, while fewer than `max_parallel` steps are running
  and the spending limit allows. The existing per-run lock serialises every decision.
- **Waiting for a person:** an approval step waiting does not stop other ready branches. The run shows "Waiting for
  you" (paused) only when nothing else is running or startable.
- **A step fails:** no new steps start; steps already running are cancelled (the same way Cancel cancels them); the
  run fails. Retry re-runs the failed step and then continues normally.
- **Run again from step N** (7a) now resets N **and every step that depends on it, directly or indirectly**; other
  steps keep their results.
- `{{prev}}` for a step means the combined output of the steps in its `after`, in step order, each under a short
  heading "From <step name>:" when there is more than one; `{{prev.name}}` looks in those steps in order and takes
  the first that has `name`. `{{stageN}}` is unchanged. A step with `after: []` has an empty `{{prev}}`.
- `current_stage` is kept for compatibility as "the lowest-numbered step not yet finished"; nothing new depends on
  it.
- Restart recovery: every running gate and agent step of a run is recovered as today (they are already keyed by
  `(run_id, i)`).
- The project check lock (7a), approve ahead, per-step spend and status words work per step unchanged.

## Screens

- **Run and editor canvas:** steps are placed in columns by depth (the longest chain of waits leading to them);
  steps in the same column are stacked; lines go from each step it waits for. A plain line workflow looks exactly as
  today.
- **Editor:** each step gets "Waits for" — a multi-select of earlier steps by name (default: the step before). A
  small "Run beside the step before" action makes a step wait for the same steps as the one before it. The workflow
  gets "Steps at the same time" (1–4). Validation sentences are shown above the canvas, all of them.
- **Run view:** the header counts steps done as today; several steps can be "Running" at once; the step list in the
  details follows step order.

## Not covered / to know

- Branch-only conditions ("run this only if …") and joins that wait for "any one of" are not built (forge has
  neither).
- Two agent steps running at once in one project share its folder, as two tasks already do.
- Not run against the real `claude` CLI.

## Rulings

- `after` by index of earlier steps (not by name, not forward references): no cycle check needed; cost: reordering
  in the editor must re-number references (done in the editor).
- Default `max_parallel` 2, cap 4: cost if wrong: more parallel spend than expected — the spending limit still holds.
- On a failure the other running steps are cancelled rather than left to finish: cost: work in flight is lost; it
  stays in the run's history.

## Anti-goals

Changing how an existing linear workflow runs; a step starting before everything it waits for has succeeded; more
than `max_parallel` steps running; technical strings (indexes, JSON) on the main view.
