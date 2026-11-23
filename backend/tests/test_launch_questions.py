"""Phase 5b Task 3: launch_item on the open questions list."""

import asyncio
import uuid

import pytest

from app.database import SessionLocal
from app.models import LaunchItem, Project, Task, TaskQuestion


def _seed(admin_id, items, questions):
    pid = uuid.uuid4().hex
    tid = uuid.uuid4().hex
    ids = []

    async def go():
        async with SessionLocal() as s:
            s.add(Project(id=pid, slug=f"s-{pid[:10]}", name=f"lq-{pid[:6]}", directory="/tmp"))
            await s.flush()
            for n, (title, status) in enumerate(items, 1):
                s.add(LaunchItem(project_id=pid, number=n, title=title, status=status))
            s.add(Task(id=tid, title="t", prompt="p", root_id=tid, status="completed", created_by=admin_id,
                       project_id=pid))
            await s.flush()
            for text, blocks in questions:
                q = TaskQuestion(task_id=tid, root_id=tid, project_id=pid, question=text, blocks=blocks,
                                 source="agent", status="open", options=[])
                s.add(q)
                await s.flush()
                ids.append(q.id)
            await s.commit()

    asyncio.run(go())
    return ids


@pytest.fixture
def admin_id(client):
    return client.get("/auth/me").json()["id"]


def _by_id(client):
    return {q["id"]: q for q in client.get("/questions").json()}


def test_launch_item_link(client, admin_id):
    a, b, c, d, e = _seed(admin_id,
                          [("Billing export", "open"), ("Old thing", "done"), ("ui", "open")],
                          [("Should we ship?", "the BILLING EXPORT work"),
                           ("Is old thing needed?", None),
                           ("Something about ui?", None),
                           ("Unrelated", "nothing"),
                           ("Mentions billing export in the question", None)])
    got = _by_id(client)
    assert got[a]["launch_item"] == {"number": 1, "title": "Billing export"}
    assert got[b]["launch_item"] is None  # done item
    assert got[c]["launch_item"] is None  # 2-character title
    assert got[d]["launch_item"] is None
    assert got[e]["launch_item"] == {"number": 1, "title": "Billing export"}


def test_no_project_and_no_items(client, admin_id):
    (q,) = _seed(admin_id, [], [("Anything?", "x")])
    assert _by_id(client)[q]["launch_item"] is None


def test_one_items_query_per_request(client, admin_id):
    from sqlalchemy import event
    from app.database import engine

    _seed(admin_id, [("Billing export", "open")], [("billing export?", None)] * 5)
    seen = []

    def count(conn, cursor, statement, *a):
        if "launch_items" in statement:
            seen.append(statement)

    sync_engine = engine.sync_engine
    event.listen(sync_engine, "before_cursor_execute", count)
    try:
        client.get("/questions")
    finally:
        event.remove(sync_engine, "before_cursor_execute", count)
    assert len(seen) == 1


def test_started_item_counts_as_open(client, admin_id):
    pid_q = _seed(admin_id, [("Billing export", "started"), ("Gone", "dropped")],
                  [("billing export?", None), ("gone?", None)])
    got = _by_id(client)
    assert got[pid_q[0]]["launch_item"] == {"number": 1, "title": "Billing export"}
    assert got[pid_q[1]]["launch_item"] is None
