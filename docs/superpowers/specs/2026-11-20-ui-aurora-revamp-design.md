# UI revamp — "Aurora Comet", Sunset palette — design

## Intent and why
The owner dislikes the current UI (dark warm-brown Ant Design theme, a long
row of segmented tabs). Goals in their words: **ease of use** and **very
visually appealing / futuristic, with 3D**. They reviewed browser mockups and
chose: **Aurora Comet**, **light theme**, **Sunset palette** (amber → orange →
rose), with the three page mockups (Home, Run detail, Projects) as the layout.

## Decisions (from the conversation)
- Direction: Aurora Comet (light) — workflows drawn as glowing comet trails
  through their stages; soft aurora haze at the top of every page.
- Palette: Sunset — `#f59e0b` (amber) → `#f97316` (orange) → `#e11d48` (rose).
- Approach 1: re-theme Ant Design centrally + hand-build the signature pieces;
  every existing page keeps working.
- **three.js** for the comets and the Home scene (user's explicit choice over
  SVG).

## Goals
- One app shell: slim left **icon rail** (with tooltips), top bar with a
  **⌘K command palette**, the user's Claude-credential status and avatar menu.
- **Home** (new default view): greeting; **Needs you** (approvals the viewer
  can decide, own failed tasks in the last 24h, paused schedules, missing
  credential); **Live workflows** rendered as a 3D comet scene; **Recent**
  (own last 5 finished threads).
- **Run detail**: workflow runs show a horizontal 3D comet timeline across
  their stages (done = lit, current = glowing pulse, next = hollow); below it
  live output, cost/tokens/files, and a follow-up box. Plain task threads
  keep today's TaskDetail content, restyled.
- **Projects**: card grid — name, your role, team, budget used, current work
  as a mini comet (SVG is fine for these small cards — see Constraints), idle
  / paused states.
- All other pages (Tasks list, Automation, Approvals, MCP, Usage, Security,
  Users, Teams, settings modals) keep their functionality and adopt the new
  look via the theme.
- **Night aurora** toggle (dark variant of the same palette), remembered per
  browser.

## Non-goals
- No change to backend APIs except, if needed, read-only conveniences the Home
  needs (see Constraints — prefer existing endpoints).
- No new features beyond Home's aggregation and ⌘K.
- No mobile app; responsive down to laptop width (≥1024px); usable but not
  optimised below.

## Anti-goals
- 3D that hurts readability: text, numbers and controls are never rendered in
  WebGL — they are HTML overlays.
- A slower app: three.js must not be in the initial bundle; pages without 3D
  load as fast as today.
- Motion people can't turn off: respect `prefers-reduced-motion` (and a
  "Reduce motion" setting) with a static render.
- Breaking existing flows (task create, approvals, credentials, members…).

## Approaches considered
| Approach | Trade-off | Outcome |
|---|---|---|
| 1. Re-theme antd centrally + hand-built signature pieces | lowest risk, fastest, everything keeps working | **chosen** |
| 2. Replace antd with Tailwind + custom components | full control; rewrite of ~6.5k lines, high regression risk | rejected |
| 3. three.js for comets/home | real 3D depth; heavier, readability risk | **adopted inside approach 1** for Home + Run detail only, lazy-loaded, HTML labels, reduced-motion fallback |

## Visual system (tokens)
- Light: page background `linear-gradient(180deg,#ffffff,#fff7f0 60%,#fff4f6)`;
  surface `rgba(255,255,255,.85)` with 1px `#fde7d6` border, radius 16,
  shadow `0 10px 24px rgba(249,115,22,.08)`; text `#3b2433`, secondary
  `#8a6f7d`; primary `#f97316`; gradient accent `linear-gradient(135deg,#e11d48,#f97316)`
  for primary buttons and the logo (conic amber→orange→rose); aurora haze
  `linear-gradient(90deg, transparent, rgba(245,158,11,.28), rgba(249,115,22,.25), rgba(225,29,72,.22), transparent)`
  blurred 30px at the top of the shell, drifting slowly.
- Status colours: success `#10b981`, warning `#f59e0b`, danger `#e11d48`,
  info `#6366f1` (kept distinct from the brand gradient where meaning matters).
- Night aurora: background `#0f0a14` → `#1a0f1f`; surfaces `rgba(255,255,255,.06)`;
  text `#fbeff3`; same accents, haze stronger.
- Typography: Inter (Google Fonts), 14px base; numbers tabular.
- All of the above live in **one theme module** (antd `ConfigProvider` tokens
  + CSS variables). The ~40 hard-coded colours in components are replaced by
  tokens.

## 3D (three.js)
- Library: `three` (pin an exact version) used via a small React wrapper
  component; no react-three-fiber (keeps deps small) — builder may choose
  r3f if it's clearly simpler, stating why.
- Loaded with dynamic `import()` only on Home and Run detail.
- **Home scene**: each live workflow run (and each running coordinator thread)
  is a comet: a glowing head (additive-blended sprite) moving along a curved
  path through stage "stars"; done stars lit, current star pulsing, future
  stars dim; soft particle field and parallax with the pointer. Max ~12
  comets; extra ones collapse into "+N more". Click a comet → open its run.
- **Run detail timeline**: same visual language laid out horizontally across
  the stages; current stage pulses; hover/click a star → stage tasks.
- HTML overlay labels positioned from projected 3D coordinates; keyboard
  focusable list fallback below the canvas for accessibility.
- Performance: pause rendering when the tab is hidden or the canvas is off
  screen; cap pixel ratio at 2; target 60fps on a laptop iGPU; dispose
  geometry/materials on unmount.
- Fallbacks: no WebGL or reduced motion → static SVG rendering of the same
  scene.

## Data for Home (existing endpoints first)
- Needs you: `GET /approvals` (already filtered to what the viewer can decide);
  `GET /tasks?status=failed` (own, last 24h, filter client-side by
  `created_by`); `GET /schedules` (`blocked_reason`); `GET /me/credentials`.
- Live workflows: `GET /workflows/runs` (active ones) + `GET /workflows/runs/{id}`
  for stage/task state; running coordinator threads from `GET /tasks?status=running`.
- Refresh: poll every 10s (existing patterns) — the task WebSocket stays for
  the detail page.

## ⌘K command palette
Opens with ⌘K/Ctrl-K or the top-bar field. Fuzzy search across: pages, projects,
recent tasks (title), workflows ("Run <workflow>…" opens the run dialog),
"New task", "My credentials", "Toggle night mode". Keyboard-only usable.

## Constraints
- React 18 + antd 5 + Vite stay. New deps: `three` (+ optionally a tiny fuzzy
  search lib, e.g. `fuse.js`), exact versions pinned.
- Existing behaviour and role-gating (Phase 2/3a) must not regress; admin-only
  rail items (Users, Teams) only for admins.
- Small project-card comets use SVG (dozens of WebGL contexts would be wasteful).

## Testing / verification
- `npm run build` passes; initial JS bundle does not include three.js (check the
  Vite build output chunk list).
- Playwright click-through on a scratch backend (temp SQLite, fake claude):
  rail navigation to every page, ⌘K navigation, Home sections populated from
  seeded data, comet click → run detail, night toggle, reduced-motion fallback
  (emulated), admin vs member rail items. Screenshots kept for review.
- Backend test suite unaffected (run once if any backend file changes).

## Clarifications (builder check; all designer's calls)
1. **Navigation/URLs:** add lightweight hash routing (no new router library is
   required; react-router is acceptable if simpler): `#/home` (default),
   `#/tasks`, `#/tasks/<id>`, `#/runs/<id>`, `#/projects`, `#/automation`,
   `#/approvals`, `#/mcp`, `#/usage`, `#/security`, `#/users`, `#/teams`.
   Back/forward and deep links work; ⌘K and comet clicks navigate by URL.
   A workflow run has its own page (`#/runs/<id>`).
2. **Live data:** add ONE read-only endpoint `GET /workflows/runs/live` →
   active runs (status pending/running/paused) visible to the viewer, each with
   `project_ids` and per-stage summaries `[{name, state: done|current|next|failed|waiting_approval, task_count}]`.
   Coordinator threads (root task with `coordinator=true`, running) appear as
   comets whose stars are their dispatched children in creation order (state
   from each child's status); returned by the same endpoint as
   `{kind: "thread", ...}`. Scoped with the existing Phase 2 access rules.
3. **Run detail** is a new page: comet timeline on top; below it, the selected
   stage's tasks; selecting a task embeds today's TaskDetail content (live
   output, follow-up, WebSocket). Star states map to stage state: done / current
   (pulsing) / next / failed (red) / waiting_approval (🔒). No separate
   "Approved" star — the mockup's was illustrative.
4. **Project cards:** show name, your role, budget used (`total_cost_usd` vs
   `budget_usd`), task count, current work as a mini SVG comet from the live
   endpoint filtered by project; "idle" = nothing live; "paused" = any of its
   schedules has `blocked_reason`. No team name on the card (avoids N+1).
5. **Palette:** the spec's Sunset tokens win; the pages mockup is layout-only.
   Greeting stats: cost today from `GET /usage/summary` (already viewer-scoped),
   live count from the live endpoint.
6. **Needs you:** approvals (all decidable), own failed tasks from the most
   recent 50 (`GET /tasks?status=failed&limit=50`, filtered to the last 24h),
   paused schedules the viewer owns, missing credential. No PR-review item.
7. **Night mode scope:** the live-output terminal and code blocks stay dark in
   both modes; UsageView charts take their colours from the theme in both modes;
   all hard-coded colours move to tokens.
8. **Font:** self-host Inter via `@fontsource/inter` (pinned) — no dependency
   on Google Fonts at runtime.
9. **Credential status in the top bar:** presence/kind only from
   `GET /me/credentials` (no test call).

## Builder's latitude
Exact component structure, animation curves, particle counts, copy, icon set
(Ant Design icons are fine), Home polling details, and whether to use
react-three-fiber. Anything changing the chosen direction, palette or the
three-page layout comes back for a decision.
