import asyncio
import os
import uuid

from app.database import SessionLocal
from app.models import Agent, Project, Workflow
from app.workflow_templates import export_markdown, parse_import
from conftest import login_as


def _agent(name):
    async def go():
        async with SessionLocal() as s:
            s.add(Agent(name=name, system_prompt="x"))
            await s.commit()

    asyncio.run(go())


def _project(path, trust="own"):
    p = Project(name=(n := f"wi-{uuid.uuid4().hex[:8]}"), slug=n, directory=str(path), trust=trust)

    async def go():
        async with SessionLocal() as s:
            s.add(p)
            await s.commit()

    asyncio.run(go())
    return p.id


def _doc(name, agent):
    import json
    d = {"name": name, "description": "d", "stages": [{"name": "S", "items": [{"agent": agent, "prompt": "p"}]}]}
    return "# t\n\n```json\n" + json.dumps(d) + "\n```\n"


def _agent_name():
    n = f"wi-agent-{uuid.uuid4().hex[:6]}"
    _agent(n)
    return n


def test_round_trip(client):
    a = _agent_name()
    r = client.post("/workflows/import", json={"text": _doc("rt", a)})
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    md = client.get(f"/workflows/{wid}/export")
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    r2 = client.post("/workflows/import", json={"text": md.text})
    assert r2.status_code == 201
    assert r2.json()["stages"] == r.json()["stages"] and r2.json()["name"] == "rt"


def test_refusals(client):
    a = _agent_name()
    one = _doc("x", a)
    assert client.post("/workflows/import", json={"text": "no block"}).status_code == 422
    assert client.post("/workflows/import", json={"text": one + one}).status_code == 422
    assert client.post("/workflows/import", json={"text": one + "x" * 70000}).status_code == 422
    assert client.post("/workflows/import", json={"text": _doc("x", "nope-agent")}).status_code == 422
    assert client.post("/workflows/import", json={}).status_code == 422
    for t in ("```json\n{bad\n```", "```json\n[]\n```"):
        assert client.post("/workflows/import", json={"text": t}).status_code == 422


def test_non_admin_403(client):
    u = login_as(f"wi-{uuid.uuid4().hex[:6]}@test.local")
    assert u.post("/workflows/import", json={"text": _doc("x", "y")}).status_code == 403


def test_parse_helpers():
    import pytest
    with pytest.raises(ValueError):
        parse_import("")
    wf = Workflow(name="n", description="a ``` b", stages=[])
    assert parse_import(export_markdown(wf))["name"] == "n"


def _repo(tmp_path):
    d = tmp_path / ".claude-orchestrator" / "workflows"
    d.mkdir(parents=True)
    return d


def test_repo_files(client, tmp_path):
    a = _agent_name()
    d = _repo(tmp_path)
    (d / "good.md").write_text(_doc("from-repo", a))
    (d / "big.md").write_text("x" * 65 * 1024)
    (d / "bad name.md").write_text("x")
    (d / "notmd.txt").write_text("x")
    (tmp_path / "secret.md").write_text("x")
    os.symlink(tmp_path / "secret.md", d / "link.md")
    os.mkfifo(d / "pipe.md")
    pid = _project(tmp_path)
    r = client.get("/workflows/repo-files", params={"project_id": pid})
    assert r.status_code == 200
    assert [f["name"] for f in r.json()["files"]] == ["good.md"] and r.json()["reason"] is None
    r = client.post("/workflows/import", json={"project_id": pid, "file": "good.md"})
    assert r.status_code == 201 and r.json()["name"] == "from-repo"
    for bad in ("link.md", "pipe.md", "big.md", "../x.md", "missing.md"):
        r = client.post("/workflows/import", json={"project_id": pid, "file": bad})
        assert r.status_code == 422, bad


def test_symlinked_dir_and_external(client, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "a.md").write_text("x")
    proj = tmp_path / "proj"
    (proj / ".claude-orchestrator").mkdir(parents=True)
    os.symlink(real, proj / ".claude-orchestrator" / "workflows")
    r = client.get("/workflows/repo-files", params={"project_id": _project(proj)}).json()
    assert r["files"] == [] and r["reason"]
    ext = tmp_path / "ext"
    d = _repo(ext)
    (d / "a.md").write_text("x")
    r = client.get("/workflows/repo-files", params={"project_id": _project(ext, "external")}).json()
    assert r["files"] == [] and r["reason"]


def test_visibility_and_cap(client, tmp_path):
    d = _repo(tmp_path)
    for i in range(25):
        (d / f"w{i:02}.md").write_text("x")
    pid = _project(tmp_path)
    assert len(client.get("/workflows/repo-files", params={"project_id": pid}).json()["files"]) == 20
    u = login_as(f"wi-{uuid.uuid4().hex[:6]}@test.local")
    assert u.get("/workflows/repo-files", params={"project_id": pid}).status_code == 404
    assert u.post("/workflows/import", json={"project_id": pid, "file": "w00.md"}).status_code == 403


def test_budget_round_trip_and_validation(client):
    import json
    a = _agent_name()
    stages = [{"name": "S", "items": [{"agent": a, "prompt": "p"}]}]

    def doc(**kw):
        return "```json\n" + json.dumps({"name": "bud", "description": "d", "stages": stages, **kw}) + "\n```\n"

    r = client.post("/workflows/import", json={"text": doc(budget_usd=7.5)})
    assert r.status_code == 201, r.text
    assert r.json()["budget_usd"] == 7.5
    md = client.get(f"/workflows/{r.json()['id']}/export").text
    assert '"budget_usd": 7.5' in md
    r2 = client.post("/workflows/import", json={"text": md})
    assert r2.status_code == 201 and r2.json()["budget_usd"] == 7.5
    plain = client.post("/workflows/import", json={"text": doc()})
    assert plain.json()["budget_usd"] is None
    assert "budget_usd" not in client.get(f"/workflows/{plain.json()['id']}/export").text
    for bad in (0, -1, 20000, "5"):
        assert client.post("/workflows/import", json={"text": doc(budget_usd=bad)}).status_code == 422, bad
