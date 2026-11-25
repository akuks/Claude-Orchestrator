# Phase 8b — personal preferences — design

Owner-approved addition (2026-11-25, from the Cognition comparison): each person keeps a short list of standing
preferences that they write and approve themselves; it is added to every task they start, in any project. Plain
screens.

## Data

- New table `user_preferences` (migration **0025**): `user_id` (PK, FK users ON DELETE CASCADE), `enabled` (bool,
  default true), `items` (JSON list of `{id, text, on}`), `updated_at`.
- Caps: at most 12 items; each text 1–240 characters after `clean_text` (hidden/bidi characters stripped, trimmed,
  whitespace collapsed to single spaces within a line; newlines not allowed). Empty items rejected (422).
- Only the person writes their own list. Agents never write it. Admins cannot read or change other people's lists.

## API

- `GET /me/preferences` → `{enabled, items:[{id,text,on}], updated_at, preview}` (`preview` is the exact paragraph
  that will be added, or null when nothing would be added). New user → `{enabled:true, items:[], ...}`.
- `PUT /me/preferences` with `{enabled, items:[{id?, text, on}]}` → same shape. Whole-list replace. Agent tokens
  (role agent) get 403.
- `/me/` is already a proxied dev prefix.

## What the agent receives

- Added to the system prompt (the `--append-system-prompt` text) after the ground rules and before the decisions
  index, for every task whose `created_by` user has `enabled` and at least one item `on`:

  ```
  # Preferences of the person who started this task
  They set these themselves. Follow them unless the task, the project's instructions or a recorded decision says
  otherwise.
  - <item>
  - <item>
  ```
- Each item is redacted with the run's secrets at injection time.
- Applies to every task kind that carries `created_by` (direct, follow-up steps — the sender's list, workflow
  steps — the run starter's list, scheduled, agent-dispatched children — the parent's starter). Tasks with no
  `created_by`: nothing added. Helper sessions: not added (main session only).
- Applies to `external` trust projects too (the text is the person's own, not the repository's).
- Org switch: a new ground-rule switch `personal_prefs` (default on) in the existing ground-rules settings; off →
  never added.
- The `ground_rules` event's `included` list gains `"personal_prefs"` with a `personal_count` field.

## Screens

- User menu: "My preferences" beside "My credentials". Modal:
  - intro line: "Short notes about how you like to work. They are added to every task you start, in any project.
    Project rules and decisions come first."
  - master switch "Add my preferences to my tasks";
  - list of items each with an on/off switch, the text (editable inline), delete; "Add a preference" (max 12;
    counter "3 of 12"); character counter per item (240);
  - "What the agent sees" collapsed section showing `preview`;
  - Save / Cancel; saved message "Saved. Your next tasks will use these."
- Task log: the ground-rules line shows "The starter's preferences were added (3)" when included. The texts are not
  shown to others (count only).
- Ground-rules settings (admin): the new switch "People's own preferences" with help "Add each person's own
  preferences to the tasks they start."

## Rulings (designer)

- Person-written only; no agent-proposed items in this phase (keeps "person-approved" literal; proposals can come
  later).
- System-prompt channel (survives compaction, applies to resumed and continued steps).
- Count-only in the log (a person's list is theirs; others on the project see that it was used, not what it says).
