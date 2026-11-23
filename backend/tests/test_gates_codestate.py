import asyncio
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
