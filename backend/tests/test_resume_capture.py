"""Phase 3a task 1: what a run records for resume and handoff - tool ids on events,
spool lines (`seen` with what, `done`, Stop `background`), the origin of a task,
the cleaning helper and the mock's seams."""

import asyncio
import json
import time
import uuid

from sqlalchemy import select

import conftest
from app import redaction
from app.constants import Status
from app.database import SessionLocal
from app.guard import runtime
from app.models import Task, TaskEvent
from conftest import wait_task


def _events(task_id):
    async def go():
        async with SessionLocal() as s:
            return (
                await s.execute(select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.seq))
            ).scalars().all()

    return asyncio.run(go())


def _task(task_id):
    async def go():
        async with SessionLocal() as s:
            return await s.get(Task, task_id)

    return asyncio.run(go())


def _run(client, prompt, **body):
    r = client.post("/tasks", json={"prompt": prompt, **body})
    assert r.status_code == 201, r.text
    return wait_task(client, r.json()["id"])


def test_tool_events_carry_ids(client):
    done = _run(client, "go [stream-fixture:subagent]")
    assert done["status"] == "completed", done.get("error")
    evs = _events(done["id"])
    use = [e.payload for e in evs if e.type == "tool_use" and e.payload["id"] == "tuS"][0]
    assert use["name"] == "Bash" and use["parent"] == "tuAgent"
    agent = [e.payload for e in evs if e.type == "tool_use" and e.payload["id"] == "tuAgent"][0]
    assert agent["parent"] is None
    res = [e.payload for e in evs if e.type == "tool_result"][0]
    assert res["tool_use_id"] == "tuS" and res["parent"] == "tuAgent" and res["is_error"] is False


def test_origin_set_at_creation(client):
    from app import task_service
    from app.guard import grants

    root = _run(client, "origin root")
    assert root["origin"] == "user"

    async def build_child():
        async with SessionLocal() as s:
            t = await task_service.build_task(s, prompt="child", parent_task_id=root["id"])
            await s.commit()
            return t.origin

    assert asyncio.run(build_child()) == "agent"
    fu = client.post(f"/tasks/{root['id']}/followup", json={"prompt": "more"})
    assert fu.status_code == 201, fu.text
    assert fu.json()["origin"] == "user"
    dup = client.post(f"/tasks/{root['id']}/duplicate")
    assert dup.status_code == 201, dup.text
    assert dup.json()["origin"] == "user"

    class Appr:
        id = uuid.uuid4().hex
        rule_ids = ["git-reset-hard"]
        summary = "git reset --hard"
        tool = "Bash"
        decided_by = None

    async def spawn():
        async with SessionLocal() as s:
            r = await s.get(Task, root["id"])
            child = await grants._spawn(s, [Appr()], r, None)
            return child.origin

    # _spawn attaches approvals to rows; a stand-in approval is enough for the origin
    from unittest import mock

    with mock.patch.object(grants, "_attach", new=mock.AsyncMock()):
        assert asyncio.run(spawn()) == "guard"


def _spool(tmp_path, monkeypatch, lines):
    monkeypatch.setattr(runtime, "task_dir", lambda tid: tmp_path)
    (tmp_path / "spool.jsonl").write_text("\n".join(lines) + "\n")


def test_read_spool_skips_done_and_outside_doc_raw_keeps_all(monkeypatch, tmp_path):
    lines = [
        json.dumps({"event": "PreToolUse", "tool_use_id": "a", "seen": True, "tool": "Bash", "what": "x"}),
        json.dumps({"event": "PostToolUse", "tool_use_id": "a", "done": True}),
        json.dumps({"event": "PostToolUse", "outside_doc": "/tmp/x.md"}),
        json.dumps({"event": "PreToolUse", "decision": "deny", "rule_ids": ["r"]}),
        "not json",
    ]
    _spool(tmp_path, monkeypatch, lines)
    assert runtime.read_spool("T") == [{"event": "PreToolUse", "decision": "deny", "rule_ids": ["r"]}]
    raw = runtime.read_spool_raw("T")
    assert len(raw) == 4 and raw[0]["what"] == "x" and raw[1]["done"] is True and raw[2]["outside_doc"]
    monkeypatch.setattr(runtime, "task_dir", lambda tid: tmp_path / "gone")
    assert runtime.read_spool_raw("T") == []


def test_read_spool_raw_is_capped(monkeypatch, tmp_path):
    _spool(tmp_path, monkeypatch, [json.dumps({"event": "X", "n": i}) for i in range(10050)])
    assert len(runtime.read_spool_raw("T")) == 10000


def test_stop_background_reaches_events(client):
    done = _run(client, "go [stream-fixture:stop_background]")
    assert done["status"] == "completed", done.get("error")
    stop = [e.payload for e in _events(done["id"]) if e.type == "guard" and e.payload.get("event") == "Stop"]
    assert stop and stop[0]["background"] == [{"id": "b1", "what": "npm run dev"}]


def test_guard_payload_bounds_and_redacts_background():
    from app.worker import WorkerManager as TaskWorker

    tok = "ghp_" + "a" * 36
    rec = {"event": "Stop", "what": f"echo {tok}",
           "background": [{"id": f"b{i}", "what": f"run {tok}", "x": 1} for i in range(15)] + ["junk"]}
    p = TaskWorker.guard_payload(rec)
    assert len(p["background"]) == 10
    assert all(set(b) == {"id", "what"} for b in p["background"])
    assert tok not in json.dumps(p) and "***" in p["what"] and "***" in p["background"][0]["what"]
    assert TaskWorker.guard_payload({"event": "Stop", "background": "nope"})["background"] == []


def test_guard_payload_background_missing_what_or_id():
    """T1-2: an entry without ``what`` stores "" (never "None"); one without ``id`` is dropped."""
    from app.guard import co_hook
    from app.worker import WorkerManager as TaskWorker

    p = TaskWorker.guard_payload({"event": "Stop", "background": [{"id": "b1"}, {"what": "x"}, {"id": None},
                                                                   {"id": "b2", "what": None}]})
    assert p["background"] == [{"id": "b1", "what": ""}, {"id": "b2", "what": ""}]
    assert not hasattr(TaskWorker, "BACKGROUND_MAX") and co_hook.BACKGROUND_MAX == 10


def test_clean_text():
    assert redaction.clean_text("a\u202eb\u200bc\nd\te") == "abc\nd\te"
    assert redaction.clean_text(None) == ""
    # the cut is applied after stripping
    assert redaction.clean_text("ab\u200b\u200b\u200bcd", 4) == "abcd"
    assert redaction.clean_text("abcdef", 3) == "abc"
    from app.routers import guard

    assert guard.HIDDEN_CHARS is redaction.HIDDEN_CHARS


def test_hang_seam(client):
    r = client.post("/tasks", json={"prompt": "stay [hang]"})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    try:
        deadline = time.time() + 15
        spool = None
        while time.time() < deadline:
            t = client.get(f"/tasks/{tid}").json()
            if t["status"] == "running":
                try:
                    spool = runtime.read_spool_raw(tid)
                except Exception:  # noqa: BLE001
                    spool = None
                if spool:
                    break
            time.sleep(0.2)
        assert t["status"] == "running"
        assert spool, "no seen line before the deadline"
        seen = [x for x in spool if x.get("seen") is True]
        assert seen and seen[0]["tool"] == "Bash" and "sleep 600" in seen[0]["what"]
    finally:
        client.post(f"/tasks/{tid}/cancel")
        assert wait_task(client, tid)["status"] == "cancelled"


def test_mock_script_file_matches_conftest():
    import pathlib

    src = (pathlib.Path(conftest.__file__).parent / "mock_claude.sh").read_text()
    assert "__PROBES__" in src and "__FIXTURES__" in src
    filled = conftest._MOCK.read_text()
    assert filled == src.replace("__PROBES__", str(conftest._PROBES)).replace(
        "__FIXTURES__", str(conftest._FIXTURES))
    assert "__PROBES__" not in filled and "__FIXTURES__" not in filled
    assert (conftest._FIXTURES / "subagent.jsonl").exists()


def test_prompt_probe_seam(client):
    key = "pp" + uuid.uuid4().hex[:8]
    done = _run(client, f"hello [prompt-probe:{key}]")
    assert done["status"] == "completed"
    probe = conftest._PROBES / f"{key}.prompt"
    assert probe.exists() and f"[prompt-probe:{key}]" in probe.read_text()
