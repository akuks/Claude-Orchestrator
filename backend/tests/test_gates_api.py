"""Phase 5a task 7: run detail, approvals, gate allow and the log tail."""

import asyncio
import pathlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.gates import executor
from app.main import app
from app.models import Project, WorkflowGateRun, WorkflowRun
from conftest import login_as

FIX = next(pathlib.Path(__file__).parent.glob('fixtures/**/pytest_cutoff.txt'))


def _uid():
    return uuid.uuid4().hex[:8]


def _project(client):
    r = client.post("/projects", json={"name": f"ga-{_uid()}"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _grant(client, pid, user_id, role):
    keep = [
        {k: m[k] for k in ("subject_type", "subject_id", "role")}
        for m in client.get(f"/projects/{pid}/members").json()
        if m["subject_id"] != user_id
    ]
    keep.append({"subject_type": "user", "subject_id": user_id, "role": role})
    assert client.put(f"/projects/{pid}/members", json=keep).status_code == 200


def _user(client, pid=None, role=None):
    u = login_as(f"ga-{_uid()}@test.local")
    if pid:
        _grant(client, pid, u.user_id, role)
    return u


def _add(*rows):
    async def go():
        async with SessionLocal() as s:
            s.add_all(rows)
            await s.commit()

    asyncio.run(go())


def _run(pid, stages=None, state=None, status="running", current=0, created_by=None, name=None):
    stages = stages or [{"name": "Test", "kind": "gate", "run": ["pytest -q"], "project": pid}]
    run = WorkflowRun(
        workflow_id=uuid.uuid4().hex, name=name or f"ga-run-{_uid()}", status=status,
        current_stage=current, project_ids=[pid], stages=stages, stage_state=state or {},
        created_by=created_by,
    )
    _add(run)
    return run.id


def _gate_row(run_id, log_name=None, i=0):
    g = WorkflowGateRun(
        run_id=run_id, stage_index=i, attempt=0, handout="h", command="pytest -q", kind="run",
        started_at=datetime.now(timezone.utc), outcome="passed", exit_code=0, log_name=log_name,
    )
    _add(g)
    return g.id


class FakeRunner:
    def __init__(self, live=None):
        self.calls, self.live, self.err = [], live, None

    def live_gate(self, run_id):
        return self.live

    async def approve_stage(self, run_id, i, user):
        self.calls.append(("approve", run_id, i))
        if self.err:
            raise ValueError(self.err)

    async def reject_stage(self, run_id, i, user, reason):
        self.calls.append(("reject", run_id, i, reason))
        if self.err:
            raise ValueError(self.err)

    async def allow_gate(self, run_id, i, user):
        self.calls.append(("allow", run_id, i))
        if self.err:
            raise ValueError(self.err)


def _patch(monkeypatch, **kw):
    fr = FakeRunner(**kw)
    monkeypatch.setattr(app.state, "workflow_runner", fr)
    return fr


def test_detail_has_gate_keys(client, monkeypatch):
    pid = _project(client)
    rid = _run(pid, state={"0": {"status": "running", "handout": "h", "passed_cmds": []}})
    _gate_row(rid, log_name="stage0-1.log")
    _patch(monkeypatch)
    d = client.get(f"/workflows/runs/{rid}").json()
    for k in ("run", "tasks", "stage_state", "gate_runs", "live", "totals", "log"):
        assert k in d
    assert d["live"] is None
    assert d["stage_state"]["0"]["status_text"] == "not yet passed (0 of 1 commands passed, none running)"
    assert d["run"]["stage_state"]["0"]["status"] == "running"
    assert len(d["gate_runs"]) == 1 and d["gate_runs"][0]["has_log"] is True
    assert "log_name" not in d["gate_runs"][0]
    assert "line" in d["totals"]


def test_detail_live_progress(client, monkeypatch):
    pid = _project(client)
    rid = _run(pid, state={"0": {"status": "running", "handout": "h"}})
    d = executor._run_dir(rid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "stage0-1.log").write_text(open(FIX).read())
    _patch(monkeypatch, live={"stage_index": 0, "command": "pytest -q", "k": 0, "n": 1, "log_name": "stage0-1.log"})
    j = client.get(f"/workflows/runs/{rid}").json()
    assert j["live"]["command"] == "pytest -q"
    assert "log_name" not in j["live"]
    assert j["live"]["progress_line"] == "9 of 63 tests done, 2 failed"
    assert j["stage_state"]["0"]["status_text"] == "running `pytest -q` (0 of 1 commands passed)"


def test_old_run_without_stage_state(client, monkeypatch):
    pid = _project(client)
    rid = _run(pid, stages=[{"name": "S", "items": []}], state={})
    _patch(monkeypatch)
    d = client.get(f"/workflows/runs/{rid}").json()
    assert d["stage_state"] == {} and d["gate_runs"] == [] and d["log"] == []
    assert client.get("/workflows/runs").status_code == 200


def test_held_others_only_visible(client, monkeypatch):
    pid = _project(client)
    rid = _run(pid, state={"0": {"status": "held", "reason": "wait"}})
    _run(pid, name="visible-other")
    p2 = _project(client)
    hidden = WorkflowRun(
        workflow_id=uuid.uuid4().hex, name="hidden-other", status="running", current_stage=0,
        project_ids=[pid, p2], stages=[],
    )
    _add(hidden)
    u = _user(client, pid, "contributor")
    _patch(monkeypatch)
    d = u.get(f"/workflows/runs/{rid}").json()
    assert "visible-other" in d["stage_state"]["0"]["others"]
    assert d["stage_state"]["0"]["others_more"] >= 0


def test_approve_reject_permissions(client, monkeypatch):
    pid = _project(client)
    stages = [{"name": "Sign", "kind": "approval", "project": pid, "message": "ok?"}]
    creator = _user(client, pid, "contributor")
    viewer = _user(client, pid, "contributor")  # a member who did not start the run
    rid = _run(pid, stages=stages, state={"0": {"status": "waiting_approval"}}, created_by=creator.user_id)
    fr = _patch(monkeypatch)
    assert viewer.post(f"/workflows/runs/{rid}/stages/0/approve").status_code == 403
    assert viewer.post(f"/workflows/runs/{rid}/stages/0/reject", json={"reason": "no"}).status_code == 403
    assert creator.post(f"/workflows/runs/{rid}/stages/0/approve").status_code == 200
    r = creator.post(f"/workflows/runs/{rid}/stages/0/reject", json={"reason": "bad"})
    assert r.status_code == 200
    assert [c[0] for c in fr.calls] == ["approve", "reject"] and fr.calls[1][3] == "bad"
    assert creator.post(f"/workflows/runs/{rid}/stages/5/approve").status_code == 404
    fr.err = "this step is not waiting for approval"
    r = creator.post(f"/workflows/runs/{rid}/stages/0/approve")
    assert r.status_code == 409 and "not waiting" in r.json()["detail"]


def test_allow_permissions(client, monkeypatch):
    pid = _project(client)
    other = _project(client)
    contrib = _user(client, pid, "contributor")
    maint = _user(client, pid, "maintainer")
    other_maint = _user(client, other, "maintainer")
    _grant(client, pid, other_maint.user_id, "contributor")
    rid = _run(pid, state={"0": {"status": "needs_approval"}}, created_by=contrib.user_id)
    fr = _patch(monkeypatch)
    assert contrib.post(f"/workflows/runs/{rid}/stages/0/allow").status_code == 403
    assert other_maint.post(f"/workflows/runs/{rid}/stages/0/allow").status_code == 403
    assert maint.post(f"/workflows/runs/{rid}/stages/0/allow").status_code == 200
    assert client.post(f"/workflows/runs/{rid}/stages/0/allow").status_code == 200
    assert len(fr.calls) == 2
    assert client.post(f"/workflows/runs/{rid}/stages/9/allow").status_code == 404
    fr.err = "this gate is not waiting for an allowance"
    assert client.post(f"/workflows/runs/{rid}/stages/0/allow").status_code == 409


def test_log_tail_redacts_and_caps(client, monkeypatch):
    pid = _project(client)
    secret = "s3cr3t-token-value"

    async def setenv():
        async with SessionLocal() as s:
            pr = await s.get(Project, pid)
            pr.env = {"API": secret, "SHORT": "abc"}
            await s.commit()

    asyncio.run(setenv())
    rid = _run(pid)
    d = executor._run_dir(rid)
    d.mkdir(parents=True, exist_ok=True)
    body = "".join(f"line {i}\n" for i in range(300)) + f"token {secret} end\nabc ok\x00\n"
    (d / "stage0-1.log").write_text(body)
    gid = _gate_row(rid, log_name="stage0-1.log")
    out = client.get(f"/workflows/runs/{rid}/gate-runs/{gid}/log").json()
    assert len(out["lines"]) == 200 and out["truncated"] is True
    txt = "\n".join(out["lines"])
    assert secret not in txt and "token [redacted] end" in txt
    assert "abc ok" in txt and "\x00" not in txt
    assert "stage0-1.log" not in str(out)
    # a missing file
    g2 = _gate_row(rid, log_name="stage0-9.log")
    assert client.get(f"/workflows/runs/{rid}/gate-runs/{g2}/log").json() == {"lines": [], "truncated": False}
    # a gate row of another run is a 404
    other = _run(pid)
    assert client.get(f"/workflows/runs/{other}/gate-runs/{gid}/log").status_code == 404


def test_log_invisible_run_404(client):
    pid = _project(client)
    rid = _run(pid)
    gid = _gate_row(rid)
    u = _user(client)
    assert u.get(f"/workflows/runs/{rid}/gate-runs/{gid}/log").status_code == 404


def test_live_runs_gate_stage(client):
    pid = _project(client)
    stages = [
        {"name": "A", "kind": "gate", "run": ["x"], "project": pid},
        {"name": "B", "kind": "approval", "project": pid},
        {"name": "C", "kind": "gate", "run": ["y"], "project": pid},
    ]
    rid = _run(pid, stages=stages, current=1, state={"0": {"status": "passed"}, "1": {"status": "waiting_approval"}})
    item = next(i for i in client.get("/workflows/runs/live").json() if i["id"] == rid)
    assert [s["state"] for s in item["stages"]] == ["done", "waiting_approval", "next"]
    rid = _run(pid, stages=stages, current=0, state={"0": {"status": "running"}})
    item = next(i for i in client.get("/workflows/runs/live").json() if i["id"] == rid)
    assert [s["state"] for s in item["stages"]] == ["current", "next", "next"]
    rid = _run(pid, stages=stages, current=0, state={"0": {"status": "failed"}})
    item = next(i for i in client.get("/workflows/runs/live").json() if i["id"] == rid)
    assert item["stages"][0]["state"] == "failed"
