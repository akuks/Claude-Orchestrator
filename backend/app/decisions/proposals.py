"""Suggested decisions: cleaning, cap, dedupe and insert (phase 4b).
The one write path for every proposal source. Nothing here commits."""

from __future__ import annotations

from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..config import settings
from ..models import DecisionProposal
from ..redaction import clean_text, redact
from . import validate

PROPOSAL_KINDS = ("decision", "rule", "lesson")
SOURCES = ("run", "lesson", "answer", "agent", "person")
AGENT_PER_TASK = 3
LIST_MAX = 100
MAX_EVIDENCE = 300


class QueueFull(Exception):
    pass


def valid_touches(items) -> list[str]:
    """Clean each entry like a decision's touches; invalid entries are dropped (not the item)."""
    if not isinstance(items, (list, tuple)):
        return []
    out: list[str] = []
    for item in items:
        if not isinstance(item, str):
            continue
        try:
            item = redact(clean_text(item), []).strip()
            if len(item) > validate.MAX_TOUCH:
                continue
            norm = validate._rel_path(item, "a touches entry")
        except ValueError:
            continue
        if norm not in out:
            out.append(norm)
        if len(out) >= validate.MAX_TOUCHES:
            break
    return out


def clean_proposal(data: dict, *, strict_touches: bool) -> dict:
    """title, why, kind, touches cleaned. `see` and `source` are never taken. Raises ValueError."""
    kind = data.get("kind", "decision")
    if kind is None:
        kind = "decision"
    if kind not in PROPOSAL_KINDS:
        raise ValueError(f"kind must be one of: {', '.join(PROPOSAL_KINDS)}")
    raw = {"title": data.get("title"), "why": data.get("why") or "", "kind": kind}
    if raw["title"] is None:
        raise ValueError("title is required")
    touches = data.get("touches") or []
    if strict_touches:
        raw["touches"] = touches
    out = validate.clean_fields(raw, partial=False)
    if not strict_touches:
        out["touches"] = valid_touches(touches)
    return {k: out[k] for k in ("title", "why", "kind", "touches")}


def clean_evidence(text: str) -> str:
    if not isinstance(text, str):
        return ""
    t = validate.one_line(redact(clean_text(validate.one_line(text)), []))
    return t[:MAX_EVIDENCE]


async def pending_count(s, project_id: str) -> int:
    return (await s.execute(
        select(func.count()).select_from(DecisionProposal)
        .where(DecisionProposal.project_id == project_id, DecisionProposal.status == "pending"))).scalar_one()


async def key_used(s, project_id: str, prefix: str) -> bool:
    esc = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    row = (await s.execute(
        select(DecisionProposal.id)
        .where(DecisionProposal.project_id == project_id, DecisionProposal.dedupe_key.like(esc + "%", escape="\\"))
        .limit(1))).first()
    return row is not None


async def add(s, *, project_id: str, fields: dict, source: str, task_id: str | None = None, evidence: str = "",
              dedupe_key: str | None = None, proposed_by: str | None = None,
              on_full: Literal["raise", "drop"]) -> DecisionProposal | None:
    """Insert a pending proposal from already-cleaned fields. Over the cap: QueueFull or None.
    A repeated dedupe key: None. Does not commit."""
    if await pending_count(s, project_id) >= settings.proposals_pending_cap:
        if on_full == "raise":
            raise QueueFull()
        return None
    row = DecisionProposal(
        project_id=project_id, task_id=task_id, source=source, kind=fields.get("kind", "decision"),
        title=fields["title"], why=fields.get("why", ""), touches=list(fields.get("touches", [])),
        evidence=clean_evidence(evidence), dedupe_key=dedupe_key, proposed_by=proposed_by)
    try:
        async with s.begin_nested():
            s.add(row)
            await s.flush()
    except IntegrityError:
        return None
    return row
