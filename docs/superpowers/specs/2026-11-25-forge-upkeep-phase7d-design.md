# Forge port Phase 7d — upkeep and sharper notes — design

From the 2026-11-25 audit: F16 (close/archive handoffs), F19 (action-log compaction), F54 (code-map freshness
detail), F56 (old-code map prompts), F45 (decisions before shell-command edits), F4 (re-clone warning). Standing
autonomy; plain-language screens; technical strings under "Details".

## 1. Closed handoffs stay closed (F16)

- `snapshot()` (resume/handoff.py) no longer reopens a closed handoff on its own: when the row is `closed` and the
  thread has had no new run since `closed_at`, the snapshot updates the content but keeps it closed. A new run of the
  thread (Continue, Retry, a follow-up task) reopens it — that is the "stamp" forge uses.
- When a thread's task **completes successfully** and nothing is left open (no open questions for that thread), the
  handoff is closed automatically with `closed_by = "system"`. Failed, cancelled or waiting tasks stay open.
- Notes are kept (no data is deleted on close).

## 2. Tidying old history — off until an admin switches it on (F19, F16)

- Forge prunes automatically. Here the database is the record of everything, and the owner's rule is that nothing
  in it is changed without asking, so **tidying is off by default** and an admin switches it on in System health:
  "Tidy old run history" with "Keep full history for: 30 / 60 / 90 days" (default 60).
- When on, a daily job (new `_retention_loop` in main.py, like `_approval_timeout_loop`; first run 10 minutes after
  start) does, for tasks that **finished** more than N days ago:
  - replaces their task events with one `summary` event: counts by event type, first and last time, and the last 20
    log lines as plain text (each cut at 300 characters);
  - for their **closed** handoffs: drops the previous version (`prev_content`, `prev_text`), keeping the current one.
  - Never touches tasks that are running, queued, waiting, or not finished; never deletes a task, handoff, note or
    decision. Works in batches of 200 tasks with a short pause between batches.
- System health shows a card: "Run history: N tasks could be tidied (about X MB)" with the switch; after a run,
  "Last tidied <date>: N tasks".
- Migration **0022** adds an index on `task_events.created_at` and `task_events.task_id` if missing.

## 3. Sharper code-map notes (F54, F56)

- At task start the worker works out, with the safe git helper, for the project's code map and each reference: the
  indexed sha vs the current HEAD — `current`, `ahead by N commits`, `different history` (indexed sha is not an
  ancestor of HEAD), or `could not check` — and up to 10 files changed since the index (committed changes since
  the sha plus uncommitted/untracked files). This goes into the task's policy; the hook shows it:
  - "The code map is current (built at abc1234)."
  - "The code map is 3 commits behind (built at abc1234). Changed since: a.py, b/c.py, … (and 4 more)."
  - "The code map was built on a different history; treat it as a rough guide."
  - "Could not check whether the code map is current."
  The same note is not repeated in one session when nothing changed.
- **References (old code):** when a reference project's HEAD moved since its index, its old-code line adds "It has
  changed since — ask an admin to rebuild its map." At session start, references that have no usable map are listed
  once: "These reference projects have no code map yet: X, Y."
- The safe git helper's allow-list gains exactly what this needs (`rev-list --max-parents=0 HEAD`,
  `merge-base --is-ancestor`, a bounded `diff --name-only <sha> HEAD`) — nothing else.

## 4. Decisions before shell-command edits (F45)

- The guard already works out which files a shell command writes. It now returns them (first 50) with its verdict,
  and the hook looks up recorded decisions for those files exactly as it does for Edit/Write today, adding the same
  note before the command runs. Only for commands the guard allows; no extra parsing.

## 5. Re-clone warning (F4)

- A project stores its repository's first commit (`projects.root_commit`, migration 0022) when it is created, when
  its folder changes, and the first time a task starts if empty.
- If at task start the first commit differs from the stored one: a run notice "This folder now holds a different
  repository than before (it may have been cloned again). Decisions and the code map may not apply." and a System
  health warning; the stored value is updated only when a maintainer presses "This is expected" on the health card.

## Not covered / to know

- Tidying is off until switched on; once on it cannot bring back what it replaced (the summary stays).
- Not run against the real `claude` CLI.

## Rulings

- Tidying default OFF (owner's rule on changing stored data); forge's default is on. Cost: history keeps growing
  until switched on.
- Auto-close only on success with nothing open. Cost: some finished threads stay in "Open handoffs" until closed.
- Freshness computed by the worker at task start (the hook stays stdlib-only, no git calls in the hook). Cost: the
  note can be up to one task old within a long session.

## Anti-goals

Deleting tasks, notes, decisions or handoffs; changing stored data without the switch; git commands beyond the
listed ones; technical strings on main views.
