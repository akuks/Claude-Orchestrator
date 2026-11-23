import asyncio
import uuid

from app.database import SessionLocal
from app.models import Project
from app.routers.agents import PRESETS
from app.gates import spec
from app.workflow_templates import COMMIT_NOTE, GATE_HINT, TEMPLATES

NAMES = {p["name"] for p in PRESETS}


def _project(tmp_path):
    p = Project(name=(n := f"wt-{uuid.uuid4().hex[:8]}"), slug=n, directory=str(tmp_path))

    async def go():
        async with SessionLocal() as s:
            s.add(p)
            await s.commit()

    asyncio.run(go())
    return p.id


def test_keys():
    assert list(TEMPLATES) == ["software", "planned", "documents", "spike", "ui"]


def test_templates_validate_with_project(client, tmp_path):
    pid = _project(tmp_path)
    for key, t in TEMPLATES.items():
        stages = [dict(st) for st in t["stages"]]
        for st in stages:
            if st.get("kind") == "gate":
                assert st["run"] == ["npm test"] and "project" not in st
                st["project"] = pid

        async def go():
            async with SessionLocal() as s:
                return await spec.validate_stages(s, stages)

        assert asyncio.run(go()), key
        for st in t["stages"]:
            for item in st.get("items", []):
                assert item["agent"] in NAMES, (key, item["agent"])
                if item["agent"] == "Builder":
                    assert COMMIT_NOTE in item["prompt"]


def test_route(client):
    r = client.get("/workflows/templates")
    assert r.status_code == 200
    j = r.json()
    assert [t["key"] for t in j] == list(TEMPLATES)
    assert all(set(t) == {"key", "name", "description", "stages", "hint"} for t in j)
    assert next(t for t in j if t["key"] == "software")["hint"] == GATE_HINT
