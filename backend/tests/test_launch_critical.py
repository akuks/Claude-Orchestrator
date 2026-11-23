"""Phase 5b Task 3: critical-path warning event."""

import asyncio
import os
import uuid

import pytest

from app.database import SessionLocal
from app.launch import critical
from app.models import Project, ProjectLaunch, Task
from app.worker import WorkerManager

pytestmark = pytest.mark.usefixtures("client")


def _use(tid, name, **inp):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def test_changed_paths(tmp_path):
    d = str(tmp_path)
    ev = [
        {"name": "Edit", "input": {"file_path": os.path.join(d, "src/a.py")}},
        {"name": "Write", "input": {"file_path": "src/b.py"}},
        {"name": "MultiEdit", "input": {"file_path": "./src/a.py"}},
        {"name": "NotebookEdit", "input": {"notebook_path": os.path.join(d, "nb/x.ipynb")}},
        {"name": "Edit", "input": {"file_path": "/etc/passwd"}},
        {"name": "Edit", "input": {"file_path": "../outside.py"}},
        {"name": "Edit", "input": {"file_path": "src/../../up.py"}},
        {"name": "Bash", "input": {"file_path": "src/c.py"}},
        {"name": "Edit", "input": {}},
        {"name": "Edit", "input": {"file_path": d}},
    ]
    assert critical.changed_paths(ev, d) == ["src/a.py", "src/b.py", "nb/x.ipynb"]
    assert critical.changed_paths(ev, "") == []


def test_symlink_escape_ignored(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "link").symlink_to(out)
    assert critical.changed_paths([{"name": "Edit", "input": {"file_path": "link/f.py"}}], str(proj)) == []


def test_hits():
    p, g = critical.hits(["src/billing/x.py", "README.md"], ["src/billing/**", "db/*.sql"])
    assert p == ["src/billing/x.py"] and g == ["src/billing/**"]
    assert critical.hits([], ["a"]) == ([], [])


def _setup(tmp_path, globs):
    pid, tid = uuid.uuid4().hex, uuid.uuid4().hex

    async def go():
        async with SessionLocal() as s:
            s.add(Project(id=pid, slug=f"s-{pid[:10]}", name=f"cp-{pid[:6]}", directory=str(tmp_path)))
            await s.flush()
            if globs is not None:
                s.add(ProjectLaunch(project_id=pid, critical_paths=globs))
            s.add(Task(id=tid, title="t", prompt="p", root_id=tid, status="running", project_id=pid))
            await s.commit()

    asyncio.run(go())
    return tid


def _drive(tid, events):
    w = WorkerManager()
    got = []

    async def emit(t, p):
        got.append((t, p))

    ctx = {"task_id": tid, "mcp_ids": {}, "secrets": [], "model_stored": True}

    async def go():
        for ev in events:
            await w._handle_stream_event(ev, emit, {}, ctx)
        await w._emit_critical_paths(tid, ctx, emit)

    asyncio.run(go())
    return got


def test_event_emitted_after_tool_uses(tmp_path):
    tid = _setup(tmp_path, ["src/billing/**"])
    got = _drive(tid, [_use("1", "Edit", file_path=str(tmp_path / "src/billing/a.py")),
                       _use("2", "Write", file_path="README.md"),
                       _use("3", "Edit", file_path="/etc/hosts")])
    crit = [p for t, p in got if t == "critical_paths"]
    assert crit == [{"paths": ["src/billing/a.py"], "globs": ["src/billing/**"]}]
    assert got[-1][0] == "critical_paths"


def test_no_event_without_globs_or_hits(tmp_path):
    ev = [_use("1", "Edit", file_path="a.py")]
    for globs in (None, [], ["src/**"]):
        tid = _setup(tmp_path, globs)
        assert not [1 for t, _p in _drive(tid, ev) if t == "critical_paths"]


def test_paths_capped_at_ten(tmp_path):
    tid = _setup(tmp_path, ["src/**"])
    got = _drive(tid, [_use(str(i), "Write", file_path=f"src/f{i}.py") for i in range(15)])
    assert len([p for t, p in got if t == "critical_paths"][0]["paths"]) == 10


def test_error_never_raises(tmp_path):
    w = WorkerManager()

    async def emit(t, p):
        raise RuntimeError("boom")

    ctx = {"edit_calls": [{"name": "Edit", "input": {"file_path": "src/a.py"}}]}
    tid = _setup(tmp_path, ["src/**"])
    asyncio.run(w._emit_critical_paths(tid, ctx, emit))
    asyncio.run(w._emit_critical_paths("missing", ctx, emit))


def test_finish_emits_on_failure_end_once(tmp_path):
    from sqlalchemy import select
    from app.models import TaskEvent

    tid = _setup(tmp_path, ["src/**"])
    w = WorkerManager()
    got = []

    async def emit(t, p):
        got.append((t, p))

    ctx = {"task_id": tid, "mcp_ids": {}, "secrets": [], "model_stored": True}

    async def go():
        await w._handle_stream_event(_use("1", "Edit", file_path="src/a.py"), emit, {}, ctx)
        w._run_ctx[tid] = ctx
        await w._finish(tid, "failed", error="Task exceeded time limit")  # timeout-style end
        await w._finish(tid, "failed", error="again")  # second finish adds nothing
        async with SessionLocal() as s:
            return (await s.execute(select(TaskEvent).where(TaskEvent.task_id == tid,
                                                            TaskEvent.type == "critical_paths"))).scalars().all()

    rows = asyncio.run(go())
    assert len(rows) == 1 and rows[0].payload == {"paths": ["src/a.py"], "globs": ["src/**"]}
    assert not w._run_ctx


def test_normal_path_then_finish_emits_once(tmp_path):
    tid = _setup(tmp_path, ["src/**"])
    w = WorkerManager()
    got = []

    async def emit(t, p):
        got.append(t)

    ctx = {"task_id": tid, "mcp_ids": {}, "secrets": [], "model_stored": True}

    async def go():
        await w._handle_stream_event(_use("1", "Edit", file_path="src/a.py"), emit, {}, ctx)
        await w._emit_critical_paths(tid, ctx, emit)
        w._run_ctx[tid] = ctx
        await w._finish(tid, "completed")

    asyncio.run(go())
    assert got.count("critical_paths") == 1
