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


def test_stages_that_need_earlier_output_carry_the_placeholder():
    def prompt(key, stage, n=0):
        return next(s for s in TEMPLATES[key]["stages"] if s["name"] == stage)["items"][n]["prompt"]

    assert "{{stage1}}" in prompt("planned", "Build", 0)
    assert "{{stage1}}" in prompt("planned", "Build", 1)
    assert "{{stage1}}" in prompt("planned", "Review")
    assert "{{prev}}" in prompt("documents", "Write")
    assert "{{prev}}" in prompt("spike", "Check")


def test_file_name_pattern_refuses_a_trailing_newline():
    from app.workflow_templates import FILE_RE
    assert FILE_RE.fullmatch("good.md")
    assert not FILE_RE.fullmatch("good.md\n")
