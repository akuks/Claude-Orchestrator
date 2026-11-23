import asyncio
import os
import subprocess

import pytest

from app.gates import codestate


def code_state(d):
    return asyncio.run(codestate.code_state(d))


def _git(d, *a):
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=t", *a], cwd=d, check=True,
                   capture_output=True)


@pytest.fixture
def repo(tmp_path):
    d = str(tmp_path)
    _git(d, "init", "-q")
    (tmp_path / ".gitignore").write_text("ignored.txt\n")
    (tmp_path / "a.txt").write_text("one\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "init")
    return tmp_path


def test_stable_and_sensitive(repo):
    d = str(repo)
    s0 = code_state(d)
    assert s0 and len(s0) == 64
    assert code_state(d) == s0
    (repo / "a.txt").write_text("two\n")
    s1 = code_state(d)
    assert s1 and s1 != s0
    (repo / "new.txt").write_text("x")
    s2 = code_state(d)
    assert s2 not in (s0, s1)
    (repo / "ignored.txt").write_text("x")
    assert code_state(d) == s2


def test_not_a_repo(tmp_path):
    assert code_state(str(tmp_path)) is None


def test_unborn_repo(tmp_path):
    _git(str(tmp_path), "init", "-q")
    assert code_state(str(tmp_path)) is None


@pytest.mark.parametrize("driver", ["evil", "my.evil.drv"])
def test_filter_drivers_never_run(tmp_path, driver):
    from app import gitsafe

    d = tmp_path / "r"
    d.mkdir()
    marker = tmp_path / "marker"
    _git(str(d), "init", "-q")
    cmd = f"sh -c 'touch {marker}; cat'"
    for k in ("clean", "process", "smudge"):
        _git(str(d), "config", f"filter.{driver}.{k}", cmd)
    (d / ".gitattributes").write_text(f"* filter={driver}\n")
    (d / "f.txt").write_text("one\n")
    _git(str(d), "add", "-A")
    _git(str(d), "-c", "filter.%s.clean=" % driver, "-c", "filter.%s.process=" % driver, "commit", "-qm", "i")
    marker.unlink(missing_ok=True)
    (d / "f.txt").write_text("two\n")
    assert code_state(str(d))
    r = gitsafe.run_git(str(d), "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames",
                        timeout=30)
    assert r.ok
    assert not marker.exists()


def test_too_many_filter_drivers(tmp_path):
    from app import gitsafe

    d = str(tmp_path)
    _git(d, "init", "-q")
    for i in range(51):
        _git(d, "config", f"filter.d{i}.clean", "cat")
    r = gitsafe.run_git(d, "--version", timeout=30)
    assert not r.ok and r.failed and r.err == "too many filter drivers"


def test_submodule_filter_never_runs(tmp_path):
    from app import gitsafe

    marker = tmp_path / "marker"
    sub = tmp_path / "subsrc"
    sub.mkdir()
    sd = str(sub)
    _git(sd, "init", "-q")
    (sub / "f.txt").write_text("aaaa\n")
    (sub / ".gitattributes").write_text("f.txt filter=sevil\n")
    _git(sd, "add", "-A")
    _git(sd, "commit", "-qm", "s")
    sup = tmp_path / "sup"
    sup.mkdir()
    d = str(sup)
    _git(d, "init", "-q")
    _git(d, "-c", "protocol.file.allow=always", "submodule", "add", "-q", sd, "sub")
    _git(d, "commit", "-qm", "sm")
    _git(d, "config", "submodule.sub.ignore", "none")
    _git(str(sup / "sub"), "config", "filter.sevil.clean", f"sh -c 'touch {marker}; cat'")
    f = sup / "sub" / "f.txt"
    f.write_text("bbbb\n")
    os.utime(f, (1, 1))
    assert code_state(d)
    r = gitsafe.run_git(d, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames", timeout=30)
    assert r.ok
    assert not marker.exists()


def test_non_utf8_driver_name_refused(tmp_path):
    from app import gitsafe

    d = str(tmp_path)
    _git(d, "init", "-q")
    with open(tmp_path / ".git" / "config", "ab") as fh:
        fh.write(b'[filter "x\xffy"]\n\tclean = cat\n')
    r = gitsafe.run_git(d, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames", timeout=30)
    assert not r.ok and r.failed and r.err == "unusual filter driver name"


def test_submodule_diff_driver_never_runs(tmp_path):
    from app import gitsafe

    marker = tmp_path / "marker"
    sub = tmp_path / "subsrc"
    sub.mkdir()
    sd = str(sub)
    _git(sd, "init", "-q")
    (sub / "f.txt").write_text("aaaa\n")
    (sub / ".gitattributes").write_text("f.txt diff=d\n")
    _git(sd, "add", "-A")
    _git(sd, "commit", "-qm", "s")
    sup = tmp_path / "sup"
    sup.mkdir()
    d = str(sup)
    _git(d, "init", "-q")
    _git(d, "-c", "protocol.file.allow=always", "submodule", "add", "-q", sd, "sub")
    _git(d, "commit", "-qm", "sm")
    _git(d, "config", "diff.submodule", "diff")
    inner = str(sup / "sub")
    _git(inner, "config", "diff.d.command", f"sh -c 'touch {marker}; cat'")
    (sup / "sub" / "f.txt").write_text("bbbb\n")
    _git(inner, "commit", "-qam", "moved")
    assert code_state(d)
    r = gitsafe.run_git(d, "diff", "--no-ext-diff", "--no-textconv", "--binary", "HEAD", timeout=30)
    assert r.ok
    assert not marker.exists()
