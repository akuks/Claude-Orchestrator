"""co-guard policy inputs for one task run (Phase 2, Task 5).

``gather(s, *, task, project, owner_id, mcp)`` compiles what the server
stores into the new keys of the task's policy file (``rules`` module
docstring) and says what the worker must add to the ``claude`` command:

* ``policy`` - ``trust``, ``quarantine``, ``on_no_answer``,
  ``readonly_paths``, ``custom_rules``, ``disabled_rules``, ``db_dev``,
  ``mcp_servers``, ``mcp_defaults``, ``grants`` and ``regex_budget_ms``. The
  lists are always lists (Rulings P10/P20: a null fails closed in the hook).
  ``trust`` other than ``own`` means ``external`` and ``on_no_answer`` other
  than ``deny_and_resume`` means ``deny``.
* ``add_dirs`` - the reference projects' folders, for ``--add-dir``.
* ``data_line`` - ``co_hook.DATA_LINE`` for an external project (the worker
  appends it to ``--append-system-prompt``), else None.
* ``events`` - ``guard`` events about what was left out (a reference project
  the owner cannot see or whose folder is gone, a malformed stored readonly
  entry) and the repo rules file's reported lines (``rules_file``).

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

from ..access import project_role
from ..auth import Principal
from ..models import Project, User
from . import custom, policy_store
from .co_hook import DATA_LINE

TRUST = ("own", "external")
ON_NO_ANSWER = ("deny", "deny_and_resume")


@dataclass
class PolicyInputs:
    policy: dict
    add_dirs: list[str] = field(default_factory=list)
    data_line: str | None = None
    events: list[dict] = field(default_factory=list)


def _epoch(dt: datetime) -> float:
    if dt.tzinfo is None:  # SQLite: naive UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return float(dt.timestamp())


def _within(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip("/") + "/")


def _readonly(project, events: list[dict]) -> list[dict]:
    out: list[dict] = []
    stored = getattr(project, "readonly_paths", None) if project is not None else None
    base = getattr(project, "directory", None) if project is not None else None
    for e in stored if isinstance(stored, list) else []:
        path = e.get("path") if isinstance(e, dict) else None
        if not isinstance(path, str) or not path.strip() or "\0" in path or path.startswith("~"):
            events.append({"event": "readonly_skipped", "reason": "a stored read-only entry is malformed"})
            continue
        path = path.strip()
        if not os.path.isabs(path):
            if not base:
                events.append({"event": "readonly_skipped", "path": path,
                               "reason": "a relative read-only path needs the project folder"})
                continue
            path = os.path.join(base, path)
        reason = e.get("reason") if isinstance(e.get("reason"), str) else ""
        out.append({"path": os.path.realpath(path), "reason": custom.clean_reason(reason), "source": "project"})
    return out


async def _references(s, project, owner_id: str | None, events: list[dict]) -> list[str]:
    ids = getattr(project, "reference_project_ids", None) if project is not None else None
    ids = [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else []
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
            events.append({"event": "reference_skipped", "project_id": rid, "name": ref.name,
                           "reason": "the reference project's folder does not exist"})
            continue
        if own_dir and _within(own_dir, real):
            events.append({"event": "reference_skipped", "project_id": rid, "name": ref.name,
                           "reason": "the reference project's folder holds this project"})
            continue
        if real not in dirs:
            dirs.append(real)
            events.append({"event": "reference", "project_id": rid, "name": ref.name, "path": real})
    return dirs


async def gather(s, *, task, project, owner_id: str | None, mcp: dict | None) -> PolicyInputs:
    events: list[dict] = []
    if project is None:
        trust, quarantine, on_no_answer = "own", False, "deny"  # an ad-hoc sandbox: our own workspace
    else:
        trust = project.trust if getattr(project, "trust", None) in TRUST else "external"
        quarantine = bool(getattr(project, "quarantine", False))
        on_no_answer = project.on_no_answer if getattr(project, "on_no_answer", None) in ON_NO_ANSWER else "deny"

    readonly = _readonly(project, events)
    refs = await _references(s, project, owner_id, events)
    names = {e["path"]: e.get("name") for e in events if e.get("event") == "reference"}
    for d in refs:
        readonly.append({"path": d, "reason": custom.clean_reason(f"reference project {names.get(d) or ''}".strip()),
                         "source": "reference"})

    rules = list(await policy_store.rules_for(s, getattr(project, "id", None) if project is not None else None))
    if trust == "own" and project is not None and getattr(project, "directory", None):
        repo = await asyncio.to_thread(policy_store.read_repo_rules, project.directory)
        rules += list(repo.get("confirm") or []) + list(repo.get("never") or [])
        if repo.get("errors"):
            events.append({"event": "rules_file", "path": repo.get("path"), "errors": list(repo["errors"])})

    org = await policy_store.org_settings(s)
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
    }
    return PolicyInputs(policy=policy, add_dirs=refs, data_line=DATA_LINE if trust == "external" else None,
                        events=events)
