"""Phase 7d Task 3: the re-clone warning (spec section 5) - real temporary git repositories: stored on create and
when the folder changes, filled at the first task start, a notice and a health warning when the first commit
differs, accepted only by a maintainer, nothing for a folder that is not a git repository."""

import asyncio
import os
import subprocess
import uuid

import pytest

from app import gitsafe, rootcommit
from app.health import checks
from conftest import login_as

ENV = {"PATH": os.environ.get("PATH", ""), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
       "HOME": os.devnull}

pytestmark = pytest.mark.skipif(not gitsafe.git_available(), reason="git is not installed")


def run(coro):
    return asyncio.run(coro)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], env=ENV, capture_output=True, text=True, timeout=60,
                          check=True).stdout.strip()


def make_repo(path, text="1"):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    (path / "a.txt").write_text(text)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "first " + text)
    return git(path, "rev-list", "--max-parents=0", "HEAD")


def _project(client, directory):
    r = client.post("/projects", json={"name": f"rc-{uuid.uuid4().hex[:8]}", "directory": str(directory)})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _stored(pid):
    from app.database import SessionLocal
    from app.models import Project

    async def go():
        async with SessionLocal() as s:
            return (await s.get(Project, pid)).root_commit
    return run(go())


def _set(pid, sha):
    from app.database import SessionLocal
    from app.models import Project

    async def go():
        async with SessionLocal() as s:
            (await s.get(Project, pid)).root_commit = sha
            await s.commit()
    run(go())


def test_first_commit_helper(tmp_path):
    sha = make_repo(tmp_path / "r")
    assert rootcommit.first_commit_sync(tmp_path / "r") == sha
    plain = tmp_path / "plain"
    plain.mkdir()
    assert rootcommit.first_commit_sync(plain) is None  # not a git repository
    empty = tmp_path / "empty"
    empty.mkdir()
    git(empty, "init", "-q", "-b", "main")
    assert rootcommit.first_commit_sync(empty) is None  # no commits


def test_shallow_clone_without_a_root_stores_nothing(tmp_path):
    src = tmp_path / "src"
    make_repo(src)
    (src / "b.txt").write_text("2")
    git(src, "add", "-A")
    git(src, "commit", "-q", "-m", "second")
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{src}", str(shallow)], env=ENV, check=True,
                   capture_output=True)
    # a shallow clone's oldest commit is the grafted root: still a single stable hex value or nothing, never an error
    v = rootcommit.first_commit_sync(shallow)
    assert v is None or len(v) == 40


def test_stored_on_create_and_on_folder_change(client, tmp_path):
    a = make_repo(tmp_path / "a", "a")
    b = make_repo(tmp_path / "b", "b")
    pid = _project(client, tmp_path / "a")
    assert _stored(pid) == a
    assert client.patch(f"/projects/{pid}", json={"directory": str(tmp_path / "b")}).status_code == 200
    assert _stored(pid) == b
    plain = tmp_path / "plain"
    plain.mkdir()
    assert client.patch(f"/projects/{pid}", json={"directory": str(plain)}).status_code == 200
    assert _stored(pid) is None


def test_folder_that_is_not_a_repository_stores_nothing_and_never_warns(client, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    pid = _project(client, plain)
    assert _stored(pid) is None
    assert run(rootcommit.at_task_start(pid, str(plain))) is None
    assert _stored(pid) is None
    r = run(checks.check_repo_roots(__import__("app.database", fromlist=["SessionLocal"]).SessionLocal))
    assert pid not in [c["id"] for c in r.detail["projects"]]


def test_task_start_fills_an_empty_value_then_warns_when_it_differs(client, tmp_path):
    plain = tmp_path / "p"
    plain.mkdir()
    pid = _project(client, plain)
    sha = make_repo(plain)
    assert _stored(pid) is None
    assert run(rootcommit.at_task_start(pid, str(plain))) is None  # first start: stored, no notice
    assert _stored(pid) == sha
    assert run(rootcommit.at_task_start(pid, str(plain))) is None  # same repository: quiet
    # cloned again: a different history in the same folder
    import shutil
    shutil.rmtree(plain / ".git")
    other = make_repo(plain, "other")
    assert other != sha
    note = run(rootcommit.at_task_start(pid, str(plain)))
    assert note == ("This folder now holds a different repository than before (it may have been cloned again). "
                    "Decisions and the code map may not apply.")
    assert _stored(pid) == sha  # only a maintainer's "This is expected" changes it


def test_health_warning_and_accept(client, tmp_path):
    from app.database import SessionLocal

    sha = make_repo(tmp_path / "h")
    pid = _project(client, tmp_path / "h")
    r = run(checks.check_repo_roots(SessionLocal))
    assert pid not in [c["id"] for c in r.detail["projects"]]
    _set(pid, "a" * 40)  # the folder "was" another repository
    r = run(checks.check_repo_roots(SessionLocal))
    assert r.status == checks.ATTENTION and pid in [c["id"] for c in r.detail["projects"]]
    assert "different repository" in r.summary
    assert client.post(f"/projects/{pid}/root-commit/accept").status_code == 200
    assert _stored(pid) == sha
    r = run(checks.check_repo_roots(SessionLocal))
    assert pid not in [c["id"] for c in r.detail["projects"]]


def test_accept_needs_a_maintainer(client, tmp_path):
    make_repo(tmp_path / "m")
    pid = _project(client, tmp_path / "m")
    _set(pid, "b" * 40)
    out = login_as(f"rc-out-{uuid.uuid4().hex[:6]}@test.local")
    assert out.post(f"/projects/{pid}/root-commit/accept").status_code in (403, 404)
    assert _stored(pid) == "b" * 40
    con = login_as(f"rc-con-{uuid.uuid4().hex[:6]}@test.local")
    client.put(f"/projects/{pid}/members", json=[{"subject_type": "user", "subject_id": con.user_id, "role": "contributor"}])
    assert con.post(f"/projects/{pid}/root-commit/accept").status_code == 403
    assert _stored(pid) == "b" * 40


def _events(task_id):
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import TaskEvent

    async def go():
        async with SessionLocal() as s:
            return (await s.execute(select(TaskEvent).where(TaskEvent.task_id == task_id)
                                    .order_by(TaskEvent.seq))).scalars().all()
    return run(go())


def test_a_real_task_start_stores_then_notices(client, tmp_path):
    """Through the worker (the fake claude): the first start fills the empty value; after the folder holds another
    repository the run carries the notice event and the stored value stays."""
    import shutil

    from conftest import wait_task

    d = tmp_path / "w"
    d.mkdir()
    pid = _project(client, d)
    sha = make_repo(d)
    t = client.post("/tasks", json={"prompt": "hello", "project_id": pid}).json()
    assert wait_task(client, t["id"])["status"] == "completed"
    assert _stored(pid) == sha
    assert not [e for e in _events(t["id"]) if e.type == "repo_changed"]
    shutil.rmtree(d / ".git")
    make_repo(d, "other")
    t2 = client.post("/tasks", json={"prompt": "again", "project_id": pid}).json()
    assert wait_task(client, t2["id"])["status"] == "completed"
    ev = [e for e in _events(t2["id"]) if e.type == "repo_changed"]
    assert len(ev) == 1 and ev[0].payload["message"] == rootcommit.NOTICE
    assert _stored(pid) == sha
