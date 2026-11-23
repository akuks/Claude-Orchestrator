"""Phase 4b task 1: proposal service (clean, cap, dedupe)."""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.decisions import proposals
from app.models import DecisionProposal, Project


def _run(coro):
    return asyncio.run(coro)


def _project():
    pid = uuid.uuid4().hex

    async def go():
        async with SessionLocal() as s:
            s.add(Project(id=pid, name="p" + pid[:8], slug="p" + pid[:8], directory="/x"))
            await s.commit()

    _run(go())
    return pid


def test_clean_proposal_one_line_and_redacted():
    out = proposals.clean_proposal({"title": "a\nb​ ghp_" + "x" * 36}, strict_touches=False)
    assert "\n" not in out["title"] and out["title"].startswith("a b")
    assert "ghp_" + "x" * 36 not in out["title"]


def test_clean_proposal_kinds():
    with pytest.raises(ValueError):
        proposals.clean_proposal({"title": "t", "kind": "reference"}, strict_touches=False)
    assert proposals.clean_proposal({"title": "t"}, strict_touches=False)["kind"] == "decision"
    out = proposals.clean_proposal({"title": "t", "see": "x", "source": "y"}, strict_touches=False)
    assert "see" not in out and "source" not in out


def test_valid_touches_drops_bad_entries():
    assert proposals.valid_touches(["src/**", "/etc", "a/../b", "src/**"]) == ["src/**"]
    assert len(proposals.valid_touches([f"d/{i}" for i in range(25)])) == 20


def test_strict_touches_raise():
    with pytest.raises(ValueError):
        proposals.clean_proposal({"title": "t", "touches": ["/etc"]}, strict_touches=True)
    assert proposals.clean_proposal({"title": "t", "touches": ["/etc", "a"]}, strict_touches=False)["touches"] == ["a"]


def test_add_respects_cap(client, monkeypatch):
    monkeypatch.setattr(settings, "proposals_pending_cap", 2)
    pid = _project()

    async def go():
        async with SessionLocal() as s:
            for i in range(2):
                assert await proposals.add(s, project_id=pid, fields={"title": f"t{i}"}, source="agent",
                                           on_full="drop") is not None
            assert await proposals.add(s, project_id=pid, fields={"title": "x"}, source="agent", on_full="drop") is None
            with pytest.raises(proposals.QueueFull):
                await proposals.add(s, project_id=pid, fields={"title": "x"}, source="agent", on_full="raise")
            assert await proposals.pending_count(s, pid) == 2
            rows = (await s.execute(select(DecisionProposal).where(DecisionProposal.project_id == pid))).scalars().all()
            rows[0].status = "rejected"
            await s.flush()
            assert await proposals.pending_count(s, pid) == 1
            assert await proposals.add(s, project_id=pid, fields={"title": "y"}, source="agent",
                                       on_full="raise") is not None

    _run(go())


def test_add_dedupe_returns_none(client):
    pid = _project()

    async def go():
        async with SessionLocal() as s:
            a = await proposals.add(s, project_id=pid, fields={"title": "a"}, source="run", dedupe_key="k:1",
                                    on_full="drop")
            b = await proposals.add(s, project_id=pid, fields={"title": "b"}, source="run", dedupe_key="k:1",
                                    on_full="drop")
            assert a is not None and b is None
            await s.commit()
        async with SessionLocal() as s:
            rows = (await s.execute(select(DecisionProposal).where(DecisionProposal.project_id == pid))).scalars().all()
            assert [r.title for r in rows] == ["a"]

    _run(go())


def test_key_used_prefix(client):
    pid = _project()

    async def go():
        async with SessionLocal() as s:
            await proposals.add(s, project_id=pid, fields={"title": "a"}, source="run",
                                dedupe_key="commit:abc123:1", on_full="drop")
            assert await proposals.key_used(s, pid, "commit:abc123:") is True
            assert await proposals.key_used(s, pid, "commit:def456:") is False
            assert await proposals.key_used(s, pid, "commit:%") is False

    _run(go())


def test_null_dedupe_keys_not_unique(client):
    pid = _project()

    async def go():
        async with SessionLocal() as s:
            for t in ("a", "b"):
                assert await proposals.add(s, project_id=pid, fields={"title": t}, source="person",
                                           on_full="drop") is not None
            await s.commit()

    _run(go())
