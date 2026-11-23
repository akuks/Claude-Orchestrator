import asyncio
import uuid

import pytest

from app.database import SessionLocal
from app.gates import spec
from app.models import Project, Workflow
from app.routers.workflows import workflow_projects


def run(coro):
    return asyncio.run(coro)


def mkproj(directory="/tmp/x"):
    async def go():
        async with SessionLocal() as s:
            p = Project(name="g-" + uuid.uuid4().hex[:6], slug=uuid.uuid4().hex, directory=directory)
            s.add(p)
            await s.commit()
            return p.id
    return run(go())


def validate(stages):
    async def go():
        async with SessionLocal() as s:
            return await spec.validate_stages(s, stages)
    return run(go())


def gate(pid, **kw):
    return {"name": "G", "kind": "gate", "project": pid, "run": ["npm test"], **kw}


def test_old_stage_unchanged(client):
    st = {"name": "A", "items": [{"agent": "x", "prompt": "p"}]}
    assert validate([st]) == [st]
    assert spec.stage_kind(st) == "agent"
    assert spec.stage_kind({"kind": "bogus"}) == "agent"


def test_gate_normalized_defaults(client):
    pid = mkproj()
    out = validate([gate(pid)])[0]
    assert out["timeout_min"] == 60 and out["retry"] is None and out["before"] == []
    assert out["kind"] == "gate" and out["run"] == ["npm test"]


@pytest.mark.parametrize("cmd", ["a;b", "a && b", "a | b", "a > b", "a < b", "a`b`", "a $(b)", "a\nb", "", "x" * 501])
def test_bad_commands(client, cmd):
    with pytest.raises(ValueError):
        spec.check_command(cmd)
    with pytest.raises(ValueError):
        validate([gate(mkproj(), run=[cmd])])


def test_command_reason_names_char():
    with pytest.raises(ValueError, match="backtick"):
        spec.check_command("a`b")
    assert spec.check_command("x" * 500) == "x" * 500


def test_retry_rules(client):
    pid = mkproj()
    with pytest.raises(ValueError):
        validate([gate(pid, run=["a", "b"], retry="c")])
    with pytest.raises(ValueError):
        validate([gate(pid, run=["a"], retry="a")])
    assert validate([gate(pid, run=["a"], retry="b")])[0]["retry"] == "b"


@pytest.mark.parametrize("t", [0, 721])
def test_timeout_bounds(client, t):
    with pytest.raises(ValueError):
        validate([gate(mkproj(), timeout_min=t)])


def test_project_rules(client):
    with pytest.raises(ValueError, match="unknown project"):
        validate([gate("nope")])
    with pytest.raises(ValueError, match="no directory"):
        validate([gate(mkproj(directory=""))])
    with pytest.raises(ValueError):
        validate([{"name": "G", "kind": "gate", "run": ["a"]}])


def test_run_count(client):
    pid = mkproj()
    with pytest.raises(ValueError):
        validate([gate(pid, run=[])])
    with pytest.raises(ValueError):
        validate([gate(pid, run=["a"] * 11)])


def test_approval(client):
    out = validate([{"name": "OK", "kind": "approval", "message": "go?"}])[0]
    assert out["project"] is None and out["before"] == []
    with pytest.raises(ValueError):
        validate([{"name": "OK", "kind": "approval", "message": "x" * 2001}])


@pytest.mark.parametrize("line", ["free memory 4.25 GB", "port 70000 open", "container -x running", "disk 4 GB"])
def test_bad_before(client, line):
    with pytest.raises(ValueError):
        validate([gate(mkproj(), before=[line])])


def test_before_limit(client):
    with pytest.raises(ValueError):
        validate([gate(mkproj(), before=["port 80 open"] * 6)])


def test_workflow_projects_includes_gate(client):
    pid = mkproj()
    wf = Workflow(name="w", stages=[gate(pid), {"name": "OK", "kind": "approval", "message": "m", "project": None}])

    async def go():
        async with SessionLocal() as s:
            return await workflow_projects(s, wf)
    assert run(go()) == [pid]


def test_rest_422_and_roundtrip(client):
    r = client.post("/workflows", json={"name": "bad", "stages": [
        {"name": "G", "kind": "gate", "project": mkproj(), "run": ["a; b"]}]})
    assert r.status_code == 422
    assert "semi" in r.json()["detail"] or ";" in r.json()["detail"]
    pid = mkproj()
    r = client.post("/workflows", json={"name": "ok-" + uuid.uuid4().hex[:6], "stages": [
        {"name": "G", "kind": "gate", "project": pid, "run": ["npm test"]}]})
    assert r.status_code == 201, r.text
    assert r.json()["stages"][0]["timeout_min"] == 60
    wid = r.json()["id"]
    r = client.patch(f"/workflows/{wid}", json={"stages": [{"name": "G", "kind": "gate", "project": pid, "run": ["a|b"]}]})
    assert r.status_code == 422
