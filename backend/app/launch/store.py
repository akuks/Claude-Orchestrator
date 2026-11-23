"""Launch-list reads and shared writes (phase 5b)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update

from ..constants import Status
from ..models import LaunchItem, Project, ProjectLaunch, Task


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def get_or_none(s, project_id: str) -> ProjectLaunch | None:
    return await s.get(ProjectLaunch, project_id)


async def launch_items_for(s, project_id: str) -> list[LaunchItem]:
    """All items: `before` first then `after`, each by number."""
    rows = (await s.execute(select(LaunchItem).where(LaunchItem.project_id == project_id))).scalars().all()
    return sorted(rows, key=lambda i: (i.section != "before", i.number))


async def next_number(s, project_id: str) -> int:
    """Increment the project's high-water mark in the caller's transaction (the UPDATE's row lock serialises
    writers), so a number is never reused."""
    n = (await s.execute(
        update(Project).where(Project.id == project_id)
        .values(launch_seq=Project.launch_seq + 1).returning(Project.launch_seq))).scalar_one_or_none()
    if n is None:
        raise ValueError("project not found")
    return n


async def all_linked_done(s, project_id: str) -> dict[int, bool]:
    """{item number: True} when the item has linked tasks and every one is completed."""
    rows = (await s.execute(
        select(LaunchItem.number, Task.status).join(Task, Task.launch_item_id == LaunchItem.id)
        .where(LaunchItem.project_id == project_id))).all()
    out: dict[int, bool] = {}
    for number, status in rows:
        out[number] = out.get(number, True) and status == Status.COMPLETED
    return out


async def mark_started_for_task(s, task) -> None:
    """A linked task went running: an `open` item becomes `started` (stamped). No-op otherwise.
    Does not commit; the caller's transaction carries it."""
    item_id = getattr(task, "launch_item_id", None)
    if not item_id:
        return
    now = _now()
    await s.execute(update(LaunchItem).where(LaunchItem.id == item_id, LaunchItem.status == "open").values(
        status="started", started_at=now, version=LaunchItem.version + 1))


MATCH_MIN = 3


async def blocked_items(s, questions) -> dict[str, dict]:
    """``{question id: {number, title}}`` for the open questions whose ``question`` or ``blocks`` text names an
    open launch item (case-insensitive substring, title >= 3 chars) of the question's project. One query
    for all projects involved; the lowest item number wins."""
    pids = {q.project_id for q in questions if q.project_id and q.status == "open"}
    if not pids:
        return {}
    rows = (await s.execute(select(LaunchItem).where(LaunchItem.project_id.in_(pids), LaunchItem.status == "open")
                            .order_by(LaunchItem.number))).scalars().all()
    by_project: dict[str, list] = {}
    for i in rows:
        title = i.title.strip()
        if len(title) >= MATCH_MIN:
            by_project.setdefault(i.project_id, []).append((i.number, title, title.casefold()))
    out: dict[str, dict] = {}
    for q in questions:
        if q.status != "open" or q.project_id not in by_project:
            continue
        text = f"{q.question or ''}\n{q.blocks or ''}".casefold()
        for number, title, key in by_project[q.project_id]:
            if key in text:
                out[q.id] = {"number": number, "title": title}
                break
    return out
