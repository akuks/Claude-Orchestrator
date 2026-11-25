"""Phase 7e task 3 (spec §3): copy a temp-folder document the thread recorded into the project."""

import asyncio
import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Task, TaskEvent, TaskHandoff
from conftest import login_as


def run(coro):
    return asyncio.run(coro)


def _setup(tmp_path, docs, **kw):
    tid = uuid.uuid4().hex
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)

    async def go():
        async with SessionLocal() as s:
            s.add(Task(id=tid, title="t", prompt="p", root_id=tid, status="completed",
                       workspace_dir=str(ws), **kw))
            s.add(TaskHandoff(root_id=tid, task_id=tid, content={"outside_docs": docs}, text="",
                              saved_at=datetime.now(timezone.utc)))
            await s.commit()

    run(go())
    return tid, ws


def _doc(name="plan.md", body="# plan"):
    d = f"/tmp/co-copytest-{uuid.uuid4().hex[:8]}"
    os.makedirs(d)
    p = f"{d}/{name}"
    open(p, "w").write(body)
    return p


def _copy(client, tid, path, dest):
    return client.post(f"/tasks/{tid}/outside-docs/copy", json={"path": path, "dest": dest})


def test_copies_and_records_event(client, tmp_path):
    src = _doc()
    tid, ws = _setup(tmp_path, [src])
    (ws / "docs").mkdir()
    r = _copy(client, tid, src, "docs/plan.md")
    assert r.status_code == 200, r.text
    f = ws / "docs" / "plan.md"
    assert f.read_text() == "# plan" and oct(f.stat().st_mode & 0o777) == "0o644"

    async def ev():
        async with SessionLocal() as s:
            return (await s.execute(select(TaskEvent).where(TaskEvent.task_id == tid, TaskEvent.type == "doc_copied"))).scalars().all()

    evs = run(ev())
    assert len(evs) == 1 and evs[0].payload == {"from": src, "to": "docs/plan.md"}


def test_never_overwrites(client, tmp_path):
    src = _doc()
    tid, ws = _setup(tmp_path, [src])
    (ws / "a.md").write_text("old")
    r = _copy(client, tid, src, "a.md")
    assert r.status_code == 409 and r.json()["detail"] == "A file with that name already exists."
    assert (ws / "a.md").read_text() == "old"


def test_refuses_unrecorded_and_bad_paths(client, tmp_path):
    src, other = _doc(), _doc("other.md")
    tid, ws = _setup(tmp_path, [src])
    (ws / "docs").mkdir()
    assert _copy(client, tid, other, "x.md").status_code == 404
    for dest in ("../x.md", "/etc/x.md", "docs/../../x.md", "nofolder/x.md", "docs/", ""):
        assert _copy(client, tid, src, dest).status_code == 400, dest
    assert not (tmp_path / "x.md").exists()


def test_refuses_symlink_source_and_symlinked_folder(client, tmp_path):
    real = _doc()
    link = real + ".md"
    os.symlink(real, link)
    tid, ws = _setup(tmp_path, [link])
    assert _copy(client, tid, link, "x.md").status_code in (400, 404)
    src = _doc()
    tid2, ws2 = _setup(tmp_path / "b" if (tmp_path / "b").mkdir() is None else tmp_path, [src])
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, ws2 / "esc")
    assert _copy(client, tid2, src, "esc/x.md").status_code == 400
    assert not (outside / "x.md").exists()


def test_refuses_big_and_non_doc(client, tmp_path):
    big = _doc("big.md", "x" * (5 * 1024 * 1024 + 1))
    txt = _doc("a.txt")
    tid, ws = _setup(tmp_path, [big, txt])
    assert _copy(client, tid, big, "big.md").status_code == 400
    assert _copy(client, tid, txt, "a.txt").status_code == 400


def test_only_people_who_may_edit(client, tmp_path):
    src = _doc()
    tid, ws = _setup(tmp_path, [src])
    u = login_as(f"cp-{uuid.uuid4().hex[:6]}@test.local")
    assert _copy(u, tid, src, "x.md").status_code in (403, 404)
    assert not (ws / "x.md").exists()
