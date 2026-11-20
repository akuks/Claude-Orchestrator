"""A fresh workflow run can be advanced by the request path (start_run) and the
background tick at the same moment; stage 0 must be started exactly once."""

import asyncio
import uuid

from sqlalchemy import func, select

from app.database import SessionLocal
from app.main import app
from app.models import Task, WorkflowRun


def test_concurrent_start_creates_stage_once(client):
    tag = uuid.uuid4().hex[:8]
    for n in ("A", "B"):
        r = client.post("/agents", json={"name": f"race-{n}-{tag}", "system_prompt": "x", "default_prompt": "x"})
        assert r.status_code == 201, r.text
    wf = client.post("/workflows", json={
        "name": f"race-{tag}",
        "stages": [{"name": "s0", "items": [
            {"agent": f"race-A-{tag}", "prompt": "a {{input}}"},
            {"agent": f"race-B-{tag}", "prompt": "b {{input}}"},
        ]}],
    }).json()

    async def scenario():
        async with SessionLocal() as s:
            run = WorkflowRun(workflow_id=wf["id"], name=wf["name"], input="i",
                              stages=wf["stages"], status="pending", current_stage=0)
            s.add(run)
            await s.commit()
            run_id = run.id
        runner = app.state.workflow_runner
        await asyncio.gather(runner.start_run(run_id), runner._advance(run_id), runner._advance(run_id))
        async with SessionLocal() as s:
            return await s.scalar(
                select(func.count()).select_from(Task).where(
                    Task.workflow_run_id == run_id, Task.stage_index == 0
                )
            )

    assert asyncio.run(scenario()) == 2
