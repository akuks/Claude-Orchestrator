"""co-guard worker wiring (Task 3): argv order, env, per-task policy file,
hook-disabling settings, spool ingestion into redacted `guard` events.

No real claude: the mock (conftest) records argv/env/policy for prompts
carrying [env-probe:KEY] and writes fake hook spool lines for [guard-spool]."""

import asyncio
import json
import types
import uuid
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.guard import runtime
from app.models import McpServer, McpToolPolicy, SkillPack, TaskEvent
from conftest import env_probe_key, read_env_probe, read_probe_args, read_probe_file, wait_task

CO_TOKEN = "co_" + "A" * 43


def _uid():
    return uuid.uuid4().hex[:8]


def _events(task_id):
    async def go():
        async with SessionLocal() as s:
            return (
                await s.execute(select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.seq))
            ).scalars().all()

    return asyncio.run(go())


def _project(client, **extra):
    r = client.post("/projects", json={"name": f"gw-{_uid()}", **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _run(client, prompt, **body):
    r = client.post("/tasks", json={"prompt": prompt, **body})
    assert r.status_code == 201, r.text
    return wait_task(client, r.json()["id"])


def _add_rows(*rows):
    async def go():
        async with SessionLocal() as s:
            for r in rows:
                s.add(r)
                await s.flush()
            await s.commit()

    asyncio.run(go())


# ---- argv + env + policy ----------------------------------------------------


def test_plugin_dir_first_hook_events_env_and_policy(client, tmp_path):
    proj = _project(client, env={"CLAUDE_CODE_SIMPLE": "1", "CLAUDE_CODE_SAFE_MODE": "1"})
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    srv = McpServer(name=f"gsrv{_uid()}", transport="stdio", scope="project", project=proj["name"],
                    command="echo", enabled=True)
    _add_rows(srv)
    _add_rows(
        SkillPack(name=f"pack-{_uid()}", local_path=str(pack_dir), enabled=True, scope="project",
                  project_id=proj["id"]),
        McpToolPolicy(server_id=srv.id, tool_name="query", action="require_approval"),
        McpToolPolicy(server_id=srv.id, tool_name="drop", action="block"),
        McpToolPolicy(server_id=srv.id, tool_name="list", action="auto_approve"),
        McpToolPolicy(server_id=srv.id, tool_name="*", action="require_approval"),
    )
    key = env_probe_key()
    done = _run(client, f"work [env-probe:{key}]", project_id=proj["id"])
    assert done["status"] == "completed", done.get("error")
    tid = done["id"]

    args = read_probe_args(key)
    assert args is not None
    assert "--include-hook-events" in args
    dirs = [args[i + 1] for i, a in enumerate(args) if a == "--plugin-dir"]
    plugin = str(runtime.plugin_root().resolve())
    assert dirs[0] == plugin, dirs
    assert str(pack_dir) in dirs and dirs.index(str(pack_dir)) > 0
    assert "--setting-sources" not in args

    pairs, names = read_env_probe(key)
    tdir = runtime.task_dir(tid).resolve()
    assert pairs["CO_TASK_ID"] == tid
    assert pairs["CO_API_BASE"] == settings.api_base.rstrip("/")
    assert pairs["CO_TASK_DIR"] == str(tdir)
    assert pairs["CO_POLICY_FILE"] == str(tdir / "policy.json")
    # project env cannot switch plugin hooks off
    assert "CLAUDE_CODE_SIMPLE" not in names and "CLAUDE_CODE_SAFE_MODE" not in names

    assert read_probe_file(key, "policymode").strip() == "600"
    assert read_probe_file(key, "taskdirmode").strip() == "700"
    pol = json.loads(read_probe_file(key, "policy"))
    cfg = pairs["CLAUDE_CONFIG_DIR"]
    assert pol["task_id"] == tid
    assert pol["api_base"] == settings.api_base.rstrip("/")
    assert pol["project_dir"] == str(Path(proj["directory"]).resolve())
    assert pol["cwd"] == str(Path(proj["directory"]).resolve())
    assert pol["home"] == runtime.real_home()
    assert pol["claude_config_dir"] == cfg
    assert pol["guard_enabled"] is True
    assert pol["approval_wait_seconds"] == 300
    s = srv.name
    assert pol["mcp_tools"] == {f"mcp__{s}__query": "require_approval", f"mcp__{s}__drop": "block",
                                f"mcp__{s}__list": "auto_approve", f"mcp__{s}__*": "require_approval"}
    assert pol["env_paths"] == {"CO_POLICY_FILE": str(tdir / "policy.json"), "CO_TASK_DIR": str(tdir),
                                "CLAUDE_CONFIG_DIR": cfg, "CLAUDE_PLUGIN_ROOT": plugin}
    prot = pol["protected_paths"]
    home = Path(runtime.real_home())
    ws = settings.workspaces_dir.resolve()
    for p in (str(runtime.plugin_root()), plugin, str(tdir), str(tdir / "policy.json"), str(tdir / "spool.jsonl"),
              cfg, str(home / ".claude"), str(home / ".claude.json"), str(ws / "_system"),
              str(runtime.BACKEND_DIR / "app"), str(runtime.BACKEND_DIR / ".venv")):
        assert p in prot, p
    # the data dirs that live inside the backend by default, and the project, stay writable
    for p in (str(ws), str(settings.projects_dir.resolve()), str(Path(proj["directory"]).resolve())):
        assert p not in prot, p
    # the task dir is gone after the run
    assert not runtime.task_dir(tid).exists()


def test_adhoc_task_project_dir_is_its_workspace(client):
    key = env_probe_key()
    done = _run(client, f"adhoc [env-probe:{key}]")
    assert done["status"] == "completed", done.get("error")
    pol = json.loads(read_probe_file(key, "policy"))
    ws = str((settings.workspaces_dir / done["id"]).resolve())
    assert pol["project_dir"] == ws and pol["cwd"] == ws
    assert pol["mcp_tools"] == {} or all(k.startswith("mcp__") for k in pol["mcp_tools"])
    assert pol["guard_enabled"] is True


# ---- policy unit -------------------------------------------------------------


def test_build_policy_reads_project_settings_with_defaults(tmp_path):
    base = dict(task_id="T1", workspace=tmp_path, cwd=tmp_path, claude_config_dir=str(tmp_path / "cfg"),
                mcp_tools=None)
    p = runtime.build_policy(project=types.SimpleNamespace(directory=str(tmp_path)), **base)
    assert p["guard_enabled"] is True and p["approval_wait_seconds"] == 300 and p["mcp_tools"] == {}
    for wait, want in ((5, 60), (60, 60), (450, 450), (900, 900), (5000, 900), ("x", 300), (None, 300)):
        proj = types.SimpleNamespace(directory=str(tmp_path), guard_enabled=False, approval_wait_seconds=wait)
        p = runtime.build_policy(project=proj, **base)
        assert p["guard_enabled"] is False and p["approval_wait_seconds"] == want, wait
    p = runtime.build_policy(project=None, **base)
    assert p["project_dir"] == str(tmp_path.resolve()) and p["guard_enabled"] is True


def test_orchestrator_paths_keep_data_dirs_and_project():
    backend = runtime.BACKEND_DIR
    prot = runtime.orchestrator_paths(str(settings.projects_dir / "x"), str(settings.projects_dir / "x"))
    assert str(backend / "app") in prot
    assert str(Path(settings.secret_key_file).resolve()) in prot
    for keep in (settings.workspaces_dir, settings.projects_dir, settings.skillpacks_dir):
        k = Path(keep).resolve()
        assert not any(k == Path(p) or str(k).startswith(p.rstrip("/") + "/") for p in prot), keep
    # a project inside the orchestrator checkout still gets the app code protected
    prot2 = runtime.orchestrator_paths(str(backend), str(backend))
    assert str(backend / "app") in prot2 and str(backend) not in prot2


# ---- settings that would switch our hooks off (Ruling R6 follow-up) ----------


def test_project_settings_disabling_hooks_drop_project_sources(client):
    proj = _project(client)
    d = Path(proj["directory"]) / ".claude"
    d.mkdir(parents=True, exist_ok=True)
    (d / "settings.json").write_text(json.dumps({"disableAllHooks": True}))
    key = env_probe_key()
    done = _run(client, f"work [env-probe:{key}]", project_id=proj["id"])
    assert done["status"] == "completed", done.get("error")
    args = read_probe_args(key)
    i = args.index("--setting-sources")
    assert args[i + 1] == "user"
    warn = [e.payload for e in _events(done["id"]) if e.type == "guard"]
    assert any(p.get("event") == "settings_override" for p in warn), warn


def test_project_settings_disabling_plugin_drop_project_sources(client):
    proj = _project(client)
    d = Path(proj["directory"]) / ".claude"
    d.mkdir(parents=True, exist_ok=True)
    (d / "settings.local.json").write_text(json.dumps({"enabledPlugins": {"co-guard@inline": False}}))
    key = env_probe_key()
    done = _run(client, f"work [env-probe:{key}]", project_id=proj["id"])
    assert done["status"] == "completed", done.get("error")
    assert "--setting-sources" in read_probe_args(key)


def test_hook_blockers_unit(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    sub = repo / "pkg"
    sub.mkdir()
    assert runtime.hook_blockers(sub, None) == {"project": [], "user": []}
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text('{"disableAllHooks": false}')
    assert runtime.hook_blockers(sub, None)["project"] == []
    (repo / ".claude" / "settings.json").write_text('{"disableAllHooks": true}')
    assert runtime.hook_blockers(sub, None)["project"] == [str(repo / ".claude" / "settings.json")]
    # above the git root: not this project's settings
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text('{"disableAllHooks": true}')
    assert len(runtime.hook_blockers(sub, None)["project"]) == 1
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "settings.json").write_text('{"disableAllHooks": true}')
    assert runtime.hook_blockers(sub, str(cfg))["user"] == [str(cfg / "settings.json")]


def test_user_settings_disabling_hooks_fail_the_task(client):
    from conftest import login_as

    owner = login_as(f"gw-{_uid()}@test.local")
    cfg = settings.workspaces_dir / "_users" / owner.user_id / "claude"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "settings.json").write_text(json.dumps({"disableAllHooks": True}))
    key = env_probe_key()
    done = _run(owner, f"work [env-probe:{key}]")
    assert done["status"] == "failed"
    assert "co-guard" in (done.get("error") or "")
    assert read_probe_args(key) is None  # claude never started


# ---- spool ingestion -----------------------------------------------------------


def test_spool_is_ingested_as_redacted_guard_events(client):
    done = _run(client, "act [guard-spool]")
    assert done["status"] == "completed", done.get("error")
    guard = [e.payload for e in _events(done["id"]) if e.type == "guard"]
    hook = [p for p in guard if p.get("event") in ("PreToolUse", "SessionEnd")]
    assert [p["event"] for p in hook] == ["PreToolUse", "PreToolUse", "SessionEnd"]
    deny, approved, end = hook
    assert deny["decision"] == "deny" and deny["rule_ids"] == ["rm-rf-outside"] and deny["tool"] == "Bash"
    assert approved["decision"] == "approved" and approved["request_id"] == "r1"
    blob = json.dumps(guard)
    assert "co_" not in approved["summary"] and "***" in approved["summary"]
    assert "junk_key" not in end
    assert not runtime.task_dir(done["id"]).exists()
    # ingested before the run's completed event
    types_ = [e.type for e in _events(done["id"])]
    assert types_.index("guard") < types_.index("completed")
    assert "co_" not in blob.replace("co-guard", "")
