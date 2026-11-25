# Forge port Phase 7f — UI checks (F71) — design

Forge's "UI path" (F71) gives a project three browser checks — screenshots at two widths, an accessibility scan
(axe, WCAG 2.1 AA, light and dark), and Lighthouse budgets — plus a UI workflow and a brand skill template. Forge
ships them as files to copy by hand (`docs/research/forge-f71/`, MIT). This phase brings them to the app. Standing
autonomy; plain screens.

## 1. The "UI" workflow template

Stages (template only; existing workflows untouched):
1. **Direction** (agent: Planner) — what the screens should look like.
2. **Approve look** (approval, `ahead: true`).
3. **Build** (agent: Builder).
4. **Test** (gate: `npm test`).
5. **Screens** (gate, 60 min): `npm run test:ui`, `npm run test:a11y`, `npm run test:lighthouse` — the project's own
   scripts (no `npx <package>` run by the server; versions pinned by the project's lockfile). Its hint: "Needs the UI
   checks in the project first (Project → Recommended → Set up UI checks) and Node 20 or newer."
6. **Design review** (agent: Reviewer) — "check the screenshots, accessibility and Lighthouse results".
7. **Approve** (approval; release-style, not ahead).

## 2. "Set up UI checks" (project action)

- Offered on the Recommended tab for projects with a user interface (maintainers): "Set up UI checks". It starts an
  ordinary agent task in the project (the guard, approvals and normal review apply) with a fixed prompt that embeds,
  verbatim, the three check files adapted from forge (`backend/app/ui_checks.py` constants):
  - `tests/ui/visual.spec.ts` (widths 390 and 1280, height 900, `maxDiffPixelRatio: 0.01`, full page; a `SCREENS`
    list to fill from the project's routes),
  - `tests/a11y/a11y.spec.ts` (`@axe-core/playwright`, tags wcag2a/wcag2aa/wcag21a/wcag21aa, 390×844 and 1280×800,
    light and dark, no violations),
  - `lighthouserc.json` (3 runs; errors below accessibility 0.95, best-practices 0.9, CLS above 0.1; warn below
    performance 0.8),
  and tells the agent to: create them only where no file exists (never overwrite — report instead); add
  `playwright.config.ts` only if missing (with `baseURL`); add the scripts `test:ui` = `playwright test tests/ui
  --reporter=list`, `test:a11y` = `playwright test tests/a11y --reporter=list`, `test:lighthouse` = `lhci autorun`
  (and `test:ui:update` = `playwright test tests/ui --update-snapshots`); install `@playwright/test`,
  `@axe-core/playwright`, `@lhci/cli` as dev dependencies; fill `SCREENS`; commit on the current branch; not push;
  finish with a short list of what it added and what a person must do next (record the screenshot baselines once
  after approving the look, on the same OS that runs the checks).
- The task is created like any task by that person (their credential, their budget defaults); the API returns the task.

## 3. Brand guide template (Context Store)

- The Context Store gets "Start from a template" → "Brand guide": creates a project doc `brand/brand.md`, title
  "Brand guide", abstract "Brand rules: tokens, colours, type, spacing, components, accessibility, states, voice. Read
  before changing anything a user sees.", body = forge's brand skill sections 0–6 with the `<…>` placeholders kept,
  rule 6 naming the three npm scripts. Refused (409) if that path already exists in the project.
- The UI-skills paragraph (Phase 6c) also says: "If the project's Context Store has a Brand guide, read it."

## Not covered / to know

- Nothing here was run with real browsers. Known risks: Playwright browsers must be installed where gate runs can
  find them (`PLAYWRIGHT_BROWSERS_PATH` in the project env); screenshot baselines are OS-specific; Lighthouse needs
  the app to start on port 3000; threshold flakiness near 0.95/0.9.
- No package versions are pinned by us; the project's lockfile pins them.

## Rulings

- Agent task, not a server-side write, for adding files to a repo (guard + review apply; no new write path).
- Project scripts, not `npx <pkg>`, in gates.

## Anti-goals

Overwriting a project's existing tests or config; running packages the project didn't install; changing existing
workflows.
