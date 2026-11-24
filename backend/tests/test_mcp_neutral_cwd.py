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


def _tid():
    """A task id as the app makes them (uuid4 hex): the only names cleanup removes."""
    return uuid.uuid4().hex


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


STOPPERS = [".npmrc", "node_modules", "uv.toml"]  # no package.json: an ancestor "workspaces" glob would adopt it


def _assert_only_stoppers(d: Path):
    """The folder holds exactly the inert marker files, private, with their fixed content."""
    d = Path(d)
    assert sorted(os.listdir(d)) == STOPPERS
    assert (d / ".npmrc").read_text() == "" and (d / "uv.toml").read_text() == ""
    assert (d / "node_modules").is_dir() and os.listdir(d / "node_modules") == []
    for n in (".npmrc", "uv.toml"):
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
        "env": {"TOKEN": "t", "CO_PROJECT_DIR": "/proj/x", "npm_config_cache": str(mcp_manager.npm_cache_dir()),
                "npm_config_prefix": str(mcp_manager.npm_global_dir())},
    }}}
    assert d == (root / "tool-a").resolve()


def test_tools_get_their_own_npm_cache(tmp_path):
    cache = mcp_manager.npm_cache_dir()
    assert cache == settings.workspaces_dir / "_mcp_cache" / "npm"
    assert stat.S_IMODE(cache.stat().st_mode) == 0o700
    assert stat.S_IMODE(cache.parent.stat().st_mode) == 0o700
    # the tool's own setting wins, in either spelling
    for key in ("npm_config_cache", "NPM_CONFIG_CACHE"):
        srv = _srv(f"own-{key}", command="npx", secrets_encrypted=encrypt_json({"env": {key: "/mine"}}))
        env = mcp_manager.build_config([srv], task_cwd_root=tmp_path)["mcpServers"][srv.name]["env"]
        assert env == {key: "/mine", "npm_config_prefix": str(mcp_manager.npm_global_dir())}, key
    for key in ("npm_config_prefix", "NPM_CONFIG_PREFIX"):
        srv = _srv(f"own-{key}", command="npx", secrets_encrypted=encrypt_json({"env": {key: "/g"}}))
        env = mcp_manager.build_config([srv], task_cwd_root=tmp_path)["mcpServers"][srv.name]["env"]
        assert env == {key: "/g", "npm_config_cache": str(mcp_manager.npm_cache_dir())}, key
    glob = mcp_manager.npm_global_dir()
    assert glob == settings.workspaces_dir / "_mcp_cache" / "npm-global"
    assert stat.S_IMODE(glob.stat().st_mode) == 0o700
    # a tool that starts in the project, and the old call, are left exactly as they were
    on = _srv("on", command="npx", start_in_project=True)
    assert mcp_manager.build_config([on], task_cwd_root=tmp_path)["mcpServers"]["on"] == {"command": "npx",
                                                                                         "args": []}


def test_codemap_build_keeps_its_own_private_npm_cache():
    """The code map build already has a private HOME (so ~/.npm is its own) under
    the guard-protected _codemap folder; nothing points it at the shared cache."""
    from app.codemap import indexer

    env = indexer.index_env(None)
    home = Path(env["HOME"])
    assert home == settings.workspaces_dir / "_codemap" / "_home"
    assert not any(k.lower() == "npm_config_cache" for k in env)
    assert env["npm_config_prefix"] == str(mcp_manager.npm_global_dir())  # not the server user's global folder


def test_switch_off_without_env_or_project_dir(tmp_path):
    srv = _srv("tool-b", command="uvx", args=["thing"])
    cfg = mcp_manager.build_config([srv], task_cwd_root=tmp_path)
    entry = cfg["mcpServers"]["tool-b"]
    assert entry["env"] == {"npm_config_cache": str(mcp_manager.npm_cache_dir()),
                            "npm_config_prefix": str(mcp_manager.npm_global_dir())}
    assert entry["command"] == sys.executable
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
    t1, t2 = _tid(), _tid()
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
        (d / "package.json").write_text("{}")  # the stopper an earlier version wrote
        _assert_only_stoppers(indexer.run_dir(pid))  # each run puts them back exactly
    finally:
        shutil.rmtree(d, ignore_errors=True)


MARKERS = ("package.json", "package-lock.json", "node_modules", ".npmrc", "pnpm-workspace.yaml", ".pnpmfile.cjs",
           "uv.toml", "bunfig.toml", ".yarnrc.yml")


def _ancestor_dirs(*roots):
    out = []
    for r in roots:
        for form in {os.path.normpath(os.path.abspath(str(r))), os.path.realpath(str(r))}:
            cur = Path(form).parent
            while True:
                out.append(cur)
                if cur.parent == cur:
                    break
                cur = cur.parent
    return list(dict.fromkeys(out))


def _policy(project_dir, workspace):
    import types

    from app.guard import runtime

    proj = types.SimpleNamespace(directory=str(project_dir)) if project_dir else None
    return runtime.build_policy(task_id=f"t-{_uid()}", project=proj, workspace=Path(workspace),
                                cwd=Path(project_dir or workspace), claude_config_dir=None, mcp_tools=None)


def _prod_layout(monkeypatch):
    """workspaces inside backend/, as in production (the tests keep it in a temp dir)."""
    from app.guard.runtime import BACKEND_DIR

    monkeypatch.setattr(settings, "workspaces_dir", BACKEND_DIR / "workspaces")
    return BACKEND_DIR / "workspaces"


def test_guard_protects_walkup_names_above_tool_folders_adhoc(tmp_path):
    ws = settings.workspaces_dir
    pol = _policy(None, ws / f"t{_uid()}")
    prot = set(pol["protected_paths"])
    anc = _ancestor_dirs(ws / "_mcp_configs", ws / "_codemap" / "run")
    assert Path("/") in anc and ws.resolve() in anc
    for a in anc:
        for n in MARKERS:
            assert str(a / n) in prot, (a, n)
    assert str(ws.resolve() / "_mcp_cache") in prot


def test_guard_protects_walkup_names_in_every_ancestor_for_a_normal_project(tmp_path, monkeypatch):
    from app.guard import rules, runtime

    ws = _prod_layout(monkeypatch)
    checkout = runtime.BACKEND_DIR.parent
    home = Path(runtime.real_home())
    proj = tmp_path / "proj"
    proj.mkdir()
    pol = _policy(proj, proj)
    prot = set(pol["protected_paths"])
    anc = _ancestor_dirs(ws / "_mcp_configs", ws / "_codemap" / "run")
    for must in (ws, runtime.BACKEND_DIR, checkout, Path("/")):
        assert must.resolve() in [a.resolve() for a in anc], must
    if checkout.resolve().is_relative_to(home):
        assert home in anc or home.resolve() in anc
    for a in anc:
        for n in MARKERS:
            assert str(a / n) in prot, (a, n)
    # the rule engine: planting the npm PATH escape or a workspaces glob is not allowed
    for target in (checkout / "node_modules" / ".bin" / "node", checkout / "package.json",
                   runtime.BACKEND_DIR / "node_modules" / ".bin" / "node", runtime.BACKEND_DIR / "pnpm-workspace.yaml",
                   home / "node_modules" / ".bin" / "node", home / "package.json"):
        v = rules.evaluate("Write", {"file_path": str(target), "content": "x"}, pol, str(proj))
        assert v.decision != "allow", target
    # the project's own package files stay its own
    assert rules.evaluate("Write", {"file_path": str(proj / "package.json"), "content": "{}"},
                          pol, str(proj)).decision == "allow"


def test_checkout_root_project_keeps_its_own_package_files(monkeypatch):
    """The owner's orchestrator project (its folder is the checkout, which holds
    workspaces/): its own root names stay writable (the stated residual); the
    names above it are protected."""
    from app.guard import runtime

    ws = _prod_layout(monkeypatch)
    checkout = runtime.BACKEND_DIR.parent.resolve()
    pol = _policy(checkout, checkout)
    prot = set(pol["protected_paths"])
    for inside in (checkout, runtime.BACKEND_DIR.resolve(), ws.resolve()):
        for n in MARKERS:
            assert str(inside / n) not in prot, (inside, n)
    for a in _ancestor_dirs(checkout / "x"):  # every folder above the checkout
        if a == checkout:
            continue
        for n in MARKERS:
            assert str(a / n) in prot, (a, n)
    # the core is still protected for it
    assert str(runtime.BACKEND_DIR / "app") in prot


def test_core_is_protected_even_if_the_project_folder_resolves_inside_it(tmp_path):
    """A project folder swapped for a symlink into backend/app after the save-time
    check must not switch the core's protection off."""
    from app.guard import rules, runtime

    link = tmp_path / "proj-link"
    link.symlink_to(runtime.BACKEND_DIR / "app")
    pol = _policy(link, link)
    prot = set(pol["protected_paths"])
    for n in ("app", "migrations", "alembic.ini", ".env", ".venv", "requirements.txt"):
        assert str(runtime.BACKEND_DIR / n) in prot, n
    v = rules.evaluate("Write", {"file_path": str(runtime.BACKEND_DIR / "app" / "mcp_launch.py"), "content": "x"},
                       pol, str(link))
    assert v.decision == "deny"
    assert str(runtime.BACKEND_DIR / "app") in runtime.orchestrator_paths(
        str(runtime.BACKEND_DIR / "app" / "x"), str(runtime.BACKEND_DIR / "app" / "x"))


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

    keep = _tid()
    _add(Task(id=keep, title="x", prompt="x", status="queued"))
    gone_done = _tid()
    _add(Task(id=gone_done, title="x", prompt="x", status="completed"))
    unknown = _tid()
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
    (mcp_manager.task_config_dir(_tid())).mkdir(parents=True, exist_ok=True)
    assert asyncio.run(mcp_manager.sweep_stale_configs()) == 0


# ---- project folders that are the orchestrator's own ------------------------

ORCH = "That folder is part of the orchestrator itself"


def test_project_folder_inside_the_orchestrator_is_refused(client, tmp_path):
    from app.guard.runtime import BACKEND_DIR

    (settings.workspaces_dir / "_newthing").mkdir(parents=True, exist_ok=True)
    bad = [BACKEND_DIR / "app", BACKEND_DIR / "app" / "sub", BACKEND_DIR / "migrations",
           BACKEND_DIR / ".venv" / "x", Path(sys.prefix), settings.workspaces_dir / "_mcp_configs",
           settings.workspaces_dir / "_tasks" / "x", settings.workspaces_dir / "_newthing"]
    for d in bad:
        r = client.post("/projects", json={"name": f"orch-{_uid()}", "directory": str(d)})
        assert r.status_code == 422 and r.json()["detail"] == ORCH, (d, r.text)
    assert not (BACKEND_DIR / "app" / "sub").exists()  # refused before anything is created
    assert not (settings.workspaces_dir / "_tasks" / "x").exists()
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


def test_project_folders_that_only_contain_the_orchestrator_are_allowed(client):
    """The owner develops the orchestrator as a project (its checkout), and keeps
    projects under backend/projects/: folders that hold the core are fine."""
    from app.guard.runtime import BACKEND_DIR

    checkout = BACKEND_DIR.parent
    inner = BACKEND_DIR / "projects" / f"x-{_uid()}"
    made = []
    try:
        for d in (checkout, BACKEND_DIR, settings.workspaces_dir, inner):
            r = client.post("/projects", json={"name": f"own-{_uid()}", "directory": str(d)})
            assert r.status_code == 201, (d, r.text)
            made.append(r.json()["id"])
        # re-saving the checkout project with its unchanged folder (and other fields) is accepted
        r = client.patch(f"/projects/{made[0]}", json={"directory": str(checkout), "instructions": "dev"})
        assert r.status_code == 200, r.text
        assert r.json()["directory"] == str(checkout.resolve())
    finally:
        for pid in made:
            client.delete(f"/projects/{pid}")
        import shutil as _sh

        _sh.rmtree(inner, ignore_errors=True)
        try:
            (BACKEND_DIR / "projects").rmdir()  # only if this test made it and it is empty
        except OSError:
            pass


def test_resaving_an_unchanged_folder_is_never_refused(client):
    """A project already stored inside a core folder (from before the rule) can
    still be saved as long as its folder does not change."""
    from app.guard.runtime import BACKEND_DIR
    from app.models import Project

    pid = uuid.uuid4().hex
    legacy = str((settings.workspaces_dir / "_tasks" / f"legacy-{_uid()}").resolve())
    Path(legacy).mkdir(parents=True)
    _add(Project(id=pid, name=f"legacy-{_uid()}", slug=f"legacy-{_uid()}", directory=legacy))
    try:
        r = client.patch(f"/projects/{pid}", json={"directory": legacy, "instructions": "x"})
        assert r.status_code == 200, r.text
        r = client.patch(f"/projects/{pid}", json={"directory": str(BACKEND_DIR / "app")})
        assert r.status_code == 422 and r.json()["detail"] == ORCH
    finally:
        client.delete(f"/projects/{pid}")


def test_npm_escapes_from_ancestors_have_no_effect(tmp_path, monkeypatch):
    """The re-review's escapes, in a scratch copy of the production layout:
    (A) an ancestor package.json whose "workspaces" glob matches the tool folder
    cannot adopt it, because the folder has no package.json; (B) npm puts every
    ancestor's node_modules/.bin on PATH, which no marker can stop, so the guard
    must protect node_modules in every ancestor. Only local npm commands run
    (prefix, ls, exec -c without a package, --offline)."""
    from app.guard import rules, runtime

    repo = tmp_path / "repo"
    ws = repo / "backend" / "workspaces"
    root = ws / "_mcp_configs" / "t1" / "cwd"
    root.mkdir(parents=True)
    cfg = mcp_manager.build_config([_srv("tool", command="npx", args=["-y", "some-mcp@1.2.3"])],
                                   task_cwd_root=root)
    d = Path(cfg["mcpServers"]["tool"]["args"][2])
    _assert_only_stoppers(d)
    # the guard side of (B), for this layout: no agent can create these
    monkeypatch.setattr(settings, "workspaces_dir", ws)
    pol = _policy(None, ws / "tadhoc")
    prot = set(pol["protected_paths"])
    for a in (repo, repo / "backend", ws):
        assert str(a.resolve() / "node_modules") in prot and str(a.resolve() / "package.json") in prot, a
        v = rules.evaluate("Write", {"file_path": str(a / "node_modules" / ".bin" / "node"), "content": "x"},
                           pol, str(ws / "tadhoc"))
        assert v.decision != "allow", a
    npm = shutil.which("npm")
    if not npm:
        import pytest

        pytest.skip("npm is not on PATH (the markers and the guard are checked above)")
    # plant what the re-review planted
    (repo / "package.json").write_text(json.dumps({"name": "planted", "workspaces": ["**"]}))
    (repo / ".npmrc").write_text("registry=https://evil2.invalid/\n")
    mod = repo / "node_modules" / "some-mcp"
    mod.mkdir(parents=True)
    (mod / "package.json").write_text(json.dumps({"name": "some-mcp", "version": "1.2.3"}))
    binx = repo / "node_modules" / ".bin"
    binx.mkdir()
    (binx / "node").write_text("#!/bin/sh\necho planted\n")
    (binx / "node").chmod(0o755)
    # control for (A): a folder with the old package.json stopper is adopted by the glob
    old = root / "old"
    old.mkdir()
    (old / "node_modules").mkdir()
    (old / "package.json").write_text("{}")
    assert os.path.realpath(_npm(npm, old, "prefix")) == os.path.realpath(repo)
    # (A) the tool folder stays its own project: not adopted, planted registry and module unseen
    assert os.path.realpath(_npm(npm, d, "prefix")) == os.path.realpath(d)
    assert "evil2.invalid" not in _npm(npm, d, "config", "get", "registry")
    ls = subprocess.run([npm, "ls", "--json"], cwd=d, capture_output=True, text=True, timeout=60,
                        env=dict(os.environ, npm_config_update_notifier="false"))
    assert "some-mcp" not in ls.stdout
    # (B) is real: npm's PATH reaches the ancestor .bin (hence the guard protection above)
    r = subprocess.run([npm, "exec", "--offline", "-c", "command -v node"], cwd=d, capture_output=True,
                       text=True, timeout=60, env=dict(os.environ, npm_config_update_notifier="false"))
    assert r.returncode == 0, r.stderr
    assert os.path.realpath(r.stdout.strip()) == os.path.realpath(binx / "node")


def test_inline_code_naming_package_files_is_not_self_protect(tmp_path):
    """The walk-up names under / are protected, but code that merely names a
    package.json or node_modules path is not treated as touching the guard."""
    from app.guard import rules

    proj = tmp_path / "proj"
    proj.mkdir()
    pol = _policy(proj, proj)
    for cmd in ("python3 -c \"import json; print(json.load(open('frontend/package.json')))\"",
                "node -e \"require('./node_modules/x')\""):
        v = rules.evaluate("Bash", {"command": cmd}, pol, str(proj))
        assert not any(h.rule_id == "self-protect" for h in v.hits), cmd


# ---- package-manager installs count as writes to <target>/node_modules -------

DENIED_INSTALLS = ["npm install --prefix .. ./evil", "npm install --prefix=.. ./evil", "cd ~ && npm i ./evil",
                   "cd .. && npm install", "pnpm add --dir ~ x", "pnpm -C ~ add x", "yarn --cwd ~ add x",
                   "cd ~ && yarn", "bun add --cwd ~ x", "npm ci --prefix ~", "npm link --prefix ~ ./evil"]
ALLOWED_INSTALLS = ["npm install", "npm i left-pad", "npm install --prefix ./sub", "pnpm add x", "yarn add x",
                    "npm ls", "npm run build", "npm view x --prefix ~"]


def _bash(pol, cmd, cwd):
    from app.guard import rules

    return rules.evaluate("Bash", {"command": cmd}, pol, str(cwd))


def test_installs_above_tool_folders_are_denied_adhoc(monkeypatch):
    ws = _prod_layout(monkeypatch)
    cwd = ws / "tadhoc"
    pol = _policy(None, cwd)
    for cmd in DENIED_INSTALLS:
        v = _bash(pol, cmd, cwd)
        assert v.decision == "deny" and any(h.rule_id == "self-protect" for h in v.hits), cmd
    for cmd in ALLOWED_INSTALLS:
        v = _bash(pol, cmd, cwd)
        assert not any(h.rule_id == "self-protect" for h in v.hits), cmd


def test_installs_above_tool_folders_are_denied_for_a_project(tmp_path, monkeypatch):
    from app.guard import runtime

    _prod_layout(monkeypatch)
    proj = tmp_path / "proj"
    (proj / "sub").mkdir(parents=True)
    pol = _policy(proj, proj)
    checkout = runtime.BACKEND_DIR.parent
    for cmd in DENIED_INSTALLS[2:] + [f"cd {checkout} && npm i x", f"npm i --prefix {runtime.BACKEND_DIR} x"]:
        if cmd.startswith(("cd .. ", "npm install --prefix ..", "npm install --prefix=..")):
            continue  # the folder above this temp project is not above a tool folder
        v = _bash(pol, cmd, proj)
        assert v.decision == "deny" and any(h.rule_id == "self-protect" for h in v.hits), cmd
    for cmd in ALLOWED_INSTALLS + ["cd sub && npm install"]:
        v = _bash(pol, cmd, proj)
        assert not any(h.rule_id == "self-protect" for h in v.hits), cmd


def test_global_installs_are_left_to_the_tools_own_prefix(monkeypatch):
    """-g is not judged as a local install; tools never see the server user's global folder (npm_config_prefix)."""
    ws = _prod_layout(monkeypatch)
    cwd = ws / "tadhoc"
    v = _bash(_policy(None, cwd), "npm install -g x", cwd)
    assert not any(h.rule_id == "self-protect" for h in v.hits)


# ---- CO_TOOL_RUN_DIR ----------------------------------------------------------


def test_tool_run_dir_setting(client, tmp_path, monkeypatch):
    from app.guard import runtime

    conf = (tmp_path / "runs").resolve()
    conf.mkdir()
    monkeypatch.setattr(settings, "tool_run_dir", conf)
    runs = conf / "co-tool-folders"  # a dedicated subfolder; the configured folder itself is never swept
    name = f"trd-{_uid()}"
    _add(_srv(name, command="npx", args=["-y", "x@1"]))
    tid = _tid()
    try:
        cdir, entry = _prepare(name, tid)
        d = Path(entry["args"][2])
        assert d.parent == runs / tid and d.is_dir()
        _assert_only_stoppers(d)
        assert stat.S_IMODE((runs / tid).stat().st_mode) == 0o700
        assert stat.S_IMODE(runs.stat().st_mode) == 0o700
        assert not (cdir / "cwd").exists()
        w = settings.workspaces_dir / _tid()
        pol = runtime.build_policy(task_id="t-trd", project=None, workspace=w, cwd=w,
                                   claude_config_dir=None, mcp_tools=None)
        prot = set(pol["protected_paths"])
        assert str(runs) in prot and str(conf / "node_modules") in prot
        assert str(conf) not in prot  # only the subfolder, not the admin's folder as a whole
        mcp_manager.cleanup_task(tid)
        assert not (runs / tid).exists() and not cdir.exists()
        (runs / _tid()).mkdir()
        assert asyncio.run(mcp_manager.sweep_stale_configs()) >= 1
        assert [p.name for p in runs.iterdir()] == []
    finally:
        _drop(name)
        mcp_manager.cleanup_task(tid)


def test_sweeps_only_remove_real_task_folders(client, tmp_path, monkeypatch):
    """Data-loss guard: anything in the configured folder that is not a real
    folder named like a task id survives, and a link is never followed."""
    conf = (tmp_path / "shared").resolve()
    conf.mkdir()
    monkeypatch.setattr(settings, "tool_run_dir", conf)
    runs = conf / "co-tool-folders"
    runs.mkdir()
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("precious")
    # foreign things next to and inside the tool folder root, and in _mcp_configs
    (conf / "foreign.txt").write_text("x")
    (conf / _tid()).mkdir()  # named like a task, but outside co-tool-folders
    (runs / "foreign-dir").mkdir()
    (runs / "foreign.txt").write_text("x")
    link = runs / _tid()
    link.symlink_to(victim)  # a link named like a task id
    mcfg = settings.workspaces_dir / "_mcp_configs"
    mcfg.mkdir(parents=True, exist_ok=True)
    (mcfg / "not-a-task").mkdir(exist_ok=True)
    mlink = mcfg / _tid()
    mlink.symlink_to(victim)
    try:
        asyncio.run(mcp_manager.sweep_stale_configs())
        mcp_manager.cleanup_task(link.name)
        mcp_manager.cleanup_task(mlink.name)
        mcp_manager.cleanup_task("../shared")  # not a task id: nothing happens
        assert (victim / "keep.txt").read_text() == "precious"
        assert link.is_symlink() and mlink.is_symlink()
        assert (conf / "foreign.txt").exists() and len(list(conf.iterdir())) == 3
        assert (runs / "foreign-dir").is_dir() and (runs / "foreign.txt").exists()
        assert (mcfg / "not-a-task").is_dir()
    finally:
        mlink.unlink()
        shutil.rmtree(mcfg / "not-a-task", ignore_errors=True)


# ---- installs through runners, and init / pkg ---------------------------------

RUNNER_DENIED = ["npx npm i --prefix .. ./e", "npm exec -- npm i --prefix .. ./e", "pnpm dlx npm i --prefix .. ./e",
                 "corepack npm i --prefix .. ./e", "corepack pnpm add --dir ~ ./e",
                 "corepack pnpm@9.1.0 add --dir ~ ./e"]
INIT_DENIED = ["cd ~ && npm init -y", "npm init -y --prefix ~", "cd .. && npm init -y", "cd ~ && pnpm init",
               "cd ~ && bun init -y", "cd ~ && yarn init -y", "cd ~ && npm pkg set name=x",
               "cd ~ && npm create vite@latest app"]
INIT_ALLOWED = ["npm init -y", "npm pkg set name=x", "npm pkg delete scripts.x", "pnpm init",
                "npm create vite@latest my-app", "npx create-react-app app", "npx npm i", "corepack enable",
                "npx tsc --noEmit", "cd sub && npm init -y"]


def test_runner_and_init_writes_above_tool_folders_are_denied(tmp_path, monkeypatch):
    ws = _prod_layout(monkeypatch)
    adhoc = ws / "tadhoc"
    proj = tmp_path / "proj"
    (proj / "sub").mkdir(parents=True)
    for pol, cwd in ((_policy(None, adhoc), adhoc), (_policy(proj, proj), proj)):
        for cmd in RUNNER_DENIED + INIT_DENIED:
            if cwd == proj and " .. " in f" {cmd} ".replace("..", " .. "):
                continue  # the folder above this temp project is not above a tool folder
            v = _bash(pol, cmd, cwd)
            assert v.decision == "deny" and any(h.rule_id == "self-protect" for h in v.hits), (cwd, cmd)
        for cmd in INIT_ALLOWED:
            v = _bash(pol, cmd, cwd)
            assert not any(h.rule_id == "self-protect" for h in v.hits), (cwd, cmd)
