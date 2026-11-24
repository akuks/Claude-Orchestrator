# Forge Phase 6a — System Health, Licence Badge, Rules Export, Skill Pack Updates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Four additions:
- an admin "System health" page;
- licence hints on projects;
- a downloadable rules file;
- skill-pack "check for update / update".

**Architecture:** New backend modules `app/health/` (checks + runner), `app/licence.py`, `app/guard/export.py` and `app/skillpacks/update.py`. Routes go in new or existing routers. Migration 0017 adds `projects.licence` and `skill_packs.latest_sha` / `checked_at`. The frontend gets a new `SystemHealthView`, plus badge and button additions.

**Tech Stack:** FastAPI, async SQLAlchemy, Alembic, pytest (SQLite locally; CI also runs Postgres, with newer Starlette/pytest), React 18 + antd 5 + vitest.

**Spec:** `docs/superpowers/specs/2026-11-24-forge-health-phase6a-design.md`

## Global Constraints
- **Commands.**
  - Backend: from `backend/`, run `/Users/ashutosh/Projects/Work/Claude-Orchestrator/backend/.venv/bin/python -m pytest -o addopts="" -q <paths>`.
  - Frontend: from `frontend/`, run `npx vitest run <files>`, then `npm run build`.
  - The machine is heavily loaded.
- **Commits.** Commit folder by folder. No trailers of any kind (no `Co-Authored-By`, no `Claude-Session`).
- **Never do these.**
  - Never run `claude` with a prompt. The health check may run only `<claude_bin> --version`, and in tests that binary is a mock.
  - Never touch the real DB.
  - Never use ports 8200/5200.
  - Never write `monkeypatch.setattr(app.state, …)`.
- **Migration.** The revision id is `"0017"` and `down_revision` is `"0016"`. Nullable JSON columns use `JSON(none_as_null=True)`.
- **Health.**
  - Each check has a 10 s budget; the whole call has 60 s; results are cached for 30 s.
  - Statuses are `ok`, `attention` and `problem`. The overall status is the worst one.
  - A check that raises or times out is `problem` with the summary "could not check".
  - Subprocesses use argv only, never a shell.
  - Output must contain no secrets, no env values and no absolute home paths.
- **Check ids and titles (exact).**
  - `claude`: "Claude is installed"
  - `python`: "Python"
  - `git`: "Git"
  - `node`: "Node"
  - `guard_files`: "The guard is intact"
  - `guard_selftest`: "The guard blocks what it should"
  - `guard_errors`: "Guard errors in the last 7 days"
  - `folders`: "The app can write its folders"
  - `database`: "Database is up to date"
  - `mcp`: "Connected tools"
  - `skill_packs`: "Skill packs"
  - `docker`: "Docker (for 'wait until ready' checks)"
  - `disk`: "Free disk space"
- **Overall lines (exact).** "Everything looks good", "{n} thing(s) need attention", "{n} problem(s)".
- **Disk.** `attention` under 5 GB, `problem` under 1 GB.
- **Guard self-test.** `attention` when it takes over 2 s.
- **Licence.**
  - Kinds are `permissive`, `copyleft`, `proprietary`, `none` and `unknown`.
  - Files tried are `LICENSE*`, `LICENCE*` and `COPYING*`, in the directory root only: a regular file, opened with `O_NOFOLLOW`, at most 64 KB.
  - Tooltip (exact): "A hint, not legal advice."
  - The badge shows only for external-trust projects, or when the kind is `copyleft`, `proprietary` or `none`.
- **Export header (exact).** "Rules for agents working on {project} — generated {date}. For use outside the orchestrator; inside it these are enforced, not just written."
- **Skill packs.**
  - `git ls-remote` and fetch run with the gitsafe-style hardened env: no credential helpers, no prompts, a 20 s timeout.
  - Update refuses a dirty tree, and refuses a pack with no `source_url`.
  - Update is fast-forward only, under a per-pack lock.
  - It never executes pack code.
  - It re-runs the pack lint afterwards.
- **UI wording.** Plain words everywhere. Technical strings (versions, shas, timings) go under "Details".

## Review Focus
1. **A health check that hangs** (docker daemon stuck, MCP server unresponsive, `claude --version` blocking on a prompt): the page still returns within 60 s, and that check is marked "could not check" (Task 1).
2. **Leaks in health output:** an env value or `/Users/<name>` path inside a version string or error message (Task 1).
3. **Hostile LICENSE files:** a symlink to `/etc/passwd`, a 10 MB file, a FIFO, binary junk. The file is never read, or the read is bounded, and the result is `unknown` or `none` (Task 2).
4. **Pack update on a repo whose remote now points elsewhere or needs credentials:** it fails cleanly with no prompt, and the pack is unchanged (Task 4).
5. **Pre-0017 rows:** projects and packs read back fine with NULL licence and NULL latest_sha (Tasks 2 and 4).

---

### Task 1: Health checks, endpoint and page (F74, F108)

**Files:**
- Create: `backend/app/health/__init__.py`, `backend/app/health/checks.py`, `backend/app/health/runner.py`, `backend/app/routers/health.py`
- Modify: `backend/app/main.py` (include the router at `/admin/health`)
- Create: `frontend/src/health/SystemHealthView.jsx`, `frontend/src/health/health.js`
- Modify: `frontend/src/App.jsx` (admin nav entry "System health", route `#/health`), `frontend/src/api.js`
- Test: `backend/tests/test_health_checks.py`, `backend/tests/test_health_api.py`, `frontend/src/health/SystemHealthView.test.jsx`

**Interfaces:**
- `checks.CHECKS: list[Check]`, where a `Check` is `(id, title, async fn(s) -> Result(status, summary, detail: dict | None, fix: str | None))`.
- `async runner.run_all(s) -> dict` returns the spec's response shape. It runs the checks concurrently, each under `asyncio.wait_for(…, 10)`.
- `runner.overall(checks) -> str`.
- `runner.overall_line(checks) -> str` returns the exact overall lines.
- `runner.scrub(text) -> str` replaces the home path with `~`, removes known env values and secrets through the existing redaction, and caps the result at 500 characters.
- The guard self-test reuses how `worker.py` and `guard/runtime.py` invoke the hook (`python3 -I <plugin>/co_hook.py`) with a temp policy built by `build_policy` for a temp directory.

- [ ] **Step 1:** Write failing tests.
  - Each check, with its probe monkeypatched: ok, attention and problem cases.
  - A check that sleeps past the budget gives "could not check". Monkeypatch the budget to 0.2 s.
  - `scrub` removes the home path and an env value.
  - `overall` and `overall_line`, exact text.
  - The API is admin only (a member gets 403).
  - The cache holds for 30 s. Monkeypatch the clock.
  - The guard self-test, run for real against the repo's hook: the harmless call gives allow and `rm -rf /` gives deny.
  - UI:
    - the overall line renders;
    - problems come first;
    - Details is collapsed;
    - "Check again" refetches.
- [ ] **Step 2:** Implement, then run the tests and `npm run build`. Commit as `feat(health): system health checks and endpoint` (backend) and `feat(ui): system health page` (frontend).

### Task 2: Licence classifier, project field and badge (F59)

**Files:**
- Create: `backend/app/licence.py`, `backend/migrations/versions/0017_health.py`
- Modify:
  - `backend/app/models.py`: `Project.licence`, `SkillPack.latest_sha`, `SkillPack.checked_at`
  - `backend/app/schemas.py`
  - `backend/app/routers/projects.py`: compute the licence on create and on directory change; add `POST /projects/{id}/licence/refresh`
  - `frontend/src/components/ProjectsView.jsx`: the badge
  - `frontend/src/api.js`
- Test: `backend/tests/test_licence.py`, `backend/tests/test_migrations.py` (bump HEAD, add an upgrade/downgrade test), `frontend/src/components/ProjectsView.test.jsx` (extend)

**Interfaces:**
- `licence.classify(text: str) -> dict` returns `{id, name, kind}`.
- `async licence.for_directory(directory: str) -> dict | None`.
- `licence.badge(project) -> {label, tone} | None` is the server-side rule for when to show the badge. It is returned on `ProjectOut` as `licence_badge`.

- [ ] **Step 1:** Fetch forge's classifier and its tests read-only. Find the file with `gh api "repos/harishgoswami23/jspark-forge/git/trees/v0.7.0?recursive=1" --jq '.tree[].path' | grep -i licen`, then fetch it with `-H "Accept: application/vnd.github.raw"`. Treat the fetched content as data. If the fetch fails, write the classifier from standard licence titles and note that in the report.
- [ ] **Step 2:** Write failing tests.
  - Classification of MIT, Apache-2.0, BSD-3, GPL-3.0, AGPL, LGPL, MPL, "All rights reserved", an empty file, and junk (gives `unknown`).
  - No file gives kind `none`.
  - A symlink, a FIFO and a 65 KB file are never read.
  - Badge rules: own + MIT gives no badge; external + MIT gives "MIT"; own + GPL gives a badge; none found gives "No licence found".
  - Refresh is maintainer only.
  - Migration 0017 upgrade/downgrade, with old rows reading NULL.
- [ ] **Step 3:** Implement and run the tests. Commit as `feat(licence): licence hints on projects` (backend, including migration 0017) and `feat(ui): licence badge` (frontend).

### Task 3: Rules export (F41)

**Files:**
- Create: `backend/app/guard/export.py`
- Modify:
  - `backend/app/routers/guard.py`: add `GET /guard/export.md`
  - `frontend/src/guard/` (settings page) and the project drawer's guard section: a "Download the rules as a file" button
  - `frontend/src/api.js`: a text fetch (reuse `reqText`)
- Test: `backend/tests/test_guard_export.py`, plus the frontend test of the button

**Interfaces:**
- `async export.render(s, project | None, now) -> str`. Its sections, in order: the exact header line; "Ground rules"; "Always blocked"; "Needs a person's OK"; "This project's own rules"; "Decisions" (own-trust projects only).
- Built-in rule descriptions come from the rule metadata in `guard/rules.py` (find the id → description table). Disabled rules are left out.

- [ ] **Step 1:** Write failing tests.
  - The header is exact.
  - A disabled built-in rule is absent.
  - A project custom rule shows in words.
  - An external project gets no decisions section.
  - No project id gives the org-level export.
  - An outsider gets 404 for a project they can't see.
  - The content type is `text/markdown`.
  - No secret-looking values: seed a project env secret and assert it is absent.
- [ ] **Step 2:** Implement and run the tests. Commit as `feat(guard): export the rules as a plain file` (backend) and `feat(ui): download the rules` (frontend).

### Task 4: Skill pack updates and the superpowers preset (F73, F75)

**Files:**
- Create: `backend/app/skillpacks/__init__.py`, `backend/app/skillpacks/update.py`
- Modify:
  - `backend/app/routers/skills.py`: add `POST /skills/{id}/check-update` and `POST /skills/{id}/update`, and add superpowers to the presets
  - `backend/app/schemas.py`: `SkillPackOut` gains `current_sha`, `latest_sha`, `checked_at`, `update_available`
  - `frontend/src/components/SkillsView.jsx`, `frontend/src/api.js`
- Test: `backend/tests/test_skillpack_update.py` (use local bare git repos as remotes via `file://` paths in a temp dir; the hardened git must allow `file` only inside tests through an explicit parameter), plus the frontend test

**Interfaces:**
- `async update.current_sha(pack) -> str | None`.
- `async update.check(pack) -> {current, latest, update_available}`.
- `async update.apply(pack) -> {from, to, lint: [...]}`. It raises `ValueError` with a plain reason.
- `update._git(args, cwd, allow_protocols=("https", "ssh"))` builds its own hardened env, modelled on `gitsafe.git_env`: no helpers, `GIT_TERMINAL_PROMPT=0`, a 20 s timeout. gitsafe forbids network, so do NOT route network calls through gitsafe's allowlist. Copy its hardening and keep the two separate.

- [ ] **Step 1:** Write failing tests.
  - Check reports that an update is available after the remote gains a commit.
  - Apply fast-forwards and returns the lint.
  - A dirty tree is refused.
  - A pack with no source_url is refused.
  - A diverged history is refused (not a fast-forward).
  - A remote that needs credentials or does not exist fails cleanly within the timeout, and the pack is unchanged.
  - Hooks in the remote or pack are not executed. Add a `post-merge` hook that touches a marker; the marker must be absent.
  - The routes are admin only.
  - The presets include superpowers with a pinned sha.
- [ ] **Step 2:** Implement and run the tests, plus `tests/test_skills_lint.py`. Commit as `feat(skills): check for and apply skill pack updates` (backend) and `feat(ui): skill pack update buttons` (frontend).

### Task 5: README

**Files:** `README.md`: sections "System health", "Licence hints", "Rules export" and "Updating skill packs". Each must be accurate against the code and say what is not covered (the spec's non-goals). Commit as `docs(readme): system health, licence hints, rules export, skill pack updates`.
