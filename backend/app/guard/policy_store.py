"""co-guard policy inputs stored by the server (Phase 2).

What the worker compiles into a task's policy file (Task 5) and what the
settings APIs show:

* ``org_settings(s)`` - ``{"disabled_rules": [str], "dev_db_patterns": [str], "ground_rules": {...}}``
  from the ``GuardOrgSettings`` row (always lists; none stored -> empty; ``ground_rules`` is
  ``ground_rules.DEFAULTS`` updated by what is stored).
* ``rules_for(s, project_id)`` - the enabled org rules, then the enabled rules
  of that project, in the policy ``custom_rules`` shape ``{id, kind, match,
  pattern, reason}`` (``id`` is ``org.<slug>`` / ``project.<slug>``).
* ``read_repo_rules(directory)`` - the project's
  ``.claude-orchestrator/rules.md`` parsed add-only by ``custom.parse_rules_md``:
  ``{"path", "present", "confirm", "never", "errors"}``; each rule is policy
  shaped with a ``repo.<slug>`` id (slug from its reason, else its pattern,
  unique within the file) plus its ``line``. Only a regular file whose real
  path stays inside the project is read (a link out of the project, a FIFO or
  a folder is reported, line 0); at most ``REPO_FILE_MAX`` bytes are read.
  The caller decides whether the project is trusted enough to read it.
* ``active_grants(s, *, root_id, task_id, now)`` - the thread's live run
  grants and this task's unused once grants (not revoked, not expired).
"""

from __future__ import annotations

import os
import stat
from datetime import datetime
from pathlib import Path

from sqlalchemy import and_, case, or_, select

from ..models import ActionGrant, ActionRule, GuardOrgSettings
from ..resume import ground_rules
from . import custom

RULES_FILE = Path(".claude-orchestrator") / "rules.md"


def _str_list(value) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


async def org_settings(s) -> dict:
    row = await s.get(GuardOrgSettings, 1)
    if row is None:
        return {"disabled_rules": [], "dev_db_patterns": [], "ground_rules": ground_rules.normalized(None)}
    return {"disabled_rules": _str_list(row.disabled_rules), "dev_db_patterns": _str_list(row.dev_db_patterns),
            "ground_rules": ground_rules.normalized(row.ground_rules)}


def rule_entry(r: ActionRule) -> dict:
    return {"id": custom.rule_id(r.scope, r.slug), "kind": r.kind, "match": r.match, "pattern": r.pattern,
            "reason": r.reason or ""}


async def rules_for(s, project_id: str | None) -> list[dict]:
    where = ActionRule.scope == "org"
    if project_id is not None:
        where = or_(where, and_(ActionRule.scope == "project", ActionRule.project_id == project_id))
    rows = (
        await s.execute(
            select(ActionRule)
            .where(ActionRule.enabled.is_(True), where)
            .order_by(case((ActionRule.scope == "org", 0), else_=1), ActionRule.created_at, ActionRule.slug)
        )
    ).scalars().all()
    return [rule_entry(r) for r in rows]


def _read_limited(path: Path, root: Path) -> tuple[str | None, str | None]:
    """(text, None) or (None, why it was not read)."""
    try:
        real = Path(os.path.realpath(path))
        if real != root and root not in real.parents:
            return None, "the rules file links outside the project, so it is not read"
        fd = os.open(real, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except OSError as e:
        return None, f"the rules file could not be read: {e.strerror or e}"
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None, "the rules file is not a regular file, so it is not read"
        chunks, size = [], 0
        while size <= custom.REPO_FILE_MAX:
            b = os.read(fd, 65536)
            if not b:
                break
            chunks.append(b)
            size += len(b)
    except OSError as e:
        return None, f"the rules file could not be read: {e.strerror or e}"
    finally:
        os.close(fd)
    data = b"".join(chunks)
    if len(data) > custom.REPO_FILE_MAX + 3:  # + a UTF-8 BOM, which parse_rules_md ignores
        return None, f"the rules file is larger than {custom.REPO_FILE_MAX} bytes, so none of it is used"
    return data.decode("utf-8", errors="replace"), None


def read_repo_rules(directory) -> dict:
    root = Path(os.path.realpath(directory))
    path = Path(directory) / RULES_FILE
    out = {"path": str(path), "present": os.path.lexists(path), "confirm": [], "never": [], "errors": []}
    if not out["present"]:
        return out
    text, why = _read_limited(path, root)
    if why is not None:
        out["errors"].append({"line": 0, "message": why})
        return out
    parsed = custom.parse_rules_md(text)
    taken: set[str] = set()
    for kind in custom.KINDS:
        for r in parsed[kind]:
            slug = custom.unique_slug(r["reason"] or r["pattern"], taken)
            taken.add(slug)
            out[kind].append({"id": custom.rule_id("repo", slug), "kind": kind, "match": r["match"],
                              "pattern": r["pattern"], "reason": r["reason"], "line": r["line"]})
    out["errors"] = list(parsed["errors"])
    return out


async def active_grants(s, *, root_id: str, task_id: str | None, now: datetime) -> list[ActionGrant]:
    live = and_(ActionGrant.root_id == root_id, ActionGrant.revoked_at.is_(None), ActionGrant.expires_at > now)
    kinds = ActionGrant.scope == "run"
    if task_id is not None:
        kinds = or_(kinds, and_(ActionGrant.scope == "once", ActionGrant.task_id == task_id,
                                ActionGrant.used_at.is_(None)))
    rows = await s.execute(select(ActionGrant).where(live, kinds).order_by(ActionGrant.created_at))
    return list(rows.scalars().all())
