"""Connected tools start from a private, empty folder (spec 2026-11-24): the
config entry, the per-task folders, their cleanup, the API switch and the
relative-command refusal, and the health count. No real tool or claude runs."""

import asyncio
import json
import stat
import sys
import uuid
from pathlib import Path

from sqlalchemy import select

from app import mcp_manager
from app.config import settings
from app.crypto import encrypt_json
from app.database import SessionLocal
from app.models import McpServer
from conftest import login_as, wait_task

REFUSAL = "A command that starts with ./ needs 'Start in the project folder'"


def _uid():
    return uuid.uuid4().hex[:8]


def _srv(name, **kw):
    kw.setdefault("transport", "stdio")
    kw.setdefault("scope", "team")
    kw.setdefault("enabled", True)
    kw.setdefault("args", [])
    return McpServer(name=name, **kw)


def _add(*rows):
    async def go():
        async with SessionLocal() as s:
            for r in rows:
                s.add(r)
            await s.commit()

    asyncio.run(go())


def _drop(*names):
    async def go():
        async with SessionLocal() as s:
            for r in (await s.execute(select(McpServer).where(McpServer.name.in_(names)))).scalars():
                await s.delete(r)
            await s.commit()

    asyncio.run(go())


# ---- the config entry -------------------------------------------------------


def test_switch_off_wraps_with_the_launcher(tmp_path):
    srv = _srv("tool-a", command="npx", args=["-y", "some-mcp@1.2.3", "a b"],
               secrets_encrypted=encrypt_json({"env": {"TOKEN": "t"}}))
    root = tmp_path / "cwd"
    root.mkdir()
    cfg = mcp_manager.build_config([srv], task_cwd_root=root, project_dir="/proj/x")
    d = mcp_manager.neutral_dir(root, srv)
    assert cfg == {"mcpServers": {"tool-a": {
        "command": sys.executable,
        "args": ["-I", mcp_manager.launcher_path(), str(d), "--", "npx", "-y", "some-mcp@1.2.3", "a b"],
        "env": {"TOKEN": "t", "CO_PROJECT_DIR": "/proj/x"},
    }}}
    assert d == (root / "tool-a").resolve()


def test_switch_off_without_env_or_project_dir(tmp_path):
    srv = _srv("tool-b", command="uvx", args=["thing"])
    cfg = mcp_manager.build_config([srv], task_cwd_root=tmp_path)
    entry = cfg["mcpServers"]["tool-b"]
    assert "env" not in entry and entry["command"] == sys.executable
    assert entry["args"][-3:] == ["--", "uvx", "thing"]


def test_switch_on_is_byte_identical_to_the_old_shape(tmp_path):
    sec = encrypt_json({"env": {"TOKEN": "t"}})
    on = _srv("tool-c", command="./bin/tool", args=["--x"], secrets_encrypted=sec, start_in_project=True)
    old = _srv("tool-c", command="./bin/tool", args=["--x"], secrets_encrypted=sec)
    new_cfg = mcp_manager.build_config([on], task_cwd_root=tmp_path, project_dir="/proj/x")
    old_cfg = mcp_manager.build_config([old])  # the old single-argument call: no wrapping
    assert json.dumps(new_cfg, indent=2) == json.dumps(old_cfg, indent=2)
    assert old_cfg == {"mcpServers": {"tool-c": {"command": "./bin/tool", "args": ["--x"], "env": {"TOKEN": "t"}}}}
    assert list(tmp_path.iterdir()) == []  # no folder for a tool that starts in the project


def test_old_single_argument_call_does_no_wrapping():
    srv = _srv("tool-d", command="npx", args=["-y", "x"])
    assert mcp_manager.build_config([srv]) == {"mcpServers": {"tool-d": {"command": "npx", "args": ["-y", "x"]}}}


def test_http_server_is_unchanged(tmp_path):
    srv = _srv("web", transport="http", url="https://mcp.example/x",
               secrets_encrypted=encrypt_json({"headers": {"Authorization": "Bearer z"}}))
    want = {"mcpServers": {"web": {"type": "http", "url": "https://mcp.example/x",
                                   "headers": {"Authorization": "Bearer z"}}}}
    assert mcp_manager.build_config([srv], task_cwd_root=tmp_path, project_dir="/p") == want
    assert mcp_manager.build_config([srv]) == want
    assert list(tmp_path.iterdir()) == []


def test_odd_names_give_a_safe_folder_inside_the_root(tmp_path):
    root = (tmp_path / "cwd").resolve()
    for name in ("../../etc", "a b/c", "..", ".", "", "x\\..\\y", "github.com"):
        d = mcp_manager.neutral_dir(root, _srv(name, command="x"))
        assert d.parent == root, name
        assert d.name == (mcp_manager.mcp_server_prefix(name) or "_"), name
        assert "/" not in d.name and d.name not in (".", "..")


def test_same_prefix_tools_get_their_own_folders(tmp_path):
    a, b = _srv("a.b", command="x"), _srv("a_b", command="y")
    cfg = mcp_manager.build_config([a, b], task_cwd_root=tmp_path)
    da, db = cfg["mcpServers"]["a.b"]["args"][2], cfg["mcpServers"]["a_b"]["args"][2]
    assert da != db and Path(da).name == "a_b" and Path(db).name == "a_b-2"


# ---- per-task folders -------------------------------------------------------


def _prepare(name, task_id, project_dir=None):
    config_dir = mcp_manager.task_config_dir(task_id)
    mcp = asyncio.run(mcp_manager.prepare_for_task(None, config_dir, project_dir=project_dir))
    cfg = json.loads(Path(mcp["config_path"]).read_text())
    return config_dir, cfg["mcpServers"][name]


def test_prepare_makes_a_private_empty_folder_per_task(client):
    name = f"nc-{_uid()}"
    _add(_srv(name, command="npx", args=["-y", "x@1"]))
    t1, t2 = f"t{_uid()}", f"t{_uid()}"
    try:
        cdir1, e1 = _prepare(name, t1, project_dir="/proj/one")
        cdir2, e2 = _prepare(name, t2)
        d1, d2 = Path(e1["args"][2]), Path(e2["args"][2])
        assert d1 != d2
        for cdir, d in ((cdir1, d1), (cdir2, d2)):
            assert d.is_dir() and list(d.iterdir()) == []
            assert stat.S_IMODE(d.stat().st_mode) == 0o700
            assert stat.S_IMODE(d.parent.stat().st_mode) == 0o700
            assert d.parent == (cdir / "cwd").resolve()
        assert e1["env"]["CO_PROJECT_DIR"] == "/proj/one"
        assert "CO_PROJECT_DIR" not in (e2.get("env") or {})
        # a re-run of the same task starts from an empty folder again
        (d1 / "left-over").write_text("x")
        _, again = _prepare(name, t1)
        assert Path(again["args"][2]) == d1 and list(d1.iterdir()) == []
    finally:
        _drop(name)
        mcp_manager.cleanup_task(t1)
        mcp_manager.cleanup_task(t2)
    assert not cdir1.exists() and not cdir2.exists()


def test_task_run_passes_the_project_folder_and_cleans_up(client, monkeypatch):
    """A real task (mock claude): the config names the launcher and the folder
    claude starts in, and after the task its config folder - with every tool's
    start folder - is gone."""
    name = f"run-{_uid()}"
    _add(_srv(name, command="npx", args=["-y", "x@1"]))
    seen = {}
    real = mcp_manager.prepare_for_task

    async def spy(project, config_dir, project_dir=None):
        out = await real(project, config_dir, project_dir=project_dir)
        seen["config_dir"] = Path(config_dir)
        seen["project_dir"] = project_dir
        seen["entry"] = json.loads(Path(out["config_path"]).read_text())["mcpServers"][name]
        return out

    monkeypatch.setattr(mcp_manager, "prepare_for_task", spy)
    try:
        r = client.post("/projects", json={"name": f"nc-{_uid()}"})
        assert r.status_code == 201, r.text
        proj = r.json()
        r = client.post("/tasks", json={"prompt": "hello", "project_id": proj["id"]})
        assert r.status_code == 201, r.text
        tid = r.json()["id"]
        done = wait_task(client, tid)
        assert done["status"] == "completed", done.get("error")
        assert seen["config_dir"] == settings.workspaces_dir / "_mcp_configs" / tid
        assert Path(seen["project_dir"]).resolve() == Path(proj["directory"]).resolve()
        entry = seen["entry"]
        assert entry["command"] == sys.executable
        assert entry["args"][:2] == ["-I", mcp_manager.launcher_path()]
        assert entry["env"]["CO_PROJECT_DIR"] == seen["project_dir"]
        assert not seen["config_dir"].exists()  # removed with the task
    finally:
        _drop(name)


# ---- API --------------------------------------------------------------------


def test_api_switch_admin_only_and_relative_command(client):
    a = client
    m = login_as(f"nc-member-{_uid()}@test.local")
    # a bare name is fine; the switch defaults off and reads back
    r = a.post("/mcp/servers", json={"name": f"nc-{_uid()}", "command": "npx", "args": ["-y", "x"]})
    assert r.status_code == 201, r.text
    srv = r.json()
    assert srv["start_in_project"] is False
    # absolute paths are fine
    assert a.post("/mcp/servers", json={"name": f"nc-{_uid()}", "command": "/usr/bin/env"}).status_code == 201
    # ./bin/x and bin/x are refused with the exact text, unless the switch is on
    for cmd in ("./bin/x", "bin/x", "../x"):
        r = a.post("/mcp/servers", json={"name": f"nc-{_uid()}", "command": cmd})
        assert r.status_code == 400 and r.json()["detail"] == REFUSAL, cmd
    r = a.post("/mcp/servers", json={"name": f"nc-{_uid()}", "command": "./bin/x", "start_in_project": True})
    assert r.status_code == 201 and r.json()["start_in_project"] is True
    on = r.json()
    # update: turning the switch off under a relative command is refused, and so is a relative command
    r = a.patch(f"/mcp/servers/{on['id']}", json={"start_in_project": False})
    assert r.status_code == 400 and r.json()["detail"] == REFUSAL
    assert a.get(f"/mcp/servers/{on['id']}").json()["start_in_project"] is True
    r = a.patch(f"/mcp/servers/{srv['id']}", json={"command": "./bin/y"})
    assert r.status_code == 400 and r.json()["detail"] == REFUSAL
    r = a.patch(f"/mcp/servers/{srv['id']}", json={"start_in_project": True})
    assert r.status_code == 200 and r.json()["start_in_project"] is True
    r = a.patch(f"/mcp/servers/{srv['id']}", json={"start_in_project": None})  # null leaves it
    assert r.status_code == 200 and r.json()["start_in_project"] is True
    # an http server's url is never a "command"
    assert a.post("/mcp/servers", json={"name": f"nc-{_uid()}", "transport": "http",
                                        "url": "http://x/y"}).status_code == 201
    # members cannot set it
    assert m.patch(f"/mcp/servers/{srv['id']}", json={"start_in_project": False}).status_code == 403
    assert m.post("/mcp/servers", json={"name": f"nc-{_uid()}", "command": "npx",
                                        "start_in_project": True}).status_code == 403
    assert m.get(f"/mcp/servers/{srv['id']}").json()["start_in_project"] is True
    # presets carry the field, off
    presets = a.get("/mcp/presets").json()
    assert presets and all(p["start_in_project"] is False for p in presets)


# ---- health -----------------------------------------------------------------


def test_health_counts_tools_that_start_in_the_project(client):
    from app.health import checks

    check = next(c for c in checks.CHECKS if c.id == "mcp")
    base = asyncio.run(check.fn(SessionLocal))
    n0 = base.detail["start_in_project"]
    names = [f"hc-in-{_uid()}", f"hc-in-{_uid()}"]
    _add(*(_srv(n, command="./bin/x", start_in_project=True) for n in names))
    try:
        r = asyncio.run(check.fn(SessionLocal))
        assert r.detail["start_in_project"] == n0 + 2
        assert r.summary.endswith(f"; {n0 + 2} start(s) in the project folder")
        assert r.status == base.status  # an admin's choice: never attention by itself
    finally:
        _drop(*names)
    r = asyncio.run(check.fn(SessionLocal))
    assert r.detail["start_in_project"] == n0
    assert ("start(s) in the project folder" in r.summary) == bool(n0)
