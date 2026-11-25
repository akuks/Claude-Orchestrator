"""Phase 7b Task 1: `after` / `max_parallel` through the API."""

import uuid

from app.database import SessionLocal
from app.models import Agent
import asyncio


def _agent():
    n = "af-" + uuid.uuid4().hex[:8]

    async def go():
        async with SessionLocal() as s:
            s.add(Agent(name=n, system_prompt="x"))
            await s.commit()
    asyncio.run(go())
    return n


def stage(a, name, **kw):
    return {"name": name, "items": [{"agent": a, "prompt": "p"}], **kw}


def test_create_keeps_after_and_max_parallel(client):
    a = _agent()
    body = {"name": "w", "max_parallel": 3,
            "stages": [stage(a, "A", after=[]), stage(a, "B", after=[0]), stage(a, "C", after=[0]), stage(a, "D", after=[1, 2])]}
    r = client.post("/workflows", json=body)
    assert r.status_code == 201, r.text
    j = r.json()
    assert [s.get("after") for s in j["stages"]] == [[], [0], [0], [1, 2]]
    assert j["max_parallel"] == 3
    # PATCH stages only keeps max_parallel; PATCH max_parallel only keeps stages
    r = client.patch(f"/workflows/{j['id']}", json={"stages": body["stages"][:2]})
    assert r.json()["max_parallel"] == 3 and len(r.json()["stages"]) == 2
    r = client.patch(f"/workflows/{j['id']}", json={"max_parallel": 1})
    assert r.status_code == 200 and r.json()["max_parallel"] == 1 and len(r.json()["stages"]) == 2


def test_default_max_parallel(client):
    a = _agent()
    r = client.post("/workflows", json={"name": "w", "stages": [stage(a, "A")]})
    assert r.status_code == 201 and r.json()["max_parallel"] == 2
    assert "after" not in r.json()["stages"][0]


def test_422_lists_every_problem(client):
    a = _agent()
    r = client.post("/workflows", json={"name": "w", "max_parallel": 9, "stages": [
        stage(a, "A", after=[0]), stage(a, "B", after=[0, 0]), stage(a, "C", after=[3]), stage(a, "D", after=[7])]})
    assert r.status_code == 422
    assert r.json()["detail"] == [
        "Run at most 1 to 4 steps at once.",
        "Step 1 waits for itself.", "Step 2 lists step 1 twice.",
        "Step 3 waits for step 4, which comes later.", "Step 4 waits for step 8, which does not exist."]


def test_other_errors_stay_strings(client):
    r = client.post("/workflows", json={"name": "w", "stages": [{"name": "A", "items": [{"agent": "nope", "prompt": "p"}]}]})
    assert r.status_code == 400 and isinstance(r.json()["detail"], str)
    r = client.post("/workflows", json={"name": "w", "stages": [{"name": "A", "kind": "gate"}]})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)


def test_max_parallel_lives_on_first_step_so_runs_snapshot_it(client):
    a = _agent()
    r = client.post("/workflows", json={"name": "w", "max_parallel": 4, "stages": [stage(a, "A", after=[]), stage(a, "B", after=[])]})
    from app import flowgraph as fg
    assert fg.get_max_parallel(r.json()["stages"]) == 4  # a run copies wf.stages, so the snapshot carries it


def test_export_import_round_trip(client):
    a = _agent()
    r = client.post("/workflows", json={"name": "rt", "max_parallel": 3,
                                        "stages": [stage(a, "A", after=[]), stage(a, "B", after=[]), stage(a, "C", after=[0, 1])]})
    wid = r.json()["id"]
    md = client.get(f"/workflows/{wid}/export").text
    assert '"max_parallel": 3' in md
    r = client.post("/workflows/import", json={"text": md})
    assert r.status_code == 201, r.text
    assert r.json()["max_parallel"] == 3 and [s.get("after") for s in r.json()["stages"]] == [[], [], [0, 1]]
