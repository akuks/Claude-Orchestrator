"""Phase 5c Task 1: validation of outputs, auto_retry, fix, budget_usd and raise_risk."""

import asyncio
import uuid

import pytest

from app.database import SessionLocal
from app.gates import spec
from app.main import app
from app.models import Agent, Project, WorkflowRun
from conftest import login_as


def run(coro):
    return asyncio.run(coro)


def mkproj():
    async def go():
        async with SessionLocal() as s:
            p = Project(name="s-" + uuid.uuid4().hex[:6], slug=uuid.uuid4().hex, directory="/tmp/x")
            s.add(p)
            await s.commit()
            return p.id
    return run(go())


def mkagent(project_id=None, name=None):
    async def go():
        async with SessionLocal() as s:
            a = Agent(name=name or "ag-" + uuid.uuid4().hex[:8], system_prompt="x", project_id=project_id)
            s.add(a)
            await s.commit()
            return a.id, a.name
    return run(go())


def validate(stages):
    async def go():
        async with SessionLocal() as s:
            return await spec.validate_stages(s, stages)
    return run(go())


def agent_stage(**kw):
    item = {"agent": "x", "prompt": "p", **kw.pop("item", {})}
    return {"name": "A", "items": [item], **kw}


def gate(pid, **kw):
    return {"name": "G", "kind": "gate", "project": pid, "run": ["npm test"], **kw}


def test_old_stages_round_trip_byte_identical(client):
    a = {"name": "A", "items": [{"agent": "x", "prompt": "p"}]}
    assert validate([a]) == [a]
    pid = mkproj()
    g = validate([gate(pid)])
    assert "fix" not in g[0] and "auto_retry" not in g[0]
    assert validate(g) == g
    assert validate([a, *g]) == [a, *g]


def test_outputs_normalized(client):
    st = validate([agent_stage(item={"outputs": ["plan_path", "head_commit"]})])[0]
    assert st["items"][0]["outputs"] == ["plan_path", "head_commit"]
    assert "outputs" not in validate([agent_stage(item={"outputs": []})])[0]["items"][0]
    assert "outputs" not in validate([agent_stage(item={"outputs": None})])[0]["items"][0]


@pytest.mark.parametrize("outs", [
    ["Bad"], ["a-b"], ["a" * 41], ["a", "a"], [f"o{i}" for i in range(11)], [1], "abc", ["1a"], [""],
])
def test_bad_outputs(client, outs):
    with pytest.raises(ValueError):
        validate([agent_stage(item={"outputs": outs})])


def test_outputs_limits_ok(client):
    ok = ["a" * 40] + [f"o{i}" for i in range(9)]
    assert validate([agent_stage(item={"outputs": ok})])[0]["items"][0]["outputs"] == ok


def test_auto_retry(client):
    assert validate([agent_stage(auto_retry=True)])[0]["auto_retry"] is True
    assert "auto_retry" not in validate([agent_stage(auto_retry=False)])[0]
    for bad in ("yes", 1, 0, []):
        with pytest.raises(ValueError, match="auto_retry"):
            validate([agent_stage(auto_retry=bad)])


def test_fix_ok_by_id_and_name(client):
    pid = mkproj()
    aid, aname = mkagent(pid)
    assert validate([gate(pid, fix={"agent": aid, "rounds": 2})])[0]["fix"] == {"agent": aid, "rounds": 2}
    # A name is accepted on input; the resolved id is what is stored.
    assert validate([gate(pid, fix={"agent": aname, "rounds": 3})])[0]["fix"] == {"agent": aid, "rounds": 3}
    assert validate([gate(pid, fix={"agent": aid})])[0]["fix"]["rounds"] == 1
    assert "fix" not in validate([gate(pid, fix=None)])[0]


@pytest.mark.parametrize("rounds", [0, 4, True, "2", 1.5])
def test_fix_rounds_rejected(client, rounds):
    pid = mkproj()
    aid, _ = mkagent(pid)
    with pytest.raises(ValueError, match="rounds"):
        validate([gate(pid, fix={"agent": aid, "rounds": rounds})])


def test_fix_name_resolved_inside_gate_project(client):
    """A same-named agent in another project neither blocks nor hijacks the gate's fixer."""
    pid, other = mkproj(), mkproj()
    name = "fx-same-" + uuid.uuid4().hex[:8]
    mine, _ = mkagent(pid, name)
    mkagent(other, name)
    assert validate([gate(pid, fix={"agent": name})])[0]["fix"] == {"agent": mine, "rounds": 1}
    with pytest.raises(ValueError, match="another project"):
        validate([gate(mkproj(), fix={"agent": name})])
    mkagent(pid, name)  # now two in the gate's own project
    with pytest.raises(ValueError, match="ambiguous"):
        validate([gate(pid, fix={"agent": name})])


def test_fix_agent_rejected(client):
    pid, other = mkproj(), mkproj()
    with pytest.raises(ValueError, match="unknown"):
        validate([gate(pid, fix={"agent": "nope-" + uuid.uuid4().hex, "rounds": 1})])
    noproj, _ = mkagent(None)
    with pytest.raises(ValueError, match="no project"):
        validate([gate(pid, fix={"agent": noproj, "rounds": 1})])
    foreign, _ = mkagent(other)
    with pytest.raises(ValueError, match="another project"):
        validate([gate(pid, fix={"agent": foreign, "rounds": 1})])
    for bad in ("x", ["a"], {"rounds": 1}, {"agent": 5}):
        with pytest.raises(ValueError):
            validate([gate(pid, fix=bad)])


def test_new_keys_dropped_on_wrong_kind(client):
    pid = mkproj()
    aid, _ = mkagent(pid)
    st = validate([{"name": "Ok", "kind": "approval", "fix": {"agent": aid}, "auto_retry": True}])[0]
    assert "fix" not in st and "auto_retry" not in st
    assert "fix" not in validate([agent_stage(fix={"agent": aid})])[0]


def _wf(client, **kw):
    return client.post("/workflows", json={"name": "w-" + uuid.uuid4().hex[:6],
                                           "stages": [{"name": "A", "items": [{"agent": _agent_name(client), "prompt": "p"}]}],
                                           **kw})


def _agent_name(client):
    return client.post("/agents", json={"name": "wa-" + uuid.uuid4().hex[:8], "system_prompt": "x"}).json()["name"]


@pytest.mark.parametrize("bad", [0, 0.001, 10001, "abc", "5", True, [1]])
def test_budget_rejected(client, bad):
    assert _wf(client, budget_usd=bad).status_code == 422


def test_budget_accepted_and_patched(client):
    r = _wf(client, budget_usd=12.5)
    assert r.status_code == 201 and r.json()["budget_usd"] == 12.5
    assert _wf(client, budget_usd=10000).status_code == 201
    assert _wf(client, budget_usd=0.01).status_code == 201
    assert _wf(client).json()["budget_usd"] is None
    wid = r.json()["id"]
    assert client.patch(f"/workflows/{wid}", json={"budget_usd": 0}).status_code == 422
    assert client.patch(f"/workflows/{wid}", json={"budget_usd": 20}).json()["budget_usd"] == 20
    assert client.patch(f"/workflows/{wid}", json={"name": "z"}).json()["budget_usd"] == 20
    assert client.patch(f"/workflows/{wid}", json={"budget_usd": None}).json()["budget_usd"] is None


def test_run_snapshots_budget(client, monkeypatch):
    class _R:
        async def start_run(self, run_id):
            return None

    monkeypatch.setitem(app.state._state, "workflow_runner", _R())
    wid = _wf(client, budget_usd=7.25).json()["id"]
    r = client.post(f"/workflows/{wid}/run", json={"input": ""})
    assert r.status_code == 201 and r.json()["budget_usd"] == 7.25
    client.patch(f"/workflows/{wid}", json={"budget_usd": 99})

    async def go():
        async with SessionLocal() as s:
            return (await s.get(WorkflowRun, r.json()["id"])).budget_usd
    assert run(go()) == 7.25
    wid2 = _wf(client).json()["id"]
    assert client.post(f"/workflows/{wid2}/run", json={"input": ""}).json()["budget_usd"] is None


def test_workflow_items_outputs_via_api(client):
    name = _agent_name(client)
    st = {"name": "A", "auto_retry": True, "items": [{"agent": name, "prompt": "p", "outputs": ["plan_path"]}]}
    r = client.post("/workflows", json={"name": "o-" + uuid.uuid4().hex[:6], "stages": [st]})
    assert r.status_code == 201, r.text
    assert r.json()["stages"] == [st]
    st["items"][0]["outputs"] = ["Bad"]
    assert client.post("/workflows", json={"name": "o2", "stages": [st]}).status_code == 422


def test_raise_risk_put(client):
    pid = client.post("/projects", json={"name": f"rr-{uuid.uuid4().hex[:6]}"}).json()["id"]
    maint = login_as(f"rm-{uuid.uuid4().hex[:6]}@test.local")
    contrib = login_as(f"rc-{uuid.uuid4().hex[:6]}@test.local")
    assert client.put(f"/projects/{pid}/members", json=[
        {"subject_type": "user", "subject_id": maint.user_id, "role": "maintainer"},
        {"subject_type": "user", "subject_id": contrib.user_id, "role": "contributor"}]).status_code == 200
    base = f"/projects/{pid}/launch"
    assert contrib.put(base, json={"raise_risk": True}).status_code == 403
    r = maint.put(base, json={"raise_risk": True})
    assert r.status_code == 200, r.text
    assert r.json()["launch"]["raise_risk"] is True
    ver = r.json()["launch"]["version"]
    r = maint.put(base, json={"version": ver, "critical_paths": ["a/*"]})  # omitted keeps it
    assert r.json()["launch"]["raise_risk"] is True
    r = maint.put(base, json={"version": r.json()["launch"]["version"], "raise_risk": False})
    assert r.json()["launch"]["raise_risk"] is False
    assert maint.put(base, json={"version": r.json()["launch"]["version"], "raise_risk": "yes"}).status_code == 422
