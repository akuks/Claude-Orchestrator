"""Project-scoped access: visibility and role resolution.

Not visible -> 404 (never reveal existence); visible but role too low -> 403.
Effective role = highest of the direct grant and every team grant. Admins are
implicit maintainers; inactive users have nothing. Agent-token principals act
with their owner's rights (`principal.user`). Functions never commit.
"""
from fastapi import HTTPException
from sqlalchemy import and_, false, or_, select, true

from .auth import Principal
from .models import ProjectMember, Task, TeamMember

ALL = object()  # sentinel: every project is visible (admin)

_RANK = {"contributor": 1, "maintainer": 2}


def _grants(user_id: str):
    """Select (project_id, role) for every grant reaching this user."""
    direct = select(ProjectMember.project_id, ProjectMember.role).where(
        ProjectMember.subject_type == "user", ProjectMember.subject_id == user_id
    )
    via_team = (
        select(ProjectMember.project_id, ProjectMember.role)
        .join(TeamMember, TeamMember.team_id == ProjectMember.subject_id)
        .where(ProjectMember.subject_type == "team", TeamMember.user_id == user_id)
    )
    return direct.union_all(via_team)


def _best(roles) -> str | None:
    return max(roles, key=lambda r: _RANK.get(r, 0), default=None)


async def visible_project_ids(s, p: Principal):
    if not p.user.active:
        return set()
    if p.is_admin:
        return ALL
    rows = await s.execute(select(_grants(p.user.id).subquery().c.project_id))
    return set(rows.scalars())


async def maintained_project_ids(s, p: Principal):
    """Projects where the principal is maintainer (ALL for an active admin)."""
    if not p.user.active:
        return set()
    if p.is_admin:
        return ALL
    sub = _grants(p.user.id).subquery()
    rows = await s.execute(select(sub.c.project_id).where(sub.c.role == "maintainer"))
    return set(rows.scalars())


async def project_role(s, p: Principal, project_id: str) -> str | None:
    if not p.user.active:
        return None
    if p.is_admin:
        return "maintainer"
    sub = _grants(p.user.id).subquery()
    rows = await s.execute(select(sub.c.role).where(sub.c.project_id == project_id))
    return _best(rows.scalars())


async def require_project_role(
    s, p: Principal, project_id: str | None, min_role: str = "contributor"
) -> str:
    if project_id is None:
        if not p.user.active:
            raise HTTPException(404, "Not found")
        if p.is_admin:
            return "maintainer"
        if min_role == "maintainer":
            raise HTTPException(403, "Insufficient role")
        return "contributor"
    role = await project_role(s, p, project_id)
    if role is None:
        raise HTTPException(404, "Not found")
    if _RANK.get(role, 0) < _RANK.get(min_role, 0):
        raise HTTPException(403, "Insufficient role")
    return role


async def task_visibility_clause(s, p: Principal):
    if not p.user.active:
        return false()
    visible = await visible_project_ids(s, p)
    if visible is ALL:
        return true()
    me = p.user.id
    # A root is visible by its own rule: in a visible project, or an ad-hoc root I created.
    root_ok = or_(
        Task.project_id.in_(visible),
        and_(Task.project_id.is_(None), Task.created_by == me),
    )
    root_ids = select(Task.id).where(root_ok)
    return or_(
        Task.project_id.in_(visible),
        and_(Task.project_id.is_(None), or_(Task.created_by == me, Task.root_id.in_(root_ids))),
    )


async def can_see_task(s, p: Principal, task: Task) -> bool:
    if not p.user.active:
        return False
    visible = await visible_project_ids(s, p)
    if visible is ALL:
        return True
    if task.project_id is not None:
        return task.project_id in visible
    if task.created_by == p.user.id:
        return True
    if not task.root_id or task.root_id == task.id:
        return False
    root = (
        await s.execute(select(Task.project_id, Task.created_by).where(Task.id == task.root_id))
    ).first()
    if root is None:
        return False
    if root.project_id is not None:
        return root.project_id in visible
    return root.created_by == p.user.id


async def require_task(s, p: Principal, task_id: str) -> Task:
    task = await s.get(Task, task_id)
    if task is None or not await can_see_task(s, p, task):
        raise HTTPException(404, "Task not found")
    return task


async def effective_project_id(s, task: Task) -> str | None:
    """The task's project, or for an ad-hoc task its thread root's project."""
    if task.project_id is not None:
        return task.project_id
    if not task.root_id or task.root_id == task.id:
        return None
    return (
        await s.execute(select(Task.project_id).where(Task.id == task.root_id))
    ).scalar_one_or_none()


async def task_role(s, p: Principal, task: Task) -> str | None:
    if task.project_id is not None:
        return await project_role(s, p, task.project_id)
    if not p.user.active:
        return None
    if p.is_admin or task.created_by == p.user.id:
        return "maintainer"
    # An ad-hoc step in a project thread takes the viewer's role on that project.
    pid = await effective_project_id(s, task)
    if pid is not None:
        return await project_role(s, p, pid)
    return "contributor" if await can_see_task(s, p, task) else None
