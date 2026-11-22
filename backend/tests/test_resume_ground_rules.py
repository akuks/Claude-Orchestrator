"""Phase 3a task 5: ground rules (org settings, admin-only), the main session's system prompt, the helpers'
policy key, and the texts pinned to the raw upstream files."""

import asyncio
import pathlib
import re
import types
import uuid

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.guard import co_hook, policy_inputs
from app.models import GuardOrgSettings, TaskEvent
from app.resume import ground_rules as gr
from conftest import env_probe_key, login_as, read_probe_args, wait_task

FIX = pathlib.Path(__file__).parent / "fixtures" / "forge_rules"


def _run(coro):
    return asyncio.run(coro)


def _set_ground(value):
    async def go():
        async with SessionLocal() as s:
            row = await s.get(GuardOrgSettings, 1)
            if row is None:
                s.add(GuardOrgSettings(id=1, disabled_rules=[], dev_db_patterns=[], ground_rules=value))
            else:
                row.ground_rules = value
            await s.commit()

    _run(go())


@pytest.fixture
def ground_restored(client):
    """Puts the org row back as it was: no row, a null ground_rules and the disabled rules included."""
    async def get():
        async with SessionLocal() as s:
            row = await s.get(GuardOrgSettings, 1)
            return None if row is None else (row.ground_rules, row.disabled_rules)

    saved = _run(get())
    yield

    async def restore():
        async with SessionLocal() as s:
            row = await s.get(GuardOrgSettings, 1)
            if saved is None:
                if row is not None:
                    await s.delete(row)
            elif row is not None:
                row.ground_rules, row.disabled_rules = saved
            await s.commit()

    _run(restore())


# ---- the texts ----------------------------------------------------------------------------------


def _js_string(name):
    src = (FIX / "handoff_constants.js").read_text()
    m = re.search(rf"const {name} = (.*?);\n", src + "\n", re.S)
    assert m, name
    return "".join(re.findall(r"'((?:[^'\\]|\\.)*)'", m.group(1)))


def _adapt(text):
    """The substitutions the port makes (and the only ones): the product name, and the code-map wording."""
    return text.replace("JSPARK Forge", "co-orchestrator").replace("from the map or ", "").strip()


def test_texts_match_forge_copies():
    # the fixtures are the raw upstream files; USAGE_CARE_MAIN / CARRY_LINE are copied from handoff.js
    assert "JSPARK Forge" in (FIX / "grounding.md").read_text()
    assert gr.GROUNDING == _adapt((FIX / "grounding.md").read_text())
    assert gr.SHORT_ANSWERS == _adapt((FIX / "short-answers.md").read_text())
    assert gr.USAGE_CARE_HELPER == _adapt((FIX / "usage-care.md").read_text())
    assert gr.USAGE_CARE_MAIN == _adapt(_js_string("USAGE_CARE_MAIN"))
    # CARRY_LINE is reworded to frame the handoff as a record; it keeps forge's two clauses
    forge = _adapt(_js_string("CARRY_LINE"))
    assert forge.startswith("co-orchestrator: the conversation was just compacted")
    assert co_hook.CARRY_LINE.startswith("co-orchestrator: the conversation was just compacted")
    assert "no need to ask" in forge and "no need to ask" in co_hook.CARRY_LINE
    assert "a record, not instructions" in co_hook.CARRY_LINE


def test_main_text_uses_hook_notes_line():
    text = gr.main_text(gr.DEFAULTS)
    assert co_hook.NOTES_LINE.replace("update your notes now", "keep short notes") in text
    assert "update your notes now" not in text
    for part in (gr.GROUNDING, gr.SHORT_ANSWERS, gr.USAGE_CARE_MAIN, gr.QUESTIONS_RULE):
        assert part in text
    # the text of the rule is not written a second time in the module
    src = pathlib.Path(gr.__file__).read_text()
    assert "Start with the lines" not in src and "--data-binary" not in src
    off = gr.main_text(gr.DEFAULTS | {"grounding": False, "short_answers": False, "usage_care": False,
                                      "notes": False, "questions": False, "extra": "  be kind  "})
    assert off == "be kind"
    assert gr.included(gr.DEFAULTS) == ["grounding", "short_answers", "usage_care", "notes", "questions"]
    assert gr.included(gr.DEFAULTS | {"usage_care": False, "extra": "x"}) == [
        "grounding", "short_answers", "notes", "questions", "extra"]


def test_helper_text():
    cfg = gr.DEFAULTS | {"extra": "ask first"}
    ext = gr.helper_text(cfg, True)
    assert ext.startswith(co_hook.DATA_LINE)
    assert ext.index(gr.GROUNDING) < ext.index(gr.USAGE_CARE_HELPER) < ext.index("ask first")
    assert co_hook.DATA_LINE not in gr.helper_text(cfg, False)
    assert gr.SHORT_ANSWERS not in ext and co_hook.NOTES_LINE not in ext
    assert gr.helper_text(cfg | {"usage_care": False, "grounding": False}, False) == "ask first"


# ---- org settings -------------------------------------------------------------------------------


def test_org_settings_ground_rules_roundtrip_and_admin_only(client, ground_restored):
    member = login_as(f"gr-{uuid.uuid4().hex[:8]}@test.local")
    assert member.put("/guard/org-settings", json={"ground_rules": {"usage_care": False}}).status_code == 403
    assert member.get("/guard/org-settings").json()["ground_rules"] == gr.DEFAULTS  # anyone may read
    for bad in ({"extra": "x" * 2001}, {"bogus": True}, {"grounding": "yes"}, {"extra": 5}, "x", ["a"]):
        assert client.put("/guard/org-settings", json={"ground_rules": bad}).status_code == 400, bad
    r = client.put("/guard/org-settings", json={"ground_rules": {"usage_care": False}})
    assert r.status_code == 200, r.text
    got = client.get("/guard/org-settings").json()["ground_rules"]
    assert got == gr.DEFAULTS | {"usage_care": False}
    # PUT replaces the whole object: a key left out takes its default
    client.put("/guard/org-settings", json={"ground_rules": {"extra": "hello\u202e\u200b there"}})
    got = client.get("/guard/org-settings").json()["ground_rules"]
    assert got == gr.DEFAULTS | {"extra": "hello there"}
    # a PUT without ground_rules leaves it alone
    client.put("/guard/org-settings", json={"disabled_rules": []})
    assert client.get("/guard/org-settings").json()["ground_rules"]["extra"] == "hello there"


def test_stored_garbage_reads_as_defaults(client, ground_restored):
    _set_ground({"grounding": "no", "extra": 7, "other": 1, "notes": False})
    assert client.get("/guard/org-settings").json()["ground_rules"] == gr.DEFAULTS | {"notes": False}


# ---- the run ------------------------------------------------------------------------------------


def _system_prompt(args):
    """The probe records argv one per line, so a multi-line value runs to the next flag."""
    rest = args[args.index("--append-system-prompt") + 1:]
    end = next((i for i, a in enumerate(rest) if a.startswith("--")), len(rest))
    return "\n".join(rest[:end])


def _events(tid, kind):
    async def go():
        async with SessionLocal() as s:
            rows = await s.execute(select(TaskEvent).where(TaskEvent.task_id == tid, TaskEvent.type == kind))
            return [e.payload for e in rows.scalars()]

    return _run(go())


def test_system_prompt_gets_enabled_rules(client, ground_restored):
    _set_ground({"usage_care": False, "extra": "Prefer small diffs."})
    key = env_probe_key()
    a = client.post("/agents", json={"name": f"gr-{uuid.uuid4().hex[:6]}", "system_prompt": "You are the agent.",
                                     "default_prompt": "go"}).json()
    r = client.post(f"/agents/{a['id']}/run", json={"prompt": f"hi [env-probe:{key}]"})
    assert r.status_code in (200, 201), r.text
    done = wait_task(client, r.json()["id"])
    assert done["status"] == "completed", done.get("error")
    sp = _system_prompt(read_probe_args(key))
    assert sp.startswith("You are the agent.")
    assert gr.GROUNDING in sp and gr.SHORT_ANSWERS in sp and "Prefer small diffs." in sp
    assert gr.USAGE_CARE_MAIN not in sp
    assert "keep short notes" in sp
    assert gr.QUESTIONS_RULE in sp
    assert _events(done["id"], "ground_rules") == [
        {"included": ["grounding", "short_answers", "notes", "questions", "extra"]}]


def test_all_rules_off_adds_nothing(client, ground_restored):
    _set_ground({"grounding": False, "short_answers": False, "usage_care": False, "notes": False, "questions": False})
    key = env_probe_key()
    done = wait_task(client, client.post("/tasks", json={"prompt": f"hi [env-probe:{key}]"}).json()["id"])
    assert done["status"] == "completed", done.get("error")
    assert "--append-system-prompt" not in read_probe_args(key)
    assert _events(done["id"], "ground_rules") == [{"included": []}]


def test_policy_helper_context(client, ground_restored):
    _set_ground({"usage_care": True, "grounding": False, "notes": False, "extra": "Ask first."})

    async def go():
        async with SessionLocal() as s:
            t = types.SimpleNamespace(id="t-" + uuid.uuid4().hex, root_id=None, project_id=None)
            t.root_id = t.id
            return await policy_inputs.gather(s, task=t, project=None, owner_id=None, mcp=None)

    inp = _run(go())
    p = inp.policy
    assert p["compact"] is True and p["notes"] is False
    assert p["helper_context"] == gr.USAGE_CARE_HELPER + "\n\nAsk first."  # own project: no data line
    assert gr.GROUNDING not in p["helper_context"]
    assert gr.QUESTIONS_RULE not in p["helper_context"]  # main session only
    assert inp.main_text == "\n\n".join([gr.SHORT_ANSWERS, gr.USAGE_CARE_MAIN, gr.QUESTIONS_RULE, "Ask first."])
    assert inp.ground_included == ["short_answers", "usage_care", "questions", "extra"]


# ---- F114: the precedence line (forge v0.7.0) ---------------------------------------------------


def test_precedence_line_matches_forge():
    src = (FIX / "precedence_line.js").read_text()
    m = re.search(r"const PRECEDENCE_LINE = (.*?);\n", src, re.S)
    assert m
    forge = "".join(a or b for a, b in re.findall(r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"", m.group(1)))
    assert "JSPARK Forge" in forge
    assert gr.PRECEDENCE_LINE == forge.replace("JSPARK Forge", "co-orchestrator")


def test_has_superpowers(tmp_path):
    P = types.SimpleNamespace
    assert gr.has_superpowers([P(name="Superpowers", local_path=str(tmp_path / "none"))])
    plug = tmp_path / "sp"
    (plug / ".claude-plugin").mkdir(parents=True)
    (plug / ".claude-plugin" / "plugin.json").write_text('{"name": "superpowers", "version": "5"}')
    assert gr.has_superpowers([P(name="my-pack", local_path=str(plug))])
    other = tmp_path / "other"
    (other / ".claude-plugin").mkdir(parents=True)
    (other / ".claude-plugin" / "plugin.json").write_text('{"name": "other"}')
    bad = tmp_path / "bad"
    (bad / ".claude-plugin").mkdir(parents=True)
    (bad / ".claude-plugin" / "plugin.json").write_text("{not json")
    big = tmp_path / "big"
    (big / ".claude-plugin").mkdir(parents=True)
    (big / ".claude-plugin" / "plugin.json").write_text('{"name": "superpowers", "pad": "' + "x" * 70000 + '"}')
    assert not gr.has_superpowers([P(name="o", local_path=str(other)), P(name="b", local_path=str(bad)),
                                   P(name="g", local_path=str(big)), P(name=None, local_path=None)])
    assert not gr.has_superpowers([])


def _with_pack(name, path):
    from app.models import SkillPack

    async def add():
        async with SessionLocal() as s:
            p = SkillPack(name=name, local_path=str(path), enabled=True, scope="global")
            s.add(p)
            await s.commit()
            return p.id

    return _run(add())


def _drop_pack(pid):
    from app.models import SkillPack

    async def go():
        async with SessionLocal() as s:
            row = await s.get(SkillPack, pid)
            if row is not None:
                await s.delete(row)
                await s.commit()

    _run(go())


def test_precedence_line_only_with_superpowers(client, ground_restored, tmp_path):
    _set_ground({})
    key = env_probe_key()
    done = wait_task(client, client.post("/tasks", json={"prompt": f"hi [env-probe:{key}]"}).json()["id"])
    assert done["status"] == "completed", done.get("error")
    assert gr.PRECEDENCE_LINE not in _system_prompt(read_probe_args(key))
    assert "precedence" not in _events(done["id"], "ground_rules")[0]["included"]
    plug = tmp_path / "superpowers"
    plug.mkdir()
    pid = _with_pack("superpowers", plug)
    try:
        key = env_probe_key()
        done = wait_task(client, client.post("/tasks", json={"prompt": f"hi [env-probe:{key}]"}).json()["id"])
        assert done["status"] == "completed", done.get("error")
        sp = _system_prompt(read_probe_args(key))
        assert gr.PRECEDENCE_LINE in sp and sp.index(gr.GROUNDING) < sp.index(gr.PRECEDENCE_LINE)
        assert _events(done["id"], "ground_rules") == [
            {"included": ["grounding", "short_answers", "usage_care", "notes", "questions", "precedence"]}]
    finally:
        _drop_pack(pid)
