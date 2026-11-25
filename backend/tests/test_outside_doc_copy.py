import pytest
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


@pytest.fixture(autouse=True)
def _own_temp_root(monkeypatch):
    # On Linux CI pytest's tmp_path (the task folder) lives under /tmp, and a temp root that holds the
    # task's folder is not treated as a temp root. Give the docs a temp root of their own via TMPDIR.
    root = f"/tmp/co-copytest-root-{uuid.uuid4().hex[:8]}"
    os.makedirs(root)
    monkeypatch.setenv("TMPDIR", root)
    yield root


def _doc(name="plan.md", body="# plan"):
    d = os.path.join(os.environ["TMPDIR"], f"co-copytest-{uuid.uuid4().hex[:8]}")
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


def test_copy_needs_a_document_name(client, tmp_path):
    src = _doc()
    tid, ws = _setup(tmp_path, [src])
    for dest in ("x.py", "x", "docs.md/x.sh", ".env"):
        (ws / "docs.md").mkdir(exist_ok=True)
        r = _copy(client, tid, src, dest)
        assert r.status_code == 400 and r.json()["detail"] == "Only documents can be copied (.md, .html, .pdf).", dest
    assert _copy(client, tid, src, "ok.HTML").status_code == 200


def test_a_folder_swapped_for_a_link_is_refused(tmp_path):
    from app.resume import copydoc

    inner = tmp_path / "ws" / "docs"
    inner.mkdir(parents=True)
    outside = tmp_path / "out"
    outside.mkdir()
    fd = copydoc._open_chain(str(tmp_path / "ws"), ["docs"])
    os.close(fd)
    inner.rmdir()
    os.symlink(outside, inner)
    try:
        copydoc._open_chain(str(tmp_path / "ws"), ["docs"])
        raise AssertionError("a linked folder was followed")
    except OSError:
        pass
    assert list(outside.iterdir()) == []


def test_no_project_role_cannot_copy_contributors_can(client, tmp_path):
    src = _doc()
    pid = client.post("/projects", json={"name": f"cp-{uuid.uuid4().hex[:6]}"}).json()["id"]
    u = login_as(f"cv-{uuid.uuid4().hex[:6]}@test.local")

    def grant(role):
        r = client.put(f"/projects/{pid}/members", json=[{"subject_type": "user", "subject_id": u.user_id, "role": role}])
        assert r.status_code == 200, r.text

    tid, ws = _setup(tmp_path, [src], created_by=u.user_id, project_id=pid)
    r = _copy(u, tid, src, "v.md")
    assert r.status_code in (403, 404) and not (ws / "v.md").exists()
    grant("contributor")
    assert _copy(u, tid, src, "v.md").status_code == 200
