# Connected tools start from a neutral folder — design

Owner decision (2026-11-24, "Fix it for all"): close the gap the Phase 6b final review described.

## The problem (existing behaviour, before this change)
A task's connected tools (MCP servers) with the `stdio` transport are started by `claude` as its own children.
`mcp_manager.build_config` writes only `command`, `args` and `env`; the worker starts `claude` with
`cwd = the project folder`. So a tool such as `npx -y some-mcp@1.2.3` starts **inside the project's repository**.
`npx` reads `<repo>/.npmrc` (which can point at another registry) and prefers a matching package in
`<repo>/node_modules`. Repo content — committed, or written by an agent in an earlier task — can therefore run as
the tool process at the start of every task in that project, before any tool call, outside co-guard, with that
task's credentials (the Claude credential, the GitHub token, `CO_API_TOKEN`, the project env and the tool's own
vault secrets). The same holds for other launchers that resolve from the working directory (`uvx`, `bunx`,
`python -m`, a relative `./bin/tool`).

## Decision
Every `stdio` connected tool is started through a small **launcher** that changes into a private folder (holding only inert marker files)
first and then runs the tool's command unchanged. A per-tool switch restores the old behaviour for a tool that
truly needs to start in the project folder. Applies to **all projects**.

### Launcher
- `backend/app/mcp_launch.py`: a stdlib-only script, run as `<server python> -I <launcher> <neutral_dir> --
  <command> <args…>`. It `os.chdir(neutral_dir)`, sets `PWD` to it, removes `INIT_CWD` and `OLDPWD`, and
  `os.execvp(command, [command, *args])`. If the folder is missing or not a directory, or exec fails, it prints one
  plain line to stderr and exits 127. It adds nothing else to the environment and never reads the project.
- The launcher file lives in the server's code tree, which is already a guard-protected path (agents cannot edit
  it); `-I` keeps Python from importing anything from the working directory or user site.
- `build_config` writes, for a stdio server whose switch is off (the default):
  `{"command": "<sys.executable>", "args": ["-I", "<abs launcher>", "<neutral_dir>", "--", "<command>", *args],
  "env": {…as today…, "CO_PROJECT_DIR": "<project folder>"}}`. `CO_PROJECT_DIR` lets a tool be told where the
  project is without starting there (only added when the task has a project folder).
- **Neutral folder:** `workspaces/_mcp_configs/<task_id>/cwd/<server prefix>/` — per task and per tool, created
  0700 by `prepare_for_task`, under the per-task config folder that agents already cannot read and that is
  removed with the task's config. Never shared between tasks or users. *Amended after the security review:* it is
  not strictly empty. The server writes only inert stopper files into it when it is created — `package.json`
  containing `{}`, an empty `node_modules/`, an empty `.npmrc` and an empty `uv.toml` (files 0600, folder 0700) —
  because npm takes the nearest ancestor with `node_modules` or `package.json` as the project (reading its
  `.npmrc` and preferring its `node_modules`) and uv walks up for `uv.toml`; without them the search climbed to
  `workspaces/`, which agents can write. The code map index folder (`workspaces/_codemap/run/<project_id>/`) gets
  the same stoppers, and the guard also protects those marker names directly under `workspaces/`.
- A relative `command` (contains `/` but is not absolute, e.g. `./bin/tool`) cannot work from a neutral folder;
  the registry refuses to save one unless the switch is on ("A command that starts with ./ needs 'Start in the
  project folder'"). A bare name (`npx`, `uvx`, `node`) is resolved through PATH as today.

### The switch
- `mcp_servers.start_in_project` (bool, default false, migration **0019**). Admin-only, on the tool's form:
  **"Start in the project folder"** with the help text "Only for tools that must start inside the project. The
  project's own files can then change what the tool runs." When on, `build_config` writes the entry exactly as
  before this change.
- Existing tools get the default (off) → they move to the neutral folder on their next task. This is the point of
  the change; the README and the health page say so.

### What does not change
- `http` tools (no process). The server-side "Test" probe (already runs from the server's own folder).
- The environment the tool gets (credentials are unchanged — the tool is the admin's chosen code; the change stops
  the *project* from replacing that code).
- Tool names and prefixes, policies, the guard.

### Health and visibility
- The System health "Connected tools" card adds, under Details, how many tools have "Start in the project folder"
  on; `attention` is **not** raised for it (it is an admin's choice), but the summary names the count when above 0.
- The run log's existing tools line is unchanged.

## Not covered / to know
- This has not been run against the real `claude` CLI or real MCP servers (the port cannot run them). The wrapper
  is a plain `chdir` + `exec`; if a tool stops working, turn its switch on.
- A tool can still read the project (it is given `CO_PROJECT_DIR`, and it runs as the same OS user); the change
  only stops the project from choosing what code runs as the tool.
- `npx` still reads the server user's own `~/.npmrc` and cache (needed for private registries).

## Anti-goals
- A launcher that imports from, reads, or writes the project folder.
- A neutral folder shared across tasks, or left behind after the task's config is removed.
- Changing what an existing tool's command or environment is, beyond the working folder and `CO_PROJECT_DIR`.
