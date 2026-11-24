"""Phase 6c Task 1: GET /projects/{id}/schema."""

import asyncio
import shutil
import time
import uuid

from fastapi.testclient import TestClient

from app import auth
from app.database import SessionLocal
from app.main import app
from app.models import ProjectDecision, Task
from app.routers import schema as schema_router
from conftest import login_as


def _project(client, tmp_path):
    d = tmp_path / f"d-{uuid.uuid4().hex[:6]}"
    d.mkdir()
    r = client.post("/projects", json={"name": f"sc-{uuid.uuid4().hex[:8]}", "directory": str(d)})
    assert r.status_code == 201, r.text
    return r.json(), d


def _sql(d, name, text, root="migrations"):
    p = d / root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_reads_tables(client, tmp_path):
    p, d = _project(client, tmp_path)
    _sql(d, "1_init.sql", "CREATE TABLE users (id int PRIMARY KEY, email text NOT NULL);\n"
                          "CREATE TABLE posts (id int PRIMARY KEY, user_id int REFERENCES users(id));")
    j = client.get(f"/projects/{p['id']}/schema").json()
    assert j["found"] is True and j["as_of"] is None and j["limits"] == []
    assert j["not_understood"] == {"count": 0, "samples": []} and j["note"]
    root = j["roots"][0]
    assert root["root"] == "migrations" and root["files"] == 1
    t = {x["name"]: x for x in root["tables"]}
    assert t["users"]["defined_at"] == "migrations/1_init.sql:1" and t["users"]["uncertain"] is False
    assert t["users"]["columns"][0] == {"name": "id", "type": "int", "nullable": False, "primary_key": True,
                                        "defined_at": "migrations/1_init.sql:1"}
    assert t["posts"]["links"] == [{"columns": ["user_id"], "to_table": "users", "to_columns": ["id"],
                                    "defined_at": "migrations/1_init.sql:2"}]
    assert t["posts"]["decisions"] == []


def test_table_filter_and_not_understood(client, tmp_path):
    p, d = _project(client, tmp_path)
    _sql(d, "1.sql", "CREATE TABLE a (id int);\nnonsense words;\nCREATE TABLE b (id int);")
    j = client.get(f"/projects/{p['id']}/schema").json()
    assert j["not_understood"] == {"count": 1, "samples": ["migrations/1.sql:2"]}
    j = client.get(f"/projects/{p['id']}/schema", params={"table": "B"}).json()
    assert [t["name"] for t in j["roots"][0]["tables"]] == ["b"]
    j = client.get(f"/projects/{p['id']}/schema", params={"table": "zzz"}).json()
    assert j["roots"][0]["tables"] == [] and "zzz" in j["note"]


def test_no_folder_or_no_files(client, tmp_path):
    p, d = _project(client, tmp_path)
    j = client.get(f"/projects/{p['id']}/schema").json()
    assert j["found"] is False and j["roots"] == [] and j["note"].startswith("No SQL migration files found. Looked in migrations/")
    shutil.rmtree(d)
    assert client.get(f"/projects/{p['id']}/schema").json()["found"] is False
    nodir = client.post("/projects", json={"name": f"nd-{uuid.uuid4().hex[:8]}"}).json()
    r = client.get(f"/projects/{nodir['id']}/schema")
    assert r.status_code == 200 and r.json()["found"] is False


def test_roles(client, tmp_path):
    p, _ = _project(client, tmp_path)
    out = login_as(f"sc-o-{uuid.uuid4().hex[:6]}@test.local")
    assert out.get(f"/projects/{p['id']}/schema").status_code == 404
    reader = login_as(f"sc-r-{uuid.uuid4().hex[:6]}@test.local")
    r = client.put(f"/projects/{p['id']}/members",
                   json=[{"subject_type": "user", "subject_id": reader.user_id, "role": "contributor"}])
    assert r.status_code == 200
    assert reader.get(f"/projects/{p['id']}/schema").status_code == 200
    assert client.get("/projects/nope/schema").status_code == 404


def test_caps_named(client, tmp_path):
    p, d = _project(client, tmp_path)
    sql = "".join(f"CREATE TABLE t{i} (id int);\n" for i in range(61))
    sql += "CREATE TABLE wide (" + ", ".join(f"c{i} int" for i in range(201)) + ");\n"
    _sql(d, "1.sql", sql)
    j = client.get(f"/projects/{p['id']}/schema").json()
    assert len(j["roots"][0]["tables"]) == 60
    assert any(x.startswith("migrations: ") and "60 of 62 tables" in x for x in j["limits"])
    j = client.get(f"/projects/{p['id']}/schema", params={"table": "wide"}).json()
    assert len(j["roots"][0]["tables"][0]["columns"]) == 200 and any("migrations: wide: showing 200 of 201 columns" in x for x in j["limits"])
    p2, d2 = _project(client, tmp_path)
    _sql(d2, "1.sql", "CREATE TABLE t (id int, " + ", ".join(f"r{i} int REFERENCES o{i}(id)" for i in range(51)) + ");")
    j = client.get(f"/projects/{p2['id']}/schema").json()
    assert len(j["roots"][0]["tables"][0]["links"]) == 50 and any("migrations: t: showing 50 of 51 links" in x for x in j["limits"])


def _wait_idle(limit=2.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit:
        with schema_router._lock:
            if not schema_router._inflight:
                return time.monotonic() - t0
        time.sleep(0.02)
    raise AssertionError("a schema worker is still running")


def test_real_deadline_stops_real_slow_parse(client, tmp_path, monkeypatch):
    p, d = _project(client, tmp_path)
    _sql(d, "1.sql", "".join(f"CREATE TABLE t{i} (id int PRIMARY KEY, x text, y int REFERENCES o(id));\n"
                              for i in range(30000)))
    monkeypatch.setattr(schema_router, "BUDGET_SECONDS", 0.3)
    t0 = time.monotonic()
    j = client.get(f"/projects/{p['id']}/schema").json()
    assert time.monotonic() - t0 < 1.0
    assert j == {"found": True, "roots": [], "as_of": None, "not_understood": {"count": 0, "samples": []},
                 "limits": [schema_router.NOTE_SLOW], "note": "Reading the migration files took too long."}
    assert _wait_idle(1.0) < 1.0  # no worker still running past deadline + 1 s
    assert schema_router._slots.acquire(blocking=False)  # and its slot came back
    schema_router._slots.release()
    # the timeout is remembered for this file signature: the retry answers at once, with no new work
    assert schema_router._timed_out
    monkeypatch.setattr(schema_router.schema_replay, "replay", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    monkeypatch.setattr(schema_router, "BUDGET_SECONDS", 5.0)
    assert client.get(f"/projects/{p['id']}/schema").json()["note"] == schema_router.NOTE_SLOW


def _overlap(client, pid, n, calls, monkeypatch):
    import threading

    real = schema_router.schema_replay.replay

    def slowish(files, deadline=None):
        calls.append(1)
        time.sleep(0.4)
        return real(files, deadline=deadline)

    monkeypatch.setattr(schema_router.schema_replay, "replay", slowish)
    out = []

    def go():
        out.append(client.get(f"/projects/{pid}/schema").json())

    ts = [threading.Thread(target=go) for _ in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return out


def test_overlapping_requests_share_one_read(client, tmp_path, monkeypatch):
    for n in (2, 10):
        p, d = _project(client, tmp_path)
        _sql(d, "1.sql", "CREATE TABLE a (id int);")
        calls = []
        out = _overlap(client, p["id"], n, calls, monkeypatch)
        assert len(out) == n and all(o["found"] and o["roots"][0]["tables"][0]["name"] == "a" for o in out)
        assert len(calls) == 1  # the parse ran once


def test_waits_for_a_free_worker_within_budget(client, tmp_path, monkeypatch):
    import threading

    p, d = _project(client, tmp_path)
    _sql(d, "1.sql", "CREATE TABLE a (id int);")
    held = 0
    while schema_router._slots.acquire(blocking=False):
        held += 1
    threading.Timer(0.3, lambda: [schema_router._slots.release() for _ in range(held)]).start()
    t0 = time.monotonic()
    j = client.get(f"/projects/{p['id']}/schema").json()
    assert 0.25 < time.monotonic() - t0 < 3 and j["roots"][0]["tables"][0]["name"] == "a"
    # budget runs out while both workers stay busy: only then "took too long"
    p2, d2 = _project(client, tmp_path)
    _sql(d2, "1.sql", "CREATE TABLE b (id int);")
    held = 0
    try:
        while schema_router._slots.acquire(blocking=False):
            held += 1
        monkeypatch.setattr(schema_router, "BUDGET_SECONDS", 0.3)
        j = client.get(f"/projects/{p2['id']}/schema").json()
    finally:
        for _ in range(held):
            schema_router._slots.release()
    assert j["note"] == schema_router.NOTE_SLOW and j["found"] is True and j["roots"] == []


def test_unexpected_error_is_a_plain_answer(client, tmp_path, monkeypatch):
    p, d = _project(client, tmp_path)
    _sql(d, "1.sql", "CREATE TABLE a (id int);")
    real = schema_router.schema_replay.replay

    def boom(files, deadline=None):
        raise ValueError("boom")

    monkeypatch.setattr(schema_router.schema_replay, "replay", boom)
    r = client.get(f"/projects/{p['id']}/schema")
    assert r.status_code == 200
    j = r.json()
    assert j["found"] is True and j["roots"] == [] and j["note"] == "The migration files could not be read."
    monkeypatch.setattr(schema_router.schema_replay, "replay", real)
    assert client.get(f"/projects/{p['id']}/schema").json()["roots"][0]["tables"][0]["name"] == "a"


def test_crafted_statement_does_not_break_the_tab(client, tmp_path):
    p, d = _project(client, tmp_path)
    _sql(d, "1.sql", "CREATE TABLE ok (id int);\nCREATE TABLE public. . a AS SELECT 1;\n")
    r = client.get(f"/projects/{p['id']}/schema")
    assert r.status_code == 200 and r.json()["roots"][0]["tables"][0]["name"] == "ok"


def test_big_single_statement_and_garbage_answer(client, tmp_path):
    p, d = _project(client, tmp_path)
    big = "CREATE TABLE huge (" + ", ".join(f"c{i} int" for i in range(500_000))[:4_000_000] + ");\n"
    assert len(big) > 3_900_000
    _sql(d, "1.sql", "CREATE TABLE a (id int);\n" + big + "garbage garbage;\n")
    t0 = time.monotonic()
    j = client.get(f"/projects/{p['id']}/schema").json()
    assert time.monotonic() - t0 < 5
    assert j["found"] is True and [t["name"] for t in j["roots"][0]["tables"]] == ["a"]
    assert j["not_understood"]["count"] == 2  # the 4 MB statement and the garbage one


def test_decisions_listed(client, tmp_path):
    p, d = _project(client, tmp_path)
    _sql(d, "1_init.sql", "CREATE TABLE a (id int);")
    _sql(d, "2_more.sql", "CREATE TABLE b (id int);")

    async def go():
        async with SessionLocal() as s:
            for n, touches in ((1, ["migrations/1_init.sql"]), (2, ["migrations/**"]), (3, ["src/**"]),
                               (4, ["migrations/"]), (5, ["migrations/*.sql"]), (6, ["migrations/**"])):
                s.add(ProjectDecision(project_id=p["id"], number=n, title=f"Decision {n}", touches=touches,
                                      status="active"))
            await s.commit()

    asyncio.run(go())
    j = client.get(f"/projects/{p['id']}/schema").json()
    t = {x["name"]: x for x in j["roots"][0]["tables"]}
    assert [x["id"] for x in t["a"]["decisions"]] == ["D-006", "D-005", "D-004"]
    assert [x["id"] for x in t["b"]["decisions"]] == ["D-006", "D-005", "D-004"]  # D-001 touches only file 1
    assert t["a"]["decisions"][0]["title"] == "Decision 6"


def _task(created_by, project_id):
    tid = uuid.uuid4().hex

    async def go():
        async with SessionLocal() as s:
            s.add(Task(id=tid, title=f"t-{tid[:6]}", prompt="p", root_id=tid, status="running",
                       created_by=created_by, project_id=project_id))
            await s.commit()

    asyncio.run(go())
    return tid


def _agent(user_id, task_id):
    async def go():
        async with SessionLocal() as s:
            raw = await auth.mint_token(s, user_id, "agent-task", task_id, 3600)
            await s.commit()
            return raw

    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {asyncio.run(go())}"
    return c


def test_task_token_own_project_only(client, tmp_path):
    me = client.get("/auth/me").json()["id"]
    p, d = _project(client, tmp_path)
    other, _ = _project(client, tmp_path)
    _sql(d, "1.sql", "CREATE TABLE a (id int);")
    a = _agent(me, _task(me, p["id"]))
    assert a.get(f"/projects/{p['id']}/schema").status_code == 200
    r = a.get(f"/projects/{other['id']}/schema")
    assert r.status_code == 403
    assert a.get(f"/projects/{p['id']}/code-map").status_code == 403  # nothing else opened up
