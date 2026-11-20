import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

from app import access, auth
from app.database import SessionLocal, init_db
from app.models import ApiToken, Project, ProjectMember, Task, Team, TeamMember, User, _now
from datetime import timedelta


@pytest.fixture(scope="module", autouse=True)
def _db():
    asyncio.run(init_db())
    yield

    # Leave the shared test DB empty: the admin bootstrap only seeds when no users exist.
    async def clean():
        async with SessionLocal() as s:
            for m in (ApiToken, ProjectMember, TeamMember, Team, Task, Project, User):
                await s.execute(delete(m))
            await s.commit()

    asyncio.run(clean())


def run(coro_fn):
    async def go():
        async with SessionLocal() as s:
            r = await coro_fn(s)
            await s.commit()
            return r

    return asyncio.run(go())


_n = 0


def _uniq() -> str:
    global _n
    _n += 1
    return f"{_n}-{id(object())}"


async def _user(s, role="member", active=True):
    u = User(email=f"u{_uniq()}@t.local", name="T", password_hash="x", role=role, active=active)
    s.add(u)
    await s.flush()
    return u


async def _project(s):
    p = Project(name=f"p{_uniq()}", slug=f"p{_uniq()}", directory="/x")
    s.add(p)
    await s.flush()
    return p


async def _grant(s, project, subject_type, subject_id, role):
    s.add(ProjectMember(project_id=project.id, subject_type=subject_type, subject_id=subject_id, role=role))
    await s.flush()


def test_admin_sees_all():
    async def f(s):
        a = await _user(s, role="admin")
        p = await _project(s)
        P = auth.Principal(user=a)
        assert await access.visible_project_ids(s, P) is access.ALL
        assert await access.project_role(s, P, p.id) == "maintainer"

    run(f)


def test_no_grant_no_role():
    async def f(s):
        m = await _user(s)
        p = await _project(s)
        P = auth.Principal(user=m)
        assert await access.project_role(s, P, p.id) is None
        with pytest.raises(HTTPException) as e:
            await access.require_project_role(s, P, p.id)
        assert e.value.status_code == 404

    run(f)


def test_direct_contributor():
    async def f(s):
        m = await _user(s)
        p = await _project(s)
        await _grant(s, p, "user", m.id, "contributor")
        P = auth.Principal(user=m)
        assert await access.project_role(s, P, p.id) == "contributor"
        assert await access.require_project_role(s, P, p.id) == "contributor"
        with pytest.raises(HTTPException) as e:
            await access.require_project_role(s, P, p.id, "maintainer")
        assert e.value.status_code == 403

    run(f)


def test_no_project_rules():
    async def f(s):
        m = await _user(s)
        a = await _user(s, role="admin")
        assert await access.require_project_role(s, auth.Principal(user=a), None, "maintainer") == "maintainer"
        assert await access.require_project_role(s, auth.Principal(user=m), None) == "contributor"
        with pytest.raises(HTTPException) as e:
            await access.require_project_role(s, auth.Principal(user=m), None, "maintainer")
        assert e.value.status_code == 403

    run(f)


def test_highest_of_team_and_direct():
    async def f(s):
        m = await _user(s)
        p = await _project(s)
        ta, tb = Team(name=f"a{_uniq()}"), Team(name=f"b{_uniq()}")
        s.add_all([ta, tb])
        await s.flush()
        s.add_all([TeamMember(team_id=ta.id, user_id=m.id), TeamMember(team_id=tb.id, user_id=m.id)])
        await _grant(s, p, "user", m.id, "contributor")
        await _grant(s, p, "team", ta.id, "contributor")
        await _grant(s, p, "team", tb.id, "maintainer")
        P = auth.Principal(user=m)
        assert await access.project_role(s, P, p.id) == "maintainer"
        assert await access.visible_project_ids(s, P) == {p.id}
        tm = (await s.execute(select(TeamMember).where(TeamMember.team_id == tb.id))).scalar_one()
        await s.delete(tm)
        await s.flush()
        assert await access.project_role(s, P, p.id) == "contributor"

    run(f)


def test_inactive_user_has_nothing():
    async def f(s):
        m = await _user(s, active=False)
        p = await _project(s)
        await _grant(s, p, "user", m.id, "maintainer")
        P = auth.Principal(user=m)
        assert await access.visible_project_ids(s, P) == set()
        assert await access.project_role(s, P, p.id) is None

    run(f)


def test_agent_principal_uses_owner():
    async def f(s):
        m = await _user(s)
        p = await _project(s)
        await _grant(s, p, "user", m.id, "contributor")
        tok = ApiToken(
            token_hash=_uniq(), user_id=m.id, purpose="agent-task", task_id="x",
            expires_at=_now() + timedelta(hours=1),
        )
        s.add(tok)
        await s.flush()
        P = auth.Principal(user=m, token=tok)
        assert P.is_agent
        assert await access.project_role(s, P, p.id) == "contributor"

    run(f)


def test_task_visibility_clause():
    async def f(s):
        m = await _user(s)
        other = await _user(s)
        vis, hid = await _project(s), await _project(s)
        await _grant(s, vis, "user", m.id, "contributor")

        async def task(name, project=None, by=None, root=None):
            t = Task(title=name, prompt="x", project_id=project.id if project else None, created_by=by)
            s.add(t)
            await s.flush()
            t.root_id = root or t.id
            await s.flush()
            return t

        in_vis = await task("in_vis", vis, other.id)
        in_hid = await task("in_hid", hid, other.id)
        own = await task("own", None, m.id)
        theirs = await task("theirs", None, other.id)
        child = await task("child", None, other.id, root=in_vis.id)
        expect = {in_vis.id: True, in_hid.id: False, own.id: True, theirs.id: False, child.id: True}

        P = auth.Principal(user=m)
        clause = await access.task_visibility_clause(s, P)
        ids = set((await s.execute(select(Task.id).where(clause))).scalars())
        for t in (in_vis, in_hid, own, theirs, child):
            assert (t.id in ids) == expect[t.id], t.title
            assert await access.can_see_task(s, P, t) == expect[t.id], t.title
        assert (await access.require_task(s, P, own.id)).id == own.id
        with pytest.raises(HTTPException) as e:
            await access.require_task(s, P, theirs.id)
        assert e.value.status_code == 404
        with pytest.raises(HTTPException):
            await access.require_task(s, P, "nope")
        assert await access.task_role(s, P, own) == "maintainer"
        assert await access.task_role(s, P, child) == "contributor"
        assert await access.task_role(s, P, in_vis) == "contributor"
        assert await access.task_role(s, P, theirs) is None

        A = auth.Principal(user=await _user(s, role="admin"))
        assert await access.can_see_task(s, A, theirs) is True
        assert await access.task_role(s, A, theirs) == "maintainer"

    run(f)
