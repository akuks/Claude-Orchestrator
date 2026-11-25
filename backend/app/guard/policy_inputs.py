"""co-guard policy inputs for one task run (Phase 2, Task 5).

``gather(s, *, task, project, owner_id, mcp)`` compiles what the server
stores into the new keys of the task's policy file (``rules`` module
docstring) and says what the worker must add to the ``claude`` command:

* ``policy`` - ``trust``, ``quarantine``, ``on_no_answer``,
  ``readonly_paths``, ``custom_rules``, ``disabled_rules``, ``db_dev``,
  ``mcp_servers``, ``mcp_defaults``, ``grants`` and ``regex_budget_ms``; ``decisions`` (the project's active
  decisions, ``decisions.snapshot``) and ``decisions_index`` only when trust is ``own``, the ground-rules
  switch ``decisions`` is on and the project has active decisions (phase 4a). The
  lists are always lists (Rulings P10/P20: a null fails closed in the hook).
  ``trust`` other than ``own`` means ``external`` and ``on_no_answer`` other
  than ``deny_and_resume`` means ``deny``.
  ``code_map`` / ``code_map_refs`` (phase 6b) only for a switched-on project with an ``ok`` index and a
  registered code map server; with them (phase 7d) ``code_map.fresh`` (how fresh the map is, worked out here at
  task start with the safe git helper, bounded in time), each reference's ``changed`` and
  ``code_map_unmapped`` (the names of the references without a usable map).
* ``add_dirs`` - the reference projects' folders, for ``--add-dir``.
* ``decisions_index`` - the same index, for the worker to append after the ground rules, or None.
* ``data_line`` - ``co_hook.DATA_LINE`` for an external project (the worker
  appends it to ``--append-system-prompt``), else None.
* ``brief_text`` / ``brief_layers`` - the task's brief (phase 4c) as system-prompt text and as scope layers
  for ``briefs.scope.apply``; None / [] without a brief.
* ``events`` - ``guard`` events about what was left out (a reference project
  the owner cannot see or whose folder is gone, by id only; a relative
  readonly path with no project folder), how many references were mounted
  (``references``: a count only - task viewers may not see those projects,
  so no name or path) and the repo rules file's reported lines
  (``rules_file``).

``PolicyInputError`` (Ruling P22, fail closed): a stored ``readonly_paths``
that is not a list or has a malformed entry, or ``reference_project_ids``
that is not a list of ids - dropping either would fail open, so the task
does not start.

``project`` is the task's project - for an ad-hoc step of a project thread,
the thread root's project (``access.effective_project_id``) - or None. Its
readonly paths are resolved against ITS directory (also for ad-hoc steps,
which run in their own workspace) to absolute real paths, with ``source``
``project``. Reference projects are kept only when the task's owner can see
them (``access.project_role``) and their folder exists; each becomes a
readonly root with ``source`` ``reference`` and an ``--add-dir``. The repo
rules file is read only when trust is ``own`` (an external repo's file is
never opened). ``grants`` are the thread's live grants
(``policy_store.active_grants``) with ``expires_at`` in epoch seconds.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select

from ..access import project_role
from ..briefs import render, scope
from ..auth import Principal
from ..decisions import snapshot as decisions_snapshot
from ..decisions.texts import SUGGEST_LINE
from ..models import McpServer, Project, ProjectIndex, User
from ..resume import ground_rules
from . import custom, policy_store
from .co_hook import DATA_LINE, NOTES_AFTER_ACTS

TRUST = ("own", "external")
ON_NO_ANSWER = ("deny", "deny_and_resume")


@dataclass
class PolicyInputs:
    policy: dict
    add_dirs: list[str] = field(default_factory=list)
    data_line: str | None = None
    events: list[dict] = field(default_factory=list)
    main_text: str = ""  # the enabled ground rules, for the main session's --append-system-prompt
    ground_included: list[str] = field(default_factory=list)  # which rules those are
    decisions_index: str | None = None  # the project's decision index (phase 4a), after the ground rules
    brief_text: str | None = None  # the task's brief for the system prompt (phase 4c), None without one
    brief_layers: list = field(default_factory=list)  # its scope layers for the policy (briefs.scope.layers)


def _epoch(dt: datetime) -> float:
    if dt.tzinfo is None:  # SQLite: naive UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return float(dt.timestamp())


def _within(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip("/") + "/")


class PolicyInputError(ValueError):
    """Stored policy that cannot be compiled safely: the task must not start."""


def _readonly(project, events: list[dict]) -> list[dict]:
    out: list[dict] = []
    stored = getattr(project, "readonly_paths", None) if project is not None else None
    base = getattr(project, "directory", None) if project is not None else None
    if stored is not None and not isinstance(stored, list):
        # Ruling P22: dropping a read-only root would fail open
        raise PolicyInputError("the project's read-only paths are malformed")
    for e in stored or []:
        path = e.get("path") if isinstance(e, dict) else None
        reason = e.get("reason") if isinstance(e, dict) else None
        if not isinstance(path, str) or not path.strip() or "\0" in path or path.startswith("~") \
                or (reason is not None and not isinstance(reason, str)):
            raise PolicyInputError("a stored read-only path of the project is malformed")
        path = path.strip()
        if not os.path.isabs(path):
            if not base:
                events.append({"event": "readonly_skipped", "path": path,
                               "reason": "a relative read-only path needs the project folder"})
                continue
            path = os.path.join(base, path)
        reason = reason or ""
        out.append({"path": os.path.realpath(path), "reason": custom.clean_reason(reason), "source": "project"})
    return out


async def _references(s, project, owner_id: str | None, events: list[dict],
                      kept: list | None = None) -> list[str]:
    """The mounted reference folders (real paths); ``kept`` (when given) gets (real path, reference project)."""
    ids = getattr(project, "reference_project_ids", None) if project is not None else None
    if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)):
        raise PolicyInputError("the project's reference projects are malformed")  # P22: never fail open
    ids = ids or []
    if not ids:
        return []
    owner = await s.get(User, owner_id) if owner_id else None
    principal = Principal(owner) if owner is not None else None
    own_dir = os.path.realpath(project.directory) if getattr(project, "directory", None) else None
    dirs: list[str] = []
    for rid in dict.fromkeys(ids):
        ref = await s.get(Project, rid)
        # never name a project the owner cannot see
        if ref is None or principal is None or rid == getattr(project, "id", None) \
                or await project_role(s, principal, rid) is None:
            events.append({"event": "reference_skipped", "project_id": rid,
                           "reason": "the task's owner cannot see this reference project"})
            continue
        real = os.path.realpath(ref.directory) if ref.directory else ""
        if not real or not os.path.isdir(real):
            events.append({"event": "reference_skipped", "project_id": rid,
                           "reason": "the reference project's folder does not exist"})
            continue
        if own_dir and _within(own_dir, real):
            events.append({"event": "reference_skipped", "project_id": rid,
                           "reason": "the reference project's folder holds this project"})
            continue
        if real not in dirs:
            dirs.append(real)
            if kept is not None:
                kept.append((real, ref))
    if dirs:  # task viewers may not see the reference projects: a count only, no names or paths
        events.append({"event": "references", "count": len(dirs)})
    return dirs


def _iso(dt) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:  # SQLite: naive UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


async def _ok_index(s, project_id: str, *, need_sha: bool = True):
    """The project's `ok` index row, or None. A reference's needs a sha (the old-code line prints it); the
    project's own may have none (no commit to compare: the hook says "freshness unknown")."""
    row = await s.get(ProjectIndex, project_id)
    return row if row is not None and row.status == "ok" and (row.sha or not need_sha) else None


FRESH_WAIT = 3.0  # seconds gather waits for all the freshness checks (each is bounded by its own budget)


def _fresh_unknown() -> dict:
    return {"state": "unknown", "behind": None, "changed": [], "more": 0, "head": None}


async def _freshness(checks: list) -> list[dict]:
    """Phase 7d: ``freshness.freshness`` for each (directory, sha, files), side by side off the event loop; all
    of them together wait at most FRESH_WAIT seconds, and one that fails or is still running says unknown."""
    from ..codemap import freshness  # lazy, like the status module

    async def one(directory, sha, files):
        if not sha or not directory:
            return _fresh_unknown()
        return await asyncio.to_thread(freshness.freshness, directory, sha, files=files)

    tasks = [asyncio.ensure_future(one(*c)) for c in checks]
    if not tasks:
        return []
    await asyncio.wait(tasks, timeout=FRESH_WAIT)
    out = []
    for t in tasks:
        if t.done() and not t.cancelled() and t.exception() is None and isinstance(t.result(), dict):
            out.append(t.result())
        else:
            t.cancel()  # the thread itself ends within its own budget
            out.append(_fresh_unknown())
    return out


async def _code_map(s, project, ref_projects: list) -> dict:
    """Phase 6b: ``code_map`` {sha, indexed_at, prefix, fresh} and ``code_map_refs`` [{dir, sha, changed}] for
    co-guard's code map answers, only when the project is switched on, its index is ``ok`` (with or without a
    sha: none means freshness unknown) and a code map server is registered for it (an old project: nothing). A
    reference is listed only with its own ``ok`` index and a sha; the names of the others are in
    ``code_map_unmapped`` (phase 7d: the hook lists them once at session start). ``fresh`` is
    ``freshness.freshness`` of the project's folder (with the changed files); a reference's ``changed`` is true
    when its HEAD moved since its index (``ahead`` or ``different``)."""
    if project is None or getattr(project, "code_map", False) is not True:
        return {}
    row = await _ok_index(s, project.id, need_sha=False)
    if row is None:
        return {}
    from ..codemap import status as cm_status  # lazy: the code map package pulls in the job runner

    servers = (await s.execute(select(McpServer).where(McpServer.enabled.is_(True)))).scalars().all()
    server = cm_status.code_map_server(
        [m for m in servers if m.scope != "project" or m.project == project.name])
    if server is None:
        return {}
    refs, unmapped = [], []
    for real, ref in ref_projects:
        rrow = await _ok_index(s, ref.id) if getattr(ref, "code_map", False) is True else None
        if rrow is None:
            if ref.name not in unmapped:
                unmapped.append(ref.name)
            continue
        refs.append({"dir": real, "sha": rrow.sha})
    fresh = await _freshness([(getattr(project, "directory", None), row.sha, True)]
                             + [(r["dir"], r["sha"], False) for r in refs])
    for r, f in zip(refs, fresh[1:]):
        r["changed"] = f["state"] in ("ahead", "different")
    return {"code_map": {"sha": row.sha, "indexed_at": _iso(row.indexed_at), "prefix": cm_status.tool_prefix(server),
                         "fresh": fresh[0]},
            "code_map_refs": refs, "code_map_unmapped": unmapped}


async def _ui_skills(project) -> str | None:
    """Phase 6c: the design-skills paragraph for a project that has a user interface and the switch on; the folder
    is looked at off the event loop. Never raises (a failure means no paragraph)."""
    if project is None or getattr(project, "ui_skills", True) is not True or not getattr(project, "directory", None):
        return None
    from ..recommend import detect as ui_detect, panel as ui_panel  # lazy

    try:
        found = await asyncio.wait_for(asyncio.to_thread(ui_detect.detect, project.directory), ui_detect.TIMEOUT)
        return ui_panel.ui_skills_text(project, found)
    except Exception:
        return None


async def gather(s, *, task, project, owner_id: str | None, mcp: dict | None) -> PolicyInputs:
    events: list[dict] = []
    if project is None:
        trust, quarantine, on_no_answer = "own", False, "deny"  # an ad-hoc sandbox: our own workspace
    else:
        trust = project.trust if getattr(project, "trust", None) in TRUST else "external"
        quarantine = bool(getattr(project, "quarantine", False))
        on_no_answer = project.on_no_answer if getattr(project, "on_no_answer", None) in ON_NO_ANSWER else "deny"

    readonly = _readonly(project, events)
    ref_projects: list = []
    refs = await _references(s, project, owner_id, events, ref_projects)
    for d in refs:
        # the deny text shows this reason to task viewers: no project name
        readonly.append({"path": d, "reason": "a reference project", "source": "reference"})

    rules = list(await policy_store.rules_for(s, getattr(project, "id", None) if project is not None else None))
    if trust == "own" and project is not None and getattr(project, "directory", None):
        repo = await asyncio.to_thread(policy_store.read_repo_rules, project.directory)
        rules += list(repo.get("confirm") or []) + list(repo.get("never") or [])
        if repo.get("errors"):
            events.append({"event": "rules_file", "path": repo.get("path"), "errors": list(repo["errors"])})

    org = await policy_store.org_settings(s)
    ground = org["ground_rules"]
    root_id = getattr(task, "root_id", None) or task.id
    grants = await policy_store.active_grants(s, root_id=root_id, task_id=task.id, now=datetime.now(timezone.utc))
    mcp = mcp or {}
    servers = mcp.get("servers")
    defaults = mcp.get("tool_defaults")
    policy = {
        "trust": trust,
        "quarantine": quarantine,
        "on_no_answer": on_no_answer,
        "readonly_paths": readonly,
        "custom_rules": rules,
        "disabled_rules": list(org["disabled_rules"]),
        "db_dev": list(org["dev_db_patterns"]),
        "mcp_servers": dict(servers) if isinstance(servers, dict) else {},
        "mcp_defaults": dict(defaults) if isinstance(defaults, dict) else {},
        "grants": [{"id": g.id, "scope": g.scope, "rule_id": g.rule_id, "key_kind": g.key_kind, "key": g.key,
                    "expires_at": _epoch(g.expires_at)} for g in grants],
        "regex_budget_ms": custom.REGEX_BUDGET_MS,
        "notes": ground["notes"],
        "notes_after": NOTES_AFTER_ACTS,
        "helper_context": ground_rules.helper_text(ground, trust == "external"),
        "compact": True,
    }
    index = None
    if trust == "own" and project is not None and ground.get("decisions") is True:
        snap = await decisions_snapshot.snapshot(s, project)
        index = decisions_snapshot.index_text(snap)
        if index:
            index = index + "\n" + SUGGEST_LINE
        if snap and index:
            policy["decisions"] = snap
            policy["decisions_index"] = index
    policy.update(await _code_map(s, project, ref_projects))
    main_text, helper_text = ground_rules.main_text(ground), policy["helper_context"]
    ui_text = await _ui_skills(project)
    included = list(ground_rules.included(ground))
    if ui_text:  # phase 6c: after the ground rules, for the main session and for every helper
        main_text = "\n\n".join(filter(None, [main_text, ui_text]))
        policy["helper_context"] = "\n\n".join(filter(None, [helper_text, ui_text]))
        included.append("ui_skills")
    brief = getattr(task, "brief", None)  # JSON null reads as None: falsy is no brief
    brief_text = brief_layers = None
    if brief:
        bounds = getattr(task, "brief_bounds", None) or []
        if not isinstance(brief, dict) or not isinstance(bounds, list) \
                or not all(isinstance(b, dict) for b in bounds):
            raise PolicyInputError("the task's brief is malformed")  # never fail open
        brief_text = render.brief_text(brief, bounds)
        brief_layers = scope.layers(task.id, brief, bounds)
    return PolicyInputs(policy=policy, brief_text=brief_text, brief_layers=brief_layers or [], add_dirs=refs, data_line=DATA_LINE if trust == "external" else None,
                        events=events, main_text=main_text,
                        ground_included=included, decisions_index=index)
