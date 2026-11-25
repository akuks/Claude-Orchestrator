"""Phase 7a §2: one check at a time per project folder (F126). Gate stages of different runs
in one folder take turns; a held stage is never failed; the lock is let go on every exit."""

import asyncio
import os
import tempfile

import pytest

from app.database import SessionLocal
from app.models import Agent, Project, Task
from app.constants import Status
from app import workflow_runner as wr
from app.workflow_runner import LOCK_WORDS, WorkflowRunner
from _runs7a import (  # noqa: F401 - fixtures
    ANN, FAIL, FakeWorker, _end_runs, _project, _run, _tasks, agent, agents, fakes, gate, has, lock_owners,
    make_run, oc, settle, texts, use_runner, wait_for,
)


async def _dir(pid) -> str:
    async with SessionLocal() as s:
        return (await s.get(Project, pid)).directory


def test_second_run_waits_its_turn_then_runs(fakes):
    fakes.script["slow"] = ["block"]

    async def go():
        pid = await _project()
        r = WorkflowRunner(FakeWorker(), 999)
        a = await make_run([gate(pid, run=("slow",))], [pid])
        b = await make_run([gate(pid, run=("fast",))], [pid])
        await r.start_run(a)
        await wait_for(lambda: (a, 0) in r._procs)
        await r.start_run(b)
        await settle_b(r, b)
        run = await _run(b)
        st = run.stage_state["0"]
        assert run.status == "running" and st["status"] == "held" and st["reason"] == LOCK_WORDS
        assert fakes.calls == ["slow"]
        # Ticks while A still runs: B stays held, one log line only.
        await r._advance(b)
        await r._advance(b)
        run = await _run(b)
        assert run.stage_state["0"]["status"] == "held"
        assert texts(run).count("Step Test (gate) held: waiting for checks from another run in this project") == 1
        # A ends (cancelled here): the lock is free, B runs on the next tick and passes.
        assert await r.cancel_run(a)
        assert lock_owners(a) == []
        await r._advance(b)
        await until_status(b, "completed")
        run = await _run(b)
        assert fakes.calls == ["slow", "fast"]
        assert lock_owners(b) == []

    asyncio.run(go())


async def until_status(rid, want, timeout=5.0):
    """Poll the run until it has status `want` (a gate's own advance runs after it left _gates)."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while (await _run(rid)).status != want:
        assert loop.time() < end, f"run did not reach {want}"
        await asyncio.sleep(0.01)


async def settle_b(r, rid):
    """Wait until run rid's gate task (if any) is done."""
    await wait_for(lambda: (rid, 0) not in r._gates or r._gates[(rid, 0)].done())


def test_same_folder_through_a_symlink_is_one_lock(fakes):
    fakes.script["slow"] = ["block"]

    async def go():
        pid = await _project()
        real = await _dir(pid)
        link = os.path.join(tempfile.mkdtemp(prefix="r7a-link-"), "here")
        os.symlink(real, link)
        async with SessionLocal() as s:
            p2 = Project(name=f"r7a-{os.path.basename(link)}-{os.getpid()}-{id(link)}", slug=f"r7a{id(link)}",
                         directory=link)
            s.add(p2)
            await s.commit()
            pid2 = p2.id
        r = WorkflowRunner(FakeWorker(), 999)
        a = await make_run([gate(pid, run=("slow",))], [pid])
        b = await make_run([gate(pid2, run=("fast",))], [pid2])
        await r.start_run(a)
        await wait_for(lambda: (a, 0) in r._procs)
        await r.start_run(b)
        await settle_b(r, b)
        assert (await _run(b)).stage_state["0"]["status"] == "held"
        assert await r.cancel_run(a)
        await r.cancel_run(b)

    asyncio.run(go())


def test_different_folders_run_side_by_side(fakes):
    fakes.script["slow"] = ["block"]

    async def go():
        p1, p2 = await _project(), await _project()
        r = WorkflowRunner(FakeWorker(), 999)
        a = await make_run([gate(p1, run=("slow",))], [p1])
        b = await make_run([gate(p2, run=("slow",))], [p2])
        await r.start_run(a)
        await r.start_run(b)
        await wait_for(lambda: (a, 0) in r._procs and (b, 0) in r._procs)
        assert await r.cancel_run(a) and await r.cancel_run(b)
        assert lock_owners(a) == [] and lock_owners(b) == []

    asyncio.run(go())


# --- every exit lets go ------------------------------------------------------------------


async def _fixer(pid) -> str:
    async with SessionLocal() as s:
        a = Agent(name=f"r7a-fix-{os.urandom(4).hex()}", system_prompt="x", project_id=pid)
        s.add(a)
        await s.commit()
        return a.id


@pytest.mark.parametrize("how", ["passed", "failed", "guard_deny", "needs_approval", "error", "cancel",
                                 "fix_failed", "budget_during_fix"])
def test_every_exit_lets_go(fakes, how):
    cmd = "check"
    if how == "failed":
        fakes.script[cmd] = [FAIL()]
    elif how == "guard_deny":
        fakes.guard["second"] = ("deny", ["no"])
    elif how == "needs_approval":
        fakes.guard["second"] = ("approve", ["ask"])
    elif how == "error":
        fakes.script[cmd] = [RuntimeError("boom")]
    elif how == "cancel":
        fakes.script[cmd] = ["block"]
    elif how in ("fix_failed", "budget_during_fix"):
        fakes.script[cmd] = [FAIL(), oc()]

    async def go():
        pid = await _project()
        stage = gate(pid, run=(cmd, "second") if how in ("guard_deny", "needs_approval") else (cmd,))
        if how in ("fix_failed", "budget_during_fix"):
            stage["fix"] = {"agent": await _fixer(pid), "rounds": 1}
        rid = await make_run([stage], [pid], budget=1.0 if how == "budget_during_fix" else None)
        if how == "budget_during_fix":
            async with SessionLocal() as s:  # earlier spending used the whole limit
                s.add(Task(title="spent", prompt="x", status=Status.COMPLETED, workflow_run_id=rid,
                           stage_index=0, stage_attempt=0, total_cost_usd=1.0))
                await s.commit()
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        if how == "cancel":
            await wait_for(lambda: (rid, 0) in r._procs)
            assert lock_owners(rid)  # held while the command runs
            assert await r.cancel_run(rid)
        await settle(r)
        if how == "fix_failed":
            run = await _run(rid)
            assert run.stage_state["0"]["status"] == "fixing"
            assert lock_owners(rid), "the fix round keeps the folder"
            t = await _tasks(rid, 0)
            assert len(t) == 1  # the fix task
            async with SessionLocal() as s:
                (await s.get(Task, t[0].id)).status = Status.FAILED
                await s.commit()
            await r._advance(rid)
            assert (await _run(rid)).status == "failed"
        run = await _run(rid)
        want = {"passed": "passed", "failed": "failed", "guard_deny": "failed", "needs_approval": "needs_approval",
                "error": "failed", "cancel": "failed", "fix_failed": "failed", "budget_during_fix": "budget"}[how]
        assert run.stage_state["0"]["status"] == want, texts(run)
        assert lock_owners(rid) == [], how

    asyncio.run(go())


def test_needs_approval_then_allow_takes_the_lock_again(fakes):
    fakes.guard["second"] = ("approve", ["ask"])

    async def go():
        pid = await _project()
        rid = await make_run([gate(pid, run=("first", "second"))], [pid])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await settle(r)
        assert lock_owners(rid) == []
        await r.allow_gate(rid, 0, ANN)
        await until_status(rid, "completed")
        assert fakes.calls == ["first", "second"]
        assert lock_owners(rid) == []

    asyncio.run(go())


# --- restart -------------------------------------------------------------------------------


def test_restart_held_gate_takes_the_lock_again(fakes):
    """Locks start empty after a restart. A gate held for the lock before it is handed out
    again and takes the lock; a stale entry (an owner no longer checking) never blocks."""
    fakes.script["slow"] = ["block", oc()]

    async def go():
        pid = await _project()
        r = WorkflowRunner(FakeWorker(), 999)
        a = await make_run([gate(pid, run=("slow",))], [pid])
        b = await make_run([gate(pid, run=("fast",))], [pid])
        await r.start_run(a)
        await wait_for(lambda: (a, 0) in r._procs)
        await r.start_run(b)
        await settle_b(r, b)
        assert (await _run(b)).stage_state["0"]["status"] == "held"
        # The server dies: A's process and every in-memory table are gone.
        task = r._gates[(a, 0)]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        wr._PROJECT_LOCKS.clear()
        r2 = WorkflowRunner(FakeWorker(), 999)
        await r2._advance(b)  # B's tick first: the lock is free, B takes it and runs
        await until_status(b, "completed")
        await r2._advance(a)  # A resumes and takes the lock again
        await until_status(a, "completed")
        run = await _run(a)
        assert run.status == "completed" and has(run, "resumed after a server restart")
        assert lock_owners(a) == [] and lock_owners(b) == []

    asyncio.run(go())


def test_stale_owner_never_blocks(fakes):
    async def go():
        pid = await _project()
        gone = await make_run([gate(pid)], [pid], status="failed")
        wr._PROJECT_LOCKS[os.path.realpath(await _dir(pid))] = (gone, 0)
        rid = await make_run([gate(pid)], [pid])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await until_status(rid, "completed")
        assert lock_owners(gone) == [] and lock_owners(rid) == []

    asyncio.run(go())


def test_held_step_words_in_the_api(client, fakes, monkeypatch):
    fakes.script["slow"] = ["block"]
    r = WorkflowRunner(FakeWorker(), 999)
    use_runner(monkeypatch, r)

    async def go():
        pid = await _project()
        a = await make_run([gate(pid, run=("slow",))], [pid])
        b = await make_run([gate(pid, run=("fast",))], [pid])
        await r.start_run(a)
        await wait_for(lambda: (a, 0) in r._procs)
        await r.start_run(b)
        await settle_b(r, b)
        return a, b

    loop = asyncio.new_event_loop()
    try:
        a, b = loop.run_until_complete(go())
        d = client.get(f"/workflows/runs/{b}").json()
        st = d["stage_state"]["0"]
        assert st["status"] == "held" and st["reason"] == LOCK_WORDS
        assert st["status_word"] == "Waiting its turn" and "lock_wait" not in st
        da = client.get(f"/workflows/runs/{a}").json()
        assert da["stage_state"]["0"]["status_word"] == "Running"
        loop.run_until_complete(r.cancel_run(a))
        loop.run_until_complete(r.cancel_run(b))
    finally:
        loop.close()


# --- fix round 1 -----------------------------------------------------------------------------


def test_cancel_kill_window_keeps_the_folder(fakes):
    """While cancel_run is still killing A's process, B's held gate must not start (I-1)."""
    fakes.script["slow"] = ["block"]

    async def go():
        pid = await _project()
        r = WorkflowRunner(FakeWorker(), 999)
        a = await make_run([gate(pid, run=("slow",))], [pid])
        b = await make_run([gate(pid, run=("fast",))], [pid])
        await r.start_run(a)
        await wait_for(lambda: (a, 0) in r._procs)
        await r.start_run(b)
        await settle_b(r, b)
        assert (await _run(b)).stage_state["0"]["status"] == "held"
        seen = {}
        orig = r._kill_gates

        async def slow_kill(run_id, record):
            # A is committed failed but its process still runs: a tick for B happens now.
            assert (await _run(a)).status == "failed"
            await r._advance(b)
            await settle_b(r, b)
            seen["b_state"] = (await _run(b)).stage_state["0"]["status"]
            seen["b_ran"] = "fast" in fakes.calls
            await orig(run_id, record)

        r._kill_gates = slow_kill
        assert await r.cancel_run(a)
        assert seen == {"b_state": "held", "b_ran": False}
        assert (a, 0) not in wr._IN_FLIGHT
        await r._advance(b)  # A's process is gone: B runs now
        await until_status(b, "completed")

    asyncio.run(go())


def test_fix_after_budget_hold_takes_the_lock_again(fakes):
    """M-1: the fix round works in the folder, so after a budget hold (which lets the lock go)
    it takes the lock again before the fix task starts; busy -> held, then the fix starts."""
    fakes.script["check"] = [FAIL(), oc()]
    fakes.script["slow"] = ["block"]

    async def go():
        pid = await _project()
        stage = gate(pid, run=("check",))
        stage["fix"] = {"agent": await _fixer(pid), "rounds": 1}
        rid = await make_run([stage], [pid], budget=1.0)
        async with SessionLocal() as s:
            s.add(Task(title="spent", prompt="x", status=Status.COMPLETED, workflow_run_id=rid,
                       stage_index=0, stage_attempt=0, total_cost_usd=1.0))
            await s.commit()
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await settle(r)
        assert (await _run(rid)).stage_state["0"]["status"] == "budget" and lock_owners(rid) == []
        other = await make_run([gate(pid, run=("slow",))], [pid])  # another run's checks take the folder
        await r.start_run(other)
        await wait_for(lambda: (other, 0) in r._procs)
        await r.raise_budget(rid, 5.0, ANN)
        run = await _run(rid)
        st = run.stage_state["0"]
        assert st["status"] == "held" and st["reason"] == LOCK_WORDS and st["fix_pending"] == 1
        assert [t.title for t in await _tasks(rid, 0)] == ["spent"]  # no fix task yet
        assert await r.cancel_run(other)
        await r._advance(rid)
        run = await _run(rid)
        st = run.stage_state["0"]
        assert st["status"] == "fixing" and st.get("fix_task") and lock_owners(rid)
        assert "fix_pending" not in st and st["reason"] != LOCK_WORDS

    asyncio.run(go())


def test_preflight_hold_after_a_fix_round_keeps_the_turn(client):
    """The same gate stage continuing after a fix round and held for its pre-flight still owns the
    folder; a plain pre-flight hold (no fix round) does not."""
    async def go():
        pid = await _project()
        a = await make_run([gate(pid)], [pid], status="running",
                           state={"0": {"status": "held", "reason": "x", "fix_round": 1}})
        b = await make_run([gate(pid)], [pid], status="running", state={"0": {"status": "held", "reason": "x"}})
        async with SessionLocal() as s:
            assert await wr._owner_live(s, (a, 0)) is True
            assert await wr._owner_live(s, (b, 0)) is False

    asyncio.run(go())
