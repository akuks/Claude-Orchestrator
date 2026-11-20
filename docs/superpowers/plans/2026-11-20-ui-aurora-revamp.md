# UI Revamp — Aurora Comet (Sunset) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-skin and re-shell the orchestrator UI as "Aurora Comet" (light + night, Sunset palette) with an icon rail, ⌘K palette, a new Home, a Run detail page and Projects cards, using three.js for the comet scenes.

**Architecture:** One theme module drives antd tokens + CSS variables for both modes. A small hash router replaces the segmented tabs. Signature pieces (rail, palette, Home, comets, run page, project cards) are new components; every existing view is kept and restyled by the theme. three.js is lazy-loaded only by the two 3D components, which fall back to an SVG renderer.

**Tech Stack:** React 18, antd 5, Vite 5, three, fuse.js, @fontsource/inter, vitest (pure-function tests), Playwright (click-through), FastAPI (one read-only endpoint).

**Spec:** `docs/superpowers/specs/2026-11-20-ui-aurora-revamp-design.md` (read "Visual system", "3D", "Clarifications").

## Global Constraints
- Exact new dependency versions (pin, no ranges): `three@0.169.0`, `fuse.js@7.0.0`, `@fontsource/inter@5.1.0`; dev: `vitest@2.1.8`, `jsdom@25.0.1`.
- Sunset tokens (light): primary `#f97316`; gradient `linear-gradient(135deg,#e11d48,#f97316)`; logo conic amber `#f59e0b` → orange `#f97316` → rose `#e11d48`; page bg `linear-gradient(180deg,#ffffff,#fff7f0 60%,#fff4f6)`; surface `rgba(255,255,255,.85)`, border `#fde7d6`, radius 16, shadow `0 10px 24px rgba(249,115,22,.08)`; text `#3b2433`, secondary `#8a6f7d`. Status: success `#10b981`, warning `#f59e0b`, danger `#e11d48`, info `#6366f1`. Night: bg `#0f0a14`→`#1a0f1f`, surface `rgba(255,255,255,.06)`, text `#fbeff3`.
- three.js must NOT be in the entry chunk: only `src/three/*` imports `three`, and only via `React.lazy`/dynamic `import()`.
- Text, numbers and controls are HTML, never drawn in WebGL.
- `prefers-reduced-motion: reduce`, the in-app "Reduce motion" setting, or no WebGL → SVG fallback.
- Routes (hash): `#/home` (default), `#/tasks`, `#/tasks/<id>`, `#/runs/<id>`, `#/projects`, `#/automation`, `#/approvals`, `#/mcp`, `#/usage`, `#/security`, `#/users`, `#/teams`. Users/Teams rail items admin-only (Phase 2 rules unchanged).
- The live-output terminal and code blocks stay dark in both modes.
- Never run the real `claude`; scratch servers on ports 8313/5313 with a temp SQLite DB; don't touch ports 8200/5200 or the DB `claude_orchestrator`.
- Commits folder-wise, **no Co-Authored-By trailer**. Backend suite stays green: `cd backend && CO_DB_NULL_POOL=1 .venv/bin/python -m pytest -p no:warnings`. Frontend: `cd frontend && npm run build` and `npx vitest run` green.

## Review Focus
1. A member (non-admin) with zero projects lands on Home → empty states everywhere, no errors, no admin-only calls (Task 7 Playwright).
2. 30+ live workflows → scene shows 12 comets + "+N more", stays responsive (Task 5 vitest on `layoutComets`, Task 7 visual).
3. Deep link `#/runs/<id>` to a run the viewer can't see → friendly "not found" page, not a blank screen (Task 6).
4. Browser back/forward after navigating via ⌘K and comet clicks returns to the previous page and selection (Task 3 vitest + Task 7).
5. Night mode + reduced motion together → readable contrast, static comets (Task 7 screenshots).

---

### Task 1: `GET /workflows/runs/live` (backend)

**Files:** Modify `backend/app/routers/workflows.py`, `backend/app/schemas.py`; Test `backend/tests/test_runs_live.py`; Modify `backend/tests/test_scoping_coverage.py` only if the route needs listing (it's a viewer-scoped list — it should be covered by the list-leak sweep; make sure P's run appears in its fixture data path).

**Interfaces — Produces:** `GET /workflows/runs/live` → `list[LiveItemOut]`, newest first, max 50:
`LiveItemOut{kind: "run"|"thread", id, title, project_ids: list[str], status, created_at, stages: list[LiveStageOut]}`;
`LiveStageOut{name, state: "done"|"current"|"next"|"failed"|"waiting_approval", task_count}`.
- Runs: status in pending/running/paused, visible per existing run visibility rules. Stage state from that stage's tasks: all completed → done; any failed/cancelled → failed; any awaiting_approval → waiting_approval; index == current_stage → current; else next.
- Threads: running/queued root tasks with `coordinator=true` visible via `task_visibility_clause`; stars = children (`parent_task_id == root.id`) in creation order, `name` = child title, state from child status (completed→done, running/queued→current, awaiting_approval→waiting_approval, failed/cancelled→failed); `project_ids` = distinct non-null child/root project ids.
- [ ] Tests: `test_live_includes_active_run_with_stage_states` (run with stage0 completed tasks, stage1 running → ["done","current"]); `test_live_excludes_finished_runs`; `test_live_scoped_for_outsider` (member with no grant sees none of P's runs/threads); `test_live_thread_from_coordinator_children`; `test_live_waiting_approval_state`. RED → implement → GREEN → full suite → commit (`feat(api): live workflows endpoint for the home scene`; tests).

### Task 2: Theme system, fonts, night mode, colour sweep

**Files:** Rewrite `frontend/src/theme.js`; Create `frontend/src/theme/tokens.js`, `frontend/src/theme/ThemeModeContext.jsx`, `frontend/src/theme/aurora.css`; Modify `frontend/src/main.jsx`, `frontend/index.html` (body bg via CSS var), every component with hard-coded colours (≈11 files), `frontend/src/components/UsageView.jsx` + `StatsBar.jsx` (chart colours from tokens), `frontend/package.json` (+ `@fontsource/inter`, `vitest`, `jsdom`, `"test": "vitest run"`); Test `frontend/src/theme/tokens.test.js`.

**Interfaces — Produces:** `tokens.js`: `export const palette` (Global Constraints values), `export function antdTheme(mode: "light"|"night")` → antd theme config, `export function cssVars(mode)` → `{ "--co-bg": ..., ... }`; `ThemeModeContext.jsx`: `ThemeModeProvider`, `useThemeMode() → { mode, setMode, reduceMotion, setReduceMotion }` (persisted in localStorage keys `co.mode`, `co.reduceMotion`, wrapped in try/catch; initial reduceMotion = `matchMedia('(prefers-reduced-motion: reduce)')`). `aurora.css`: shell background, drifting aurora haze (`.co-aurora`, disabled when `[data-reduce-motion="true"]`), glass surface utility `.co-surface`, terminal stays dark (`.co-terminal`).
- [ ] Tests (vitest): `antdTheme("light").token.colorPrimary === "#f97316"`; night `colorBgBase` is dark; `cssVars` returns the same keys for both modes.
- [ ] Sweep: `grep -rnE "#[0-9a-fA-F]{3,6}\b" frontend/src --include=*.jsx` returns only `tokens.js`-sourced usages (allowlist: none in .jsx); replace with tokens/CSS vars.
- [ ] Verify build; commit (`frontend: aurora sunset theme, night mode, self-hosted Inter`).

### Task 3: App shell — hash router, icon rail, top bar, ⌘K

**Files:** Create `frontend/src/shell/router.js`, `frontend/src/shell/Rail.jsx`, `frontend/src/shell/TopBar.jsx`, `frontend/src/shell/CommandPalette.jsx`, `frontend/src/shell/commands.js`; Modify `frontend/src/App.jsx` (remove the Segmented nav; render by route; Tasks list at `#/tasks`, `#/tasks/<id>` opens TaskDetail); Tests `frontend/src/shell/router.test.js`, `frontend/src/shell/commands.test.js`.

**Interfaces — Produces:** `router.js`: `parseHash(hash: string) → { page: string, id?: string }` (unknown → `{page:"home"}`), `toHash({page,id}) → string`, `useRoute() → [route, navigate(routeOrHash)]` (hashchange-driven so back/forward work). `commands.js`: `buildCommands({projects, tasks, workflows, isAdmin, actions}) → Command[]` with `Command{id, label, group: "Pages"|"Projects"|"Tasks"|"Workflows"|"Actions", run()}`; `searchCommands(commands, query) → Command[]` (fuse.js, threshold 0.35, max 20). Rail order: Home, Tasks, Projects, Automation, Approvals (badge = pending count), Usage, Security, MCP, then Users, Teams (admin only); tooltip labels. TopBar: palette trigger field ("Ask or jump…  ⌘K"), credential tag (presence/kind from `/me/credentials`, existing wording), night toggle, reduce-motion toggle in the avatar menu, existing user menu items.
- [ ] Tests: `parseHash("#/runs/abc") → {page:"runs",id:"abc"}`; `parseHash("#/nope") → {page:"home"}`; round-trip `toHash(parseHash(h)) === h` for every route in Global Constraints; `buildCommands` hides Users/Teams for non-admins; `searchCommands(cmds,"canon")` ranks project "Canonizer" first.
- [ ] Verify build; quick manual run on scratch servers: every rail item opens its page; ⌘K opens/closes with keyboard; back button works. Commit (`frontend: app shell with icon rail, hash routes and command palette`).

### Task 4: Home page (data + layout + SVG comets)

**Files:** Create `frontend/src/home/HomeView.jsx`, `frontend/src/home/useHomeData.js`, `frontend/src/comets/CometSvg.jsx`, `frontend/src/comets/layout.js`; Modify `frontend/src/api.js` (`liveRuns: () => req('GET','/workflows/runs/live')`), `App.jsx` (route `home`); Tests `frontend/src/comets/layout.test.js`, `frontend/src/home/useHomeData.test.js` (pure selectors).

**Interfaces — Produces:** `layout.js`: `layoutComets(items: LiveItem[], {max=12}) → { comets: Comet[], overflow: number }`, `Comet{ id, kind, title, stars: [{name,state,x,y,z}], headIndex }` — deterministic curved paths (same input → same coords); `CometSvg({ comets, overflow, onOpen })` renders the same layout in SVG (used as fallback and for project cards with a `compact` prop). `useHomeData()` → `{ needsYou: Item[], live: LiveItem[], recent: Task[], stats: {costToday, liveCount}, loading, error }`, polling every 10s; pure selectors `selectNeedsYou({approvals, failedTasks, schedules, creds, me, now})` (failed = own, `completed_at`/`created_at` within 24h; schedules = owned by me with `blocked_reason`; missing credential when `creds.claude === "none"`).
- [ ] Tests: `layoutComets` with 30 items → 12 comets, overflow 18; stable coords for the same input; `headIndex` = index of the "current" (or last done) star; `selectNeedsYou` filters by owner and 24h window and includes the credential item.
- [ ] Home layout per the approved mockup: greeting with name and stats; "Needs you" panel with action buttons (Approve → existing approve API; Open → route); "Live workflows" panel (renders `CometScene` lazily in Task 5 — in this task render `CometSvg`); "Recent". Empty states for each. Commit (`frontend: home with needs-you, live comets (svg) and recent`).

### Task 5: three.js comet scene + timeline

**Files:** Create `frontend/src/three/CometScene.jsx`, `frontend/src/three/CometTimeline.jsx`, `frontend/src/three/scene.js` (pure three setup helpers), `frontend/src/comets/Comets3D.jsx` (lazy wrapper choosing 3D vs SVG); Modify `HomeView.jsx` to use `Comets3D`; `package.json` (+ `three`, `fuse.js` if not yet); Test `frontend/src/comets/Comets3D.test.jsx` (fallback selection logic, jsdom).

**Interfaces — Produces:** `Comets3D({ comets, overflow, onOpen, variant: "scene"|"timeline" })` → lazy `CometScene`/`CometTimeline` when WebGL available and motion not reduced, else `CometSvg`. `CometScene`: perspective camera, additive-blended head sprites moving along `CatmullRomCurve3` through stars, pulsing current star, dim future stars, small particle field, pointer parallax; HTML labels positioned each frame from projected coordinates; click → `onOpen(id)`; keyboard-focusable list of comets rendered below the canvas. `CometTimeline({ stages, onSelectStage })`: horizontal comet across stages, same visual language. Both: pause on `document.hidden` and when off-screen (IntersectionObserver), `setPixelRatio(Math.min(devicePixelRatio, 2))`, dispose geometries/materials/renderer on unmount.
- [ ] Tests: `Comets3D` renders SVG when `reduceMotion` true or WebGL unavailable (mock `WebGLRenderingContext` absent).
- [ ] Verify `npm run build` output: three.js appears only in a separate chunk (paste the chunk list in the report) and the entry chunk is not larger than before + 60 kB gzip. Commit (`frontend: three.js comet scene and timeline with svg fallback`).

### Task 6: Run detail page + Projects cards

**Files:** Create `frontend/src/runs/RunView.jsx`, `frontend/src/projects/ProjectCards.jsx`; Modify `App.jsx` (route `runs`), `frontend/src/components/ProjectsView.jsx` (card grid as the default projects view; existing drawer/tabs open from a card), `frontend/src/components/WorkflowsView.jsx` (run links navigate to `#/runs/<id>`); Test `frontend/src/runs/RunView.test.jsx` (stage mapping, not-found state).

**Interfaces — Consumes:** `GET /workflows/runs/{id}` (board: run + tasks), `Comets3D variant="timeline"`, existing `TaskDetail`. **Produces:** `RunView({ id })`: header (title, project, started by, branch if any, Cancel/Retry per existing permissions), timeline, selected stage's tasks list, embedded TaskDetail for the selected task; 404 → friendly "Run not found or you don't have access" with a link Home. `ProjectCards({ projects, live, onOpen })`: card per project — name, `my_role`, budget used (`total_cost_usd` / `budget_usd`), task count, mini `CometSvg compact` from live items with that project id, "idle" when none, "paused" tag when any of its schedules has `blocked_reason`.
- [ ] Tests: stage-state → star state mapping; 404 renders the not-found message.
- [ ] Commit (`frontend: run detail page with comet timeline; project cards`).

### Task 7: Polish + Playwright verification

**Files:** Touch-ups anywhere in `frontend/src`; Create nothing in the repo for Playwright (scripts live in the scratchpad).
- [ ] Scratch backend 8313 + Vite 5313, temp SQLite, fake `claude` that exits 0 printing a minimal stream-json result (never the real binary), seed via API: admin + member, 3 projects, a workflow with 3 agents and an active run, a coordinator thread, a paused schedule, a failed task. Click through: every rail item (admin and member), ⌘K navigate to a project/run, comet click → run page, back/forward, night toggle, reduced-motion emulation (static comets), deep link to an invisible run (not-found), member with zero projects (empty states; no request to an admin-only endpoint in the network log). Screenshots light + night for Home, Run, Projects, Tasks, Automation, Users.
- [ ] Fix anything found (with vitest where logic changed). `npm run build`, `npx vitest run`, backend suite green. Commit (`frontend: aurora polish from click-through`).
