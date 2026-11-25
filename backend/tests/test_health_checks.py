"""System health: each check (probe replaced: ok / attention / problem), the
runner (budget, scrub, overall lines, cache) and the real guard self-test."""

import asyncio
import os
import pwd
import sys
import time
from collections import namedtuple
from pathlib import Path

import pytest

from app.config import settings
from app.guard import runtime as guard_runtime
from app.guard.plugin import ensure_plugin
from app.health import checks, runner
from app.health.checks import ATTENTION, OK, PROBLEM, Result


def run(coro):
    return asyncio.run(coro)


def by_id(cid):
    return next(c for c in checks.CHECKS if c.id == cid)


def test_check_ids_and_titles_are_exact():
    assert [(c.id, c.title) for c in checks.CHECKS] == [
        ("claude", "Claude is installed"), ("python", "Python"), ("git", "Git"), ("node", "Node"),
        ("guard_files", "The guard is intact"), ("guard_plugin", "Guard plugin"),
        ("guard_selftest", "The guard blocks what it should"),
        ("guard_errors", "Guard errors in the last 7 days"), ("agent_tokens", "Agent tokens"), ("folders", "The app can write its folders"),
        ("database", "Database is up to date"), ("mcp", "Connected tools"), ("skill_packs", "Skill packs"),
        ("code_map", "Code map"), ("repo_roots", "Project folders"),
        ("docker", "Docker (for 'wait until ready' checks)"), ("disk", "Free disk space")]


def _fake_argv(monkeypatch, answers):
    seen = []

    async def fake(argv, timeout=8):
        seen.append(argv)
        return answers(argv)

    monkeypatch.setattr(checks, "run_argv", fake)
    return seen


@pytest.mark.parametrize("cid,missing", [("claude", PROBLEM), ("git", PROBLEM), ("node", ATTENTION), ("docker", ATTENTION)])
def test_tool_checks_ok_and_missing(monkeypatch, cid, missing):
    _fake_argv(monkeypatch, lambda argv: (0, "tool 1.2.3"))
    assert run(by_id(cid).fn(None)).status == OK
    _fake_argv(monkeypatch, lambda argv: (None, "not found"))
    r = run(by_id(cid).fn(None))
    assert r.status == missing and r.fix


def test_claude_only_runs_version(monkeypatch):
    seen = _fake_argv(monkeypatch, lambda argv: (0, "2.1.0 (Claude Code)"))
    r = run(by_id("claude").fn(None))
    assert seen == [[settings.claude_bin, "--version"]]
    assert r.detail["version"] == "2.1.0 (Claude Code)"


def test_claude_runs_the_mock_for_real():
    r = run(by_id("claude").fn(None))  # CO_CLAUDE_BIN is the mock script; stdin is /dev/null
    assert r.status == OK and r.detail["version"]  # never hangs


def test_tool_check_nonzero_exit_is_not_ok(monkeypatch):
    _fake_argv(monkeypatch, lambda argv: (3, "boom"))
    assert run(by_id("git").fn(None)).status == PROBLEM
    assert run(by_id("node").fn(None)).status == ATTENTION


def test_docker_installed_but_stopped_is_attention(monkeypatch):
    _fake_argv(monkeypatch, lambda argv: (1, "Cannot connect to the Docker daemon"))
    r = run(by_id("docker").fn(None))
    assert r.status == ATTENTION and "not running" in r.summary


def test_python_ok():
    assert run(by_id("python").fn(None)).status == OK


@pytest.mark.parametrize("free_gb,status", [(50, OK), (3, ATTENTION), (0.5, PROBLEM)])
def test_disk(monkeypatch, free_gb, status):
    monkeypatch.setattr(checks, "disk_free_bytes", lambda p: int(free_gb * checks.GB))
    assert run(by_id("disk").fn(None)).status == status


def test_folders(monkeypatch):
    assert run(by_id("folders").fn(None)).status == OK
    monkeypatch.setattr(checks, "_can_write", lambda d: d.name != "projects" and "projects" not in str(d))
    r = run(by_id("folders").fn(None))
    assert r.status == PROBLEM and "projects" in r.summary


def test_can_write_leaves_nothing(tmp_path):
    assert checks._can_write(tmp_path / "later" / "deeper")  # the nearest existing parent is tested
    assert list(tmp_path.iterdir()) == []
    os.chmod(tmp_path, 0o500)
    try:
        if os.access(tmp_path, os.W_OK):
            pytest.skip("running as root")
        assert not checks._can_write(tmp_path)
    finally:
        os.chmod(tmp_path, 0o700)


def test_guard_files_ok_and_tampered():
    ensure_plugin(guard_runtime.plugin_root())
    assert run(by_id("guard_files").fn(None)).status == OK
    root = guard_runtime.plugin_root()
    saved = os.readlink(root)
    try:
        os.unlink(root)  # missing
        r = run(by_id("guard_files").fn(None))
        assert r.status == PROBLEM and r.fix == "Restart the app to restore it."
    finally:
        os.symlink(saved, root)
    ensure_plugin(root)


def test_guard_files_changed_file(monkeypatch):
    monkeypatch.setattr(checks, "_guard_intact", lambda: (False, "a guard file was changed"))
    r = run(by_id("guard_files").fn(None))
    assert r.status == PROBLEM and "changed" in r.summary and r.fix == "Restart the app to restore it."


def test_guard_selftest_runs_the_real_hook():
    ensure_plugin(guard_runtime.plugin_root())
    r = run(by_id("guard_selftest").fn(None))
    assert r.detail["harmless_call"] == "allow", r
    assert r.detail["dangerous_call"] == "deny", r
    assert r.status in (OK, ATTENTION)  # slow only when the machine is loaded
    assert "harmless_ms" in r.detail and "dangerous_ms" in r.detail
    assert not any(p.name.startswith("health-") for p in (settings.workspaces_dir / "_tasks").glob("*"))


def test_guard_selftest_wrong_answers_are_a_problem(monkeypatch):
    ensure_plugin(guard_runtime.plugin_root())
    monkeypatch.setattr(checks, "_hook_call", lambda argv, env, payload: ("allow", 5))
    r = run(by_id("guard_selftest").fn(None))
    assert r.status == PROBLEM and "not blocked" in r.summary


def test_guard_selftest_slow_is_attention(monkeypatch):
    ensure_plugin(guard_runtime.plugin_root())
    answers = iter([("allow", 2500), ("deny", 10)])
    monkeypatch.setattr(checks, "_hook_call", lambda argv, env, payload: next(answers))
    assert run(by_id("guard_selftest").fn(None)).status == ATTENTION


def test_guard_selftest_without_plugin(monkeypatch, tmp_path):
    monkeypatch.setattr(guard_runtime, "plugin_root", lambda: tmp_path / "nope")
    assert run(by_id("guard_selftest").fn(None)).status == PROBLEM


# -- DB checks ---------------------------------------------------------------
@pytest.fixture
def db(client):
    from app.database import SessionLocal

    return SessionLocal


def test_database_up_to_date_and_behind(db, monkeypatch):
    r = run(by_id("database").fn(db))
    assert r.status == OK and r.detail["current"] == r.detail["latest"]
    monkeypatch.setattr(checks, "_head_revision", lambda: "9999")
    r = run(by_id("database").fn(db))
    assert r.status == PROBLEM and r.fix


def test_guard_errors(db):
    import uuid
    from datetime import datetime, timedelta, timezone

    from app.models import Task, TaskEvent

    async def go(rows):
        async with db() as s:
            tid = uuid.uuid4().hex
            s.add(Task(id=tid, title="t", prompt="p", root_id=tid, status="done"))
            await s.flush()
            for i, (payload, age) in enumerate(rows):
                s.add(TaskEvent(task_id=tid, seq=i, type="guard", payload=payload,
                                created_at=datetime.now(timezone.utc) - age))
            await s.commit()
            return tid

    async def wipe(tid):
        async with db() as s:
            t = await s.get(Task, tid)
            await s.delete(t)
            await s.commit()

    before = run(by_id("guard_errors").fn(db)).detail["count"]
    tid = run(go([
        ({"event": "guard_error", "hook_event": "Stop", "error": "ValueError: bad"}, timedelta(hours=1)),
        ({"event": "guard_error", "hook_event": "Stop", "error": "old"}, timedelta(days=9)),
        ({"event": "PreToolUse", "decision": "deny"}, timedelta(hours=1)),
    ]))
    try:
        r = run(by_id("guard_errors").fn(db))
        assert r.status == ATTENTION and r.detail["count"] == before + 1
        assert r.detail["latest"].startswith("Stop: ")
    finally:
        run(wipe(tid))
    assert run(by_id("guard_errors").fn(db)).detail["count"] == before


def test_mcp(db):
    from app.models import McpServer

    async def go(status):
        async with db() as s:
            m = McpServer(name=f"hc-{status}", command="x", status=status)
            s.add(m)
            await s.commit()
            return m.id

    async def rm(i):
        async with db() as s:
            await s.delete(await s.get(McpServer, i))
            await s.commit()

    assert run(by_id("mcp").fn(db)).status == OK
    i = run(go("disconnected"))
    try:
        r = run(by_id("mcp").fn(db))
        assert r.status == ATTENTION and "hc-disconnected" in r.summary
        assert any(m["name"] == "hc-disconnected" and m["status"] == "disconnected" and m["version"] is None
                   for m in r.detail["servers"])
    finally:
        run(rm(i))


def test_skill_packs(db, tmp_path):
    import subprocess

    from app.models import SkillPack

    repo = tmp_path / "pack"
    repo.mkdir()
    env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "PATH": os.environ["PATH"]}
    for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "x"]):
        subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, env=env, check=True, capture_output=True,
                         text=True).stdout.strip()

    async def go(**kw):
        async with db() as s:
            p = SkillPack(name="hc-pack", local_path=str(repo), version="7.0.2", **kw)
            s.add(p)
            await s.commit()
            return p.id

    async def rm(i):
        async with db() as s:
            await s.delete(await s.get(SkillPack, i))
            await s.commit()

    def listing():
        r = run(by_id("skill_packs").fn(db))
        assert r.status == OK
        return r, r.detail["packs"]

    i = run(go())  # no latest_sha: nothing to compare
    try:
        r, packs = listing()
        assert {"name": "hc-pack", "pinned": sha[:7], "version": "7.0.2", "update_known": False} in packs
    finally:
        run(rm(i))

    i = run(go(latest_sha=sha))  # current equals latest: no update
    try:
        r, packs = listing()
        assert [p for p in packs if p["name"] == "hc-pack"][0]["update_known"] is False
        assert "with an update" not in r.summary
    finally:
        run(rm(i))

    i = run(go(latest_sha="b" * 40))
    try:
        r, packs = listing()
        assert [p for p in packs if p["name"] == "hc-pack"][0]["update_known"] is True
        assert "1 with an update" in r.summary
    finally:
        run(rm(i))


# -- runner ------------------------------------------------------------------
def entry(status):
    return {"status": status}


def test_overall_and_lines():
    assert runner.overall([]) == "ok"
    assert runner.overall([entry("ok"), entry("attention")]) == "attention"
    assert runner.overall([entry("attention"), entry("problem"), entry("ok")]) == "problem"
    assert runner.overall_line([entry("ok")]) == "Everything looks good"
    assert runner.overall_line([entry("ok"), entry("attention"), entry("attention")]) == "2 things need attention"
    assert runner.overall_line([entry("attention")]) == "1 thing needs attention"
    assert runner.overall_line([entry("attention"), entry("problem")]) == "1 problem"
    assert runner.overall_line([entry("problem"), entry("problem"), entry("ok")]) == "2 problems"


def test_scrub(monkeypatch):
    home = guard_runtime.real_home()
    monkeypatch.setenv("CO_API_TOKEN", "supersecretvalue1234")
    out = runner.scrub(f"v1 at {home}/bin/claude token supersecretvalue1234 and /Users/someoneelse/x")
    assert home not in out and "supersecretvalue1234" not in out and "someoneelse" not in out
    assert "~/bin/claude" in out
    assert len(runner.scrub("x" * 2000)) == 500
    assert runner.scrub(None) == ""
    assert "sk-ant-" not in runner.scrub("key sk-ant-" + "a" * 40)


def test_scrub_covers_detail_and_fix(monkeypatch):
    async def leaky(s):
        return Result(OK, f"in {guard_runtime.real_home()}/x", {"v": ["/Users/zed/a"]}, f"fix {guard_runtime.real_home()}")

    monkeypatch.setattr(checks, "CHECKS", [checks.Check("leaky", "Leaky", leaky)])
    runner.clear_cache()
    out = run(runner.run_all(lambda: None, force=True))["checks"][0]
    assert out["summary"] == "in ~/x" and out["detail"] == {"v": ["~/a"]} and out["fix"] == "fix ~"


def test_a_check_that_hangs_or_raises_is_could_not_check(monkeypatch):
    async def hang(s):
        await asyncio.sleep(5)

    async def boom(s):
        raise RuntimeError(f"bad at {guard_runtime.real_home()}")

    async def fine(s):
        return Result(OK, "fine")

    monkeypatch.setattr(runner, "CHECK_BUDGET", 0.2)
    monkeypatch.setattr(checks, "CHECKS", [checks.Check("a", "A", hang), checks.Check("b", "B", boom), checks.Check("c", "C", fine)])
    runner.clear_cache()
    import time

    t = time.monotonic()
    data = run(runner.run_all(lambda: None, force=True))
    assert time.monotonic() - t < 3
    a, b, c = data["checks"]
    assert (a["status"], a["summary"]) == ("problem", "could not check")
    assert (b["status"], b["summary"]) == ("problem", "could not check")
    assert guard_runtime.real_home() not in str(b)
    assert c["status"] == "ok" and data["overall"] == "problem"
    assert set(data) == {"checked_at", "overall", "checks"}


def test_total_budget_cuts_off_everything(monkeypatch):
    async def hang(s):
        await asyncio.sleep(5)

    monkeypatch.setattr(runner, "CHECK_BUDGET", 30)
    monkeypatch.setattr(runner, "TOTAL_BUDGET", 0.2)
    monkeypatch.setattr(checks, "CHECKS", [checks.Check("a", "A", hang)])
    runner.clear_cache()
    assert run(runner.run_all(lambda: None, force=True))["checks"][0]["summary"] == "could not check"


def test_cache_holds_for_30_seconds(monkeypatch):
    calls = []

    async def counted(s):
        calls.append(1)
        return Result(OK, "x")

    now = [1000.0]
    monkeypatch.setattr(runner, "_clock", lambda: now[0])
    monkeypatch.setattr(checks, "CHECKS", [checks.Check("a", "A", counted)])
    runner.clear_cache()

    async def go():
        a = await runner.run_all(lambda: None)
        now[0] += 29
        b = await runner.run_all(lambda: None)
        assert a is b and len(calls) == 1
        await runner.run_all(lambda: None, force=True)
        assert len(calls) == 2
        now[0] += 31
        await runner.run_all(lambda: None)
        assert len(calls) == 3

    run(go())
    runner.clear_cache()


def test_concurrent_refreshes_share_one_run(monkeypatch):
    calls = []

    async def slow(s):
        calls.append(1)
        await asyncio.sleep(0.2)
        return Result(OK, "x")

    monkeypatch.setattr(checks, "CHECKS", [checks.Check("a", "A", slow)])
    runner.clear_cache()

    async def go():
        return await asyncio.gather(*(runner.run_all(lambda: None, force=True) for _ in range(5)))

    out = run(go())
    assert len(calls) == 1 and all(o is out[0] for o in out)
    runner.clear_cache()


def test_real_run_has_no_home_path_and_is_complete(client):
    runner.clear_cache()
    data = run(runner.run_all(force=True))
    assert [c["id"] for c in data["checks"]] == [c.id for c in checks.CHECKS]
    assert guard_runtime.real_home() not in str(data)
    runner.clear_cache()


def test_code_map(db):
    """Independent of rows other test files leave in the shared database: every code map project and enabled
    server already there is set aside for the test (and put back after), so the check sees only its own."""
    from sqlalchemy import select

    from app.models import McpServer, Project, ProjectIndex

    async def set_aside():
        async with db() as s:
            projects = (await s.execute(select(Project).where(Project.code_map.is_(True)))).scalars().all()
            servers = (await s.execute(select(McpServer).where(McpServer.enabled.is_(True)))).scalars().all()
            aside = ([p.id for p in projects], [m.id for m in servers])
            for p in projects:
                p.code_map = False
            for m in servers:
                m.enabled = False
            await s.commit()
        return aside

    async def put_back(aside):
        async with db() as s:
            for pid in aside[0]:
                p = await s.get(Project, pid)
                if p is not None:
                    p.code_map = True
            for mid in aside[1]:
                m = await s.get(McpServer, mid)
                if m is not None:
                    m.enabled = True
            await s.commit()

    aside = run(set_aside())
    try:
        _code_map_cases(db, McpServer, Project, ProjectIndex)
    finally:
        run(put_back(aside))


def _code_map_cases(db, McpServer, Project, ProjectIndex):
    async def go(tool, projects):
        ids = []
        async with db() as s:
            if tool:
                m = McpServer(name="hc-cm", command="npx", args=["-y", "codebase-memory-mcp@0.12.1"])
                s.add(m)
                await s.flush()
                ids.append((McpServer, m.id))
            for i, st in enumerate(projects):
                p = Project(name=f"hc-cm-{i}", slug=f"hc-cm-{i}", directory="/x", code_map=True)
                s.add(p)
                await s.flush()
                s.add(ProjectIndex(project_id=p.id, status=st))
                ids.append((Project, p.id))
            await s.commit()
        return ids

    async def rm(ids):
        async with db() as s:
            for model, i in ids:
                await s.delete(await s.get(model, i))
            await s.commit()

    def check(tool, projects):
        ids = run(go(tool, projects))
        try:
            return run(by_id("code_map").fn(db))
        finally:
            run(rm(ids))

    r = check(False, [])
    assert (r.status, r.summary) == (OK, "The Code map tool is not added yet")
    r = check(True, [])
    assert (r.status, r.summary) == (OK, "No project keeps a code map")
    r = check(False, ["ok"])
    assert (r.status, r.summary) == (ATTENTION, "The Code map tool is not added yet")
    r = check(True, ["ok", "ok"])
    assert (r.status, r.summary) == (OK, "2 projects keep a code map; all up to date")
    r = check(True, ["ok", "failed"])
    assert (r.status, r.summary) == (ATTENTION, "1 project's code map is failing")
    r = check(True, ["ok", "running", "never"])
    assert (r.status, r.summary) == (OK, "3 projects keep a code map; 1 not built yet, 1 building")


# -- phase 7e: versions, code-map errors, Node floor, self-tests ---------------
def test_node_floor(monkeypatch):
    _fake_argv(monkeypatch, lambda argv: (0, "v18.19.1"))
    r = run(by_id("node").fn(None))
    assert r.status == ATTENTION
    assert r.summary == "Node 18.19.1 is too old for the browser tools (they need 20 or newer)."
    assert r.detail["major"] == 18
    for line in ("v20.0.0", "v22.3.1"):
        _fake_argv(monkeypatch, lambda argv, line=line: (0, line))
        assert run(by_id("node").fn(None)).status == OK
    _fake_argv(monkeypatch, lambda argv: (0, "something odd"))  # does not parse: no warning
    assert run(by_id("node").fn(None)).status == OK
    assert checks.node_major("v9.1.0") == 9 and checks.node_major("") is None


def _mcp_rows(db, specs):
    from app.models import McpServer

    async def go():
        async with db() as s:
            ids = []
            for name, args, version in specs:
                m = McpServer(name=name, command="npx", args=args, server_version=version, status="healthy")
                s.add(m)
                await s.flush()
                ids.append(m.id)
            await s.commit()
        return ids

    async def rm(ids):
        async with db() as s:
            for i in ids:
                await s.delete(await s.get(McpServer, i))
            await s.commit()

    return go, rm


def test_mcp_versions_and_pin_mismatch(db):
    go, rm = _mcp_rows(db, [("hv-same", ["-y", "@playwright/mcp@0.0.86"], "0.0.86"),
                            ("hv-off", ["-y", "chrome-devtools-mcp@1.13.0"], "1.14.0"),
                            ("hv-nopin", ["-y", "other-tool@2.0.0"], "9.9.9"),
                            ("hv-latest", ["-y", "shadcn@latest", "mcp"], "4.0.0"),
                            ("hv-noinfo", ["-y", "codebase-memory-mcp@0.12.1"], None),
                            ("hv-old", ["-y", "shadcn@4.0.0", "mcp"], "4.0.0")])
    ids = run(go())
    try:
        r = run(by_id("mcp").fn(db))
    finally:
        run(rm(ids))
    by = {m["name"]: m for m in r.detail["servers"]}
    assert r.status == ATTENTION and "hv-off is at 1.14.0, expected 1.13.0." in r.summary
    assert by["hv-same"]["version"] == "0.0.86" and by["hv-same"]["version_differs"] is False
    assert by["hv-off"]["pinned"] == "1.13.0" and by["hv-off"]["version_differs"] is True
    for n in ("hv-nopin", "hv-latest", "hv-noinfo"):  # no certain pin, or no version told: no warning
        assert by[n]["version_differs"] is False and by[n]["pinned"] is None
    assert by["hv-noinfo"]["version"] is None
    assert by["hv-old"]["pinned"] == "4.23.0" and by["hv-old"]["version_differs"] is True  # the preset moved on
    assert "hv-old is at 4.0.0, expected 4.23.0." in r.summary
    assert summary_only(r.summary, "hv-nopin") and summary_only(r.summary, "hv-noinfo")


def summary_only(summary, name):
    return f"{name} is at" not in summary


def test_handshake_keeps_server_info(tmp_path):
    from app.mcp_client import probe_stdio

    def script(name, info):
        f = tmp_path / name
        f.write_text("import sys, json\n"
                     "for line in sys.stdin:\n"
                     "    m = json.loads(line)\n"
                     "    if m.get('method') == 'initialize':\n"
                     f"        print(json.dumps({{'jsonrpc': '2.0', 'id': m['id'], 'result': {{'protocolVersion': '2024-11-05', 'capabilities': {{}}{info}}}}}), flush=True)\n"
                     "    elif m.get('method') == 'tools/list':\n"
                     "        print(json.dumps({'jsonrpc': '2.0', 'id': m['id'], 'result': {'tools': []}}), flush=True)\n")
        return str(f)

    r = run(probe_stdio(sys.executable, [script("a.py", ", 'serverInfo': {'name': 'x', 'version': '1.2.3'}")], None, timeout=10))
    assert r["ok"] and r["server_version"] == "1.2.3" and r["server_name"] == "x"
    r = run(probe_stdio(sys.executable, [script("b.py", "")], None, timeout=10))  # no serverInfo at all
    assert r["ok"] and r["server_version"] is None
    r = run(probe_stdio(sys.executable, [script("c.py", ", 'serverInfo': 'oops'")], None, timeout=10))
    assert r["ok"] and r["server_version"] is None


def test_code_map_error_and_two_engines(db):
    from sqlalchemy import select

    from app.models import McpServer, Project, ProjectIndex

    async def go():
        async with db() as s:
            others = (await s.execute(select(McpServer).where(McpServer.enabled.is_(True)))).scalars().all()
            off_ids = [m.id for m in others]
            for m in others:
                m.enabled = False
            projs = (await s.execute(select(Project).where(Project.code_map.is_(True)))).scalars().all()
            proj_ids = [p.id for p in projs]
            for p in projs:
                p.code_map = False
            made = []
            for n in ("hce-a", "hce-b"):
                m = McpServer(name=n, command="npx", args=["-y", "codebase-memory-mcp@0.12.1"])
                s.add(m)
                await s.flush()
                made.append((McpServer, m.id))
            p = Project(name="hce-p", slug="hce-p", directory="/x", code_map=True)
            s.add(p)
            await s.flush()
            s.add(ProjectIndex(project_id=p.id, status="failed", error="x" * 400, fail_count=3))
            made.append((Project, p.id))
            await s.commit()
        return off_ids, proj_ids, made

    async def undo(state):
        off_ids, proj_ids, made = state
        async with db() as s:
            for model, i in made:
                await s.delete(await s.get(model, i))
            for i in off_ids:
                (await s.get(McpServer, i)).enabled = True
            for i in proj_ids:
                (await s.get(Project, i)).code_map = True
            await s.commit()

    state = run(go())
    try:
        r = run(by_id("code_map").fn(db))
    finally:
        run(undo(state))
    assert r.status == ATTENTION
    assert "Two code-map engines are set up (hce-a, hce-b); only hce-a is used." in r.summary
    row = r.detail["projects"][0]
    assert row["error"] == "x" * 200 and row["failures"] == 3
    assert r.detail["engines"] == ["hce-a", "hce-b"]


def test_agent_tokens_self_test(db):
    from sqlalchemy import func, select

    from app.models import ApiToken

    async def count():
        async with db() as s:
            return (await s.execute(select(func.count()).select_from(ApiToken))).scalar()

    before = run(count())
    r = run(by_id("agent_tokens").fn(db))
    assert r.status == OK, r
    if r.detail.get("tested") is not False:
        assert r.detail == {"accepted_for_its_task": True, "accepted_for_another_task": False,
                            "refused_after_revoke": True}
    assert run(count()) == before  # nothing left behind


def test_agent_tokens_fail_when_revoke_does_nothing(db, monkeypatch):
    from app import auth

    async def noop(s, task_id):
        return None

    monkeypatch.setattr(auth, "revoke_task_tokens", noop)
    r = run(by_id("agent_tokens").fn(db))
    if r.detail and r.detail.get("tested") is False:
        pytest.skip("no user in this database")
    assert r.status == PROBLEM and "revoked" in r.summary


def _fake_claude(tmp_path, body):
    f = tmp_path / "claude"
    f.write_text("#!/bin/sh\n" + body)
    f.chmod(0o755)
    return str(f)


@pytest.fixture
def plugin_root(monkeypatch, tmp_path):
    root = tmp_path / "plug"
    root.mkdir()
    monkeypatch.setattr(guard_runtime, "plugin_root", lambda: root)
    return root


def test_guard_plugin_validate(monkeypatch, tmp_path, plugin_root):
    log = tmp_path / "calls.txt"
    ok = _fake_claude(tmp_path, f'echo "$@" >> {log}\nif [ "$2" = "--help" ]; then printf "Commands:\\n  validate <path>\\n"; exit 0; fi\nexit 0\n')
    monkeypatch.setattr(settings, "claude_bin", ok)
    r = run(by_id("guard_plugin").fn(None))
    assert (r.status, r.summary) == (OK, "Claude accepts the guard plugin")
    assert f"plugin validate {plugin_root.resolve()}" in log.read_text()
    bad = _fake_claude(tmp_path, 'if [ "$2" = "--help" ]; then echo "validate"; exit 0; fi\necho "manifest broken"; exit 1\n')
    monkeypatch.setattr(settings, "claude_bin", bad)
    r = run(by_id("guard_plugin").fn(None))
    assert r.status == ATTENTION and r.detail["answer"] == "manifest broken"


def test_guard_plugin_command_missing_is_ok_and_never_run(monkeypatch, tmp_path, plugin_root):
    log = tmp_path / "calls.txt"
    old = _fake_claude(tmp_path, f'echo "$@" >> {log}\nif [ "$2" = "--help" ]; then echo "Usage: claude [prompt]"; exit 0; fi\nexit 2\n')
    monkeypatch.setattr(settings, "claude_bin", old)
    r = run(by_id("guard_plugin").fn(None))
    assert r.status == OK and "no validate command" in r.summary
    assert "validate" not in log.read_text()  # an old CLI never gets the validate call (it could take it as a prompt)


def test_guard_plugin_hang_is_bounded(monkeypatch, tmp_path, plugin_root):
    hang = _fake_claude(tmp_path, 'if [ "$2" = "--help" ]; then echo validate; exit 0; fi\nexec sleep 30\n')
    monkeypatch.setattr(settings, "claude_bin", hang)
    monkeypatch.setattr(checks, "PLUGIN_VALIDATE_TIMEOUT", 0.5)
    started = time.monotonic()
    r = run(by_id("guard_plugin").fn(None))
    assert r.status == ATTENTION and "did not answer in time" in r.summary
    assert time.monotonic() - started < 10
    assert by_id("guard_plugin").budget > checks.PLUGIN_HELP_TIMEOUT + checks.PLUGIN_VALIDATE_TIMEOUT
    monkeypatch.setattr(settings, "claude_bin", str(tmp_path / "nope"))
    assert run(by_id("guard_plugin").fn(None)).status == ATTENTION


def test_agent_tokens_scope_is_really_tested(db, monkeypatch):
    from app import auth

    async def everything(s, tok, task_id):
        return True

    monkeypatch.setattr(auth, "_agent_may_read", everything)  # a token that fits every task is a failure
    r = run(by_id("agent_tokens").fn(db))
    if r.detail and r.detail.get("tested") is False:
        pytest.skip("no user in this database")
    assert r.status == PROBLEM and "another task" in r.summary


def test_guard_plugin_help_needs_a_validate_line(monkeypatch, tmp_path, plugin_root):
    log = tmp_path / "calls.txt"
    old = _fake_claude(tmp_path, f'echo "$@" >> {log}\nif [ "$2" = "--help" ]; then echo "Usage: claude [prompt]  (see validate docs)"; exit 0; fi\nexit 2\n')
    monkeypatch.setattr(settings, "claude_bin", old)
    assert run(by_id("guard_plugin").fn(None)).status == OK
    assert "plugin validate" not in log.read_text()


def test_guard_plugin_env_is_minimal(monkeypatch, tmp_path, plugin_root):
    out = tmp_path / "env.txt"
    fake = _fake_claude(tmp_path, f'env > {out}\nif [ "$2" = "--help" ]; then echo "validate"; fi\nexit 0\n')
    monkeypatch.setattr(settings, "claude_bin", fake)
    monkeypatch.setenv("CO_SECRET_THING", "hunter2")
    run(by_id("guard_plugin").fn(None))
    assert "hunter2" not in out.read_text() and "CO_SECRET_THING" not in out.read_text()


def test_guard_plugin_result_is_remembered_until_check_again(monkeypatch, tmp_path, plugin_root):
    log = tmp_path / "calls.txt"
    fake = _fake_claude(tmp_path, f'if [ "$1" = "--version" ]; then echo "9.9.9 (Claude Code)"; exit 0; fi\necho "$@" >> {log}\nif [ "$2" = "--help" ]; then echo validate; fi\nexit 0\n')
    monkeypatch.setattr(settings, "claude_bin", fake)
    checks.PLUGIN_CACHE.clear()
    assert run(by_id("guard_plugin").fn(None)).status == OK
    assert run(by_id("guard_plugin").fn(None)).status == OK
    assert log.read_text().count("plugin validate") == 1
    from app.health import runner

    async def again():
        await runner.run_all(force=True)

    run(again())
    assert log.read_text().count("plugin validate") == 2  # "Check again" validated afresh
    checks.PLUGIN_CACHE.clear()
