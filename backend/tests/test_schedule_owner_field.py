"""Schedules expose their owner (created_by) so Home can list the viewer's own
paused schedules under "Needs you"."""

import uuid

from conftest import login_as


def _uid():
    return uuid.uuid4().hex[:8]


def test_schedule_out_includes_created_by(client):
    pid = client.post("/projects", json={"name": f"sch-own-{_uid()}"}).json()["id"]
    u = login_as(f"sch-own-{_uid()}@test.local")
    r = client.put(f"/projects/{pid}/members", json=[
        {"subject_type": "user", "subject_id": u.user_id, "role": "contributor"},
    ])
    assert r.status_code == 200, r.text
    r = u.post("/schedules", json={
        "name": f"sch-own-{_uid()}", "cron": "0 3 * * *", "prompt": "hi", "project_id": pid,
    })
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert r.json()["created_by"] == u.user_id
    assert u.get(f"/schedules/{sid}").json()["created_by"] == u.user_id
    row = next(x for x in client.get("/schedules").json() if x["id"] == sid)
    assert row["created_by"] == u.user_id
