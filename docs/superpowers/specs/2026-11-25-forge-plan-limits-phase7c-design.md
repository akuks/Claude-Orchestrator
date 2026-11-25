# Forge port Phase 7c — Claude plan limits — design

From the 2026-11-25 audit: forge F23 (plan 5-hour / 7-day windows, 80/95 bands, reset time) and P8 (limit handover,
reset time shown). Forge reads plan figures from the interactive status line, which does not run for headless
`claude -p`; this app must use what the headless stream emits. Standing autonomy; plain-language screens.

Research (2026-11-25, Claude Code docs + forge v0.8.1 source):
- `--output-format stream-json` documents `{"type":"rate_limit_event","rate_limit_info":{"status":
  "allowed"|"allowed_warning"|"rejected","resetsAt"?: number,"utilization"?: number, …},"uuid","session_id"}`.
  Not documented: which window it is about, the unit of `resetsAt`, the scale of `utilization`, how often it comes.
- `system` events with subtype `api_retry` carry `attempt, max_retries, retry_delay_ms, error_status, error`
  (`error: "rate_limit"` = a 429 against the quota). `assistant` messages may carry `error: "rate_limit"`. `result`
  has `is_error`, `api_error_status`. Limit texts include "You've hit your session limit", "…weekly limit",
  "…Opus limit", "…Sonnet limit".
- Today the worker drops `rate_limit_event`, keeps only `{subtype, session_id}` of system events, and detects a limit
  only on stderr (`_TRANSIENT_MARKERS`), retrying after 30 s × 2^attempt (2 attempts).

## 1. Record what Claude says about the plan

- The stream parser handles `rate_limit_event`: store the **raw** `rate_limit_info` plus our normalised reading in a
  new table `plan_limit_status` (migration **0021**): `id, credential_owner` (user id, or `"org"` for the org
  fallback key), `status`, `resets_at` (UTC datetime or null), `utilization` (float or null, raw), `raw` (JSON,
  capped 2 KB), `seen_at`, `task_id`. One row per owner, updated in place (latest wins); a task event
  `plan_limit` with the same fields is added to the task.
- Normalising `resetsAt`: a number > 10^12 is milliseconds, otherwise seconds; outside now−1 day … now+8 days →
  null (logged once). `utilization` is stored raw and shown only under "Details" (its scale is not documented).
- `system/api_retry`: keep all its documented fields in the task event (not just subtype).
- API-key credentials: plan limits do not apply; nothing is stored for them unless an event arrives anyway.

## 2. Notice a limit hit reliably

- A run counts as **plan limit reached** when any of: a `rate_limit_event` with `status: "rejected"`; an
  `api_retry` or `assistant.error` equal to `rate_limit` followed by the run ending in error; a `result` with
  `is_error` whose text contains one of the limit texts; or today's stderr markers.
- On a limit hit with a known reset time: the task is **requeued to start at the reset time + 1 minute** (not the
  30-second backoff), its status shows "Paused — your Claude plan limit was reached. Starts again at 14:05.", and
  this does not count as one of the transient retry attempts. Without a reset time: today's backoff.
- While an owner's latest status is `rejected` with a future reset time, **no new task using that credential
  starts**; queued tasks wait with the same message. Workflow steps wait the same way (held, never failed).
- `allowed_warning`: one `plan_limit` warning event on the task ("Your Claude plan is close to its limit. It resets
  at 14:05.") — no pause.
- No automatic switch to the org fallback key (forge never switches either: it costs someone else's money).

## 3. Screens

- **Home / top bar:** when the signed-in person's latest status is `rejected` (future reset) or `allowed_warning`
  (seen within the last 5 hours): a banner "Your Claude plan limit was reached. Work starts again at 14:05." /
  "Your Claude plan is close to its limit. It resets at 14:05."
- **Usage page:** a "Claude plan" card: the last status in plain words, the reset time, "Last checked 3 min ago";
  under Details the raw figures. For API keys: "Plan limits don't apply to API keys."
- **Task page:** the paused message and the time it starts again; the event in the run log as one plain line.
- Admins see each person's plan status on the Users page as a small tag (Limit reached until 14:05 / Close to
  limit).

## Not covered / to know

- Not run against the real `claude` CLI. The event's exact shape and when it is sent are from the documentation;
  the owner should check (see the README list): whether a setup-token run emits `rate_limit_event`, the unit of
  `resetsAt`, the scale of `utilization`, which window it describes, and what a run that hits the limit prints.
- There is no live "% of your 5-hour window" between events — the headless stream does not give one reliably.
- No automatic fallback to the org key.

## Rulings

- Store raw + normalised, show only what is certain (status, reset time); utilisation under Details.
- Pause per credential owner rather than per task: one hit means every task with that credential would hit it too.
- Cost if wrong: a stale `rejected` row could hold work — mitigated: it expires at its reset time, and an admin can
  clear it ("Try now").

## Anti-goals

Guessing field meanings on the main view; switching someone's work onto the org key; failing a task or workflow
step because of a plan limit.
