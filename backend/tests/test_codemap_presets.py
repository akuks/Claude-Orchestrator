"""MCP presets and code map server detection (phase 6b)."""

from types import SimpleNamespace as NS

from app import mcp_manager
from app.codemap import status as cm_status
from conftest import login_as


def test_presets_exact(client):
    r = client.get("/mcp/presets")
    assert r.status_code == 200
    by = {p["name"]: p for p in r.json()}
    assert list(by) == ["Code map", "Browser (Playwright)"]
    cm, br = by["Code map"], by["Browser (Playwright)"]
    assert (cm["transport"], cm["command"], cm["args"], cm["pinned"]) == (
        "stdio", "npx", ["-y", "codebase-memory-mcp@0.12.1"], "0.12.1")
    assert (br["transport"], br["command"], br["args"], br["pinned"]) == (
        "stdio", "npx", ["-y", "@playwright/mcp@0.0.86"], "0.0.86")
    for p in (cm, br):
        assert set(p) == {"key", "name", "description", "transport", "command", "args", "pinned", "start_in_project"}
        assert p["start_in_project"] is False


def test_presets_open_to_members_not_anonymous(client):
    m = login_as("preset-member@example.com")
    assert m.get("/mcp/presets").status_code == 200
    from fastapi.testclient import TestClient

    from app.main import app

    assert TestClient(app).get("/mcp/presets").status_code in (401, 403)


def test_detection_by_name_and_command():
    a = NS(name="docs", command="npx", args=["-y", "other"], enabled=True)
    b = NS(name="my-codebase-memory", command="x", args=[], enabled=True)
    c = NS(name="cm", command="npx", args=["-y", "codebase-memory-mcp@0.12.1"], enabled=True)
    assert cm_status.code_map_server([a]) is None
    assert cm_status.code_map_server([a, b]) is b
    assert cm_status.code_map_server([a, c]) is c
    off = NS(name="codebase-memory", command="x", args=[], enabled=False)
    assert cm_status.code_map_server([off, c]) is c


def test_tool_prefix_matches_worker_name():
    s = NS(name="code.map v2", command="x", args=[])
    assert cm_status.tool_prefix(s) == "mcp__code_map_v2__"
    assert cm_status.tool_prefix(s) == mcp_manager.mcp_tool_ref(s.name, "")
    assert cm_status.tool_prefix(NS(name="codebase-memory")) == "mcp__codebase-memory__"
