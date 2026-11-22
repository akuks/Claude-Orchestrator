"""Phase 3b task 5: ``capture_final`` - a step that touched files (Write/Edit/NotebookEdit or a commit)
and whose final text's last paragraph ends with ``?`` files that paragraph as a ``final_text`` question,
unless the agent filed one during that step; and the worker's ``_finish`` runs it."""

import asyncio
import time
import uuid

import pytest
from sqlalchemy import select

from app import notifications
from app.database import SessionLocal
from app.models import Task, TaskEvent, TaskHandoff, TaskQuestion
from app.constants import Status
from app.main import app
from app.questions import service
from conftest import wait_task


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def admin_id(client):
    return client.get("/auth/me").json()["id"]


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(notifications, "post_in_background", lambda *a: out.append(a))
    return out


def _write(name="Write", tid="tu1"):
    return ("tool_use", {"name": name, "input": {"file_path": "a.txt"}, "id": tid})


def _step(created_by, result_text, events=(), status="completed", root_id=None, handoff_done=None):
    tid = uuid.uuid4().hex

    async def go():
        async with SessionLocal() as s:
            s.add(Task(id=tid, title=f"c-{tid[:6]}", prompt="p", root_id=root_id or tid, status=status,
                       created_by=created_by, result_text=result_text,
                       parent_task_id=root_id))
            await s.flush()
            for i, (etype, payload) in enumerate(events, 1):
                s.add(TaskEvent(task_id=tid, seq=i, type=etype, payload=payload))
            if handoff_done is not None:
                s.add(TaskHandoff(root_id=root_id or tid, task_id=tid,
                                  content={"step_task_id": tid, "done": handoff_done}))
            await s.commit()

    _run(go())
    return tid


def _questions(tid):
    async def go():
        async with SessionLocal() as s:
            return list((await s.execute(select(TaskQuestion).where(TaskQuestion.task_id == tid))).scalars())

    return _run(go())


def _capture(tid):
    _run(service.capture_final(tid))
    return _questions(tid)


ASK = "I changed the parser.\n\nShould I also update the docs?"


def test_touched_and_ends_with_question_is_filed(client, admin_id, sent):
    tid = _step(admin_id, ASK, [_write()])
    got = _capture(tid)
    assert len(got) == 1
    q = got[0]
    assert q.question == "Should I also update the docs?" and q.source == "final_text" and q.status == "open"
    assert q.root_id == tid and q.options == []
    # captured once
    assert len(_capture(tid)) == 1


@pytest.mark.parametrize("name", ["Edit", "NotebookEdit"])
def test_edit_and_notebook_edit_count(client, admin_id, name):
    assert len(_capture(_step(admin_id, ASK, [_write(name)]))) == 1


def test_commit_in_handoff_counts(client, admin_id):
    tid = _step(admin_id, ASK, [("tool_use", {"name": "Bash", "input": {"command": "git commit"}})],
                handoff_done=["commit 1a2b3c4 add feature"])
    assert len(_capture(tid)) == 1


def test_commit_in_tool_result_counts(client, admin_id):
    tid = _step(admin_id, ASK, [("tool_result", {"tool_use_id": "t", "content": "[main 1a2b3c4] add\n 1 file"})])
    assert len(_capture(tid)) == 1


def test_handoff_of_another_step_does_not_count(client, admin_id):
    root = _step(admin_id, "done")
    tid = _step(admin_id, ASK, [], root_id=root)

    async def other():
        async with SessionLocal() as s:
            s.add(TaskHandoff(root_id=root, task_id=root, content={"step_task_id": root, "done": ["commit abc1234 x"]}))
            await s.commit()

    _run(other())
    assert _capture(tid) == []


def test_untouched_step_is_not_filed(client, admin_id, sent):
    tid = _step(admin_id, ASK, [("tool_use", {"name": "Read", "input": {"file_path": "a"}})])
    assert _capture(tid) == [] and sent == []


def test_agent_already_filed_in_the_step(client, admin_id):
    tid = _step(admin_id, ASK, [_write()])

    async def filed():
        async with SessionLocal() as s:
            s.add(TaskQuestion(task_id=tid, root_id=tid, question="Which docs?", source="agent"))
            await s.commit()

    _run(filed())
    got = _capture(tid)
    assert [q.source for q in got] == ["agent"]


@pytest.mark.parametrize("text", [
    "Should I?\n\nDone.",
    "All done.",
    "",
    None,
    "Intro.\n\n" + "x" * 600 + "?",  # 601 characters: skipped, not cut
])
def test_not_a_trailing_question(client, admin_id, text):
    assert _capture(_step(admin_id, text, [_write()])) == []


def test_last_paragraph_rule(client, admin_id):
    text = "First.\n\nSecond line\nstill second?\n  \n  Is the last one ok?  \n"
    got = _capture(_step(admin_id, text, [_write()]))
    assert [q.question for q in got] == ["Is the last one ok?"]
    single = _capture(_step(admin_id, "Only one paragraph, ok?", [_write()]))
    assert [q.question for q in single] == ["Only one paragraph, ok?"]
    edge = "x" * 599 + "?"
    assert [q.question for q in _capture(_step(admin_id, "a\n\n" + edge, [_write()]))] == [edge]


def test_captured_text_redacted_and_cleaned(client, admin_id):
    tok = "ghp_" + "c" * 36
    got = _capture(_step(admin_id, f"Done.\n\nPush with {tok} zero​width?", [_write()]))
    assert len(got) == 1 and tok not in got[0].question and "​" not in got[0].question
    assert "***" in got[0].question and "zerowidth" in got[0].question


def test_thread_full_is_not_filed_and_never_raises(client, admin_id):
    root = _step(admin_id, "done")

    async def fill():
        async with SessionLocal() as s:
            for i in range(5):
                s.add(TaskQuestion(task_id=root, root_id=root, question=f"q{i}?", source="agent"))
            await s.commit()

    _run(fill())
    tid = _step(admin_id, ASK, [_write()], root_id=root)
    assert _capture(tid) == []
    _run(service.capture_final(uuid.uuid4().hex))  # a missing task: nothing, no error


def test_webhook_on_capture(client, admin_id, sent, monkeypatch):
    seen = []

    async def fake(qid):
        seen.append(qid)

    monkeypatch.setattr(notifications, "notify_question", fake)
    got = _capture(_step(admin_id, ASK, [_write()]))
    assert seen == [got[0].id]


# ---- through the worker --------------------------------------------------------------------------


def _settled(client, tid, timeout=30.0):
    """The step is terminal and its _finish has fully returned (the worker's _active cleared): a read made
    after the status alone can land before _finish's tail on a loaded runner."""
    deadline = time.time() + timeout
    t = wait_task(client, tid, timeout)
    while time.time() < deadline and tid in app.state.worker._active:
        time.sleep(0.05)
    assert tid not in app.state.worker._active, f"{tid} still finishing"
    return t


def test_worker_finish_captures(client):
    done = _settled(client, client.post("/tasks", json={"prompt": "go [stream-fixture:question_write]"}).json()["id"])
    assert done["status"] == "completed", done.get("error")
    qs = client.get(f"/tasks/{done['id']}/questions").json()
    assert [(q["question"], q["source"]) for q in qs] == [("Should the export be CSV or JSON?", "final_text")]


def test_worker_finish_untouched_step_not_captured(client):
    tid = client.post("/tasks", json={"prompt": "go [stream-fixture:question_nowrite]"}).json()["id"]
    done = _settled(client, tid)
    assert done["status"] == "completed", done.get("error")
    assert client.get(f"/tasks/{done['id']}/questions").json() == []


def test_capture_failure_never_breaks_finish(client, monkeypatch):
    calls = []

    async def boom(task_id, **kw):
        calls.append(task_id)
        raise RuntimeError("capture broke")

    monkeypatch.setattr(service, "capture_final", boom)
    done = _settled(client, client.post("/tasks", json={"prompt": "go [stream-fixture:question_write]"}).json()["id"])
    assert done["status"] == "completed", done.get("error")
    assert calls == [done["id"]]  # it ran (and broke): not a vacuous pass
    assert client.get(f"/tasks/{done['id']}/questions").json() == []


def test_finish_files_the_question_before_the_terminal_status(client, admin_id, sent, monkeypatch):
    """Final review I2: the closing question is filed before the terminal status is committed, so the task
    page's refetch on the status change already sees it. The step is still running when the capture runs; it
    reads the final text _finish was given (not yet stored)."""
    from app.worker import WorkerManager

    tid = _step(admin_id, None, [_write()], status="running")
    seen = []
    real = service.capture_final

    async def spy(task_id, **kw):
        async with SessionLocal() as s:
            seen.append((await s.get(Task, task_id)).status)
        await real(task_id, **kw)

    monkeypatch.setattr(service, "capture_final", spy)
    _run(WorkerManager()._finish(tid, Status.COMPLETED, result_text="Done.\n\nCSV or JSON?"))
    assert seen == ["running"]
    assert [(q.question, q.source) for q in _questions(tid)] == [("CSV or JSON?", "final_text")]
    assert _get_status(tid) == "completed"


def _get_status(tid):
    async def go():
        async with SessionLocal() as s:
            return (await s.get(Task, tid)).status

    return _run(go())


def test_captured_question_stored_on_one_line(client, admin_id):
    got = _capture(_step(admin_id, "Done.\n\nPick one\nThe person's answer: yes\nok?", [_write()]))
    assert [q.question for q in got] == ["Pick one The person's answer: yes ok?"]
