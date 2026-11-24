"""Connected tools start from a private, empty folder (spec 2026-11-24): the
config entry, the per-task folders, their cleanup, the API switch and the
relative-command refusal, and the health count. No real tool or claude runs."""

import asyncio
import json
import os
import shutil
import stat
import subprocess
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


STOPPERS = [".npmrc", "node_modules", "package.json", "uv.toml"]


def _assert_only_stoppers(d: Path):
    """The folder holds exactly the inert marker files, private, with their fixed content."""
    d = Path(d)
    assert sorted(os.listdir(d)) == STOPPERS
    assert (d / "package.json").read_text().strip() == "{}"
    assert (d / ".npmrc").read_text() == "" and (d / "uv.toml").read_text() == ""
    assert (d / "node_modules").is_dir() and os.listdir(d / "node_modules") == []
    for n in (".npmrc", "package.json", "uv.toml"):
        assert stat.S_IMODE((d / n).stat().st_mode) == 0o600, n
        assert not (d / n).is_symlink()
    assert stat.S_IMODE((d / "node_modules").stat().st_mode) == 0o700


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


def test_same_prefix_tools_ignoring_case_get_their_own_folders(tmp_path):
    a, b = _srv("Tool", command="x"), _srv("tool", command="y")
    cfg = mcp_manager.build_config([a, b], task_cwd_root=tmp_path)
    da, db = Path(cfg["mcpServers"]["Tool"]["args"][2]), Path(cfg["mcpServers"]["tool"]["args"][2])
    assert (da.name, db.name) == ("Tool", "tool-2")
    assert da.is_dir() and db.is_dir() and not os.path.samefile(da, db)


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
            assert d.is_dir()
            _assert_only_stoppers(d)
            assert stat.S_IMODE(d.stat().st_mode) == 0o700
            assert stat.S_IMODE(d.parent.stat().st_mode) == 0o700
            assert stat.S_IMODE(cdir.stat().st_mode) == 0o700
            assert d.parent == (cdir / "cwd").resolve()
        assert e1["env"]["CO_PROJECT_DIR"] == "/proj/one"
        assert "CO_PROJECT_DIR" not in (e2.get("env") or {})
        # a re-run of the same task starts from an empty folder again
        (d1 / "left-over").write_text("x")
        (d1 / "node_modules" / "x").mkdir()
        (d1 / "package.json").write_text('{"name": "planted"}')
        _, again = _prepare(name, t1)
        assert Path(again["args"][2]) == d1
        _assert_only_stoppers(d1)
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


# ---- walk-up stoppers (review: npm climbs to workspaces/) ------------------


def _plant(ws: Path, at: Path):
    (at / "node_modules" / "some-mcp").mkdir(parents=True, exist_ok=True)
    (at / ".npmrc").write_text("registry=https://evil.invalid/\n")


def _npm(npm, cwd, *args):
    env = dict(os.environ, npm_config_update_notifier="false")
    env.pop("npm_config_prefix", None)
    env.pop("NPM_CONFIG_PREFIX", None)
    r = subprocess.run([npm, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_npm_does_not_climb_out_of_the_tool_folder(tmp_path):
    """The reviewer's check: node_modules + .npmrc planted in workspaces/ (and two
    levels above the tool folder) must not become npm's project. Only `npm prefix`
    and `npm config get` run (local, read-only); skipped without npm."""
    ws = tmp_path / "ws"
    root = ws / "_mcp_configs" / "t1" / "cwd"
    root.mkdir(parents=True)
    cfg = mcp_manager.build_config([_srv("tool", command="npx", args=["-y", "some-mcp@1"])], task_cwd_root=root)
    d = Path(cfg["mcpServers"]["tool"]["args"][2])
    _assert_only_stoppers(d)
    _plant(ws, ws)
    _plant(ws, ws / "_mcp_configs" / "t1")  # two levels up from the tool folder
    npm = shutil.which("npm")
    if not npm:
        import pytest

        pytest.skip("npm is not on PATH (the stopper files are checked above)")
    # control: a folder without the markers, next to the tool's, climbs to the plant
    bare = root / "bare"
    bare.mkdir()
    assert os.path.realpath(_npm(npm, bare, "prefix")) == os.path.realpath(ws / "_mcp_configs" / "t1")
    assert _npm(npm, bare, "config", "get", "registry") == "https://evil.invalid/"
    # the tool folder is its own project; the planted registry is not read
    assert os.path.realpath(_npm(npm, d, "prefix")) == os.path.realpath(d)
    assert "evil.invalid" not in _npm(npm, d, "config", "get", "registry")


def test_codemap_run_folder_has_the_stoppers(client):
    from app.codemap import indexer

    pid = uuid.uuid4().hex
    d = indexer.run_dir(pid)
    try:
        _assert_only_stoppers(d)
        assert stat.S_IMODE(d.stat().st_mode) == 0o700
        (d / "node_modules" / "planted").mkdir()
        (d / ".npmrc").write_text("registry=https://evil.invalid/\n")
        _assert_only_stoppers(indexer.run_dir(pid))  # each run puts them back exactly
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_guard_protects_walkup_markers_in_workspaces(tmp_path):
    from app.guard import runtime

    ws = settings.workspaces_dir.resolve()
    pol = runtime.build_policy(task_id="t-walkup", project=None, workspace=tmp_path, cwd=tmp_path,
                               claude_config_dir=None, mcp_tools=None)
    prot = set(pol["protected_paths"])
    for n in ("node_modules", "package.json", "package-lock.json", ".npmrc", "uv.toml", "pyproject.toml",
              "bunfig.toml"):
        assert str(ws / n) in prot, n


def test_guard_blocks_writing_a_marker_into_workspaces(tmp_path):
    """The rule engine refuses a write to workspaces/node_modules/... from an ad-hoc task."""
    from app.guard import rules, runtime

    ws = settings.workspaces_dir.resolve()
    cwd = ws / f"t{_uid()}"
    cwd.mkdir(parents=True)
    try:
        pol = runtime.build_policy(task_id="t-walkup2", project=None, workspace=cwd, cwd=cwd,
                                   claude_config_dir=None, mcp_tools=None)
        for target in (ws / "node_modules" / "some-mcp" / "package.json", ws / ".npmrc"):
            v = rules.evaluate("Write", {"file_path": str(target), "content": "x"}, pol, str(cwd))
            assert v.decision != "allow", target
        assert rules.evaluate("Write", {"file_path": str(cwd / "package.json"), "content": "{}"},
                              pol, str(cwd)).decision == "allow"  # its own folder stays writable
    finally:
        shutil.rmtree(cwd, ignore_errors=True)


# ---- boot sweep -------------------------------------------------------------


def test_boot_sweep_removes_configs_of_tasks_not_running(client):
    from app.models import Task

    keep = f"k{_uid()}"
    _add(Task(id=keep, title="x", prompt="x", status="queued"))
    gone_done = f"d{_uid()}"
    _add(Task(id=gone_done, title="x", prompt="x", status="completed"))
    unknown = f"u{_uid()}"
    for tid in (keep, gone_done, unknown):
        (mcp_manager.task_config_dir(tid) / "cwd").mkdir(parents=True)
    try:
        n = asyncio.run(mcp_manager.sweep_stale_configs())
        assert n >= 2
        assert mcp_manager.task_config_dir(keep).is_dir()
        assert not mcp_manager.task_config_dir(gone_done).exists()
        assert not mcp_manager.task_config_dir(unknown).exists()
    finally:
        mcp_manager.cleanup_task(keep)

        async def rm():
            async with SessionLocal() as s:
                for tid in (keep, gone_done):
                    t = await s.get(Task, tid)
                    if t is not None:
                        await s.delete(t)
                await s.commit()

        asyncio.run(rm())


def test_boot_sweep_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(mcp_manager, "SessionLocal", boom)
    (mcp_manager.task_config_dir(f"z{_uid()}")).mkdir(parents=True, exist_ok=True)
    assert asyncio.run(mcp_manager.sweep_stale_configs()) == 0


# ---- project folders that are the orchestrator's own ------------------------

ORCH = "That folder is part of the orchestrator itself"


def test_project_folder_inside_the_orchestrator_is_refused(client, tmp_path):
    from app.guard.runtime import BACKEND_DIR

    bad = [BACKEND_DIR / "app", BACKEND_DIR / "app" / "sub", BACKEND_DIR / "migrations",
           BACKEND_DIR / ".venv" / "x", Path(sys.prefix), settings.workspaces_dir / "_mcp_configs",
           settings.workspaces_dir / "_tasks" / "y", settings.workspaces_dir / "_newthing",
           settings.workspaces_dir, BACKEND_DIR]  # the last two hold internals
    (settings.workspaces_dir / "_newthing").mkdir(parents=True, exist_ok=True)
    for d in bad:
        r = client.post("/projects", json={"name": f"orch-{_uid()}", "directory": str(d)})
        assert r.status_code == 422 and r.json()["detail"] == ORCH, (d, r.text)
    assert not (BACKEND_DIR / "app" / "sub").exists()  # refused before anything is created
    # through a symlink too (real paths)
    link = tmp_path / "link"
    link.symlink_to(BACKEND_DIR / "app")
    r = client.post("/projects", json={"name": f"orch-{_uid()}", "directory": str(link)})
    assert r.status_code == 422 and r.json()["detail"] == ORCH
    # a plain folder is fine; moving it into the server is refused on update
    ok = client.post("/projects", json={"name": f"orch-{_uid()}", "directory": str(tmp_path / "fine")})
    assert ok.status_code == 201, ok.text
    pid = ok.json()["id"]
    r = client.patch(f"/projects/{pid}", json={"directory": str(BACKEND_DIR / "migrations")})
    assert r.status_code == 422 and r.json()["detail"] == ORCH
    assert client.get(f"/projects/{pid}").json()["directory"] == str((tmp_path / "fine").resolve())
    # import
    r = client.post("/projects/import", json={"directories": [str(BACKEND_DIR / "app")]})
    assert r.status_code == 422 and r.json()["detail"] == ORCH
