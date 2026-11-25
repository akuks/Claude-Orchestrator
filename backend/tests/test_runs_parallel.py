"""Phase 7b: a run starts every step whose waited-for steps have finished, up to max_parallel.
Built on the fake task/gate machinery of test_gates_runner (no real command, no real CLI)."""

import asyncio
import dataclasses
from datetime import datetime, timezone

from app.constants import Status
from app.database import SessionLocal
from app.models import Task
from app import workflow_runner as wr
from app.workflow_runner import WorkflowRunner
from _runs7a import (  # noqa: F401 - fixtures
    ANN, FAIL, FakeWorker, _end_runs, _finish_tasks, _project, _rows, _run, _tasks, agent, agents, approval,
    fakes, gate, has, lock_owners, make_run, oc, settle, texts, wait_for,
)


def after(stage, *deps):
    return {**stage, "after": list(deps)}


def diamond(a, b, mp=None):
    """A -> (B, C) -> D: B waits for A by default, C says so, D waits for both."""
    st = [agent("A", a), agent("B", b, "b {{prev}}"), after(agent("C", a, "c {{prev}}"), 0),
          after(agent("D", b, "join: {{prev}}"), 1, 2)]
    if mp is not None:
        st[0]["max_parallel"] = mp
    return st


async def calm(r):
    """Let every gate task finish, including the work it does after leaving `_gates`."""
    await settle(r)
    for _ in range(20):
        await asyncio.sleep(0.01)
    await settle(r)


async def started(rid) -> list[int]:
    return sorted({t.stage_index for t in await _tasks(rid)})


def test_diamond_runs_branches_together_and_the_join_once(fakes, agents):
    a, b = agents

    async def go():
        rid = await make_run(diamond(a, b))
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        assert await started(rid) == [0]
        await _finish_tasks(rid, 0, text="a out")
        await r._advance(rid)
        assert await started(rid) == [0, 1, 2]  # B and C side by side
        run = await _run(rid)
        assert run.status == "running" and run.current_stage == 1
        assert {run.stage_state["1"]["status"], run.stage_state["2"]["status"]} == {"running"}
        assert "a out" in (await _tasks(rid, 2))[0].prompt

        await _finish_tasks(rid, 1, text="b out")
        await r._advance(rid)
        run = await _run(rid)
        assert await started(rid) == [0, 1, 2]  # D still waits for C
        assert run.stage_state["1"]["status"] == "done" and run.current_stage == 2

        # C finishes and two advances race: the join starts exactly once.
        await _finish_tasks(rid, 2, text="c out")
        await asyncio.gather(r._advance(rid), r._advance(rid), r._advance(rid))
        d = await _tasks(rid, 3)
        assert len(d) == 1
        assert "From B:\nb out" in d[0].prompt and "From C:\nc out" in d[0].prompt
        await _finish_tasks(rid, 3)
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "completed"
        assert [x for x in texts(run) if " done." in x][0].startswith("Step A done.")
        assert sum(1 for x in texts(run) if x.startswith("Step D done.")) == 1

    asyncio.run(go())


def test_two_gates_passing_together_start_the_join_once(fakes, agents):
    a, b = agents

    async def go():
        p1, p2 = await _project(), await _project()
        rid = await make_run([agent("A", a), gate(p1, run=("one",), name="G1"),
                              after(gate(p2, run=("two",), name="G2"), 0),
                              after(agent("D", b, "join {{prev}}"), 1, 2)], [p1, p2])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        await calm(r)
        run = await _run(rid)
        assert run.stage_state["1"]["status"] == "passed" and run.stage_state["2"]["status"] == "passed"
        assert len(await _tasks(rid, 3)) == 1
        d = (await _tasks(rid, 3))[0]
        assert "From G1:\nGate G1 passed" in d.prompt and "From G2:\nGate G2 passed" in d.prompt
        assert sorted(fakes.calls) == ["one", "two"]

    asyncio.run(go())


def test_max_parallel_one_runs_one_at_a_time(fakes, agents):
    """B, C and an approval are all ready after A: with one place only B starts, the approval
    (which needs no place) waits for a person at once, C waits for B."""
    a, b = agents

    async def go():
        st = [agent("A", a), agent("B", b), after(approval("Sign", ahead=None), 0), after(agent("C", a), 0)]
        st[0]["max_parallel"] = 1
        rid = await make_run(st)
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        run = await _run(rid)
        assert await started(rid) == [0, 1]
        assert run.stage_state["2"]["status"] == "waiting_approval" and "3" not in run.stage_state
        await r._advance(rid)
        assert await started(rid) == [0, 1]
        await _finish_tasks(rid, 1)
        await r._advance(rid)
        assert await started(rid) == [0, 1, 3]

    asyncio.run(go())


def test_never_more_than_max_parallel(fakes, agents):
    a, b = agents

    async def go():
        st = [agent("A", a)] + [after(agent(f"S{k}", b), 0) for k in range(1, 5)]
        rid = await make_run(st)
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        assert await started(rid) == [0, 1, 2]  # default 2
        await _finish_tasks(rid, 2)
        await r._advance(rid)
        assert await started(rid) == [0, 1, 2, 3]

    asyncio.run(go())


def test_approval_on_one_branch_while_the_other_runs(fakes, agents):
    a, b = agents

    async def go():
        rid = await make_run([agent("A", a), agent("C", b), after(approval("Sign", ahead=None), 0),
                              after(agent("D", a), 1, 2)])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        run = await _run(rid)
        assert run.stage_state["2"]["status"] == "waiting_approval"
        assert run.status == "running"  # C still works: not "waiting for you"
        await r._advance(rid)
        assert (await _run(rid)).status == "running"

        await _finish_tasks(rid, 1)
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "paused" and run.current_stage == 2  # nothing else can run now
        assert await started(rid) == [0, 1]

        await r.approve_stage(rid, 2, ANN)
        run = await _run(rid)
        assert run.status == "running" and await started(rid) == [0, 1, 3]

    asyncio.run(go())


def test_approval_on_a_branch_can_be_decided_while_a_lower_step_runs(fakes, agents):
    a, b = agents

    async def go():
        rid = await make_run([agent("A", a), agent("C", b), after(approval("Sign", ahead=None), 0),
                              after(agent("D", a), 1, 2)])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        await r.approve_stage(rid, 2, ANN)  # step 3 while step 2 (the current one) runs
        run = await _run(rid)
        assert run.stage_state["2"]["status"] == "approved" and run.status == "running"
        assert await started(rid) == [0, 1]
        await _finish_tasks(rid, 1)
        await r._advance(rid)
        assert await started(rid) == [0, 1, 3]

    asyncio.run(go())


def test_a_task_approval_on_one_branch_does_not_pause_the_other(fakes, agents):
    a, b = agents

    async def go():
        rid = await make_run(diamond(a, b))
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        await _finish_tasks(rid, 1, Status.AWAITING_APPROVAL, "")
        await r._advance(rid)
        assert (await _run(rid)).status == "running"
        await _finish_tasks(rid, 2)
        await r._advance(rid)
        assert (await _run(rid)).status == "paused"

    asyncio.run(go())


def test_failure_stops_the_other_branches_and_retry_continues(fakes, agents):
    a, b = agents
    fakes.script["slow"] = ["block", oc()]

    async def go():
        pid = await _project()
        st = [agent("A", a), agent("B", b), after(agent("C", a), 0),
              after(gate(pid, run=("slow",), name="E"), 0)]
        st[0]["max_parallel"] = 3
        rid = await make_run(st, [pid])
        w = FakeWorker()
        r = WorkflowRunner(w, 999)
        await r.start_run(rid)
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        await wait_for(lambda: (rid, 3) in r._procs)
        assert lock_owners(rid)
        c_task = (await _tasks(rid, 2))[0].id

        await _finish_tasks(rid, 1, Status.FAILED, "boom")
        await r._advance(rid)
        await calm(r)
        run = await _run(rid)
        assert run.status == "failed"
        assert run.stage_state["1"]["status"] == "failed"
        assert run.stage_state["3"]["status"] == "failed" and run.stage_state["3"]["reason"] == "cancelled"
        assert has(run, "Step E (gate) still needs: slow")
        assert c_task in w.cancelled  # the other agent branch is cancelled like Cancel does
        assert [x.outcome for x in await _rows(rid)] == ["cancelled"]
        assert lock_owners(rid) == [] and (rid, 3) not in r._gates

        assert await r.retry_run(rid)
        await calm(r)
        run = await _run(rid)
        assert run.status == "running", texts(run)
        for i in (1, 2, 3):
            assert run.stage_state[str(i)]["attempt"] == 1
        assert has(run, "Step B retried (attempt 2)") and has(run, "Step C retried (attempt 2)")
        assert run.stage_state["3"]["status"] == "passed"
        assert sorted(t.stage_attempt for t in await _tasks(rid, 1)) == [0, 1]
        assert sorted(t.stage_attempt for t in await _tasks(rid, 2)) == [0, 1]
        await _finish_tasks(rid, 1)
        await _finish_tasks(rid, 2)
        await r._advance(rid)
        assert (await _run(rid)).status == "completed"

    asyncio.run(go())


def test_a_gate_failing_stops_the_agent_branch(fakes, agents):
    a, b = agents
    fakes.script["check"] = [FAIL("t")]

    async def go():
        pid = await _project()
        rid = await make_run([agent("A", a), agent("B", b), after(gate(pid, run=("check",), name="G"), 0)], [pid])
        w = FakeWorker()
        r = WorkflowRunner(w, 999)
        await r.start_run(rid)
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        await calm(r)
        run = await _run(rid)
        assert run.status == "failed" and run.stage_state["2"]["status"] == "failed"
        assert (await _tasks(rid, 1))[0].id in w.cancelled

    asyncio.run(go())


def test_run_again_from_a_branch_resets_it_and_its_dependants_only(fakes, agents):
    a, b = agents

    async def go():
        rid = await make_run(diamond(a, b))
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        for i in range(3):
            await _finish_tasks(rid, i, text=f"out{i}")
            await r._advance(rid)
        await _finish_tasks(rid, 3)
        await r._advance(rid)
        assert (await _run(rid)).status == "completed"

        await r.rerun_from(rid, 1, ANN)
        run = await _run(rid)
        assert run.status == "running" and run.current_stage == 1
        assert run.stage_state["1"]["attempt"] == 1 and run.stage_state["1"]["status"] == "running"
        assert run.stage_state["3"] == {"attempt": 1}  # D depends on B: reset
        assert run.stage_state["2"]["status"] == "done" and run.stage_state["2"].get("attempt", 0) == 0
        assert len(await _tasks(rid, 2)) == 1  # C keeps its result
        await _finish_tasks(rid, 1, text="new b")
        await r._advance(rid)
        d = [t for t in await _tasks(rid, 3) if t.stage_attempt == 1]
        assert len(d) == 1 and "new b" in d[0].prompt and "out2" in d[0].prompt

    asyncio.run(go())


def test_run_again_from_a_branch_of_a_failed_run_resets_the_failed_branch_too(fakes, agents):
    a, b = agents

    async def go():
        rid = await make_run(diamond(a, b))
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        await _finish_tasks(rid, 1)
        await r._advance(rid)
        await _finish_tasks(rid, 2, Status.FAILED)
        await r._advance(rid)
        assert (await _run(rid)).status == "failed"
        await r.rerun_from(rid, 1, ANN)
        run = await _run(rid)
        assert run.status == "running"
        assert run.stage_state["1"]["status"] == "running" and run.stage_state["2"]["status"] == "running"
        assert run.stage_state["2"]["attempt"] == 1

    asyncio.run(go())


def test_restart_recovers_two_running_gates(fakes, agents):
    a, b = agents

    async def go():
        p1, p2 = await _project(), await _project()
        now = datetime.now(timezone.utc).isoformat()

        def live(h):
            return {"status": "running", "handout": h, "attempt": 0, "round": "full", "passed_cmds": [],
                    "allowed": False, "hits": [], "started_at": now, "log_seq": 1}

        rid = await make_run([agent("A", a), gate(p1, run=("one",), name="G1"),
                              after(gate(p2, run=("two",), name="G2"), 0), after(agent("D", b), 1, 2)],
                             [p1, p2], status="running", current=1,
                             state={"0": {"status": "done"}, "1": live("h1"), "2": live("h2")})
        r = WorkflowRunner(FakeWorker(), 999)  # a fresh process: no live gate
        await r._advance(rid)
        await calm(r)
        run = await _run(rid)
        assert has(run, "Step G1 (gate) resumed after a server restart")
        assert has(run, "Step G2 (gate) resumed after a server restart")
        assert run.stage_state["1"]["status"] == "passed" and run.stage_state["2"]["status"] == "passed"
        assert len(await _tasks(rid, 3)) == 1

    asyncio.run(go())


def test_budget_holds_two_ready_steps_then_both_start(fakes, agents):
    a, b = agents

    async def go():
        rid = await make_run(diamond(a, b), budget=1.0)
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        async with SessionLocal() as s:
            t = (await _tasks(rid, 0))[0]
            t = await s.get(Task, t.id)
            t.status, t.result_text, t.total_cost_usd = Status.COMPLETED, "x", 1.0
            await s.commit()
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "paused"
        assert run.stage_state["1"]["status"] == "budget" and run.stage_state["2"]["status"] == "budget"
        assert has(run, "Step B held: budget reached") and has(run, "Step C held: budget reached")
        assert await started(rid) == [0]
        await r.raise_budget(rid, 5.0, ANN)
        run = await _run(rid)
        assert run.status == "running" and await started(rid) == [0, 1, 2]

    asyncio.run(go())


def test_approve_ahead_on_a_branch_approval(fakes, agents):
    a, b = agents

    async def go():
        rid = await make_run([agent("A", a), agent("B", b), after(approval("Sign"), 0),
                              after(agent("D", a), 1, 2)])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await r.approve_ahead(rid, 2, ANN)
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        run = await _run(rid)
        assert run.stage_state["2"]["status"] == "approved" and has(run, "Sign: approved ahead by Ann")
        assert run.status == "running" and await started(rid) == [0, 1]
        await _finish_tasks(rid, 1)
        await r._advance(rid)
        assert await started(rid) == [0, 1, 3]

    asyncio.run(go())


def test_old_line_run_prev_reaches_back_past_a_gate_without_summary(fakes, agents):
    """Review Focus 1: an old snapshot (no `after`) keeps the old {{prev}}: a gate with no
    summary gives way to the highest earlier output, exactly as before 7b."""
    a, b = agents

    async def go():
        pid = await _project()
        rid = await make_run([agent("Dev", a), gate(pid), agent("Review", b, "review: {{prev}}")], [pid],
                             status="running", current=1,
                             state={"0": {"status": "done"}, "1": {"status": "passed"}})
        async with SessionLocal() as s:
            s.add(Task(title="dev", prompt="x", status=Status.COMPLETED, workflow_run_id=rid, stage_index=0,
                       stage_attempt=0, result_text="first out"))
            await s.commit()
        r = WorkflowRunner(FakeWorker(), 999)
        await r._advance(rid)
        t = await _tasks(rid, 2)
        assert len(t) == 1 and t[0].prompt == "review: first out"
        assert (await _run(rid)).current_stage == 2

    asyncio.run(go())


def test_old_line_run_keeps_order_and_lines(fakes, agents):
    """Review Focus 1: a plain line run starts one step at a time, in order, with the same lines."""
    a, b = agents

    async def go():
        rid = await make_run([agent("One", a), agent("Two", b, "two {{prev}}"), agent("Three", a)])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        for i in range(3):
            assert await started(rid) == list(range(i + 1))
            run = await _run(rid)
            assert run.current_stage == i and run.status == "running"
            await _finish_tasks(rid, i, text=f"o{i}")
            await r._advance(rid)
        run = await _run(rid)
        assert run.status == "completed"
        assert [x.split(".")[0] for x in texts(run)] == ["Step One done", "Step Two done", "Step Three done"]
        assert (await _tasks(rid, 1))[0].prompt == "two o0"

    asyncio.run(go())


def test_live_gates_lists_every_running_gate(fakes, agents):
    a, _ = agents
    fakes.script["one"] = ["block"]
    fakes.script["two"] = ["block"]

    async def go():
        p1, p2 = await _project(), await _project()
        rid = await make_run([agent("A", a), gate(p1, run=("one",), name="G1"),
                              after(gate(p2, run=("two",), name="G2"), 0)], [p1, p2])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        await wait_for(lambda: (rid, 1) in r._procs and (rid, 2) in r._procs)
        assert [g["stage_index"] for g in r.live_gates(rid)] == [1, 2]
        assert r.live_gate(rid)["stage_index"] == 1
        assert await r.cancel_run(rid)
        run = await _run(rid)
        assert has(run, "Step G1 (gate) still needs: one") and has(run, "Step G2 (gate) still needs: two")
        assert lock_owners(rid) == []
        assert wr._IN_FLIGHT.isdisjoint({(rid, 1), (rid, 2)})

    asyncio.run(go())


def test_run_view_shows_every_running_gate(client, fakes, agents, monkeypatch):
    from _runs7a import use_runner

    a, _ = agents
    fakes.script["one"] = ["block"]
    fakes.script["two"] = ["block"]
    r = WorkflowRunner(FakeWorker(), 999)
    use_runner(monkeypatch, r)

    async def go():
        p1, p2 = await _project(), await _project()
        rid = await make_run([agent("A", a), gate(p1, run=("one",), name="G1"),
                              after(gate(p2, run=("two",), name="G2"), 0)], [p1, p2])
        await r.start_run(rid)
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        await wait_for(lambda: (rid, 1) in r._procs and (rid, 2) in r._procs)
        return rid

    loop = asyncio.new_event_loop()
    try:
        rid = loop.run_until_complete(go())
        d = client.get(f"/workflows/runs/{rid}").json()
        for k, cmd in (("1", "one"), ("2", "two")):
            st = d["stage_state"][k]
            assert st["status_word"] == "Running"
            assert st["status_text"].startswith(f"running `{cmd}`")
        assert d["live"]["stage_index"] == 1
        live = client.get("/workflows/runs/live").json()
        mine = next(x for x in live if x["id"] == rid)
        assert [s["state"] for s in mine["stages"]][1:] == ["current", "current"]
        loop.run_until_complete(r.cancel_run(rid))
    finally:
        loop.close()


async def _cost(rid, stage, cost, status=Status.COMPLETED):
    async with SessionLocal() as s:
        for t in await _tasks(rid, stage):
            t = await s.get(Task, t.id)
            t.status, t.result_text, t.total_cost_usd = status, "x", cost
        await s.commit()


def test_branches_together_never_pass_the_spending_limit(fakes, agents):
    a, b = agents

    async def go():
        rid = await make_run([agent("A", a), agent("B", b), after(agent("C", a), 0)], budget=3.0)
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _cost(rid, 0, 1.5)  # $1.50 left
        await r._advance(rid)
        run = await _run(rid)
        assert [t.max_budget_usd for t in await _tasks(rid, 1)] == [1.5]
        assert await started(rid) == [0, 1]
        assert run.stage_state["2"]["status"] == "budget" and run.stage_state["2"]["reason"] == wr.RESERVE_WORDS
        assert run.status == "running"  # B still works
        await _cost(rid, 1, 0.5)
        await r._advance(rid)
        assert [t.max_budget_usd for t in await _tasks(rid, 2)] == [1.0]

    asyncio.run(go())


def test_a_step_started_later_counts_what_running_steps_may_spend(fakes, agents):
    a, b = agents

    async def go():
        # B and X after A; C after X starts while B still runs.
        rid = await make_run([agent("A", a), agent("B", b), after(agent("X", a), 0), agent("C", a)], budget=4.5)
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _cost(rid, 0, 1.5)  # $3 left
        await r._advance(rid)
        caps = {t.stage_index: t.max_budget_usd for t in await _tasks(rid)}
        assert caps[1] == 2.0 and caps[2] == 1.0
        await _cost(rid, 2, 0.5)  # $2.50 left, B may still spend $2
        await r._advance(rid)
        caps = {t.stage_index: t.max_budget_usd for t in await _tasks(rid)}
        assert caps[3] == 0.5

    asyncio.run(go())


def test_line_run_keeps_the_old_budget_caps(fakes, agents):
    a, b = agents

    async def go():
        rid = await make_run([agent("A", a), {"name": "B", "items": [{"agent": b, "prompt": "x"},
                                                                       {"agent": a, "prompt": "y"}]}], budget=3.0)
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _cost(rid, 0, 1.5)
        await r._advance(rid)
        assert [t.max_budget_usd for t in await _tasks(rid, 1)] == [1.5, 1.5]  # as before 7b

    asyncio.run(go())


def test_agent_step_with_tasks_but_no_status_is_not_started_again(fakes, agents):
    """A run active from before agent steps got a status: its running step is watched."""
    a, b = agents

    async def go():
        rid = await make_run([agent("A", a), agent("B", b)], status="running", current=0, state={})
        async with SessionLocal() as s:
            s.add(Task(title="a", prompt="x", status=Status.RUNNING, workflow_run_id=rid, stage_index=0,
                       stage_attempt=0))
            await s.commit()
        r = WorkflowRunner(FakeWorker(), 999)
        await r._advance(rid)
        assert len(await _tasks(rid, 0)) == 1
        run = await _run(rid)
        assert run.stage_state["0"]["status"] == "running" and run.status == "running"
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        assert await started(rid) == [0, 1] and len(await _tasks(rid, 0)) == 1

    asyncio.run(go())


def test_a_gate_waiting_for_allow_frees_its_place(fakes, agents):
    a, b = agents
    fakes.guard["risky"] = ("approve", ["why"])

    async def go():
        p = await _project()
        st = [agent("A", a), gate(p, run=("risky",), name="G"), after(agent("C", b), 0)]
        st[0]["max_parallel"] = 1
        rid = await make_run(st, [p])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        await calm(r)
        assert (await _run(rid)).stage_state["1"]["status"] == "needs_approval"
        await r._advance(rid)  # the next tick: the place is free
        run = await _run(rid)
        assert await started(rid) == [0, 2] and run.status == "running"
        await r.allow_gate(rid, 1, ANN)  # C holds the only place: G waits for it
        await calm(r)
        run = await _run(rid)
        assert run.stage_state["1"]["status"] == "waiting" and run.stage_state["1"]["slot_wait"]
        assert "risky" not in fakes.calls
        await _finish_tasks(rid, 2)
        await r._advance(rid)
        await calm(r)
        run = await _run(rid)
        assert run.stage_state["1"]["status"] == "passed" and run.status == "completed"

    asyncio.run(go())


def test_only_gates_waiting_for_allow_pause_the_run(fakes, agents):
    a, _ = agents
    fakes.guard["risky"] = ("approve", ["why"])

    async def go():
        p = await _project()
        rid = await make_run([agent("A", a), gate(p, run=("risky",), name="G"),
                              after(agent("C", a), 0)], [p])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        await calm(r)
        await r._advance(rid)
        assert (await _run(rid)).status == "running"
        await _finish_tasks(rid, 2)
        await r._advance(rid)
        assert (await _run(rid)).status == "paused"

    asyncio.run(go())


def test_two_gates_of_one_run_in_one_folder_take_turns(fakes, agents, monkeypatch):
    from app.gates import executor

    a, b = agents
    release = {}
    plain = fakes.run_command

    async def run_command(cmd, **kw):
        if cmd in release:
            fakes.calls.append(cmd)
            await release[cmd].wait()
            return dataclasses.replace(oc(), started_at=datetime.now(timezone.utc),
                                       finished_at=datetime.now(timezone.utc))
        return await plain(cmd, **kw)

    monkeypatch.setattr(executor, "run_command", run_command)

    async def go():
        release.update(one=asyncio.Event(), two=asyncio.Event())
        p = await _project()
        rid = await make_run([agent("A", a), gate(p, run=("one",), name="G1"),
                              after(gate(p, run=("two",), name="G2"), 0), after(agent("D", b), 1, 2)], [p])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        # Whichever gate takes the folder first runs; the other waits its turn.
        await wait_for(lambda: len(r._procs) == 1 and len([t for t in r._gates.values() if not t.done()]) == 1)
        (_, first), = r._procs.keys()
        other = 3 - first
        run = await _run(rid)
        assert run.stage_state[str(other)]["status"] == "held" and run.stage_state[str(other)]["lock_wait"]
        assert len(fakes.calls) == 1
        release["one"].set()
        release["two"].set()  # the first command ends; its own advance hands the other out
        await calm(r)
        run = await _run(rid)
        assert run.stage_state["1"]["status"] == "passed" and run.stage_state["2"]["status"] == "passed"
        assert len(await _tasks(rid, 3)) == 1 and lock_owners(rid) == []

    asyncio.run(go())


def test_auto_retry_on_a_branch_leaves_the_other_running(fakes, agents):
    a, b = agents

    async def go():
        st = [agent("A", a), {**agent("B", b), "auto_retry": True}, after(agent("C", a), 0),
              after(agent("D", b), 1, 2)]
        rid = await make_run(st)
        w = FakeWorker()
        r = WorkflowRunner(w, 999)
        await r.start_run(rid)
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        await _finish_tasks(rid, 1, Status.FAILED)
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "running" and run.stage_state["1"]["attempt"] == 1
        assert run.stage_state["1"]["status"] == "running" and w.cancelled == []
        await _finish_tasks(rid, 1)
        await _finish_tasks(rid, 2)
        await r._advance(rid)
        assert len(await _tasks(rid, 3)) == 1

    asyncio.run(go())


def test_typed_outputs_failing_on_a_branch_stops_the_other(fakes, agents):
    a, b = agents

    async def go():
        st = [agent("A", a), agent("B", b), after(agent("C", a), 0)]
        st[1]["items"][0]["outputs"] = ["result"]
        rid = await make_run(st)
        w = FakeWorker()
        r = WorkflowRunner(w, 999)
        await r.start_run(rid)
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        await _finish_tasks(rid, 1, text="no outputs block here")
        await r._advance(rid)
        assert (await _run(rid)).status == "failed"
        assert (await _tasks(rid, 2))[0].id in w.cancelled

    asyncio.run(go())


def test_retry_gives_a_new_attempt_only_to_steps_that_started(fakes, agents):
    a, b = agents

    async def go():
        rid = await make_run(diamond(a, b), budget=3.0)
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _cost(rid, 0, 1.5)
        await r._advance(rid)  # B starts, C held for the limit
        await _finish_tasks(rid, 1, Status.FAILED)
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "failed" and run.stage_state["2"]["status"] == "budget"
        assert await r.retry_run(rid)
        run = await _run(rid)
        assert has(run, "Step B retried (attempt 2)") and not has(run, "Step C retried")
        assert run.stage_state["2"].get("attempt", 0) == 0

    asyncio.run(go())


def test_run_again_keeps_a_branch_that_finished_as_another_failed(fakes, agents):
    a, b = agents

    async def go():
        rid = await make_run(diamond(a, b))
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        await _finish_tasks(rid, 2, text="c out")  # C completes, not yet marked done
        await _finish_tasks(rid, 1, Status.FAILED)
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "failed" and run.stage_state["2"]["status"] == "running"
        await r.rerun_from(rid, 1, ANN)
        run = await _run(rid)
        assert run.stage_state["2"]["status"] == "done" and run.stage_state["2"].get("attempt", 0) == 0
        assert len(await _tasks(rid, 2)) == 1

    asyncio.run(go())


def test_a_failing_pass_still_hands_out_what_it_started(fakes, agents):
    a, b = agents

    async def go():
        st = [agent("A", a), agent("B", b), after(agent("Bad", "no-such-agent"), 0)]
        rid = await make_run(st)
        w = FakeWorker()
        r = WorkflowRunner(w, 999)
        await r.start_run(rid)
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "failed" and has(run, "unknown agent no-such-agent")
        b_task = (await _tasks(rid, 1))[0].id
        assert b_task in w.submitted and b_task in w.cancelled

    asyncio.run(go())


def test_completed_run_points_at_the_last_step(fakes, agents):
    a, b = agents

    async def go():
        rid = await make_run([agent("A", a), after(agent("B", b), )])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        assert await started(rid) == [0, 1]
        await _finish_tasks(rid, 1)
        await r._advance(rid)
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        run = await _run(rid)
        assert run.status == "completed" and run.current_stage == 1

    asyncio.run(go())


async def _fixer_agent(pid) -> str:
    from app.models import Agent

    async with SessionLocal() as s:
        fx = Agent(name="r7b-fix-" + pid[:8], system_prompt="x", default_prompt="x", project_id=pid, tags=[])
        s.add(fx)
        await s.commit()
        return fx.id


def test_fix_task_waits_for_the_branch_caps_and_the_run_stays_running(fakes, agents):
    a, b = agents
    fakes.script["chk"] = [FAIL("t"), oc()]

    async def go():
        p = await _project()
        g = gate(p, run=("chk",), name="G")
        g["fix"] = {"agent": await _fixer_agent(p), "rounds": 1}
        rid = await make_run([agent("A", a), agent("B", b), after(g, 0)], [p], budget=3.0)
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _cost(rid, 0, 1.5)  # $1.50 left; B may spend all of it
        await r._advance(rid)
        await calm(r)
        await r._advance(rid)
        run = await _run(rid)
        st = run.stage_state["2"]
        assert st["status"] == "budget" and st["reason"] == wr.RESERVE_WORDS and st["fix_pending"] == 1
        assert run.status == "running"  # B works: not "waiting for you"
        assert [t.stage_index for t in await _tasks(rid)] == [0, 1]
        await _cost(rid, 1, 0.5)
        await r._advance(rid)
        run = await _run(rid)
        assert run.stage_state["2"]["status"] == "fixing" and run.status == "running"
        assert [t.max_budget_usd for t in await _tasks(rid, 2)] == [1.0]

    asyncio.run(go())


def test_run_view_says_waiting_its_turn_for_an_allowed_gate_without_a_place(client, fakes, agents, monkeypatch):
    from _runs7a import use_runner

    a, b = agents
    fakes.guard["risky"] = ("approve", ["why"])
    r = WorkflowRunner(FakeWorker(), 999)
    use_runner(monkeypatch, r)

    async def go():
        p = await _project()
        st = [agent("A", a), gate(p, run=("risky",), name="G"), after(agent("C", b), 0)]
        st[0]["max_parallel"] = 1
        rid = await make_run(st, [p])
        await r.start_run(rid)
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        await calm(r)
        await r._advance(rid)
        await r.allow_gate(rid, 1, ANN)
        await calm(r)
        return rid

    loop = asyncio.new_event_loop()
    try:
        rid = loop.run_until_complete(go())
        st = client.get(f"/workflows/runs/{rid}").json()["stage_state"]["1"]
        assert st["status"] == "waiting" and st["status_word"] == "Waiting its turn"
        assert "slot_wait" not in st and "_slot_wait" not in st
        loop.run_until_complete(r.cancel_run(rid))
    finally:
        loop.close()


def test_a_killed_gate_forgets_the_stop_request(fakes, agents):
    a, _ = agents
    fakes.script["one"] = ["block"]

    async def go():
        p = await _project()
        rid = await make_run([agent("A", a), gate(p, run=("one",), name="G1")], [p])
        r = WorkflowRunner(FakeWorker(), 999)
        await r.start_run(rid)
        await _finish_tasks(rid, 0)
        await r._advance(rid)
        await wait_for(lambda: (rid, 1) in r._procs)
        r._stop_after_gate.add(rid)
        await r._kill_gates(rid, record=False)
        assert rid not in r._stop_after_gate
        await r.cancel_run(rid)

    asyncio.run(go())
