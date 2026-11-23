"""Phase 5c task 2: typed stage outputs are parsed, checked and passed by name."""

import asyncio
import os
import subprocess
import tempfile
import time
import uuid

import pytest
from sqlalchemy import select

from app.constants import Status
from app.database import SessionLocal
from app.gates import outputs
from app.main import app
from app.models import Agent, Project, Task, TaskEvent, WorkflowRun
from app.worker import WorkerManager
from app.workflow_runner import WorkflowRunner, substitute

SUFFIX = ("When you finish, end your final message with a fenced ```json block holding exactly these keys, "
          "each a short string: plan_path, summary. A key ending in _path is a path relative to the project "
          "folder that exists; a key ending in _commit is a commit id in this repository.")


def block(body: str) -> str:
    return f"done.\n```json\n{body}\n```\n"


# ---------------------------------------------------------------------------
# suffix / parse
# ---------------------------------------------------------------------------


def test_suffix_exact():
    assert outputs.suffix(["plan_path", "summary"]) == SUFFIX


def test_parse_last_block_wins():
    text = block('{"summary": "first"}') + "more talk\n" + block('{"summary": "second"}')
    assert outputs.parse(text, ["summary"]) == {"summary": "second"}


@pytest.mark.parametrize("body,reason", [
    ('{"summary": "x", "extra": "y"}', "unexpected key extra"),
    ('{}', "missing key summary"),
    ('{"summary": 3}', "summary is not a string"),
    ('{"summary": ["a"]}', "summary is not a string"),
    ('["summary"]', "not an object"),
    ('{"summary": ', "not valid JSON"),
])
def test_parse_refusals(body, reason):
    with pytest.raises(ValueError) as e:
        outputs.parse(block(body), ["summary"])
    assert reason in str(e.value)


def test_parse_value_length():
    assert outputs.parse(block('{"summary": "%s"}' % ("a" * 2000)), ["summary"])["summary"] == "a" * 2000
    with pytest.raises(ValueError, match="longer than 2000"):
        outputs.parse(block('{"summary": "%s"}' % ("a" * 2001)), ["summary"])


def test_parse_cleans_hidden_chars():
    assert outputs.parse(block('{"summary": "a\\u200bb"}'), ["summary"]) == {"summary": "ab"}


def test_parse_no_block_large_text_is_fast():
    text = "x" * (5 * 1024 * 1024)
    t0 = time.perf_counter()
    with pytest.raises(ValueError, match="no ```json block"):
        outputs.parse(text, ["summary"])
    assert time.perf_counter() - t0 < 1.0


def test_parse_unterminated_fence_large_text_is_fast():
    text = block('{"summary": "ok"}') + "```json\n{\"summary\": \"half\"" + "y" * (5 * 1024 * 1024)
    t0 = time.perf_counter()
    with pytest.raises(ValueError, match="no ```json block|not closed"):
        outputs.parse(text, ["summary"])
    assert time.perf_counter() - t0 < 1.0
    with pytest.raises(ValueError, match="not closed"):
        outputs.parse('```json\n{"summary": "half"}', ["summary"])


@pytest.mark.parametrize("text", [None, "", "no fence here"])
def test_parse_empty(text):
    with pytest.raises(ValueError):
        outputs.parse(text, ["summary"])


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


def _git(d, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", *args], cwd=d, env=env, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo():
    d = tempfile.mkdtemp(prefix="outs-repo-")
    os.makedirs(os.path.join(d, "docs"))
    with open(os.path.join(d, "docs", "plan.md"), "w") as f:
        f.write("plan")
    _git(d, "init", "-q")
    _git(d, "add", ".")
    _git(d, "commit", "-q", "-m", "init")
    outside = tempfile.mkdtemp(prefix="outs-outside-")
    with open(os.path.join(outside, "secret"), "w") as f:
        f.write("s")
    os.symlink(os.path.join(outside, "secret"), os.path.join(d, "out_link"))
    os.symlink(os.path.join(d, "docs", "plan.md"), os.path.join(d, "in_link"))
    return d


def _verify(values, d):
    return asyncio.run(outputs.verify(values, d))


def test_verify_path_ok(repo):
    _verify({"plan_path": "docs/plan.md", "summary": "../anything"}, repo)
    _verify({"plan_path": "docs"}, repo)
    _verify({"plan_path": "in_link"}, repo)


@pytest.mark.parametrize("value,reason", [
    ("../x", "must not contain .."),
    ("docs/../docs/plan.md", "must not contain .."),
    ("/etc/passwd", "relative"),
    ("docs/missing.md", "does not exist"),
    ("out_link", "outside"),
    ("", "empty"),
])
def test_verify_path_refused(repo, value, reason):
    with pytest.raises(ValueError) as e:
        _verify({"plan_path": value}, repo)
    assert reason in str(e.value)


def test_verify_commit(repo):
    head = _git(repo, "rev-parse", "HEAD")
    _verify({"fix_commit": head}, repo)
    _verify({"fix_commit": head[:7]}, repo)
    for bad, reason in (("deadbeefdeadbeef", "not a commit in this repository"),
                        ("HEAD", "not a commit id"), (head.upper(), "not a commit id"),
                        (head[:6], "not a commit id"), (head + "\n", "not a commit id")):
        with pytest.raises(ValueError) as e:
            _verify({"fix_commit": bad}, repo)
        assert reason in str(e.value), bad


def test_verify_needs_project():
    _verify({"summary": "x"}, None)
    with pytest.raises(ValueError, match="no project folder"):
        _verify({"plan_path": "a"}, None)


# ---------------------------------------------------------------------------
# substitute
# ---------------------------------------------------------------------------


def test_substitute_named():
    named = {0: {"plan_path": "docs/plan.md"}, 1: {"summary": "all good"}}
    texts = {0: "stage one text", 1: "stage two text"}
    p = substitute("a {{stage1.plan_path}} b {{prev.summary}} c {{prev}} d {{stage1}}", "in", texts, named)
    assert p == "a docs/plan.md b all good c stage two text d stage one text"


def test_substitute_values_stay_literal():
    named = {0: {"summary": "{{input}} and {{stage1}} and {{prev.summary}}"}}
    p = substitute("go: {{stage1.summary}} / {{input}}", "IN {{prev}}", {0: "{{input}}"}, named)
    assert p == "go: {{input}} and {{stage1}} and {{prev.summary}} / IN {{prev}}"


def test_substitute_unknown_and_back_compat():
    assert substitute("x {{stage1.nope}} {{stage9.a}} {{prev.b}}", "in", {}, {}) == "x   "
    # unchanged 5a behaviour
    assert substitute("do it", "the input", {}) == "do it\n\nthe input"
    assert substitute("{{prev}}", "the input", {0: "a", 2: "c"}) == "c"
    assert substitute("{{stage2}}/{{stage0}}", "", {1: "b"}) == "b/"
    assert substitute("{{other}} {{input}}", "i", {}) == "{{other}} i"


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------


class FakeWorker:
    def __init__(self):
        self.submitted = []

    async def submit(self, tid, prio, created):
        self.submitted.append(tid)

    async def cancel(self, tid):
        pass


@pytest.fixture
def no_tick(client, monkeypatch):
    async def _no_tick():
        return None

    monkeypatch.setattr(app.state.workflow_runner, "_tick", _no_tick)


async def _setup(repo_dir):
    async with SessionLocal() as s:
        p = Project(name="so-" + uuid.uuid4().hex[:8], slug=uuid.uuid4().hex, directory=repo_dir)
        s.add(p)
        await s.flush()
        names = []
        for n in ("Lead", "Dev"):
            a = Agent(name=f"so-{n}-{uuid.uuid4().hex[:8]}", system_prompt="x", default_prompt="x", project_id=p.id)
            s.add(a)
            names.append(a.name)
        await s.commit()
        return p.id, names


async def _make_run(stages, pid) -> str:
    async with SessionLocal() as s:
        run = WorkflowRun(workflow_id="w", name="so", input="the input", stages=stages,
                          project_ids=[pid], status="pending", current_stage=0)
        s.add(run)
        await s.commit()
        return run.id


async def _tasks(rid, stage):
    async with SessionLocal() as s:
        return (await s.execute(select(Task).where(Task.workflow_run_id == rid, Task.stage_index == stage)
                                .order_by(Task.created_at))).scalars().all()


async def _finish(rid, stage, text):
    async with SessionLocal() as s:
        for t in (await s.execute(select(Task).where(Task.workflow_run_id == rid, Task.stage_index == stage,
                                                     Task.status != Status.FAILED))).scalars().all():
            t.status = Status.COMPLETED
            t.result_text = text
        await s.commit()


async def _run(rid):
    async with SessionLocal() as s:
        return await s.get(WorkflowRun, rid)


def _stages(lead, dev):
    return [
        {"name": "Plan", "items": [{"agent": lead, "prompt": "plan {{input}}", "outputs": ["plan_path", "summary"]}]},
        {"name": "Build", "items": [{"agent": dev, "prompt": "build {{stage1.plan_path}}: {{prev.summary}}"}]},
    ]


def test_runner_valid_outputs_advance(no_tick, repo):
    async def go():
        pid, (lead, dev) = await _setup(repo)
        rid = await _make_run(_stages(lead, dev), pid)
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        [t0] = await _tasks(rid, 0)
        assert t0.prompt == f"plan the input\n\n{SUFFIX}"
        await _finish(rid, 0, block('{"plan_path": "docs/plan.md", "summary": "use {{input}} here"}'))
        await r._advance(rid)
        [t0] = await _tasks(rid, 0)
        assert t0.status == Status.COMPLETED
        assert t0.outputs == {"plan_path": "docs/plan.md", "summary": "use {{input}} here"}
        run = await _run(rid)
        assert run.current_stage == 1 and run.status == "running"
        [t1] = await _tasks(rid, 1)
        assert t1.prompt == "build docs/plan.md: use {{input}} here"
        await _finish(rid, 1, "built")
        await r._advance(rid)
        assert (await _run(rid)).status == "completed"

    asyncio.run(go())


def test_runner_invalid_outputs_fail_stage(no_tick, repo):
    async def go():
        pid, (lead, dev) = await _setup(repo)
        rid = await _make_run(_stages(lead, dev), pid)
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _finish(rid, 0, block('{"plan_path": "../escape", "summary": "s"}'))
        await r._advance(rid)
        [t0] = await _tasks(rid, 0)
        assert t0.status == Status.FAILED
        assert t0.error == "outputs invalid: plan_path must not contain .."
        assert t0.outputs is None
        run = await _run(rid)
        assert run.status == "failed" and run.current_stage == 0
        assert run.stage_state["0"]["status"] == "failed"
        assert not await _tasks(rid, 1)
        # a person's retry asks again; valid outputs then advance
        assert await r.retry_run(rid)
        new = [t for t in await _tasks(rid, 0) if t.stage_attempt == 1]
        assert len(new) == 1 and new[0].prompt.endswith(SUFFIX)
        await _finish(rid, 0, block('{"plan_path": "docs/plan.md", "summary": "ok"}'))
        await r._advance(rid)
        assert (await _run(rid)).current_stage == 1

    asyncio.run(go())


def test_runner_missing_block_fails(no_tick, repo):
    async def go():
        pid, (lead, dev) = await _setup(repo)
        rid = await _make_run(_stages(lead, dev), pid)
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _finish(rid, 0, "I did it, no block")
        await r._advance(rid)
        [t0] = await _tasks(rid, 0)
        assert t0.error == "outputs invalid: no ```json block at the end of the result"
        assert (await _run(rid)).status == "failed"

    asyncio.run(go())


def test_runner_stage_without_outputs_unchanged(no_tick, repo):
    async def go():
        pid, (lead, dev) = await _setup(repo)
        rid = await _make_run([{"name": "Dev", "items": [{"agent": dev, "prompt": "x"}]}], pid)
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        [t0] = await _tasks(rid, 0)
        assert t0.prompt == "x\n\nthe input"
        assert "expects" not in (await _run(rid)).stage_state["0"]
        await _finish(rid, 0, "out")
        await r._advance(rid)
        [t0] = await _tasks(rid, 0)
        assert t0.outputs is None and t0.status == Status.COMPLETED
        assert (await _run(rid)).status == "completed"

    asyncio.run(go())


def test_runner_stale_check_is_dropped(no_tick, repo, monkeypatch):
    """A cancel that lands while the outputs are being checked wins: nothing is written."""

    async def go():
        pid, (lead, dev) = await _setup(repo)
        rid = await _make_run(_stages(lead, dev), pid)
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _finish(rid, 0, block('{"plan_path": "docs/plan.md", "summary": "s"}'))
        real = outputs.verify

        async def slow(values, d):
            await r.cancel_run(rid)
            await real(values, d)

        monkeypatch.setattr(outputs, "verify", slow)
        await r._advance(rid)
        [t0] = await _tasks(rid, 0)
        assert t0.outputs is None and t0.status == Status.COMPLETED
        run = await _run(rid)
        assert run.status == "failed" and run.current_stage == 0

    asyncio.run(go())


def test_run_detail_hides_expects(client, no_tick, repo):
    async def make():
        pid, (lead, dev) = await _setup(repo)
        rid = await _make_run(_stages(lead, dev), pid)
        await WorkflowRunner(FakeWorker(), 999).start_run(rid)
        await _finish(rid, 0, block('{"plan_path": "docs/plan.md", "summary": "s"}'))
        await WorkflowRunner(FakeWorker(), 999)._advance(rid)
        return rid

    rid = asyncio.run(make())
    r = client.get(f"/workflows/runs/{rid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "expects" not in body["stage_state"]["0"]
    t0 = next(t for t in body["tasks"] if t["stage_index"] == 0)
    assert t0["outputs"] == {"plan_path": "docs/plan.md", "summary": "s"}


class EmitWorker(FakeWorker):
    """FakeWorker with the real offline emitter (it uses nothing else of the worker)."""

    async def emit_offline(self, task_id, etype, payload):
        await WorkerManager._emit_offline(self, task_id, etype, payload)


def test_runner_invalid_outputs_emit_events(no_tick, repo):
    async def go():
        pid, (lead, dev) = await _setup(repo)
        rid = await _make_run(_stages(lead, dev), pid)
        r = WorkflowRunner(EmitWorker(), 999)
        await r.start_run(rid)
        await _finish(rid, 0, "no block")
        await r._advance(rid)
        [t0] = await _tasks(rid, 0)
        assert t0.status == Status.FAILED
        async with SessionLocal() as s:
            evs = (await s.execute(select(TaskEvent).where(TaskEvent.task_id == t0.id)
                                   .order_by(TaskEvent.seq))).scalars().all()
        assert [(e.type, e.payload) for e in evs] == [
            ("error", {"message": "outputs invalid: no ```json block at the end of the result"}),
            ("completed", {"status": "failed"}),
        ]
        assert evs[0].seq < evs[1].seq

    asyncio.run(go())


def test_runner_emit_failure_does_not_block(no_tick, repo):
    class BadEmit(FakeWorker):
        async def emit_offline(self, *a):
            raise RuntimeError("boom")

    async def go():
        pid, (lead, dev) = await _setup(repo)
        rid = await _make_run(_stages(lead, dev), pid)
        r = WorkflowRunner(BadEmit(), 999)
        await r.start_run(rid)
        await _finish(rid, 0, "no block")
        await r._advance(rid)
        assert (await _run(rid)).status == "failed"

    asyncio.run(go())


def test_unknown_agent_second_item_persists_stage_state(no_tick, repo):
    async def go():
        pid, (lead, dev) = await _setup(repo)
        rid = await _make_run([{"name": "Dev", "items": [
            {"agent": dev, "prompt": "x"},
            {"agent": "no-such-agent-" + uuid.uuid4().hex[:8], "prompt": "y"},
        ]}], pid)
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        run = await _run(rid)
        assert run.status == "failed"
        st = run.stage_state["0"]
        assert st["status"] == "failed" and st["reason"].startswith("unknown agent no-such-agent-")

    asyncio.run(go())
