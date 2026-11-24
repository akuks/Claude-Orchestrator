import asyncio
import os
import subprocess
import time
import uuid
from types import SimpleNamespace

import pytest

from app.database import SessionLocal
from app.models import SkillPack
from app.routers.skills import PRESETS
from app.skillpacks import update
from conftest import login_as

FILE = ("https", "ssh", "file")
SKILL = "---\nname: demo\ndescription: Use when asked to demo.\n---\n1. Run the thing.\n2. Say done.\n"


def git(cwd, *args):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


def commit(work, name, text="x"):
    (work / name).write_text(text)
    git(work, "add", "-A")
    git(work, "commit", "-qm", name)
    git(work, "push", "-q", "origin", "HEAD:main")


@pytest.fixture
def rig(tmp_path):
    bare = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", str(bare), str(work))
    (work / "SKILL.md").write_text(SKILL)
    git(work, "add", "-A"); git(work, "commit", "-qm", "init"); git(work, "push", "-q", "origin", "HEAD:main")
    packdir = tmp_path / "packs" / "p"
    packdir.parent.mkdir()
    git(tmp_path, "clone", "-q", f"file://{bare}", str(packdir))
    pack = SimpleNamespace(id=uuid.uuid4().hex, source_url=f"file://{bare}", local_path=str(packdir))
    return SimpleNamespace(bare=bare, work=work, pack=pack, dir=packdir, tmp=tmp_path)


def run(coro):
    return asyncio.run(coro)


def test_check_reports_update(rig):
    r = run(update.check(rig.pack, FILE))
    assert r["update_available"] is False and r["current"] == r["latest"]
    commit(rig.work, "new.txt")
    r = run(update.check(rig.pack, FILE))
    assert r["update_available"] is True and r["latest"] == git(rig.work, "rev-parse", "HEAD")


def test_apply_fast_forwards_and_lints(rig):
    before = git(rig.dir, "rev-parse", "HEAD")
    (rig.work / "bad").mkdir()
    commit(rig.work, "bad/SKILL.md", "no frontmatter\n")
    r = run(update.apply(rig.pack, FILE))
    assert r["from"] == before and r["to"] == git(rig.work, "rev-parse", "HEAD")
    assert git(rig.dir, "rev-parse", "HEAD") == r["to"]
    assert [x["file"] for x in r["lint"]] == ["bad/SKILL.md"]


def test_dirty_tree_refused(rig):
    commit(rig.work, "new.txt")
    before = git(rig.dir, "rev-parse", "HEAD")
    (rig.dir / "SKILL.md").write_text("edited")
    with pytest.raises(ValueError, match="local changes"):
        run(update.apply(rig.pack, FILE))
    assert git(rig.dir, "rev-parse", "HEAD") == before and (rig.dir / "SKILL.md").read_text() == "edited"


def test_no_source_url_refused(rig):
    rig.pack.source_url = None
    for fn in (update.check, update.apply):
        with pytest.raises(ValueError, match="no source URL"):
            run(fn(rig.pack, FILE))


def test_diverged_refused_and_unchanged(rig):
    (rig.dir / "local.txt").write_text("l")
    git(rig.dir, "add", "-A"); git(rig.dir, "commit", "-qm", "local")
    commit(rig.work, "remote.txt")
    before = git(rig.dir, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="cannot be updated automatically"):
        run(update.apply(rig.pack, FILE))
    assert git(rig.dir, "rev-parse", "HEAD") == before
    assert git(rig.dir, "status", "--porcelain") == ""


def test_missing_remote_fails_cleanly_and_fast(rig):
    rig.pack.source_url = f"file://{rig.tmp}/nope.git"
    before = git(rig.dir, "rev-parse", "HEAD")
    t = time.time()
    with pytest.raises(ValueError):
        run(update.apply(rig.pack, FILE))
    with pytest.raises(ValueError):
        run(update.check(rig.pack, FILE))
    assert time.time() - t < update.TIMEOUT
    assert git(rig.dir, "rev-parse", "HEAD") == before


def test_timeout_kills_and_raises(rig):
    with pytest.raises(ValueError, match="in time"):
        run(update._git(["-c", "alias.slow=!sleep 5", "slow"], rig.dir, FILE, timeout=0.5))


def test_hooks_do_not_run(rig):
    marker = rig.tmp / "marker"
    for d in (rig.dir / ".git" / "hooks", rig.bare / "hooks"):
        h = d / "post-merge"
        h.write_text(f"#!/bin/sh\ntouch {marker}\n")
        h.chmod(0o755)
    git(rig.dir, "config", "core.hooksPath", str(rig.dir / ".git" / "hooks"))
    commit(rig.work, "new.txt")
    run(update.apply(rig.pack, FILE))
    assert (rig.dir / "new.txt").exists() and not marker.exists()


def test_url_forms():
    ok = ["https://github.com/a/b", "ssh://git@github.com/a/b", "git@github.com:a/b.git"]
    for u in ok:
        assert update.check_url(u) == u
    for u in ["file:///x", "/tmp/x", "ext::sh -c id", "-u/x", "http://x/y", "git://x/y", "../x", ""]:
        with pytest.raises(ValueError):
            update.check_url(u)


def test_file_refused_in_production_mode(rig):
    with pytest.raises(ValueError, match="https and ssh"):
        run(update.check(rig.pack))


def test_presets_include_pinned_superpowers():
    sp = next(p for p in PRESETS if p["name"] == "superpowers")
    assert sp["source_url"] == "https://github.com/obra/superpowers"
    assert len(sp["pinned_sha"]) == 40 and int(sp["pinned_sha"], 16) >= 0


def test_routes_admin_only_and_file_url_refused(client, rig):
    async def mk():
        async with SessionLocal() as s:
            p = SkillPack(name=f"up-{uuid.uuid4().hex[:8]}", source_url=rig.pack.source_url,
                          local_path=str(rig.dir), scope="global")
            s.add(p); await s.commit(); return p.id
    pid = run(mk())
    m = login_as(f"up-{uuid.uuid4().hex[:8]}@test.local")
    for path in ("check-update", "update"):
        assert m.post(f"/skills/{pid}/{path}").status_code == 403
        r = client.post(f"/skills/{pid}/{path}")  # file:// is not allowed in production
        assert r.status_code == 400 and "https and ssh" in r.json()["detail"]
    assert client.post("/skills/nope/check-update").status_code == 404
    row = next(x for x in client.get("/skills").json() if x["id"] == pid)
    assert row["latest_sha"] is None and row["update_available"] is False and len(row["current_sha"]) == 40
