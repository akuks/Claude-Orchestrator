"""Home "Open handoffs" (phase 3b, forge's ``/status``): threads the caller started or may manage whose
latest main-line step ended in the last 14 days, is not running, and whose handoff is open (not closed) with
an open todo or a non-completed status. At most 10, newest first. People only (never on the agent token's
allowlist).

"Open todo" is ``resume.handoff.open_todo``, the same test the automatic Continue uses."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from sqlalchemy import func, or_, select

from ..access import can_see_task, task_visibility_clause
from ..constants import Status
from ..database import SessionLocal
from ..models import Task, TaskHandoff
from ..resume.handoff import open_todo
from ..schemas import TaskOut
from .tasks import _manage_allowed, _viewer_flags

router = APIRouter(prefix="/handoffs", tags=["handoffs"])

WINDOW_DAYS = 14
LIMIT = 10
SCAN_MAX = 500  # candidate threads fetched per page (newest finished first)
SCAN_PAGES = 5  # pages looked at per call at most


def _utc(d: datetime | None) -> datetime | None:
    if d is None:
        return None
    return d if d.tzinfo is not None else d.replace(tzinfo=timezone.utc)


@router.get("/open")
async def open_handoffs(request: Request):
    p = request.state.principal
    cutoff = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    thread_of = func.coalesce(Task.root_id, Task.id)
    async with SessionLocal() as s:
        # threads with a step that ended in the window, newest first
        recent = (select(thread_of.label("root"), func.max(Task.completed_at).label("ended"))
                  .where(Task.completed_at >= cutoff).group_by(thread_of).subquery())
        out = []
        for page in range(SCAN_PAGES):
            rows = (await s.execute(
                select(TaskHandoff, Task)
                .join(recent, recent.c.root == TaskHandoff.root_id)
                .join(Task, Task.id == TaskHandoff.root_id)
                .where(TaskHandoff.status == "open", await task_visibility_clause(s, p))
                .order_by(recent.c.ended.desc(), TaskHandoff.root_id)
                .limit(SCAN_MAX).offset(page * SCAN_MAX))).all()
            if not rows:
                break
            roots = [h.root_id for h, _ in rows]
            steps: dict[str, list[Task]] = {}
            for t in (await s.execute(select(Task).where(thread_of.in_(roots))
                                      .order_by(Task.created_at.desc()))).scalars():
                steps.setdefault(t.root_id or t.id, []).append(t)

            found = []
            for h, root in rows:
                thread = steps.get(h.root_id, [])
                if any(t.status in Status.ACTIVE for t in thread):
                    continue  # a step of the thread is queued, running or waiting
                latest = next((t for t in thread if t.origin != "agent"), None)
                if latest is None or latest.status not in Status.TERMINAL:
                    continue
                ended = _utc(latest.completed_at)
                if ended is None or ended < cutoff:
                    continue
                todo = open_todo(h.content, latest.id)
                if not todo and latest.status == Status.COMPLETED:
                    continue
                found.append((ended, h, root, latest, todo))
            found.sort(key=lambda r: r[0], reverse=True)

            for ended, h, root, latest, todo in found:
                if not await can_see_task(s, p, latest):
                    continue
                started = root.created_by is not None and root.created_by == p.user.id
                if not started and not (await _manage_allowed(s, p, latest, need_contributor=True))[0]:
                    continue
                step = (h.content or {}).get("step")
                step = ({"n": step.get("n"), "m": step.get("m"), "text": step.get("text") or ""}
                        if isinstance(step, dict) and step.get("n") is not None and step.get("m") else None)
                out.append({
                    "root_id": h.root_id,
                    "title": root.title,
                    "task": await _viewer_flags(s, p, TaskOut.model_validate(latest), latest),
                    "step": step,
                    "open_todo": todo,
                    "ended_at": ended.isoformat(),
                })
                if len(out) >= LIMIT:
                    break
            if len(out) >= LIMIT or len(rows) < SCAN_MAX:
                break
        return out
