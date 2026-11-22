"""Phase 3c task 1: the RunAsk model, its cascade, the startup sweep and migration 0010."""

import asyncio
import uuid

import pytest
from alembic import command
from sqlalchemy import delete

from app.asks.service import fail_interrupted_asks
from app.database import SessionLocal, _alembic_cfg
from app.models import RunAsk, Task
from test_migrations import _inspect, scratch_url  # noqa: F401 - the fixture


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def admin_id(client):
    return client.get("/auth/me").json()["id"]


def _task(user_id):
    tid = uuid.uuid4().hex

    async def go():
        async with SessionLocal() as s:
            s.add(Task(id=tid, title="t", prompt="p", root_id=tid, created_by=user_id, status="completed"))
            await s.commit()

    _run(go())
    return tid


def _ask(tid, user_id, **kw):
    async def go():
        async with SessionLocal() as s:
            a = RunAsk(task_id=tid, user_id=user_id, question="why?", **kw)
            s.add(a)
            await s.commit()
            return a.id

    return _run(go())


def _get(aid):
    async def go():
        async with SessionLocal() as s:
            return await s.get(RunAsk, aid)

    return _run(go())


def test_run_ask_defaults(client, admin_id):
    a = _get(_ask(_task(admin_id), admin_id))
    assert len(a.id) == 32 and a.status == "pending" and a.answer is None
    assert a.error is None and a.cost_usd is None and a.model is None
    assert a.created_at is not None and a.answered_at is None


def test_task_delete_cascades_asks(client, admin_id):
    tid = _task(admin_id)
    aid = _ask(tid, admin_id)

    assert client.delete(f"/tasks/{tid}").status_code == 204
    assert _get(aid) is None


def test_fail_interrupted_asks(client, admin_id):
    async def clear():
        async with SessionLocal() as s:
            await s.execute(delete(RunAsk))
            await s.commit()

    _run(clear())
    tid = _task(admin_id)
    pending = _ask(tid, admin_id)
    done = _ask(tid, admin_id, status="answered", answer="because")
    assert _run(fail_interrupted_asks()) == 1
    p, d = _get(pending), _get(done)
    assert p.status == "failed" and p.error == "interrupted" and p.answered_at is not None
    assert d.status == "answered" and d.answer == "because" and d.error is None and d.answered_at is None
    assert _run(fail_interrupted_asks()) == 0


def test_migration_0010_upgrade_downgrade(scratch_url):  # noqa: F811
    url = scratch_url
    command.upgrade(_alembic_cfg(url), "0009")
    command.upgrade(_alembic_cfg(url), "head")

    def check(sc, insp):
        assert {"id", "task_id", "user_id", "question", "answer", "status", "error", "cost_usd", "model",
                "created_at", "answered_at"} <= {c["name"] for c in insp.get_columns("run_asks")}
        idx = {tuple(i["column_names"]) for i in insp.get_indexes("run_asks")}
        assert {("task_id",), ("user_id",), ("user_id", "created_at")} <= idx
        assert {f["referred_table"] for f in insp.get_foreign_keys("run_asks")} == {"tasks", "users"}
        return sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()

    assert _inspect(url, check) == "0010"
    command.downgrade(_alembic_cfg(url), "0009")
    assert _inspect(url, lambda sc, insp: "run_asks" in insp.get_table_names()) is False
