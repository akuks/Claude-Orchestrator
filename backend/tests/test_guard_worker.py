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
    assert "--include-hook-events" in args
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
