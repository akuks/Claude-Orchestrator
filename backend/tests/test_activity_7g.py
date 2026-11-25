"""Phase 7g: the capacity readout and the what-was-built history."""
import asyncio
import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app import gitsafe
from app.health import capacity
from app.resume import activity, gitfacts
from conftest import login_as

pytestmark = pytest.mark.skipif(not gitsafe.git_available(), reason="git is not installed")

ENV = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
       "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def g(cwd, *args, **env):
    e = {**ENV, "GIT_AUTHOR_NAME": "Ann", "GIT_AUTHOR_EMAIL": "ann@x.io", "GIT_COMMITTER_NAME": "Ann",
         "GIT_COMMITTER_EMAIL": "ann@x.io", **env}
    return subprocess.run(["git", *args], cwd=cwd, env=e, check=True, capture_output=True, text=True).stdout.strip()


def repo(tmp_path, name="r"):
    d = tmp_path / name
    d.mkdir()
    g(d, "init", "-q", "-b", "main")
    return d


def commit(d, name, content="x\ny\n", msg=None, when=None, **env):
    (d / name).write_text(content)
    g(d, "add", name)
    if when:
        env.update(GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    g(d, "commit", "-q", "-m", msg or name, **env)
    return g(d, "rev-parse", "HEAD")


def iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")


# ---- gitsafe -----------------------------------------------------------------------------------------------
FMT = gitsafe.ACTIVITY_FORMAT
GOOD = ["log", "--no-merges", "--numstat", FMT, "--since=2026-11-01", "-n", "2001"]


def test_activity_form_allowed():
    gitsafe.check_args(GOOD)


@pytest.mark.parametrize("bad", [
    ["log", "--numstat", FMT, "--since=2026-11-01", "-n", "5"],  # no --no-merges
    ["log", "--no-merges", "--numstat", "--format=%H%x1f%GS", "--since=2026-11-01", "-n", "5"],
    ["log", "--no-merges", "--numstat", "--format=%H", "--since=2026-11-01", "-n", "5"],
    ["log", "--no-merges", "--numstat", FMT, "--since=yesterday", "-n", "5"],
    ["log", "--no-merges", "--numstat", FMT, "--since=2026-11-01; x", "-n", "5"],
    ["log", "--no-merges", "--numstat", FMT, "--since=2026-11-01", "-n", "-5"],
    ["log", "--no-merges", "--numstat", FMT, "--since=2026-11-01", "-n", "123456"],
    ["log", "--no-merges", "--numstat", FMT, "--since=2026-11-01", "-n", "5", "-p"],
    ["log", "--no-merges", "--numstat", FMT, "--since=2026-11-01", "-n", "5", "--output=/tmp/x"],
    ["log", "--numstat", "--no-merges", FMT, "--since=2026-11-01", "-n", "5"],  # order matters
    ["log", "--no-merges", "--numstat", FMT, "--since=2026-11-01", "--max-count=5"],
    ["log", "--numstat", "HEAD"], ["log", "--numstat"],
    ["rev-list", "--no-merges", "--numstat", FMT, "--since=2026-11-01", "-n", "5"],
])
def test_activity_near_misses_refused(bad):
    with pytest.raises(ValueError):
        gitsafe.check_args(bad)


def test_activity_form_runs_nothing_from_a_hostile_repo(tmp_path):
    d = repo(tmp_path, "hostile")
    marker = tmp_path / "ran"
    evil = tmp_path / "evil.sh"
    evil.write_text(f"#!/bin/sh\necho ran >> {marker}\ncat\n")
    evil.chmod(0o755)
    commit(d, "a.txt", "1\n")
    commit(d, "a.txt", "2\n3\n")
    for key in ("diff.external", "diff.evil.command", "diff.evil.textconv", "filter.evil.clean",
                "filter.evil.smudge", "filter.evil.process", "core.pager", "pager.log", "core.fsmonitor",
                "core.hooksPath", "log.showSignature", "gpg.program"):
        g(d, "config", key, "true" if key == "log.showSignature" else str(tmp_path if key == "core.hooksPath" else evil))
    (d / ".gitattributes").write_text("* diff=evil filter=evil\n")
    (tmp_path / "post-index-change").write_text(f"#!/bin/sh\necho ran >> {marker}\n")
    (tmp_path / "post-index-change").chmod(0o755)
    h = activity.history(str(d), 30, gitfacts.Budget())
    assert h["is_repo"] and len(h["commits"]) == 2
    assert not marker.exists()


# ---- history -----------------------------------------------------------------------------------------------
def test_history_counts_window_and_sanitises(tmp_path):
    d = repo(tmp_path)
    commit(d, "old.txt", when=iso(60), msg="ancient")
    commit(d, "a.txt", "1\n2\n3\n", when=iso(3), msg="<b>bold</b>\x07 title\x1b[31m")
    (d / "a.txt").write_text("1\n")
    (d / "bin.dat").write_bytes(b"\x00\x01\x02")
    g(d, "add", "-A")
    g(d, "commit", "-q", "-m", "second", GIT_AUTHOR_DATE=iso(1), GIT_COMMITTER_DATE=iso(1),
      GIT_AUTHOR_NAME="N" * 100, GIT_AUTHOR_EMAIL="z" * 90 + "@x.io")
    h = activity.history(str(d), 30, gitfacts.Budget())
    cs = h["commits"]
    assert [c["title"] for c in cs] == ["second", "<b>bold</b> title[31m"]
    assert "\x07" not in cs[1]["title"] and "\x1b" not in cs[1]["title"]
    assert cs[0]["added"] == 0 and cs[0]["removed"] == 2 and cs[0]["files"] == 2  # binary counts a file, 0 lines
    assert len(cs[0]["author"]) == 60
    assert cs[1]["author"] == "ann" and cs[1]["added"] == 3
    assert not h["truncated"]


def test_history_unborn_not_a_repo_and_missing_dir(tmp_path):
    d = repo(tmp_path)
    assert activity.history(str(d), 30, gitfacts.Budget()) == {"is_repo": True, "commits": [], "truncated": False}
    plain = tmp_path / "plain"
    plain.mkdir()
    assert activity.history(str(plain), 30, gitfacts.Budget())["is_repo"] is False
    assert activity.history(str(tmp_path / "nope"), 30, gitfacts.Budget())["commits"] == []


def test_history_spent_budget_is_unknown(tmp_path):
    d = repo(tmp_path)
    commit(d, "a.txt")
    h = activity.history(str(d), 30, gitfacts.Budget(0))
    assert h["commits"] == [] and h["truncated"] is True


def test_history_truncates_at_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(activity, "MAX_COMMITS", 2)
    d = repo(tmp_path)
    for i in range(4):
        commit(d, f"f{i}.txt")
    h = activity.history(str(d), 30, gitfacts.Budget())
    assert len(h["commits"]) == 2 and h["truncated"] is True


def test_parse_ignores_a_forged_record():
    sha = "a" * 40
    text = f"{sha}\x1f2026-11-20T10:00:00+00:00\x1fN\x1fn@x\x1fsub\n\n3\t1\tf.txt\n"
    assert len(activity.parse_log(text)) == 1
    bad = f"{sha}\x1fnot-a-date\x1fN\x1fn@x\x1fsub\n3\t1\tf\n{sha}\x1fonly\x1ffour\x1ffields\n"
    assert activity.parse_log(bad) == []


def test_summarize_fills_days_and_people():
    now = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)
    cs = [{"date": "2026-11-25T01:00:00", "author": "ann", "added": 5, "removed": 1},
          {"date": "2026-11-25T02:00:00", "author": "bob", "added": 1, "removed": 0},
          {"date": "2026-11-23T02:00:00", "author": "ann", "added": 2, "removed": 2}]
    days, people = activity.summarize(cs, 7, now)
    assert len(days) == 8 and days[-1] == {"date": "2026-11-25", "commits": 2, "added": 6, "removed": 1}
    assert people[0] == {"name": "ann", "commits": 2, "added": 7, "removed": 3}


# ---- route -------------------------------------------------------------------------------------------------
def test_activity_route_and_task_mapping(client, tmp_path):
    d = repo(tmp_path, "proj")
    s1 = commit(d, "a.txt", "1\n2\n", when=iso(2), msg="made by a task")
    commit(d, "b.txt", "1\n" * 60, when=iso(1), msg="big one")
    r = client.post("/projects", json={"name": f"ac-{uuid.uuid4().hex[:8]}", "directory": str(d)})
    pid = r.json()["id"]

    async def mint():
        from app.database import SessionLocal
        from app.models import Task, TaskEvent
        tid = uuid.uuid4().hex
        async with SessionLocal() as s:
            s.add(Task(id=tid, title="Build A", prompt="p", root_id=tid, status="completed", project_id=pid))
            await s.flush()
            s.add(TaskEvent(task_id=tid, seq=1, type="tool_result",
                            payload={"content": f"[main {s1[:7]}] made by a task\n 1 file changed"}))
            await s.commit()
        return tid

    tid = asyncio.run(mint())
    body = client.get(f"/projects/{pid}/activity?days=7").json()
    assert set(body) == {"days", "is_repo", "commits", "by_day", "contributors", "truncated"}
    assert body["days"] == 7 and body["is_repo"] is True and len(body["by_day"]) == 8
    assert [c["title"] for c in body["commits"]] == ["big one", "made by a task"]
    assert set(body["commits"][0]) == {"sha", "short", "date", "author", "title", "added", "removed", "files", "task"}
    assert body["commits"][0]["task"] is None
    assert body["commits"][1]["task"] == {"id": tid, "title": "Build A"}
    assert body["contributors"] == [{"name": "ann", "commits": 2, "added": 62, "removed": 0}]
    big = client.get(f"/projects/{pid}/activity?days=7&min_lines=50").json()
    assert [c["title"] for c in big["commits"]] == ["big one"]
    assert client.get(f"/projects/{pid}/activity?days=9999").json()["days"] == 365
    assert client.get(f"/projects/{pid}/activity?days=0").json()["days"] == 1
    outsider = login_as(f"ac-{uuid.uuid4().hex[:8]}@test.local")
    assert outsider.get(f"/projects/{pid}/activity").status_code == 404


def test_activity_route_not_a_repo(client, tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    pid = client.post("/projects", json={"name": f"ac-{uuid.uuid4().hex[:8]}", "directory": str(d)}).json()["id"]
    body = client.get(f"/projects/{pid}/activity").json()
    assert body["is_repo"] is False and body["commits"] == []


# ---- capacity ----------------------------------------------------------------------------------------------
GB = 1024**3
MEMINFO = "MemTotal:       {t} kB\nMemFree:  1 kB\nMemAvailable:   {a} kB\n"


def fake_linux(monkeypatch, total_gb, avail_gb, cgroup=None):
    monkeypatch.setattr(capacity.sys, "platform", "linux")
    monkeypatch.setattr(capacity, "read_meminfo",
                        lambda: MEMINFO.format(t=int(total_gb * GB / 1024), a=int(avail_gb * GB / 1024)))
    monkeypatch.setattr(capacity, "read_cgroup", lambda name: cgroup.get(name) if cgroup else None)
    monkeypatch.setattr(capacity, "load", lambda: {"one": 0.1, "five": 0.1, "cores": 8})


def test_capacity_fits_by_slots_and_memory(monkeypatch):
    fake_linux(monkeypatch, 16, 6)
    out = capacity.readout(5, 1, 2)
    assert out["fits"] == 4 and out["tips"] == [] and out["memory"]["free"] == 6 * GB
    out = capacity.readout(5, 0, 0)  # 6 GB / 1.5 = 4 < 5 free slots
    assert out["fits"] == 4 and out["tips"] == [capacity.TIP_MEMORY]


def test_capacity_busy_and_load_tips(monkeypatch):
    fake_linux(monkeypatch, 16, 16)
    monkeypatch.setattr(capacity, "load", lambda: {"one": 9, "five": 9, "cores": 8})
    out = capacity.readout(2, 2, 3)
    assert out["fits"] == 0 and out["tips"] == [capacity.TIP_BUSY, capacity.TIP_LOAD]


def test_capacity_cgroup_limit_is_used_when_lower(monkeypatch):
    fake_linux(monkeypatch, 64, 60, {"memory.max": "4294967296\n", "memory.current": "1073741824\n"})
    out = capacity.readout(5, 0, 0)
    assert out["memory"] == {"total": 4 * GB, "free": 3 * GB} and out["fits"] == 2


def test_capacity_unreadable_is_null_not_an_error(monkeypatch):
    monkeypatch.setattr(capacity.sys, "platform", "linux")
    monkeypatch.setattr(capacity, "read_meminfo", lambda: None)
    monkeypatch.setattr(capacity, "read_cgroup", lambda n: None)
    monkeypatch.setattr(capacity.os, "getloadavg", lambda: (_ for _ in ()).throw(OSError()))
    out = capacity.readout(5, 1, 0)
    assert out["memory"] is None and out["load"] is None and out["fits"] is None and out["tips"] == []
    monkeypatch.setattr(capacity, "read_meminfo", lambda: "garbage")
    assert capacity.readout(5, 1, 0)["memory"] is None


def test_capacity_macos_readers(monkeypatch):
    monkeypatch.setattr(capacity.sys, "platform", "darwin")
    monkeypatch.setattr(capacity, "read_cgroup", lambda n: None)
    vm = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free:     1000.\n"
          "Pages inactive:  500.\nPages speculative: 100.\n")
    outs = {"sysctl": str(16 * GB), "vm_stat": vm}
    monkeypatch.setattr(capacity, "run_command", lambda *a: outs.get(a[0]))
    assert capacity.memory() == {"total": 16 * GB, "free": 1600 * 16384}
    outs.clear()
    assert capacity.memory() is None


def test_capacity_route(client, monkeypatch, member_client, anon_client):
    fake_linux(monkeypatch, 16, 16)
    r = client.get("/admin/health/capacity")
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d) == {"slots", "running", "queued", "memory", "load", "fits", "tips"}
    assert d["slots"] >= 1 and isinstance(d["running"], int) and isinstance(d["queued"], int)
    assert member_client.get("/admin/health/capacity").status_code == 403
    assert anon_client.get("/admin/health/capacity").status_code == 401
