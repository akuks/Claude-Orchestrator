"""Code map index job (phase 6b): a mock script stands in for the index command."""

import asyncio
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.codemap import indexer
from app.codemap import status as cm_status
from app.config import settings
from app.database import SessionLocal
from app.models import McpServer, Project, ProjectIndex

MOCK = r'''
import os, sys, time, subprocess, json
here = os.path.dirname(os.path.abspath(__file__))
mode = open(os.path.join(here, "mode")).read().strip()
open(os.path.join(here, "argv.json"), "w").write(json.dumps({"argv": sys.argv[1:], "env": dict(os.environ), "cwd": os.getcwd()}))
with open(os.path.join(here, "runs"), "a") as f:
    f.write("x\n")
if mode == "ok":
    print("indexed")
elif mode == "slow":
    time.sleep(0.8)
    print("slow done")
elif mode == "fail":
    print("boom token=ghp_abcdefghijklmnopqrstuvwxyz0123456789ab")
    sys.exit(3)
elif mode == "hang":
    p = subprocess.Popen(["sleep", "300"])
    open(os.path.join(here, "child.pid"), "w").write(str(p.pid))
    time.sleep(300)
elif mode == "flood":
    sys.stdout.write("y" * 100 + "\n" * 1 )
    for _ in range(30000):
        sys.stdout.write("z" * 99 + "\n")
    sys.stdout.flush()
'''


@pytest.fixture
def mock(client, tmp_path, monkeypatch):
    script = tmp_path / "mockindex.py"
    script.write_text(MOCK)
    monkeypatch.setattr(settings, "code_map_index_command", [sys.executable, str(script)])
    indexer._inflight.clear()

    def set_mode(m):
        (tmp_path / "mode").write_text(m)

    set_mode("ok")
    yield tmp_path, set_mode

    async def drop():  # the code map servers this test registered
        async with SessionLocal() as s:
            for mid in _servers:
                row = await s.get(McpServer, mid)
                if row is not None:
                    await s.delete(row)
            await s.commit()
        _servers.clear()

    asyncio.run(drop())


_servers: list[str] = []


async def add_server(project_name):
    """A code map server for one project (scope project: other tests never see it); dropped by `mock`."""
    async with SessionLocal() as s:
        m = McpServer(name=f"Code map {uuid.uuid4().hex[:8]}", scope="project", project=project_name, command="npx",
                      args=["-y", "codebase-memory-mcp@0.12.1"], enabled=True)
        s.add(m)
        await s.commit()
        _servers.append(m.id)
        return m.id


def _git(d, *a):
    subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


def make_repo(d):
    d.mkdir(parents=True, exist_ok=True)
    _git(d, "init", "-q")
    (d / "a.txt").write_text("1")
    _git(d, "add", ".")
    _git(d, "commit", "-q", "-m", "one")
    return subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def commit_more(d, n="2"):
    (d / f"f{n}.txt").write_text(n)
    _git(d, "add", ".")
    _git(d, "commit", "-q", "-m", n)
    return subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


async def _mk_project(directory, code_map=True, env=None, server=True):
    async with SessionLocal() as s:
        p = Project(name=f"cm-{uuid.uuid4().hex[:8]}", slug=f"cm-{uuid.uuid4().hex[:8]}", directory=str(directory),
                    code_map=code_map, env=env or {})
        s.add(p)
        await s.commit()
        pid, name = p.id, p.name
    if server:
        await add_server(name)
    return pid


async def _row(pid):
    async with SessionLocal() as s:
        r = await s.get(ProjectIndex, pid)
        if r is not None:
            await s.refresh(r)
        return r


def test_success_stores_head_before_run(mock, tmp_path):
    mdir, _ = mock
    repo = tmp_path / "repo"
    head = make_repo(repo)

    async def go():
        pid = await _mk_project(repo)
        assert await indexer.refresh(pid) == "ok"
        r = await _row(pid)
        assert r.status == "ok" and r.sha == head and r.indexed_at and r.fail_count == 0 and r.error is None
        return pid

    pid = asyncio.run(go())
    seen = __import__("json").loads((mdir / "argv.json").read_text())
    assert seen["argv"] == [os.path.realpath(repo)]  # the real directory is the last argv item
    assert os.path.samefile(seen["cwd"], indexer.run_dir(pid))  # never the project folder
    assert not os.path.samefile(seen["cwd"], repo)
    assert os.stat(indexer.run_dir(pid)).st_mode & 0o777 == 0o700
    assert os.stat(indexer.log_path(pid)).st_mode & 0o777 == 0o600
    assert os.stat(indexer.codemap_root()).st_mode & 0o777 == 0o700


def test_sha_is_head_before_the_run(mock, tmp_path):
    mdir, set_mode = mock
    repo = tmp_path / "repo2"
    head = make_repo(repo)
    set_mode("slow")

    async def go():
        pid = await _mk_project(repo)
        indexer.start(pid)
        await asyncio.sleep(0.3)
        commit_more(repo)  # HEAD moves while the index runs
        await indexer.wait_idle()
        return (await _row(pid)).sha

    assert asyncio.run(go()) == head


def test_no_credentials_in_env(mock, tmp_path, monkeypatch):
    mdir, _ = mock
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-value")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    monkeypatch.setenv("CO_SECRET_KEY", "k")
    repo = tmp_path / "repo3"
    make_repo(repo)

    async def go():
        pid = await _mk_project(repo, env={"MY_TOOL_SETTING": "abc123456"})
        await indexer.refresh(pid)
        return pid

    asyncio.run(go())
    env = __import__("json").loads((mdir / "argv.json").read_text())["env"]
    assert not {"ANTHROPIC_API_KEY", "GITHUB_TOKEN", "CO_SECRET_KEY"} & set(env)
    assert "HOME" in env and "MY_TOOL_SETTING" in env


def test_neutral_folder_and_npm_config_off(mock, tmp_path, monkeypatch):
    """The project's .npmrc and node_modules/.bin are not in reach: the command runs elsewhere, with npm's
    user/global config off and no INIT_CWD/PWD pointing at the project."""
    mdir, _ = mock
    monkeypatch.setenv("INIT_CWD", str(tmp_path))
    monkeypatch.setenv("PWD", str(tmp_path))
    repo = tmp_path / "repo-npm"
    make_repo(repo)
    (repo / ".npmrc").write_text("registry=http://evil.invalid/\n")
    (repo / "node_modules" / ".bin").mkdir(parents=True)
    (repo / "node_modules" / ".bin" / "codebase-memory-mcp").write_text("#!/bin/sh\nexit 0\n")

    async def go():
        pid = await _mk_project(repo)
        await indexer.refresh(pid)
        return pid

    pid = asyncio.run(go())
    seen = __import__("json").loads((mdir / "argv.json").read_text())
    env = seen["env"]
    assert os.path.samefile(seen["cwd"], indexer.run_dir(pid))
    assert sorted(os.listdir(seen["cwd"])) == [".npmrc", "node_modules", "package.json", "uv.toml"]  # inert markers only
    assert env["npm_config_userconfig"] == "/dev/null" and env["npm_config_globalconfig"] == "/dev/null"
    assert env["NPM_CONFIG_UPDATE_NOTIFIER"] == "false"
    assert env["npm_config_fund"] == "false" and env["npm_config_audit"] == "false"
    assert "INIT_CWD" not in env and "PWD" not in env
    assert str(repo) not in env.get("PATH", "")


def test_git_config_hooks_are_switched_off(mock, tmp_path):
    """Every git the tool runs ignores the repo-local core.fsmonitor and core.hooksPath (gitsafe's flags)."""
    mdir, _ = mock
    repo = tmp_path / "repo-git"
    make_repo(repo)

    async def go():
        await indexer.refresh(await _mk_project(repo))

    asyncio.run(go())
    env = __import__("json").loads((mdir / "argv.json").read_text())["env"]
    assert env["GIT_CONFIG_COUNT"] == "2"
    assert (env["GIT_CONFIG_KEY_0"], env["GIT_CONFIG_VALUE_0"]) == ("core.fsmonitor", "false")
    assert (env["GIT_CONFIG_KEY_1"], env["GIT_CONFIG_VALUE_1"]) == ("core.hooksPath", "/dev/null")
    assert env["GIT_CONFIG_NOSYSTEM"] == "1" and env["GIT_CONFIG_GLOBAL"] == os.devnull
    # a repo-local fsmonitor command is not run by git under this env
    marker = tmp_path / "fsmonitor-ran"
    _git(repo, "config", "core.fsmonitor", f"touch {marker}; false")
    subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], env=env, capture_output=True)
    assert not marker.exists()


def test_index_jobs_have_their_own_home(mock, tmp_path):
    mdir, _ = mock
    repo = tmp_path / "repo-home"
    make_repo(repo)

    async def go():
        await indexer.refresh(await _mk_project(repo))

    asyncio.run(go())
    env = __import__("json").loads((mdir / "argv.json").read_text())["env"]
    home = indexer.codemap_root() / "_home"
    assert env["HOME"] == str(home) and env["XDG_CONFIG_HOME"] == str(home / ".config")
    assert env["XDG_CACHE_HOME"] == str(home / ".cache")
    assert "_gates" not in env["HOME"]
    assert os.stat(home).st_mode & 0o777 == 0o700


def test_failure_scrubbed_with_growing_capped_backoff(mock, tmp_path):
    mdir, set_mode = mock
    repo = tmp_path / "repo4"
    make_repo(repo)
    set_mode("fail")

    async def go():
        pid = await _mk_project(repo)
        assert await indexer.refresh(pid, manual=True) == "failed"
        r = await _row(pid)
        assert r.status == "failed" and r.fail_count == 1 and "\n" not in r.error
        assert "ghp_abcdefgh" not in r.error and "code 3" in r.error
        assert cm_status.failure_kind(r.error) == "error"
        nxt = r.next_try_at.replace(tzinfo=timezone.utc)
        assert timedelta(minutes=1) < nxt - datetime.now(timezone.utc) <= timedelta(minutes=2)
        await indexer.refresh(pid, manual=True)
        r = await _row(pid)
        assert r.fail_count == 2
        assert r.next_try_at.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc) > timedelta(minutes=3)

    asyncio.run(go())
    assert [indexer.backoff_minutes(n) for n in (1, 2, 5, 6, 7, 20, 1000)] == [2, 4, 32, 60, 60, 60, 60]


def test_hang_is_killed_with_children(mock, tmp_path, monkeypatch):
    mdir, set_mode = mock
    repo = tmp_path / "repo5"
    make_repo(repo)
    set_mode("hang")
    monkeypatch.setattr(settings, "code_map_timeout_seconds", 1)

    async def go():
        pid = await _mk_project(repo)
        t0 = time.monotonic()
        assert await indexer.refresh(pid) == "failed"
        assert time.monotonic() - t0 < 20
        r = await _row(pid)
        assert "Timed out" in r.error and r.status == "failed"
        assert cm_status.failure_kind(r.error) == "timeout"

    asyncio.run(go())
    child = int((mdir / "child.pid").read_text())
    for _ in range(50):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("the index command's child is still alive")


def test_output_over_cap_is_cut(mock, tmp_path, monkeypatch):
    mdir, set_mode = mock
    repo = tmp_path / "repo6"
    make_repo(repo)
    set_mode("flood")
    monkeypatch.setattr(indexer, "LOG_CAP", 10_000)

    async def go():
        pid = await _mk_project(repo)
        assert await indexer.refresh(pid) == "ok"
        return pid

    pid = asyncio.run(go())
    assert indexer.log_path(pid).stat().st_size == 10_000


def test_one_job_per_project_and_two_server_wide(mock, tmp_path, monkeypatch):
    mdir, set_mode = mock
    set_mode("slow")
    monkeypatch.setattr(settings, "code_map_max_jobs", 2)
    repos = []
    for i in range(3):
        r = tmp_path / f"c{i}"
        make_repo(r)
        repos.append(r)
    (mdir / "runs").unlink(missing_ok=True)

    async def go():
        pids = [await _mk_project(r) for r in repos]
        a = asyncio.ensure_future(indexer.refresh(pids[0]))
        await asyncio.sleep(0)
        assert await indexer.refresh(pids[0]) == "running"  # the same project: one job
        b = asyncio.ensure_future(indexer.refresh(pids[1]))
        c = asyncio.ensure_future(indexer.refresh(pids[2]))
        for _ in range(100):  # poll: two jobs start, the third waits for a slot
            if (mdir / "runs").exists() and (mdir / "runs").read_text().count("x") >= 2:
                break
            await asyncio.sleep(0.05)
        assert (mdir / "runs").read_text().count("x") == 2  # the third waits
        assert await asyncio.gather(a, b, c) == ["ok", "ok", "ok"]
        assert (mdir / "runs").read_text().count("x") == 3

    asyncio.run(go())


def test_maybe_refresh_respects_backoff_manual_ignores(mock, tmp_path):
    mdir, set_mode = mock
    repo = tmp_path / "repo7"
    head = make_repo(repo)
    runs = mdir / "runs"
    runs.unlink(missing_ok=True)

    async def go():
        pid = await _mk_project(repo)
        set_mode("fail")
        await indexer.refresh(pid)
        assert runs.read_text().count("x") == 1
        await indexer.maybe_refresh(pid)  # in back-off
        await indexer.wait_idle()
        assert runs.read_text().count("x") == 1
        assert await indexer.refresh(pid) == "failed"  # a non-manual refresh respects it too
        assert runs.read_text().count("x") == 1
        set_mode("ok")
        assert await indexer.refresh(pid, manual=True) == "ok"  # manual ignores it
        assert runs.read_text().count("x") == 2
        await indexer.maybe_refresh(pid)  # HEAD == sha: nothing to do
        await indexer.wait_idle()
        assert runs.read_text().count("x") == 2
        commit_more(repo)
        await indexer.maybe_refresh(pid)  # HEAD moved, no back-off: a job starts
        await indexer.wait_idle()
        assert runs.read_text().count("x") == 3
        assert (await _row(pid)).sha != head
        # switched off: no-op
        async with SessionLocal() as s:
            (await s.get(Project, pid)).code_map = False
            await s.commit()
        commit_more(repo, "3")
        await indexer.maybe_refresh(pid)
        await indexer.wait_idle()
        assert runs.read_text().count("x") == 3

    asyncio.run(go())


def test_stale_running_row_is_reset(mock, tmp_path):
    repo = tmp_path / "repo8"
    make_repo(repo)

    async def go():
        pid_old = await _mk_project(repo, code_map=False)
        pid_new = await _mk_project(repo, code_map=False)
        async with SessionLocal() as s:
            s.add(ProjectIndex(project_id=pid_old, status="running",
                               started_at=datetime.now(timezone.utc) - timedelta(minutes=30)))
            s.add(ProjectIndex(project_id=pid_new, status="running", started_at=datetime.now(timezone.utc)))
            await s.commit()
        await indexer.scan_once()
        old, new = await _row(pid_old), await _row(pid_new)
        assert old.status == "failed" and "restarted" in old.error and old.next_try_at is not None
        assert new.status == "running"  # young: could be live
        await indexer.scan_once(startup=True)  # at boot nothing is live
        assert (await _row(pid_new)).status == "failed"

    asyncio.run(go())


def test_scan_once_starts_due_projects(mock, tmp_path):
    mdir, _ = mock
    repo = tmp_path / "repo9"
    make_repo(repo)

    async def go():
        pid = await _mk_project(repo)
        await indexer.scan_once()
        await indexer.wait_idle()
        assert (await _row(pid)).status == "ok"

    asyncio.run(go())


def test_missing_command_fails_cleanly(mock, tmp_path, monkeypatch):
    repo = tmp_path / "repo10"
    make_repo(repo)
    monkeypatch.setattr(settings, "code_map_index_command", ["/nonexistent/index-tool"])

    async def go():
        pid = await _mk_project(repo)
        assert await indexer.refresh(pid) == "failed"
        assert "could not start" in (await _row(pid)).error
        assert cm_status.failure_kind((await _row(pid)).error) == "start"

    asyncio.run(go())


def test_cancel_while_waiting_leaves_ok_row(mock, tmp_path, monkeypatch):
    mdir, set_mode = mock
    repo = tmp_path / "repo11"
    head = make_repo(repo)
    monkeypatch.setattr(settings, "code_map_max_jobs", 1)

    async def go():
        pid = await _mk_project(repo)
        assert await indexer.refresh(pid) == "ok"
        before = await _row(pid)
        sem = indexer._semaphore()
        await sem.acquire()  # no free slot: the next job waits
        try:
            t = asyncio.ensure_future(indexer.refresh(pid, manual=True))
            await asyncio.sleep(0.2)
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t
        finally:
            sem.release()
        after = await _row(pid)
        assert (after.status, after.sha, after.fail_count, after.error) == ("ok", head, 0, None)
        assert before.indexed_at == after.indexed_at and not indexer.inflight(pid)

    asyncio.run(go())


def test_reset_does_not_overwrite_a_finished_row(mock, tmp_path):
    repo = tmp_path / "repo12"
    make_repo(repo)

    async def go():
        pid = await _mk_project(repo, code_map=False)
        async with SessionLocal() as s:
            s.add(ProjectIndex(project_id=pid, status="ok", sha="a" * 40))
            await s.commit()
        assert await indexer.reset_stale(all_running=True) == 0
        assert (await _row(pid)).status == "ok"

    asyncio.run(go())


def test_finish_hook_triggers_and_survives_a_raise(mock, tmp_path, monkeypatch):
    from app.models import Task
    from app.worker import WorkerManager

    repo = tmp_path / "repo13"
    make_repo(repo)
    calls = []

    async def spy(pid):
        calls.append(pid)

    async def boom(pid):
        raise RuntimeError("boom")

    async def go():
        pid = await _mk_project(repo)
        tids = []
        async with SessionLocal() as s:
            for _ in range(2):
                tk = Task(title="t", prompt="x", project_id=pid, status="running")
                s.add(tk)
                await s.flush()
                tk.root_id = tk.id
                tids.append(tk.id)
            await s.commit()
        w = WorkerManager()
        monkeypatch.setattr(indexer, "maybe_refresh", spy)
        await w._finish(tids[0], "completed")
        assert calls == [pid]
        monkeypatch.setattr(indexer, "maybe_refresh", boom)
        await w._finish(tids[1], "completed")  # a raising hook never fails the finish
        async with SessionLocal() as s:
            assert (await s.get(Task, tids[1])).status == "completed"

    asyncio.run(go())


def test_failure_kinds():
    assert cm_status.failure_kind(None) is None
    assert cm_status.failure_kind("The project folder is missing") == "start"
    assert cm_status.failure_kind("Interrupted (the server restarted)") == "error"
    assert cm_status.failure_kind("The index job crashed (ValueError)") == "error"


def test_no_server_no_automatic_job(mock, tmp_path):
    """Without a registered code map server the scheduler and a task finish run nothing."""
    mdir, _ = mock
    repo = tmp_path / "repo-noserver"
    make_repo(repo)
    runs = mdir / "runs"
    runs.unlink(missing_ok=True)

    async def go():
        pid = await _mk_project(repo, server=False)
        await indexer.maybe_refresh(pid)
        await indexer.scan_once()
        await indexer.wait_idle()
        assert not runs.exists() and await _row(pid) is None
        async with SessionLocal() as s:
            p = await s.get(Project, pid)
        assert indexer._due(p, None, False) is False and indexer._due(p, None, True) is True
        # a disabled server does not count
        sid = await add_server(p.name)
        async with SessionLocal() as s:
            (await s.get(McpServer, sid)).enabled = False
            await s.commit()
        await indexer.maybe_refresh(pid)
        await indexer.wait_idle()
        assert not runs.exists()
        async with SessionLocal() as s:
            (await s.get(McpServer, sid)).enabled = True
            await s.commit()
        await indexer.maybe_refresh(pid)
        await indexer.wait_idle()
        assert runs.read_text().count("x") == 1

    asyncio.run(go())


def test_no_commit_folder_refreshes_once_a_day(mock, tmp_path):
    """A folder with no commit to compare: built, `ok` with no sha; the automatic refresh re-runs it at most once
    a day; a manual refresh always runs."""
    mdir, _ = mock
    folder = tmp_path / "plain"
    folder.mkdir()
    (folder / "a.txt").write_text("1")
    runs = mdir / "runs"
    runs.unlink(missing_ok=True)

    async def go():
        pid = await _mk_project(folder)
        await indexer.scan_once()
        await indexer.wait_idle()
        r = await _row(pid)
        assert (r.status, r.sha) == ("ok", None) and runs.read_text().count("x") == 1
        await indexer.maybe_refresh(pid)
        await indexer.scan_once()
        await indexer.wait_idle()
        assert runs.read_text().count("x") == 1  # built today: nothing
        assert await indexer.refresh(pid, manual=True) == "ok"
        assert runs.read_text().count("x") == 2  # manual: always
        async with SessionLocal() as s:
            (await s.get(ProjectIndex, pid)).indexed_at = datetime.now(timezone.utc) - timedelta(hours=25)
            await s.commit()
        await indexer.maybe_refresh(pid)
        await indexer.wait_idle()
        assert runs.read_text().count("x") == 3  # a day later: once
        await indexer.maybe_refresh(pid)
        await indexer.wait_idle()
        assert runs.read_text().count("x") == 3
        # the folder gets a first commit: the sha (None) differs from HEAD, so it is due at once
        make_repo(folder)
        await indexer.maybe_refresh(pid)
        await indexer.wait_idle()
        assert runs.read_text().count("x") == 4 and (await _row(pid)).sha is not None

    asyncio.run(go())
