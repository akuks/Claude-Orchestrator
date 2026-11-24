# Forge port — Phase 6a: system health, licence badge, rules export, skill pack updates — design

Program: port every jspark-forge feature into the orchestrator. Inventory: `docs/research/jspark-forge-feature-
inventory.md` F41, F59, F73, F74, F75; `-delta-v0.5-v0.6.md` F108. Phase 6 is split: **6a** (this), **6b** code
map (F53–F57, F107), **6c** database tab (F58), recommended panel (F72, F95), task outputs list (F105).

Owner's standing UI rule (memory `plain-language-ui`): screens read plainly and show structure visually; technical
detail sits under "Details". Every label below is written for a non-technical reader.

## Intent
An admin should be able to open one page and see whether the app's moving parts are healthy, without a terminal.
Projects that hold someone else's code should show what licence it is under. The rules agents work under should be
downloadable as plain text. Skill packs should be updatable from the page that lists them.

## Decisions (owner: proceed autonomously; these are the designer's calls)

### System health page (F74, F108)
- `GET /admin/health` (admin only) runs the checks on request (no background job) and returns
  `{checked_at, overall: "ok"|"attention"|"problem", checks: [{id, title, status: "ok"|"attention"|"problem",
  summary, detail?, fix?}]}`. Each check has a 10 s budget; a check that raises or times out is `problem` with
  summary "could not check". The whole call is capped at 60 s. Results are cached 30 s per process.
- Checks (id — plain title):
  - `claude` — "Claude is installed": `<claude_bin> --version` as argv (the configured binary; **only** `--version`,
    never a prompt); shows the version; `problem` if missing.
  - `python`, `git`, `node` — versions via `--version`; `node` is `attention` (not `problem`) when missing.
  - `guard_files` — "The guard is intact": the co-guard plugin directory matches its expected content hash
    (reuse `guard/plugin.py`); `problem` on mismatch with fix "Restart the app to restore it."
  - `guard_selftest` — "The guard blocks what it should": run the hook the way the worker does (`python3 -I
    co_hook.py`) in a temp dir with a temp policy file, once with a harmless call (expect allow) and once with a
    known hard-blocked call (`rm -rf /`, expect deny); record ms; `problem` if either answer is wrong;
    `attention` if a call takes over 2 s.
  - `guard_errors` — "Guard errors in the last 7 days": count of guard error events (find the existing event/
    spool record type); `attention` above 0, with the latest message (redacted).
  - `folders` — "The app can write its folders": workspaces dir, projects dir, `_gates`, per-user dir root are
    writable (create+delete a temp file).
  - `database` — "Database is up to date": alembic current == head.
  - `mcp` — "Connected tools": each registered MCP server with its existing health status and version (name +
    status only; `attention` if any is unhealthy).
  - `skill_packs` — "Skill packs": each pack with its pinned commit (short sha) and whether an update is known.
  - `docker` — "Docker (for 'wait until ready' checks)": `docker version` argv, `attention` if missing (it is
    only needed for container checks).
  - `disk` — "Free disk space": free space on the workspaces volume; `attention` under 5 GB, `problem` under 1 GB.
- No secrets, tokens, env values or absolute home paths in any summary/detail (paths shown relative to the app
  folders; everything through the existing redaction).
- **UI:** an admin "System health" page (new nav entry next to the other admin pages): a big overall line
  ("Everything looks good" / "2 things need attention" / "1 problem"), then a grid of cards, one per check, each
  with a coloured dot, the plain title, one summary sentence, and a collapsed "Details" (version strings, timings,
  the fix). A "Check again" button. Problems first.

### Licence badge (F59)
- `app/licence.py` `classify(text) -> {id, name, kind}`: a port of forge's licence classifier (fetch read-only
  from forge v0.7.0 `scripts/lib/licence.js` or wherever `classify` lives; port its test cases). `kind` is
  `permissive` | `copyleft` | `proprietary` | `none` | `unknown`. Input capped at 64 KB; pure; linear.
- `async licence.for_directory(directory) -> dict | None`: reads the first of `LICENSE*`/`LICENCE*`/`COPYING*`
  in the directory root (regular file, no symlink, ≤ 64 KB, `O_NOFOLLOW`), returns the classification plus the
  file name and a content hash.
- Stored on the project: `projects.licence` (JSON, nullable: `{id, name, kind, file, hash, checked_at}`), migration
  **0017**. Computed when a project is created or its directory changes, and refreshed by `POST
  /projects/{id}/licence/refresh` (maintainers) — never on every page load. Reference projects are projects, so
  they get one too.
- **UI:** a small badge on the project card and drawer header for projects whose trust is `external` or whose
  licence kind is `copyleft`/`proprietary`/`none`: "MIT", "GPL-3.0 — share-alike", "No licence found", with a
  tooltip "A hint, not legal advice." Own projects with a permissive licence show nothing (no noise).

### Rules export (F41)
- `GET /guard/export.md?project_id=` (any signed-in user; with a project id it needs project visibility) returns
  `text/markdown`: the ground rules that are switched on (main-session text), then the block list **in words**
  (one line per active built-in rule: its plain description and whether it is "always blocked" or "needs a
  person's OK"), then the org's and the project's custom rules in words, then the project's active decisions
  (title + why, own projects only). Header line: "Rules for agents working on <project> — generated <date>.
  For use outside the orchestrator; inside it these are enforced, not just written."
- No secrets; custom rule patterns are shown as written (they are visible to project members today).
- **UI:** a "Download the rules as a file" button on the Guard settings page and in the project drawer's guard
  section.

### Skill pack updates (F73, F75)
- `POST /skills/{id}/check-update` (admin): for a pack with a `source_url`, `git ls-remote <url> HEAD` through a
  hardened call (no credentials helpers, no prompts, 20 s timeout, https/ssh URLs only as stored) and compare to
  the pack's current commit → `{current, latest, update_available}`; stored on the pack
  (`skill_packs.latest_sha`, `checked_at`; migration 0017).
- `POST /skills/{id}/update` (admin): `git fetch` + fast-forward to the remote HEAD in the pack's directory under a
  per-pack lock; refuses when the working tree is dirty or the pack has no `source_url`; then re-reads the pack
  manifest and re-runs the pack lint (5b) and returns its warnings. Never runs anything from the pack.
- A built-in preset entry for **superpowers** in the skill-pack presets with a pinned commit sha (the admin
  chooses to install it; nothing is installed automatically).
- **UI:** each pack row shows its short commit, "Check for update", and when one is available "Update to <sha>"
  with a confirm; lint warnings shown after an update.

## Goals
1. Health checks module + endpoint + page.
2. Licence classifier, project field (migration 0017), refresh, badge.
3. Rules export endpoint + buttons.
4. Skill pack check/update + superpowers preset + UI.
5. README sections "System health", "Licence hints", "Rules export", "Updating skill packs".

## Non-goals
- Running `claude` with a prompt in any health check. Auto-fixing problems. Auto-updating packs.
- Licence compatibility analysis. Scanning sub-folders for licences.

## Anti-goals
- A health page that leaks secrets or absolute paths, hangs on one slow check, or runs on every page load.
- A pack update that executes pack code, follows a redirect to another host silently, or clobbers local edits.
- A licence badge presented as legal advice.

## Approaches considered
| Question | Options | Outcome |
|---|---|---|
| When checks run | background schedule / **on request, cached 30 s** | simple, no idle cost; admins press "Check again" |
| Licence detection | SPDX library / **port forge's small classifier** | no dependency; forge's cases as tests |
| Pack update | re-clone / **fetch + fast-forward under a lock** | keeps the pinned-sha model, refuses dirty trees |

## Builder's latitude
Module layout (`app/health/` suggested), exact card layout, test structure, wording apart from the titles above.
