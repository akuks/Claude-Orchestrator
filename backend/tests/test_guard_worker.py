"""co-guard worker wiring (Task 3): argv order, env, per-task policy file,
hook-disabling settings, spool ingestion into redacted `guard` events.

No real claude: the mock (conftest) records argv/env/policy for prompts
carrying [env-probe:KEY] and writes fake hook spool lines for [guard-spool]."""

import asyncio
import json
import types
import uuid
from pathlib import Path

import pytest

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
    assert "--include-hook-events" not in args  # final fix wave 2: the watchdog reads the spool
    dirs = [args[i + 1] for i, a in enumerate(args) if a == "--plugin-dir"]
    plugin = str(runtime.plugin_root().resolve())
    assert dirs[0] == plugin, dirs
    assert str(pack_dir) in dirs and dirs.index(str(pack_dir)) > 0
    assert "--setting-sources" not in args

    pairs, names = read_env_probe(key)
    tdir = runtime.task_dir(tid).resolve()
    assert pairs["CO_TASK_ID"] == tid
    assert pairs["CO_API_BASE"] == runtime.guard_api_base()
    assert pairs["CO_TASK_DIR"] == str(tdir)
    assert pairs["CO_POLICY_FILE"] == str(tdir / "policy.json")
    # project env cannot switch plugin hooks off
    assert "CLAUDE_CODE_SIMPLE" not in names and "CLAUDE_CODE_SAFE_MODE" not in names

    assert read_probe_file(key, "policymode").strip() == "600"
    assert read_probe_file(key, "taskdirmode").strip() == "700"
    pol = json.loads(read_probe_file(key, "policy"))
    cfg = pairs["CLAUDE_CONFIG_DIR"]
    assert pol["task_id"] == tid
    assert pol["api_base"] == runtime.guard_api_base()
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


# ---- Ruling R21: the watchdog matches tool_use ids through co-guard's spool ----


def _guard_tool(client, mode, monkeypatch=None):
    done = _run(client, f"tool [guard-tool:{mode}]")
    evs = _events(done["id"])
    assert any(e.type == "tool_use" for e in evs), f"mock seam {mode!r} did not run"
    return done, [e.payload for e in evs if e.type == "guard"]


def _killed(guard):
    return any(p.get("event") == "watchdog" and p.get("decision") == "killed" for p in guard)


@pytest.mark.parametrize("mode", ["hooked", "parallel", "subagent"])
def test_checked_calls_pass(client, mode):
    done, guard = _guard_tool(client, mode)
    assert done["status"] == "completed", (mode, done.get("error"))
    assert not _killed(guard)
    assert not any(p.get("seen") for p in guard)  # bookkeeping is not ingested


def test_unchecked_successful_call_kills_the_task(client):
    done, guard = _guard_tool(client, "nohook")
    assert done["status"] == "failed"
    assert "co-guard was not running" in done["error"] and "tuA" in done["error"]
    assert _killed(guard)


def test_unchecked_error_result_is_only_logged(client):
    done, guard = _guard_tool(client, "errnohook")
    assert done["status"] == "completed", done.get("error")
    assert not _killed(guard)
    assert any(p.get("event") == "unchecked_error" and p.get("tool_use_id") == "tuA" for p in guard)


def test_call_waiting_for_a_person_is_not_killed(client, monkeypatch):
    monkeypatch.setattr(settings, "guard_hook_grace_seconds", 1.0)
    done, guard = _guard_tool(client, "waiting")
    assert done["status"] == "completed", done.get("error")
    assert not _killed(guard)


def test_tool_left_unchecked_past_grace_kills_the_task(client, monkeypatch):
    monkeypatch.setattr(settings, "guard_hook_grace_seconds", 1.0)
    t0 = __import__("time").monotonic()
    done, guard = _guard_tool(client, "slow")
    assert done["status"] == "failed" and "co-guard was not running" in done["error"]
    assert "no co-guard check" in done["error"]
    assert __import__("time").monotonic() - t0 < 4.0  # killed before the tool "finished"


def test_watchdog_failure_is_not_retried(client, monkeypatch):
    monkeypatch.setattr(settings, "retry_max_attempts", 3)
    done, _guard = _guard_tool(client, "nohook")
    assert done["status"] == "failed" and done.get("attempt", 0) == 0


# ---- Ruling R17 / C2 / I1 / I3 ------------------------------------------------------


def test_powershell_is_disallowed_and_api_base_is_literal(client):
    key = env_probe_key()
    done = _run(client, f"x [env-probe:{key}]")
    assert done["status"] == "completed", done.get("error")
    args = read_probe_args(key)
    i = args.index("--disallowedTools")
    assert "PowerShell" in args[i + 1:]
    pairs, _names = read_env_probe(key)
    pol = json.loads(read_probe_file(key, "policy"))
    assert pairs["CO_API_BASE"] == pol["api_base"] == "http://127.0.0.1:8200"


def test_guard_api_base_literal_only(monkeypatch):
    import socket

    runtime._API_BASE_CACHE.clear()
    try:
        for host, want in (("127.0.0.1", "http://127.0.0.1:8200"), ("0.0.0.0", "http://127.0.0.1:8200"),
                           ("::", "http://[::1]:8200"), ("::1", "http://[::1]:8200"), ("[::1]", "http://[::1]:8200"),
                           ("10.1.2.3", "http://10.1.2.3:8200")):
            monkeypatch.setattr(settings, "bind_host", host)
            assert runtime.guard_api_base() == want, host
        monkeypatch.setattr(settings, "backend_port", 9123)
        monkeypatch.setattr(settings, "bind_host", "127.0.0.1")
        assert runtime.guard_api_base() == "http://127.0.0.1:9123"
        monkeypatch.setattr(settings, "backend_port", None)

        def fake(host, port, *a, **k):
            table = {"one.example": ["10.0.0.7"], "two.example": ["127.0.0.1", "::1"]}
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in table[host]]

        monkeypatch.setattr(socket, "getaddrinfo", fake)
        monkeypatch.setattr(settings, "bind_host", "one.example")
        assert runtime.guard_api_base() == "http://10.0.0.7:8200"
        monkeypatch.setattr(settings, "bind_host", "two.example")
        import pytest

        with pytest.raises(runtime.GuardConfigError):
            runtime.guard_api_base()
    finally:
        runtime._API_BASE_CACHE.clear()


def test_hostname_with_two_addresses_fails_the_task(client, monkeypatch):
    runtime._API_BASE_CACHE.clear()
    monkeypatch.setattr(settings, "bind_host", "localhost-two.invalid")
    import socket

    real = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, p, *a, **k: (
        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", p)),
         (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", p, 0, 0))]
        if h == "localhost-two.invalid" else real(h, p, *a, **k)))
    try:
        key = env_probe_key()
        done = _run(client, f"x [env-probe:{key}]")
        assert done["status"] == "failed" and "co-guard could not be set up" in done["error"]
        assert read_probe_args(key) is None
    finally:
        runtime._API_BASE_CACHE.clear()


def test_trusted_code_is_protected(tmp_path):
    import site
    import sys

    from app.guard import rules

    pol = runtime.build_policy(task_id="T9", project=None, workspace=tmp_path, cwd=tmp_path,
                               claude_config_dir=None, mcp_tools=None)
    prot = pol["protected_paths"]
    assert sys.executable in prot and str(Path(sys.executable).resolve()) in prot
    stdlib_json = Path(json.__file__)
    v = rules.evaluate("Write", {"file_path": str(stdlib_json), "content": "x"}, pol, str(tmp_path))
    assert v.decision == "deny" and any(h.rule_id == "self-protect" and h.hard for h in v.hits)
    sp = Path(site.getsitepackages()[0]) / "evil.pth"
    v = rules.evaluate("Bash", {"command": f"echo 'import os' > {sp}"}, pol, str(tmp_path))
    assert v.decision == "deny" and any(h.rule_id == "self-protect" and h.hard for h in v.hits)
    # ordinary project work stays allowed
    assert rules.evaluate("Bash", {"command": "echo x > out.txt"}, pol, str(tmp_path)).decision == "allow"


def test_mcp_names_normalised_like_claude_code(client):
    from app import mcp_manager

    assert mcp_manager.mcp_server_prefix("github.com") == "github_com"
    assert mcp_manager.mcp_server_prefix("my server") == "my_server"
    assert mcp_manager.mcp_server_prefix("ok-name_1") == "ok-name_1"
    proj = _project(client)
    rows = []
    for name in (f"github.com{_uid()}", f"my server{_uid()}"):
        srv = McpServer(name=name, transport="stdio", scope="project", project=proj["name"], command="echo",
                        enabled=True)
        _add_rows(srv)
        rows.append(srv)
        _add_rows(McpToolPolicy(server_id=srv.id, tool_name="drop", action="block"),
                  McpToolPolicy(server_id=srv.id, tool_name="q", action="require_approval"))
    key = env_probe_key()
    done = _run(client, f"x [env-probe:{key}]", project_id=proj["id"])
    assert done["status"] == "completed", done.get("error")
    args = read_probe_args(key)
    pol = json.loads(read_probe_file(key, "policy"))
    for srv in rows:
        pre = "mcp__" + srv.name.replace(".", "_").replace(" ", "_")
        assert f"{pre}__drop" in args
        assert pol["mcp_tools"][f"{pre}__q"] == "require_approval"
        assert not any(srv.name in k for k in pol["mcp_tools"])



# ---- fix round 2 -----------------------------------------------------------------------


def test_strict_mcp_only_with_orchestrator_servers_and_no_skill_shell(client):
    """Ruling R23: no orchestrator MCP servers -> no --strict-mcp-config (skill
    packs keep their MCP servers); R20c: inline skill shell is always off."""
    key = env_probe_key()
    proj = _project(client)  # no MCP servers scoped to it (team-scoped ones may exist from other tests)
    done = _run(client, f"x [env-probe:{key}]", project_id=proj["id"])
    assert done["status"] == "completed", done.get("error")
    args = read_probe_args(key)
    if "--mcp-config" in args:
        assert "--strict-mcp-config" in args  # the orchestrator supplied servers
    else:
        assert "--strict-mcp-config" not in args
    i = args.index("--settings")
    assert json.loads(args[i + 1]) == {"disableSkillShellExecution": True}
    assert args.count("--settings") == 1


def test_config_change_block_is_ingested(client):
    done = _run(client, "act [guard-spool]")
    guard = [e.payload for e in _events(done["id"]) if e.type == "guard"]
    assert any(p.get("event") == "ConfigChange" and p.get("decision") == "blocked"
               and p.get("source") == "project_settings" for p in guard), guard


def test_followup_rechecks_project_settings(client):
    """Ruling R16 check runs before EVERY spawn: a resumed / follow-up run in a
    project whose settings now switch hooks off drops project sources too."""
    proj = _project(client)
    r = client.post("/tasks", json={"prompt": "first", "project_id": proj["id"]})
    first = wait_task(client, r.json()["id"])
    assert first["status"] == "completed", first.get("error")
    d = Path(proj["directory"]) / ".claude"
    d.mkdir(parents=True, exist_ok=True)
    (d / "settings.local.json").write_text(json.dumps({"disableAllHooks": "yes"}))  # truthy like the CLI
    key = env_probe_key()
    r = client.post(f"/tasks/{first['id']}/followup", json={"prompt": f"more [env-probe:{key}]"})
    assert r.status_code == 201, r.text
    done = wait_task(client, r.json()["id"])
    assert done["status"] == "completed", done.get("error")
    args = read_probe_args(key)
    assert args[args.index("--setting-sources") + 1] == "user"


def test_hook_blockers_truthiness(tmp_path):
    repo = tmp_path / "r"
    (repo / ".git").mkdir(parents=True)
    (repo / ".claude").mkdir()
    f = repo / ".claude" / "settings.json"
    for v, want in ((True, True), (1, True), ("true", True), ("no", True), ([], True), (0, True),
                    (False, False), ("false", False), (None, False)):
        f.write_text(json.dumps({"disableAllHooks": v}))
        assert bool(runtime.hook_blockers(repo, None)["project"]) is want, v
    f.write_text(json.dumps({"allowManagedHooksOnly": True}))
    assert runtime.hook_blockers(repo, None)["project"]


# ---- Ruling R22: the approval address must prove it is this server ----------------------


def _serve_real_app():
    import socket
    import threading
    import time as _t

    import uvicorn

    from app.main import app

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="error"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    deadline = _t.time() + 10
    while not server.started and _t.time() < deadline:
        _t.sleep(0.05)
    return server, th, port


def _serve_fake(answer):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = json.dumps(answer).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_whoami_proves_identity(client):
    r = client.get("/guard/whoami", params={"challenge": "ab" * 16})
    assert r.status_code == 200 and r.json() == {"proof": runtime.whoami_proof("ab" * 16)}
    assert client.get("/guard/whoami", params={"challenge": "not-hex"}).status_code == 422


def test_verify_passes_against_the_real_app():
    server, th, port = _serve_real_app()
    try:
        assert runtime.verify_api_base(f"http://127.0.0.1:{port}") is True
    finally:
        server.should_exit = True
        th.join(5)


@pytest.mark.parametrize("answer", [{"proof": "0" * 64}, {"proof": None}, {}, ["x"]])
def test_verify_fails_against_a_stub(answer):
    srv = _serve_fake(answer)
    try:
        assert runtime.verify_api_base(f"http://127.0.0.1:{srv.server_address[1]}") is False
    finally:
        srv.shutdown()
        srv.server_close()


def test_verify_fails_with_no_listener():
    srv = _serve_fake({})
    port = srv.server_address[1]
    srv.shutdown()
    srv.server_close()
    assert runtime.verify_api_base(f"http://127.0.0.1:{port}", timeout=1) is False


def test_unverified_approval_address_fails_the_task(client, monkeypatch):
    srv = _serve_fake({"proof": "0" * 64})  # an agent's listener on the approval address
    runtime._API_BASE_CACHE.clear()
    monkeypatch.setattr(settings, "guard_verify_api", True)
    monkeypatch.setattr(settings, "backend_port", srv.server_address[1])
    try:
        key = env_probe_key()
        done = _run(client, f"x [env-probe:{key}]")
        assert done["status"] == "failed"
        assert "co-guard approval address not verified" in done["error"]
        assert read_probe_args(key) is None
    finally:
        runtime._API_BASE_CACHE.clear()
        srv.shutdown()
        srv.server_close()


def test_verified_approval_address_runs_the_task(client, monkeypatch):
    server, th, port = _serve_real_app()
    runtime._API_BASE_CACHE.clear()
    monkeypatch.setattr(settings, "guard_verify_api", True)
    monkeypatch.setattr(settings, "backend_port", port)
    try:
        done = _run(client, "verified run")
        assert done["status"] == "completed", done.get("error")
    finally:
        runtime._API_BASE_CACHE.clear()
        server.should_exit = True
        th.join(5)


def test_scope_id_is_escaped(monkeypatch):
    runtime._API_BASE_CACHE.clear()
    try:
        monkeypatch.setattr(settings, "bind_host", "fe80::1%en0")
        assert runtime.guard_api_base() == "http://[fe80::1%25en0]:8200"
    finally:
        runtime._API_BASE_CACHE.clear()



def test_strict_mcp_absent_without_servers(client, monkeypatch):
    from app import mcp_manager

    async def none(*a, **k):
        return None

    monkeypatch.setattr(mcp_manager, "prepare_for_task", none)
    key = env_probe_key()
    done = _run(client, f"x [env-probe:{key}]")
    assert done["status"] == "completed", done.get("error")
    args = read_probe_args(key)
    assert "--strict-mcp-config" not in args and "--mcp-config" not in args


def test_strict_mcp_with_servers(client):
    proj = _project(client)
    _add_rows(McpServer(name=f"st{_uid()}", transport="stdio", scope="project", project=proj["name"],
                        command="echo", enabled=True))
    key = env_probe_key()
    done = _run(client, f"x [env-probe:{key}]", project_id=proj["id"])
    args = read_probe_args(key)
    assert "--strict-mcp-config" in args and "--mcp-config" in args


# ---- Ruling R24: lanes ----------------------------------------------------------------


@pytest.mark.parametrize("mode", ["queued", "queuedthree"])
def test_queued_calls_in_a_lane_are_not_killed(client, monkeypatch, mode):
    monkeypatch.setattr(settings, "guard_hook_grace_seconds", 1.0)
    done, guard = _guard_tool(client, mode)
    assert done["status"] == "completed", (mode, done.get("error"))
    assert not _killed(guard)


def test_other_lane_is_timed_on_its_own(client, monkeypatch):
    monkeypatch.setattr(settings, "guard_hook_grace_seconds", 1.0)
    t0 = __import__("time").monotonic()
    done, guard = _guard_tool(client, "lanes")
    assert done["status"] == "failed" and "tuB" in done["error"] and _killed(guard)
    assert __import__("time").monotonic() - t0 < 4.0  # did not wait for lane 1's long call


def test_lane_next_call_unseen_past_grace_is_killed(client, monkeypatch):
    monkeypatch.setattr(settings, "guard_hook_grace_seconds", 1.0)
    done, guard = _guard_tool(client, "laneexpire")
    assert done["status"] == "failed" and "no co-guard check" in done["error"] and "tuB" in done["error"]


# ---- round 3 minors -------------------------------------------------------------------------


def test_verify_does_not_follow_redirects():
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    server, th, port = _serve_real_app()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{port}{self.path}")
            self.send_header("Content-Length", "0")
            self.end_headers()

    redir = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=redir.serve_forever, daemon=True).start()
    try:
        assert runtime.verify_api_base(f"http://127.0.0.1:{port}") is True
        assert runtime.verify_api_base(f"http://127.0.0.1:{redir.server_address[1]}") is False
    finally:
        redir.shutdown()
        redir.server_close()
        server.should_exit = True
        th.join(5)


def test_seen_lines_never_cut_decisions(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "task_dir", lambda tid: tmp_path)
    lines = [json.dumps({"event": "PreToolUse", "tool_use_id": f"t{i}", "seen": True}) for i in range(2500)]
    lines.append(json.dumps({"event": "PreToolUse", "decision": "deny", "rule_ids": ["rm-rf-outside"]}))
    (tmp_path / "spool.jsonl").write_text("\n".join(lines) + "\n")
    recs = runtime.read_spool("T")
    assert recs == [{"event": "PreToolUse", "decision": "deny", "rule_ids": ["rm-rf-outside"]}]


def test_guard_settings_help_mentions_inline_shell():
    assert "!`cmd`" in runtime.GUARD_SETTINGS_HELP and "disabled" in runtime.GUARD_SETTINGS_HELP


def test_guard_settings_help_says_what_always_applies_and_the_ui_mirrors_it():
    import re
    from pathlib import Path

    help_ = runtime.GUARD_SETTINGS_HELP
    assert "built-in command rules" in help_ and "MCP approvals" in help_ and "self-protection" in help_
    assert "always apply" in help_
    js = (Path(__file__).resolve().parents[2] / "frontend/src/guard/guard.js").read_text(encoding="utf-8")
    block = js.split("export const GUARD_SETTINGS_HELP =", 1)[1].split("\n\n", 1)[0]
    parts = re.findall(r"\"((?:[^\"\\]|\\.)*)\"|'((?:[^'\\]|\\.)*)'", block)
    mirrored = "".join(re.sub(r"\\(.)", r"\1", a or b) for a, b in parts)
    assert mirrored == help_


@pytest.mark.parametrize("subtype", ["hook_started", "hook_response", "hook_progress"])
def test_hook_system_events_are_not_task_events(subtype):
    """Final fix wave 2: stream hook events (should any arrive) never become
    TaskEvents - co-guard's decisions come from the spool / live report."""
    from app.worker import WorkerManager

    got = []

    async def emit(etype, payload):
        got.append((etype, payload))

    ctx = {"task_id": "t-" + _uid(), "mcp_ids": {}, "secrets": []}

    async def go():
        w = WorkerManager()
        await w._handle_stream_event({"type": "system", "subtype": subtype, "hook_name": "PreToolUse:Bash"},
                                     emit, {}, ctx)
        await w._handle_stream_event({"type": "system", "subtype": "init"}, emit, {}, ctx)

    asyncio.run(go())
    assert got == [("system", {"subtype": "init", "session_id": None})]


@pytest.mark.parametrize("how", ["cancel", "stop"])
def test_cancel_and_stop_signal_the_whole_process_group(how, tmp_path):
    """Final fix wave 5: like the watchdog, cancel()/stop() reach the tools
    claude started (its process group), not only claude itself."""
    import os
    import signal as _signal
    import time as _time

    from app.worker import WorkerManager

    pidfile = tmp_path / "child.pid"

    async def go():
        w = WorkerManager()
        proc = await asyncio.create_subprocess_exec(
            "/bin/sh", "-c", f"sleep 60 & echo $! > {pidfile}; wait", start_new_session=True)
        for _ in range(100):
            if pidfile.exists() and pidfile.read_text().strip():
                break
            await asyncio.sleep(0.05)
        w._running["t"] = proc
        if how == "cancel":
            await w.cancel("t")
        else:
            await w.stop()
        await asyncio.wait_for(proc.wait(), 10)
        return int(pidfile.read_text())

    child = asyncio.run(go())
    for _ in range(100):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        _time.sleep(0.05)
    else:
        os.kill(child, _signal.SIGKILL)
        raise AssertionError("the tool claude started survived")


def test_startup_logs_the_approval_address(monkeypatch, caplog):
    """Final fix wave 7: one line with the resolved approval address; a loud
    warning when CO_BIND_HOST is a hostname (or cannot be used)."""
    import logging
    import socket

    runtime._API_BASE_CACHE.clear()
    try:
        monkeypatch.setattr(settings, "bind_host", "127.0.0.1")
        monkeypatch.setattr(settings, "backend_port", 9124)
        with caplog.at_level(logging.INFO, logger="app.guard"):
            runtime.log_approval_address()
        lines = [r for r in caplog.records if "approval address" in r.getMessage()]
        assert len(lines) == 1 and lines[0].levelno == logging.INFO
        assert "http://127.0.0.1:9124" in lines[0].getMessage()

        caplog.clear()
        monkeypatch.setattr(socket, "getaddrinfo",
                            lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                                                          ("10.0.0.7", port))])
        monkeypatch.setattr(settings, "bind_host", "box.example")
        with caplog.at_level(logging.INFO, logger="app.guard"):
            runtime.log_approval_address()
        warn = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warn and "box.example" in warn[0].getMessage() and "CO_BIND_HOST" in warn[0].getMessage()
        assert any("http://10.0.0.7:9124" in r.getMessage() for r in caplog.records)

        caplog.clear()
        runtime._API_BASE_CACHE.clear()
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
        with caplog.at_level(logging.INFO, logger="app.guard"):
            runtime.log_approval_address()  # never raises: tasks fail later with the reason
        assert any(r.levelno >= logging.ERROR and "box.example" in r.getMessage() for r in caplog.records)
    finally:
        runtime._API_BASE_CACHE.clear()


def test_start_sh_reads_the_backend_port_from_env_file():
    text = (Path(__file__).resolve().parents[2] / "start.sh").read_text()
    assert '_env_key CO_BACKEND_PORT' in text
    assert 'export CO_BACKEND_PORT="$BACKEND_PORT"' in text


def test_adhoc_child_of_a_project_thread_uses_the_root_projects_guard_settings(client):
    """Final fix wave 8: a dispatched child / follow-up with project_id=None in
    a project thread gets the thread root's guard settings (effective project,
    like access.effective_project_id); its own workspace stays its project dir."""
    from app.main import app
    from app.models import Project, Task

    proj = _project(client)

    async def setup():
        async with SessionLocal() as s:
            p = await s.get(Project, proj["id"])
            p.guard_enabled = False
            p.approval_wait_seconds = 120
            await s.commit()

    asyncio.run(setup())
    root = _run(client, "root step", project_id=proj["id"])
    assert root["status"] == "completed", root.get("error")
    key = env_probe_key()
    child_id = uuid.uuid4().hex

    async def add_child():
        async with SessionLocal() as s:
            s.add(Task(id=child_id, title="child", prompt=f"child [env-probe:{key}]", project_id=None,
                       root_id=root["id"], parent_task_id=root["id"], status="queued",
                       created_by=root.get("created_by")))
            await s.commit()
            t = await s.get(Task, child_id)
            return t.priority, t.created_at

    prio, created = asyncio.run(add_child())
    client.portal.call(app.state.worker.submit, child_id, prio, created)  # the app's own loop
    done = wait_task(client, child_id)
    assert done["status"] == "completed", done.get("error")
    pol = json.loads(read_probe_file(key, "policy"))
    assert pol["guard_enabled"] is False and pol["approval_wait_seconds"] == 120
    assert pol["project_dir"] == str((settings.workspaces_dir / child_id).resolve())


# ---- Phase 2 Task 5: policy compilation, --add-dir, the external data line --------------------


def _patch(client, pid, **fields):
    r = client.patch(f"/projects/{pid}", json=fields)
    assert r.status_code == 200, r.text
    return r.json()


def _agent_run(client, project_id, prompt, system_prompt):
    r = client.post("/agents", json={"name": f"gw-agent-{_uid()}", "system_prompt": system_prompt,
                                     "default_prompt": "go", "project_id": project_id})
    assert r.status_code == 201, r.text
    r = client.post(f"/agents/{r.json()['id']}/run", json={"prompt": prompt})
    assert r.status_code == 201, r.text
    return wait_task(client, r.json()["id"])


def _arg_values(args, flag):
    return [args[i + 1] for i, a in enumerate(args) if a == flag]


def test_external_project_policy_add_dir_and_data_line(client):
    from app.guard import co_hook

    proj = _project(client)
    ref = _project(client)
    d = Path(proj["directory"])
    (d / "vendor").mkdir(parents=True, exist_ok=True)
    (d / ".claude-orchestrator").mkdir(exist_ok=True)
    (d / ".claude-orchestrator" / "rules.md").write_text("## Confirm\n- `make deploy` : ships\n")
    _patch(client, proj["id"], trust="external", quarantine=True, on_no_answer="deny_and_resume",
           readonly_paths=[{"path": "vendor", "reason": "upstream"}], reference_project_ids=[ref["id"]])
    key = env_probe_key()
    done = _agent_run(client, proj["id"], f"work [env-probe:{key}]", "You are careful.")
    assert done["status"] == "completed", done.get("error")
    args = read_probe_args(key)
    ref_real = str(Path(ref["directory"]).resolve())
    assert ref_real in _arg_values(args, "--add-dir")
    # the mock writes argv one per line: the two-paragraph value spans three lines
    i = args.index("--append-system-prompt")
    assert "\n".join(args[i + 1:i + 4]) == "You are careful.\n\n" + co_hook.DATA_LINE
    assert args.count("--append-system-prompt") == 1
    pol = json.loads(read_probe_file(key, "policy"))
    assert pol["trust"] == "external" and pol["quarantine"] is True and pol["on_no_answer"] == "deny_and_resume"
    assert pol["readonly_paths"] == [
        {"path": str((d / "vendor").resolve()), "reason": "upstream", "source": "project"},
        {"path": ref_real, "reason": "a reference project", "source": "reference"},
    ]
    # an external repo's rules file is never read
    assert not any(r["id"].startswith("repo.") for r in pol["custom_rules"])
    for k in ("custom_rules", "disabled_rules", "db_dev", "grants"):
        assert isinstance(pol[k], list), k
    assert pol["regex_budget_ms"] == 250
    # every key the base policy had is still there
    assert pol["task_id"] == done["id"] and pol["protected_paths"]


def test_external_project_without_system_prompt_gets_the_data_line(client):
    from app.guard import co_hook

    proj = _project(client)
    _patch(client, proj["id"], trust="external")
    key = env_probe_key()
    done = _run(client, f"work [env-probe:{key}]", project_id=proj["id"])
    assert done["status"] == "completed", done.get("error")
    assert _arg_values(read_probe_args(key), "--append-system-prompt") == [co_hook.DATA_LINE]


def test_own_project_reads_repo_rules_and_has_no_data_line(client):
    proj = _project(client)
    d = Path(proj["directory"])
    (d / ".claude-orchestrator").mkdir(parents=True, exist_ok=True)
    (d / ".claude-orchestrator" / "rules.md").write_text("## Never\n- `make nuke` : wipes\n## Other\n- x\n")
    key = env_probe_key()
    done = _agent_run(client, proj["id"], f"work [env-probe:{key}]", "Role text.")
    assert done["status"] == "completed", done.get("error")
    args = read_probe_args(key)
    assert _arg_values(args, "--append-system-prompt") == ["Role text."]
    pol = json.loads(read_probe_file(key, "policy"))
    assert pol["trust"] == "own"
    repo = [r for r in pol["custom_rules"] if r["id"].startswith("repo.")]
    assert [(r["kind"], r["pattern"]) for r in repo] == [("never", "make nuke")]
    ev = [e.payload for e in _events(done["id"]) if e.type == "guard" and e.payload.get("event") == "rules_file"]
    assert ev and ev[0]["errors"]


def test_invisible_reference_is_skipped_with_an_event(client):
    """The task's owner (a maintainer of the project only) cannot see the reference project."""
    from conftest import login_as

    proj = _project(client)
    hidden = _project(client)
    _patch(client, proj["id"], reference_project_ids=[hidden["id"]])
    u = login_as(f"gw-ref-{_uid()}@test.local")
    r = client.put(f"/projects/{proj['id']}/members",
                   json=[{"subject_type": "user", "subject_id": u.user_id, "role": "maintainer"}])
    assert r.status_code == 200, r.text
    key = env_probe_key()
    r = u.post("/tasks", json={"prompt": f"x [env-probe:{key}]", "project_id": proj["id"]})
    assert r.status_code == 201, r.text
    done = wait_task(client, r.json()["id"])
    assert done["status"] == "completed", done.get("error")
    args = read_probe_args(key)
    assert str(Path(hidden["directory"]).resolve()) not in _arg_values(args, "--add-dir")
    pol = json.loads(read_probe_file(key, "policy"))
    assert pol["readonly_paths"] == []
    ev = [e.payload for e in _events(done["id"]) if e.type == "guard"
          and e.payload.get("event") == "reference_skipped"]
    assert ev and hidden["name"] not in json.dumps(ev)


def test_mcp_servers_and_defaults_reach_the_policy(client):
    proj = _project(client)
    name = f"Pg.Dev{_uid()}"
    _add_rows(McpServer(name=name, transport="stdio", scope="project", project=proj["name"], command="echo",
                        enabled=True, environment="dev", default_action="block"))
    key = env_probe_key()
    done = _run(client, f"x [env-probe:{key}]", project_id=proj["id"])
    assert done["status"] == "completed", done.get("error")
    pol = json.loads(read_probe_file(key, "policy"))
    prefix = name.replace(".", "_").lower()
    assert pol["mcp_servers"][prefix] == {"name": name, "environment": "dev"}
    assert pol["mcp_defaults"][f"mcp__{prefix}__*"] == "block"


def test_grants_of_the_thread_reach_the_policy(client):
    from datetime import datetime, timedelta, timezone

    from app.models import ActionGrant

    first = _run(client, "first")
    assert first["status"] == "completed"
    now = datetime.now(timezone.utc)
    root = first.get("root_id") or first["id"]
    gid = f"g{_uid()}"
    _add_rows(ActionGrant(id=gid, root_id=root, scope="run", rule_id="git-reset-hard", key_kind="command",
                          key="c" * 64, created_at=now, expires_at=now + timedelta(hours=1)))
    key = env_probe_key()
    r = client.post(f"/tasks/{first['id']}/followup", json={"prompt": f"more [env-probe:{key}]"})
    assert r.status_code == 201, r.text
    done = wait_task(client, r.json()["id"])
    assert done["status"] == "completed", done.get("error")
    pol = json.loads(read_probe_file(key, "policy"))
    assert [g["id"] for g in pol["grants"]] == [gid]
    assert isinstance(pol["grants"][0]["expires_at"], float)


def test_build_policy_merges_extra(tmp_path):
    base = dict(task_id="T1", workspace=tmp_path, cwd=tmp_path, claude_config_dir=None, mcp_tools=None,
                project=None)
    p = runtime.build_policy(**base, extra={"trust": "external", "grants": []})
    assert p["trust"] == "external" and p["grants"] == [] and p["task_id"] == "T1"
    assert "trust" not in runtime.build_policy(**base)


def test_granted_spool_records_keep_their_grant_ids():
    from app.worker import WorkerManager

    p = WorkerManager.guard_payload({"event": "PreToolUse", "decision": "granted", "grant_ids": ["g1", 2] + ["x"] * 60,
                                     "rule_ids": ["db-write"], "tool": "Bash", "junk": "dropped"})
    assert p["decision"] == "granted" and p["grant_ids"][:2] == ["g1", "2"] and len(p["grant_ids"]) == 50  # final wave: 50
    assert "junk" not in p
    assert WorkerManager.guard_payload({"grant_ids": "nope"})["grant_ids"] == []


@pytest.mark.parametrize("agent_id", [None, "sub-1"])
def test_granted_report_and_its_spool_line_key_alike(agent_id):
    """Task 8: the hook spools a granted record with agent_id (null for the main
    agent) and posts it to /guard/report, which forwards only its own keys; the
    worker must see one record (one TaskDetail line, one use), not two."""
    from app.worker import WorkerManager

    spool = {"event": "PreToolUse", "tool": "Bash", "rule_ids": ["project.x"], "summary": "./deploy.sh --prod",
             "tool_use_id": "toolu_1", "agent_id": agent_id, "decision": "granted", "grant_ids": ["g1"], "ts": 1.5}
    hook_report = {k: spool[k] for k in ("event", "decision", "grant_ids", "tool", "rule_ids", "summary",
                                         "tool_use_id", "agent_id")}
    hook_report["task_id"] = "T"
    # what POST /guard/report forwards for a granted record
    from app.routers import guard as guard_router

    forwarded = {k: hook_report[k] for k in guard_router.GRANTED_REPORT_KEYS if k in hook_report}
    key = WorkerManager._guard_key
    assert key(WorkerManager.guard_payload(spool)) == key(WorkerManager.guard_payload(forwarded))


# ---- phase 4c: the brief in the prompt and the policy ---------------------------------------------------


def test_brief_in_prompt_policy_and_events(client):
    from app.briefs.render import BRIEF_FIRST_LINE

    proj = _project(client)
    brief = {"goal": "Add the export", "can_change": ["src/**"], "cannot_change": ["migrations", "**/*.lock"],
             "done": "tests pass", "stop": "when blocked"}
    key = env_probe_key()
    done = _run(client, f"work [env-probe:{key}]", project_id=proj["id"], brief=brief)
    assert done["status"] == "completed", done.get("error")
    pol = json.loads(read_probe_file(key, "policy"))
    assert pol["brief"] == {"layers": [{"task": done["id"], "can": ["src/**"],
                                        "cannot": ["migrations", "**/*.lock"]}]}
    root = str((Path(proj["directory"]) / "migrations").resolve())
    assert {"path": root, "reason": "the task's brief says CANNOT change migrations", "source": "brief"} \
        in pol["readonly_paths"]
    args = read_probe_args(key)  # the mock writes argv one per line: a multi-paragraph value spans lines
    sys_prompt = "\n".join(args[args.index("--append-system-prompt") + 1:])
    assert BRIEF_FIRST_LINE in sys_prompt
    evs = _events(done["id"])
    assert "brief" in [e.payload for e in evs if e.type == "ground_rules"][0]["included"]
    scope_ev = [e.payload for e in evs if e.type == "guard" and e.payload.get("event") == "brief_scope"]
    assert scope_ev == [{"event": "brief_scope", "shell_roots": 1, "file_tools_only": ["**/*.lock"]}]


def test_no_brief_no_policy_key_or_event(client):
    key = env_probe_key()
    done = _run(client, f"work [env-probe:{key}]")
    assert done["status"] == "completed", done.get("error")
    pol = json.loads(read_probe_file(key, "policy"))
    assert "brief" not in pol and not any(e["source"] == "brief" for e in pol["readonly_paths"])
    evs = _events(done["id"])
    assert not any(e.type == "guard" and e.payload.get("event") == "brief_scope" for e in evs)
    assert "brief" not in [e.payload for e in evs if e.type == "ground_rules"][0]["included"]


def test_brief_scope_failure_fails_the_task_before_spawn(client, monkeypatch):
    from app.briefs import scope

    def boom(policy, layers):
        raise RuntimeError("scope broke")

    monkeypatch.setattr(scope, "apply", boom)
    brief = {"goal": "g", "can_change": [], "cannot_change": ["migrations"], "done": "d", "stop": "s"}
    key = env_probe_key()
    done = _run(client, f"x [env-probe:{key}]", brief=brief)
    assert done["status"] == "failed" and "co-guard could not be set up; task not started" in done["error"]
    assert "scope broke" in done["error"]
    assert read_probe_args(key) is None
