"""Phase 4b task 4: the worker starts the after-run suggestion job for a finished project task."""

import asyncio
import time
import uuid

from app.decisions import after_run
from conftest import wait_task


def _project(client):
    r = client.post("/projects", json={"name": f"pw-{uuid.uuid4().hex[:8]}"})
    assert r.status_code == 201, r.text
    return r.json()


def _wait_calls(calls, n, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end and len(calls) < n:
        time.sleep(0.05)


def test_worker_starts_after_task(client, monkeypatch):
    calls = []

    async def rec(task_id, owner_id, emit=None):
        calls.append((task_id, owner_id, emit))

    monkeypatch.setattr(after_run, "after_task", rec)
    p = _project(client)
    r = client.post("/tasks", json={"prompt": "go", "project_id": p["id"]})
    assert r.status_code == 201, r.text
    done = wait_task(client, r.json()["id"])
    assert done["status"] == "completed", done.get("error")
    _wait_calls(calls, 1)
    assert len(calls) == 1 and calls[0][0] == done["id"] and callable(calls[0][2])
    assert done["created_by"] and calls[0][1] == done["created_by"]


def test_after_task_job_is_held_while_running(client, monkeypatch):
    from app import worker as worker_mod
    seen = []

    async def rec(task_id, owner_id, emit=None):
        seen.append(any(not j.done() for j in worker_mod._AFTER_JOBS))

    monkeypatch.setattr(after_run, "after_task", rec)
    p = _project(client)
    r = client.post("/tasks", json={"prompt": "go", "project_id": p["id"]})
    wait_task(client, r.json()["id"])
    _wait_calls(seen, 1)
    assert seen == [True]
    end = time.time() + 3
    while worker_mod._AFTER_JOBS and time.time() < end:
        time.sleep(0.05)
    assert not worker_mod._AFTER_JOBS


def test_worker_skips_after_task_on_failure(client, monkeypatch):
    calls = []

    async def rec(task_id, owner_id, emit=None):
        calls.append(task_id)

    monkeypatch.setattr(after_run, "after_task", rec)
    p = _project(client)
    r = client.post("/tasks", json={"prompt": "go [env-echo] [env-echo-fail]", "project_id": p["id"]})
    done = wait_task(client, r.json()["id"])
    time.sleep(0.3)
    assert done["status"] == "failed" and calls == []
