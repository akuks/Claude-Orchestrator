# Forge port Phase 6c — database tab, recommended panel, published links — design

Last part of the forge port (owner: "Copy each and every feature of forge", standing autonomy: designer makes the
calls and records them). Covers F58, F72, F95, F105 and two Code map carry-overs. Screens follow the owner's rule:
plain words, technical strings under "Details".

## 1. Database tab (F58)

**What:** a project tab "Database" that shows the tables, columns, primary keys and links between tables, worked out
by reading the project's SQL migration files in order. It **never connects to a database** and never runs anything.

- **Where it reads:** inside `Project.directory` only, these roots, each on its own (never merged): `migrations/`,
  `db/migrations/`, `supabase/migrations/`, `prisma/migrations/`. Files ending `.sql` (for prisma:
  `<name>/migration.sql`). Order: numeric-aware path order. Skipped: down/undo files (name contains `.down.`,
  `_down.`, `.undo.`, `_undo.`, or starts with `U` + digits + `__`).
- **Limits:** at most 5,000 files and 5 MB read in total; shown at most 60 tables, 200 columns per table, 50 links
  per table. A hit limit is said plainly in the answer (`limits` list). Work runs off the event loop with a 10 s
  budget; over budget → a plain "took too long" answer, never a hang.
- **Safe reading:** every file is opened without following links (`lstat` regular file, `O_NOFOLLOW`), and its real
  path must stay inside the project folder. A migrations root that is itself a link is skipped.
- **Parsing:** `sqlglot` (new dependency, MIT, pure Python, `sqlglot>=25` in `backend/requirements.txt`). Replay
  covers CREATE TABLE, ALTER TABLE (add/drop/rename column, add/drop constraint), DROP TABLE, RENAME TABLE.
  Schema-qualified names: `public.` and `dbo.` count as no schema. A statement sqlglot cannot read is **counted,
  not guessed**: `not_understood` total plus up to 5 `file:line` samples. Table changes inside `DO` blocks or
  function bodies mark that table **uncertain** ("may differ from what is shown").
- **Answer** — `GET /projects/{id}/schema[?table=<name>]`, any project member:
  `{found: bool, roots: [{root, files, tables:[{name, uncertain, defined_at:"file:line", columns:[{name,type,nullable,primary_key,defined_at}], links:[{columns, to_table, to_columns, defined_at}], decisions:[{id,title}]}]}], as_of: "<short sha>|null", not_understood: {count, samples}, limits: [..], note}`.
  `decisions`: decisions whose recorded paths cover that table's migration file, at most 3 per table (use the
  existing server-side decision path matcher).
  `found=false` with `note` "No SQL migration files found. Looked in migrations/, db/migrations/,
  supabase/migrations/ and prisma/migrations/." — also the answer for projects whose migrations are code (Alembic,
  Django, Rails): those are **not read** (said in the README).
- **Agents:** a task may call it for its own project with its `CO_API_TOKEN` (add to the token's allowlist).
- **Nothing is stored.** Results are cached in memory by the files' (path, size, mtime) signature.

## 2. Recommended panel and UI skills (F72, F95)

**Detection** (`app/recommend/detect.py`): a project "has a user interface" when the root `package.json` (read
capped at 256 KB, no links followed) lists a front-end dependency (`react`, `next`, `vue`, `nuxt`, `svelte`,
`@sveltejs/kit`, `@angular/core`, `solid-js`, `preact`, `astro`, `vite`), or a file ending `.html`, `.tsx`, `.jsx`,
`.vue` or `.svelte` sits directly in the root or in `src/` (also checked in `frontend/` and `frontend/src/`, and
`frontend/package.json` — ruling: the owner's own layout). `react` = react/next/preact present.

**Panel** — project tab "Recommended", shown only when detected. `GET /projects/{id}/recommended` (member) →
`{has_ui, react, ui_skills, brand_skill, brand_skill_found, items:[{key,name,why,kind,present,preset}]}`:

| key | kind | shown when | one click |
|---|---|---|---|
| `browser-playwright` | tool | has_ui | existing pinned preset `@playwright/mcp@0.0.86` |
| `browser-devtools` | tool | has_ui | new pinned preset `npx -y chrome-devtools-mcp@1.13.0` |
| `ui-shadcn` | tool | react | new pinned preset `npx -y shadcn@4.23.0 mcp` |
| `frontend-design` | skill | has_ui | none — the panel says "Add it under Skills"; `present` when an enabled pack contains a `frontend-design` skill |

`present` for a tool = an enabled connected tool whose args contain the same package name is registered for this
project (`codemap.status.registered_for` rule). One click = admin only, adds the preset as a connected tool scoped
to this project through the existing `POST /mcp/servers` (so it starts in the private folder, switch off).
Non-admins see "Ask an admin to add this". Nothing is installed without a click.

**UI skills (F95):** two project columns (migration **0020**): `ui_skills` bool default **true**, `brand_skill`
string(120) null, matching `^[A-Za-z0-9_.:-]{1,120}$` (it is put into prompts). Editable by project maintainers on
the panel ("Tell agents to use the design skills"). When `ui_skills` is on **and** the project has a user interface
(detected at task start, off the event loop), this paragraph is added to the main session's ground rules and to
every helper's start text:

> This project has a user interface. Before changing anything a user sees, load the frontend-design skill
> (frontend-design:frontend-design) if you have it. {brand}

`{brand}` = "Also load the brand skill `<name>`." when `brand_skill` is set; else "Also load the project's brand
skill." when `.claude/skills/brand/SKILL.md` or `skills/brand/SKILL.md` exists; else "If your skills list has a
brand or style skill for this project, load it too." Ruling: forge limits this to building/planning/reviewing
helpers; here every helper gets it (simpler; cost is one sentence).

## 3. Published links (F105)

**What:** a card on the task page, "Published outside the project", listing what the run put somewhere else, and a
flag when there is no copy in the project.

- **Recording (co-guard PostToolUse, no network, spool only):** a record is spooled when
  (a) the tool is `mcp__…` and the last part of its name contains `create`, `publish`, `upload`, `post`, `share`,
  `deploy` or `send`, and the response holds an `http(s)` link; or
  (b) the tool is Bash, the command contains `gh pr create`, `gh gist create`, `gh release create` or
  `gh issue create`, and the response holds a link.
  Stored: `link` (first link; **query string, fragment and user:password removed**, ≤ 500 chars), `tool`, `title`
  (the input's `title`/`name`/`subject` when it is a short string, ≤ 120 chars), `local_file` (first string in the
  tool input that is a path to an existing regular file inside the project or task workspace, stored relative) or
  null. At most 20 per task; duplicates of the same link ignored.
- **Server:** the spool reader turns each into a task event of type `output` with that payload plus
  `kept: bool`. No new table.
- **Ground rule:** a new org switch `local_first` (default on): "Nothing you make may live only outside the project
  folder. If you publish a page, document or file somewhere else, keep its source file in the project too."
- **Screen:** the card shows title or tool, the link, "Copy in the project: `<file>`" or an amber "No copy in the
  project". Hidden when there are none.

## 4. Code map carry-overs

"Last built" and "Next try" show a readable local time. The build log shown under Details replaces the project's
real folder path with `<project folder>`.

## Already covered (verify only)

F76 (all tasks unattended; every "ask the user" goes to the UI), F77 (re-inject after compaction), F78 (guard test
suite; the real-`claude` integration test stays not done — the port never runs the real CLI).

## Not covered / to know

- Migrations written as code (Alembic, Django, Rails, Prisma schema files) are not read. Only SQL files.
- The schema is what the files say, not what a live database holds.
- The two new presets and the shadcn `mcp` command were taken from the npm registry on 2026-11-24 and have **not**
  been run. Chrome DevTools needs Node 20.19+.
- Published links are found by tool name and a link in the answer; a tool that publishes without returning a link
  is missed, and a link in a `create` answer that is not a publication is listed.
- None of this has been run against the real `claude` CLI.

## Anti-goals

Connecting to any database; executing anything from the project; storing link query strings; installing a tool or
skill without an admin's click; technical strings on the main screens.
