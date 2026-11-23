"""Phase 4c Task 2: helper presets and the non-blocking tier lint."""

import uuid

from app.resume import ground_rules
from app.routers.agents import PRESETS
from app.tiers import DEFAULT_TIERS, lint_agent

NO_USE = 'the description should say when to use this agent ("Use for …")'
NO_WHY = 'the description should say why ("… because …")'
GOOD = "Use for X because Y"


def _tag():
    return uuid.uuid4().hex[:8]


def _preset(name):
    return next(p for p in PRESETS if p["name"] == name)


def test_lint_texts():
    assert lint_agent("opus", GOOD, DEFAULT_TIERS) == []
    assert lint_agent("claude-opus-4-1", GOOD, DEFAULT_TIERS) == [
        'model "claude-opus-4-1" is not one of the org\'s tier models (opus, sonnet, haiku)'
    ]
    assert lint_agent(None, "Reviews code", DEFAULT_TIERS) == [NO_USE, NO_WHY]
    assert lint_agent("", GOOD, DEFAULT_TIERS) == []
    assert lint_agent(None, "USE FOR x BECAUSE y", DEFAULT_TIERS) == []
    assert lint_agent(None, "Use for x", DEFAULT_TIERS) == [NO_WHY]
    assert lint_agent(None, None, DEFAULT_TIERS) == [NO_USE, NO_WHY]
    same = {"strongest": "opus", "standard": "opus", "light": "haiku"}
    assert lint_agent("sonnet", GOOD, same) == [
        'model "sonnet" is not one of the org\'s tier models (opus, haiku)'
    ]


def test_lint_never_blocks(client):
    r = client.post("/agents", json={"name": f"l-{_tag()}", "system_prompt": "x", "description": "x"})
    assert r.status_code == 201, r.text
    assert r.json()["lint"] == [NO_USE, NO_WHY]
    aid = r.json()["id"]
    p = client.patch(f"/agents/{aid}", json={"description": GOOD})
    assert p.status_code == 200 and p.json()["lint"] == []
    rows = client.get("/agents").json()
    assert all("lint" in a for a in rows)
    assert next(a for a in rows if a["id"] == aid)["lint"] == []


def test_helper_presets():
    want = {"Planner": ("strongest", False), "Reviewer": ("strongest", True),
            "Builder": ("standard", False), "Checker": ("light", True)}
    for name, (tier, ro) in want.items():
        p = _preset(name)
        assert p["tier"] == tier and bool(p.get("read_only")) == ro, name
        assert p["category"] == "Helpers"
        assert "End with Outputs" in p["system_prompt"], name
    for name in ("Builder", "Checker"):
        assert _preset(name)["system_prompt"].endswith(ground_rules.USAGE_CARE_HELPER)
    assert "Never plan against a recorded project decision" in _preset("Planner")["system_prompt"]
    r = _preset("Reviewer")["system_prompt"]
    assert "H (must fix)" in r and "file:line" in r
    b = _preset("Builder")["system_prompt"]
    assert "Test first" in b and "CAN change" in b
    assert "facts only" in _preset("Checker")["system_prompt"]
    assert _preset("Builder")["default_prompt"] == "Implement the brief."


def test_all_presets_pass_lint():
    for p in PRESETS:
        assert lint_agent(p.get("model"), p["description"], DEFAULT_TIERS) == [], p["name"]
        assert not (p.get("model") and p.get("tier")), p["name"]
    assert _preset("Code Reviewer")["tier"] == "light"
    assert "model" not in _preset("Backend Developer")


def _from_preset(client, name):
    p = _preset(name)
    body = {"name": f"{name}-{_tag()}", "system_prompt": p["system_prompt"],
            "default_prompt": p["default_prompt"], "tier": p["tier"],
            "read_only": bool(p.get("read_only"))}
    r = client.post("/agents", json=body)
    assert r.status_code == 201, r.text
    return client.post(f"/agents/{r.json()['id']}/run", json={}).json()


def test_preset_seeds_tier(client):
    b = _from_preset(client, "Builder")
    assert b["model"] == "sonnet" and b["read_only"] is False
    c = _from_preset(client, "Checker")
    assert c["model"] == "haiku" and c["read_only"] is True
