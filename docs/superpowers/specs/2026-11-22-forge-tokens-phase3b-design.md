# Forge port — Phase 3b: token care and the Questions inbox — design

Program: port every jspark-forge feature into the orchestrator (owner: "Copy each and every feature of forge").
Inventory: `docs/research/jspark-forge-feature-inventory.md` (F22, F23, F24, F25); deltas
`docs/research/jspark-forge-delta-v0.5-v0.6.md` (F96, F97, F99) and `-delta-v0.6-v0.7.md` (F120). Builds on Phase
3a (`docs/superpowers/specs/2026-11-22-forge-resume-phase3-design.md`): the per-thread handoff, Continue (fresh
or resume), the one prompt composer, notes, ground rules and the co-guard PostToolUse nudges.

## Intent
Agents run headless (`claude -p`), so nobody watches the context grow or the spend approach the hard
`--max-budget-usd` cut, and an agent's closing question ("should I also migrate the old table?") sits unread in
the log. Forge solves these in a terminal (status line, usage bands, `/waiting`). We have something forge lacks:
the server sees every stream message. Phase 3b turns that into (1) token figures per step and thread, (2) warnings
that reach the agent before it hits a wall, (3) an automatic hand-over to a fresh session when context gets
large, and (4) an inbox where people answer agents' questions and the thread carries on.

## Decisions (owner: proceed autonomously; these are the designer's calls)
- **The server is the bridge** (inventory F23). The worker parses per-message `usage` from stream-json and writes
  `${CO_TASK_DIR}/usage.json`; co-guard's PostToolUse reads it, exactly as it reads `handoff.md` and
  `notes.stamp` in 3a. No status line, no plan 5-hour/7-day windows (not in `-p`).
- **Per-message usage** (F96): each `assistant` stream event carries `message.id`, `message.model` and
  `message.usage` (`input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`).
  One API message can arrive as several events with the same id: count each id once, keeping the **last**
  usage seen for it (output grows). An event with `parent_tool_use_id` belongs to the helper started by that
  tool call; the helper's kind is the `subagent_type` (else `description`, else "helper") of the main-thread
  `Agent`/`Task` tool_use with that id. **Unverified on our CLI version** (no real `claude` in tests): the parser
  tolerates a missing `usage`, `id` or `parent_tool_use_id` (falls back to result-only totals, as today), and the
  README says the figures are checked on the first live run.
- **Context size** = `input_tokens + cache_creation_input_tokens + cache_read_input_tokens` of the latest
  main-thread message. Peak context is kept too.
- **Cost while running is an estimate.** The stream reports cost only in the final `result`. A price table
  (`app/usage/prices.py`, per million tokens, matched by model-id prefix, longest prefix wins) gives a running
  estimate; at finish the step's figure becomes the result's `total_cost_usd`. Unknown model → no estimate: the
  budget band is off for that step and the UI shows tokens only. Values (USD per MTok: input / output /
  cache write / cache read): `claude-fable-5-2` 8/40/10/0.20; `claude-fable-5-1`, `claude-fable-5` 10/50/12.5/1;
  `claude-opus-5-5` 4/20/5/0.20; `claude-opus-5`, `claude-opus-4-8`, `claude-opus-4-7`, `claude-opus-4-6`
  5/25/6.25/0.50; `claude-sonnet-5-5` 2/10/2.5/0.10; `claude-sonnet-5` 2/10/2.5/0.20; `claude-sonnet-4-6`
  3/15/3.75/0.30; `claude-haiku-5-5` 0.10/0.50/0.125/0.01; `claude-haiku-4-5` 1/5/1.25/0.10. Cache write = 1.25 ×
  input; cache read = the published figure where known, else 0.1 × input. Labelled "est." everywhere.
- **Budget bands** (F23) at **80 %** and **95 %** of the step's `max_budget_usd` (the hard cut Claude applies).
  Each fires once per step. 80 %: "save notes now"; 95 %: "finish the small step you are on, save notes, start
  nothing new, end your turn". The project's monthly `budget_usd` keeps its existing alerts (not a band).
- **Fresh-session threshold** (F97, replaces F23's context band): a per-project `fresh_session_tokens`
  (null = off, which is the default; the settings form suggests **150000**; allowed 50000–900000). Crossing it,
  co-guard injects FRESH_LINE once: "finish the small step, save notes with what is next, end your turn; the
  thread continues in a fresh session". When that step then **completes** (not failed/cancelled/timeout) the
  server queues an automatic Continue in **fresh** mode (3a row 4) when either the handoff has an open todo or
  the notes changed after the line was injected. At most **3** automatic continues in a row per thread; any
  step not made by this rule resets the count. An auto-continue step is marked `auto_continued` and shown as
  "continued automatically: context reached N".
- **Questions inbox** (F22 + F99 fields). A `TaskQuestion` row: question, up to 4 options, a recommendation,
  `blocks` (what is waiting on it), `reversible` (bool or unknown). Two ways in: (a) the agent files it with
  `POST /tasks/{id}/questions` and its own `CO_API_TOKEN` (own task only, at most **5** open per thread, then 409);
  (b) after a run ends, the server checks the final `result_text`: when the step touched files or made a commit
  and its last paragraph ends with `?`, that paragraph (≤ 600 chars) is filed with `source="final_text"` unless
  the agent filed one during that step. A ground-rules paragraph (org toggle, default on) tells agents to file
  blocking questions this way, with a recommendation, then end the turn.
- **Answering continues the thread.** `POST /questions/{id}/answer` with a free answer or "accept the
  recommendation" marks it answered and queues a follow-up step (the existing follow-up path, resume when the
  answerer owns the session, else fresh with the handoff) whose prompt quotes the question as data and states
  the person's answer. Dismiss closes it without a step. Who may answer/dismiss: same as Continue
  (`_manage_allowed(need_contributor=True)`). The agent token can file and read its own task's questions, never
  answer or dismiss.
- **Notifications:** filing a question sends the existing `notifications.py` webhook with event `question`.
- **"So far" line per thread** (F120): "4 steps · 41 min · $2.10 · 1.2M tokens — last: <title>, 18 min, $1.40",
  summed over the thread's steps from stored totals; a running step adds its live estimate.
- **Home "Open handoffs"** (F25 `/status`): threads I started or may manage whose latest step ended in the last
  14 days, is not running, and whose handoff is open (not closed in 3a) with an open todo or a non-completed
  status — at most 10, newest first, each with the Continue button.

## Goals (Phase 3b scope)
1. Usage capture in the worker: per-message dedup, main vs helper kinds, per-model figures, context and peak,
   the 5 largest tool results (tool name and original length, measured **before** `_short` cuts it), and
   co-guard's own injected characters (from spool lines that carry `injected`). Stored in a `TaskUsage` row per
   step, updated at most every 5 s while running and at finish.
2. `usage.json` in the task dir (atomic, 0600, at most one write per 2 s): `context_tokens`, `est_cost_usd`,
   `max_budget_usd`, `fresh_session_tokens`, `ts`. co-guard ignores it when `ts` is older than 120 s.
3. co-guard PostToolUse bands: BUDGET_80_LINE, BUDGET_95_LINE, FRESH_LINE, each once per step (a spooled
   `band` record), joined with the 3a nudges. The worker turns spooled bands into run-log events.
4. The automatic fresh continue with its cap, the project setting, and its run-log line.
5. `GET /tasks/{id}/usage`: the step's breakdown, the thread totals and the so-far parts, and up to three tips
   (fixed texts: a tool result over 20 000 chars → "read narrower ranges"; peak context over the project
   threshold or 150 000 when unset → "split long work across fresh sessions"; cache reads under 50 % of input
   across 20+ messages → "keep the start of prompts stable").
6. Questions: model, agent filing route, final-text capture, list/answer/dismiss routes, ground-rules paragraph,
   webhook; Home section, Rail badge, TaskDetail card.
7. UI: on TaskDetail a context chip ("context 63k", amber at 80 % of the threshold, red past it), a budget chip
   ("budget 41 % est."), the so-far line, the "Where the tokens went" panel; the project setting; Home "Open
   handoffs"; the auto-continue line.
8. 3a leftovers: snapshot the handoff **before** committing a terminal status in `_finish`; a compose failure
   followed by Retry must not reuse the old recovery record (`recovery.pending` cleared/rebuilt);
   `tests/guard/test_custom.py` wall-only timing assert → `time_limit`; the deferred minors named in the plan.

## Non-goals
- Test-result chips from Bash output (F116b) — moves to Phase 5 with the gate parser it shares.
- Plan 5-hour/7-day usage windows; forge's status line (F24).
- Project monthly budget bands (existing alerts stay).
- Answer → decision offer (Phase 4), launch-item links (Phase 5), "Ask about this run" (Phase 3c).
- A per-message usage table (only per-step aggregates are stored).

## Anti-goals
- A band or FRESH_LINE that fires repeatedly, fires on stale figures, or changes a PreToolUse decision.
- An automatic continue that loops (cap 3), follows a failed/cancelled step, or runs under someone else's
  credentials (it is created as the thread's step owner, same as Continue).
- Agent text treated as a human answer: an agent can never mark a question answered, and the follow-up prompt
  frames the question as quoted data and the answer as the person's.
- A secret or hidden character in a question, its options or the usage breakdown (redact + `clean_text`).
- Double-counted tokens from repeated `message.id` events.

## Approaches considered
| Question | Options | Outcome |
|---|---|---|
| Where bands are decided | server kills/marks the run / **co-guard injects from usage.json** | inject: the agent saves notes and stops cleanly; the server never cuts a run |
| Running cost | wait for `result` / **price-table estimate, replaced at finish** | estimate: bands need a figure mid-run |
| Context over threshold | warn only / **wrap up + automatic fresh Continue (capped)** / hard stop | auto with cap: nobody is at a keyboard; 3a's fresh Continue carries the handoff |
| Question capture | AskUserQuestion hook (not in `-p`) / **agent route + final-text check** | both: the route is precise, the check catches agents that just ask |
| Usage storage | event per message / **one aggregate row per step** | aggregate: bounded size, cheap reads |

## Builder's latitude
Module layout under `app/usage/` and `app/questions/`, card layout and copy apart from the fixed texts, the
largest-results list rendering, exact polling intervals, test structure.
