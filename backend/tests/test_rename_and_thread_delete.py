"""Follow-ups to Phase 2: tasks resolve their project's CURRENT name at run time
(MCP servers are matched by name), thread deletion refuses while any step is
still running, and project names can't be blank."""

import asyncio
import uuid

from app.constants import Status
from app.database import SessionLocal
from app.models import Project, Task
from app.worker import project_name_for


def _tag():
    return uuid.uuid4().hex[:8]


def test_project_name_resolved_from_project_id(client):
    tag = _tag()
    p = client.post("/projects", json={"name": f"old-{tag}"}).json()
    assert client.patch(f"/projects/{p['id']}", json={"name": f"new-{tag}"}).status_code == 200

    async def go():
        async with SessionLocal() as s:
            # A task created before the rename still carries the old name string.
            t = Task(title="t", prompt="x", project=f"old-{tag}", project_id=p["id"])
            stale_adhoc = Task(title="t", prompt="x", project=f"old-{tag}", project_id=None)
            gone = Task(title="t", prompt="x", project="whatever", project_id="nope" * 8)
            return (
                await project_name_for(s, t),
                await project_name_for(s, stale_adhoc),
                await project_name_for(s, gone),
            )

    current, adhoc, missing = asyncio.run(go())
    assert current == f"new-{tag}"
    # No project link → no project-scoped servers, whatever the stale string says.
    assert adhoc is None
    assert missing is None


def test_freed_old_name_does_not_reach_old_tasks(client):
    """After A is renamed, a new project B may take A's old name. A's old tasks
    must resolve to A's new name, never to B."""
    tag = _tag()
    a = client.post("/projects", json={"name": f"shared-{tag}"}).json()
    client.patch(f"/projects/{a['id']}", json={"name": f"a-renamed-{tag}"})
    # The API keeps A's slug, so reuse the freed name with a different slug
    # directly (e.g. a project imported from another folder).

    async def go():
        async with SessionLocal() as s:
            s.add(Project(name=f"shared-{tag}", slug=f"other-{tag}", directory=f"/tmp/other-{tag}"))
            await s.commit()
            t = Task(title="t", prompt="x", project=f"shared-{tag}", project_id=a["id"])
            return await project_name_for(s, t)

    assert asyncio.run(go()) == f"a-renamed-{tag}"


def test_thread_delete_refused_while_a_step_is_active(client):
    tag = _tag()

    async def mk():
        async with SessionLocal() as s:
            root = Task(title=f"root-{tag}", prompt="x", status=Status.COMPLETED)
            s.add(root)
            await s.flush()
            root.root_id = root.id
            child = Task(title=f"child-{tag}", prompt="x", status=Status.RUNNING,
                         root_id=root.id, parent_task_id=root.id)
            s.add(child)
            await s.commit()
            return root.id, child.id

    root_id, child_id = asyncio.run(mk())
    r = client.delete(f"/tasks/{root_id}")
    assert r.status_code == 409, r.text
    assert client.get(f"/tasks/{child_id}").status_code == 200
    assert client.get(f"/tasks/{root_id}").status_code == 200

    async def finish():
        async with SessionLocal() as s:
            (await s.get(Task, child_id)).status = Status.COMPLETED
            await s.commit()

    asyncio.run(finish())
    assert client.delete(f"/tasks/{root_id}").status_code == 204
    assert client.get(f"/tasks/{child_id}").status_code == 404


def test_blank_project_name_rejected(client):
    p = client.post("/projects", json={"name": f"blank-{_tag()}"}).json()
    assert client.patch(f"/projects/{p['id']}", json={"name": ""}).status_code == 422
    assert client.patch(f"/projects/{p['id']}", json={"name": None}).status_code in (200, 422)
    async def name():
        async with SessionLocal() as s:
            return (await s.get(Project, p["id"])).name
    assert asyncio.run(name()) == p["name"]
