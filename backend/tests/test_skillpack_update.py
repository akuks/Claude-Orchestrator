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


def shallow_pack(rig):
    d = rig.tmp / "packs" / "shallow"
    git(rig.tmp, "clone", "-q", "--depth", "1", f"file://{rig.bare}", str(d))
    return SimpleNamespace(id=uuid.uuid4().hex, source_url=rig.pack.source_url, local_path=str(d)), d


def test_shallow_pack_fast_forwards(rig):
    pack, d = shallow_pack(rig)
    commit(rig.work, "new.txt")
    run(update.apply(pack, FILE))
    assert git(d, "rev-parse", "HEAD") == git(rig.work, "rev-parse", "HEAD")


def evil_remote(rig):
    (rig.work / ".gitattributes").write_text("*.txt filter=evil\n")
    commit(rig.work, "a.txt", "hello")


def plant_filter(d, marker):
    git(d, "config", "filter.evil.smudge", f"sh -c 'touch {marker}; cat'")
    git(d, "config", "filter.evil.clean", f"sh -c 'touch {marker}; cat'")


def test_filter_driver_in_pack_config_never_runs(rig):
    marker = rig.tmp / "pwned"
    evil_remote(rig)
    plant_filter(rig.dir, marker)
    with pytest.raises(ValueError, match="changed by hand"):
        run(update.apply(rig.pack, FILE))
    with pytest.raises(ValueError, match="changed by hand"):
        run(update.check(rig.pack, FILE))
    assert not marker.exists()


def test_filter_drivers_blanked_on_every_call(rig):
    marker = rig.tmp / "pwned"
    evil_remote(rig)
    git(rig.dir, "pull", "-q", "--ff-only")  # pack now tracks a.txt, with the attribute
    plant_filter(rig.dir, marker)
    os.utime(rig.dir / "a.txt", (1, 1))
    assert run(update._git(["status", "--porcelain"], rig.dir, FILE)).strip() == ""
    (rig.work / "b.txt").write_text("b")
    commit(rig.work, "c.txt", "c")
    run(update._git(["fetch", "--no-tags", "--", rig.pack.source_url, "HEAD"], rig.dir, FILE))
    run(update._git(["merge", "--ff-only", git(rig.work, "rev-parse", "HEAD")], rig.dir, FILE))
    assert (rig.dir / "c.txt").read_text() == "c" and not marker.exists()


def test_config_allowlist_accepts_a_plain_clone(rig):
    run(update.check_config(rig.dir, FILE))
    pack, d = shallow_pack(rig)
    run(update.check_config(d, FILE))
    git(rig.dir, "config", "url.https://evil/.insteadOf", "https://x/")
    with pytest.raises(ValueError, match="changed by hand"):
        run(update.check_config(rig.dir, FILE))


def test_argv_disables_redirects_and_submodules():
    a = update._argv(["fetch"], update.DEFAULT_PROTOCOLS)
    for want in ("http.followRedirects=false", "submodule.recurse=false", "core.attributesFile=/dev/null",
                 "protocol.file.allow=never", "protocol.ext.allow=never", "core.hooksPath=/dev/null"):
        assert want in a


def test_pin_checks_out_exact_commit_then_updates_from_detached(rig):
    pinned = git(rig.work, "rev-parse", "HEAD")
    commit(rig.work, "new.txt")
    d = rig.tmp / "packs" / "pinned"
    git(rig.tmp, "clone", "-q", "--depth", "1", f"file://{rig.bare}", str(d))
    pack = SimpleNamespace(id=uuid.uuid4().hex, source_url=rig.pack.source_url, local_path=str(d))
    run(update.pin(d, pack.source_url, pinned, FILE))
    assert git(d, "rev-parse", "HEAD") == pinned and not (d / "new.txt").exists()
    r = run(update.check(pack, FILE))
    assert r["update_available"] is True and r["current"] == pinned
    r = run(update.apply(pack, FILE))
    assert r["from"] == pinned and git(d, "rev-parse", "HEAD") == git(rig.work, "rev-parse", "HEAD")
    assert (d / "new.txt").exists()


def test_pin_unknown_sha_fails_cleanly(rig):
    with pytest.raises(ValueError):
        run(update.pin(rig.dir, rig.pack.source_url, "0" * 40, FILE))
    with pytest.raises(ValueError):
        run(update.pin(rig.dir, rig.pack.source_url, "nothex", FILE))


def test_error_text_is_plain(rig):
    rig.pack.source_url = f"file://{rig.tmp}/nope.git"
    with pytest.raises(ValueError) as e:
        run(update.check(rig.pack, FILE))
    assert "fatal" not in str(e.value) and str(rig.tmp) not in str(e.value)


def test_hooks_path_in_pack_config_is_refused(rig):
    git(rig.dir, "config", "core.hooksPath", str(rig.dir / ".git" / "hooks"))
    with pytest.raises(ValueError, match="changed by hand"):
        run(update.apply(rig.pack, FILE))
