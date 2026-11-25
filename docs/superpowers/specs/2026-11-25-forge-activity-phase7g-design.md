# Forge port Phase 7g — capacity readout and "what was built" — design

Forge lists P3 (council), P4 (capacity), P5 (git activity) and P7 (secrets by name) as plans; none is built in forge
v0.8.1 (design notes only). This phase builds the two that clearly help this app: P4 and P5. P3 and P7 are parked
(see Rulings). Standing autonomy; plain screens.

## 1. Capacity readout (P4)

- `GET /admin/health/capacity` (admins): `{slots: worker_concurrency, running, queued, memory: {total, free} | null,
  load: {one, five, cores} | null, fits: int | null, tips: [str]}`.
  - Memory/load from the standard library only: `os.getloadavg()`, `os.cpu_count()`, Linux `/proc/meminfo`
    (`MemAvailable`), macOS `sysctl -n hw.memsize` + `vm_stat` (bounded 2 s). A container's limit
    (`/sys/fs/cgroup/memory.max`) is used when lower than the host's. Unknown → null, never an error.
  - `fits` = min(free slots, floor(free memory / per-run estimate)), per-run estimate = 1.5 GB (constant, said so).
  - Tips (exact): "All run slots are busy — new tasks wait in the queue." when free slots = 0; "Memory is low — fewer
    runs fit at once. Lower the worker count (CO_WORKER_CONCURRENCY) or free memory." when fits < free slots because
    of memory; "The machine is busy (load above the number of cores)." when 5-minute load > cores.
- System health gets a "Capacity" card: "Room for about N more runs at once (M running, K waiting)." — the numbers
  under Details. Not a pass/fail check (always OK unless the readout fails); shown first in the System group.

## 2. What was built (P5)

- `GET /projects/{id}/activity?days=30` (project members): from one bounded `git log` through the safe git helper
  (no merges; `--numstat`; authored date; at most 2,000 commits and the helper's time budget):
  `{days, commits: [{sha, short, date, author (email local part), title, added, removed, files, task: {id, title} |
  null}], by_day: [{date, commits, added, removed}], contributors: [{name, commits, added, removed}], truncated}`.
  - Each SHA counted once; window on authored date; a commit is "substantial" when added+removed ≥ 50 (query
    `min_lines`, default 0 = show all).
  - `task`: the run that made it — matched from tasks of that project whose recorded commit list / git facts contain
    the SHA (reuse what handoffs record: `own_shas` / commits between head-at-start and head-at-end); null when
    unknown.
  - Commit titles and names are plain text (escaped by React); never HTML.
- A project tab "Activity": days picker (7/30/90), a small bar chart of commits per day, a "Only bigger changes"
  switch, and the list grouped by day; each commit shows title, author, "+12 −3 in 4 files", and "Made by task <title>"
  linking to it. Empty: "No commits in this period." Not a git repo: "This project's folder isn't a git repository."

## Rulings

- P3 council mode and P7 secrets by name are parked: forge never built either (design notes only); council is a
  costly multi-model feature better decided by the owner; P7 needs a CLI-level spike and carries the most risk.
- Standard library only for host readings (no psutil); estimates are labelled "about".

## Not covered / to know

- The per-run memory figure is an estimate. Container limits are read when present (cgroup v2 only).
- Not run against the real `claude` CLI (not needed here).
