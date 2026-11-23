"""Phase 2 Task 4: app.guard.policy_store (what the worker compiles into a
task's policy) and app.guard.trust (the own/external suggestion)."""

import asyncio
import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.database import SessionLocal
from app.guard import custom, policy_store, trust
from app.models import ActionGrant, ActionRule, GuardOrgSettings


def _uid():
    return uuid.uuid4().hex[:8]


def _run(coro):
    return asyncio.run(coro)


async def _with_session(fn):
    async with SessionLocal() as s:
        return await fn(s)


# ---- org_settings / rules_for --------------------------------------------------------------


def test_org_settings_defaults_and_stored(client):
    async def go(s):
        row = await s.get(GuardOrgSettings, 1)
        saved = (list(row.disabled_rules), list(row.dev_db_patterns)) if row else None
        if row is not None:
            await s.delete(row)
            await s.commit()
        try:
            empty = await policy_store.org_settings(s)
            s.add(GuardOrgSettings(id=1, disabled_rules=["db-write"], dev_db_patterns=["host:*.dev"]))
            await s.commit()
            stored = await policy_store.org_settings(s)
            # ill-typed stored values come back as lists of strings
            row = await s.get(GuardOrgSettings, 1)
            row.disabled_rules, row.dev_db_patterns = None, ["ok", 3]
            await s.commit()
            odd = await policy_store.org_settings(s)
        finally:
            row = await s.get(GuardOrgSettings, 1)
            if saved is None:
                await s.delete(row)
            else:
                row.disabled_rules, row.dev_db_patterns = saved
            await s.commit()
        return empty, stored, odd

    empty, stored, odd = _run(_with_session(go))
    from app.resume import ground_rules

    from app import tiers

    g = ground_rules.DEFAULTS  # nothing stored: the ground rules are their defaults
    t = tiers.DEFAULT_TIERS
    assert empty == {"disabled_rules": [], "dev_db_patterns": [], "ground_rules": g, "model_tiers": t}
    assert stored == {"disabled_rules": ["db-write"], "dev_db_patterns": ["host:*.dev"], "ground_rules": g, "model_tiers": t}
    assert odd == {"disabled_rules": [], "dev_db_patterns": ["ok"], "ground_rules": g, "model_tiers": t}


def test_rules_for_shape_and_filter(client):
    pid, other = uuid.uuid4().hex, uuid.uuid4().hex
    tag = _uid()

    async def go(s):
        s.add_all([
            ActionRule(scope="org", slug=f"o-{tag}", kind="never", match="text", pattern="curl x", reason="r1"),
            ActionRule(scope="org", slug=f"off-{tag}", kind="confirm", match="text", pattern="y", reason="",
                       enabled=False),
            ActionRule(scope="project", project_id=pid, slug=f"p-{tag}", kind="confirm", match="regex",
                       pattern=r"^make\b", reason="r2"),
            ActionRule(scope="project", project_id=other, slug=f"q-{tag}", kind="confirm", match="text",
                       pattern="z", reason=""),
        ])
        await s.commit()
        return await policy_store.rules_for(s, pid), await policy_store.rules_for(s, None)

    mine, org_only = _run(_with_session(go))
    ids = [r["id"] for r in mine]
    assert f"org.o-{tag}" in ids and f"project.p-{tag}" in ids
    assert f"org.off-{tag}" not in ids and f"project.q-{tag}" not in ids
    assert ids.index(f"org.o-{tag}") < ids.index(f"project.p-{tag}")  # org rules first
    p = next(r for r in mine if r["id"] == f"project.p-{tag}")
    assert p == {"id": f"project.p-{tag}", "kind": "confirm", "match": "regex", "pattern": r"^make\b",
                 "reason": "r2"}
    assert custom.policy_rules(mine) == mine  # the policy's custom_rules shape
    assert all(r["id"].startswith("org.") for r in org_only)


# ---- read_repo_rules ---------------------------------------------------------------------------


def test_read_repo_rules_absent(tmp_path):
    got = policy_store.read_repo_rules(tmp_path)
    assert got == {"path": str(tmp_path / ".claude-orchestrator" / "rules.md"), "present": False,
                   "confirm": [], "never": [], "errors": []}


def test_read_repo_rules_reports(tmp_path):
    d = tmp_path / ".claude-orchestrator"
    d.mkdir()
    (d / "rules.md").write_text(
        "- `before heading` : x\n"
        "## Confirm\n"
        "- `terraform apply` : infra changes\n"
        "- `re:^kubectl\\s+delete` : k8s\n"
        "- `re:(unclosed` : bad\n"
        "- disable: git-reset-hard\n"
        "## Never\n"
        "- `terraform apply` : twice\n"
        "- database dev: host:*\n"
        "## Settings\n"
        "- notes: on\n"
    )
    got = policy_store.read_repo_rules(tmp_path)
    assert got["present"] is True
    assert [(r["match"], r["pattern"]) for r in got["confirm"]] == [("text", "terraform apply"),
                                                                    ("regex", r"^kubectl\s+delete")]
    assert [r["pattern"] for r in got["never"]] == ["terraform apply"]
    ids = [r["id"] for r in got["confirm"] + got["never"]]
    assert all(i.startswith("repo.") for i in ids) and len(set(ids)) == len(ids)
    assert got["confirm"][0]["kind"] == "confirm" and got["never"][0]["kind"] == "never"
    assert got["confirm"][0]["reason"] == "infra changes" and got["confirm"][0]["line"] == 3
    assert sorted(e["line"] for e in got["errors"]) == [1, 5, 6, 9, 11]
    custom.policy_rules(got["confirm"] + got["never"])  # policy-shaped (extra "line" is fine)


def test_read_repo_rules_too_big_and_odd_files(tmp_path):
    proj = tmp_path / "proj"
    d = proj / ".claude-orchestrator"
    d.mkdir(parents=True)
    (d / "rules.md").write_bytes(b"## Confirm\n" + b"x" * (custom.REPO_FILE_MAX + 10))
    got = policy_store.read_repo_rules(proj)
    assert got["present"] is True and got["confirm"] == [] and got["errors"][0]["line"] == 0
    (d / "rules.md").unlink()
    (d / "rules.md").mkdir()  # not a file
    got = policy_store.read_repo_rules(proj)
    assert got["present"] is True and got["confirm"] == [] and got["errors"][0]["line"] == 0
    (d / "rules.md").rmdir()
    outside = tmp_path / "outside.md"
    outside.write_text("## Confirm\n- `leak` : x\n")
    (d / "rules.md").symlink_to(outside)  # a link out of the project is not followed
    got = policy_store.read_repo_rules(proj)
    assert got["present"] is True and got["confirm"] == [] and got["errors"][0]["line"] == 0
    assert "leak" not in str(got)
    (d / "rules.md").unlink()
    os.mkfifo(d / "rules.md")  # a FIFO must not block the reader
    got = policy_store.read_repo_rules(proj)
    assert got["confirm"] == [] and got["errors"][0]["line"] == 0
    (d / "rules.md").unlink()
    (d / "rules.md").symlink_to(d / "missing.md")  # a dangling link is present but unreadable
    got = policy_store.read_repo_rules(proj)
    assert got["present"] is True and got["errors"][0]["line"] == 0


def test_read_repo_rules_link_inside_project_is_read(tmp_path):
    d = tmp_path / ".claude-orchestrator"
    d.mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "r.md").write_text("## Never\n- `rm -rf data` : data\n")
    (d / "rules.md").symlink_to(tmp_path / "docs" / "r.md")
    assert [r["pattern"] for r in policy_store.read_repo_rules(tmp_path)["never"]] == ["rm -rf data"]


# ---- active_grants -----------------------------------------------------------------------------


def test_active_grants_filtering(client):
    root, other_root, task, other_task = (uuid.uuid4().hex for _ in range(4))
    now = datetime.now(timezone.utc)
    later, earlier = now + timedelta(hours=1), now - timedelta(seconds=1)

    def g(name, **kw):
        base = dict(id=f"{name}-{root[:8]}", root_id=root, task_id=None, scope="run", rule_id="db-write",
                    key_kind="tool", key="mcp__db__q", created_at=now, expires_at=later)
        base.update(kw)
        return ActionGrant(**base)

    rows = [
        g("run"),
        g("run-other-task", task_id=other_task),  # a run grant covers the whole thread
        g("expired", expires_at=earlier),
        g("revoked", revoked_at=now, revoked_by="u"),
        g("other-root", root_id=other_root),
        g("once", scope="once", task_id=task, key_kind="command", key="a" * 64),
        g("once-used", scope="once", task_id=task, used_at=now, use_count=1),
        g("once-other-task", scope="once", task_id=other_task),
        g("once-expired", scope="once", task_id=task, expires_at=earlier),
        g("once-revoked", scope="once", task_id=task, revoked_at=now),
    ]

    async def go(s):
        s.add_all(rows)
        await s.commit()
        a = await policy_store.active_grants(s, root_id=root, task_id=task, now=now)
        b = await policy_store.active_grants(s, root_id=root, task_id=None, now=now)
        c = await policy_store.active_grants(s, root_id=root, task_id=task, now=later + timedelta(seconds=1))
        return [x.id for x in a], [x.id for x in b], [x.id for x in c]

    a, b, c = _run(_with_session(go))
    tag = root[:8]
    assert sorted(a) == sorted([f"run-{tag}", f"run-other-task-{tag}", f"once-{tag}"])
    assert sorted(b) == sorted([f"run-{tag}", f"run-other-task-{tag}"])
    assert c == []


# ---- suggest_trust -----------------------------------------------------------------------------


def _git(d, *args, email="someone@else.example"):
    env = dict(os.environ, GIT_AUTHOR_NAME="x", GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_NAME="x",
               GIT_COMMITTER_EMAIL=email, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
    subprocess.run(["git", "-C", str(d), *args], check=True, capture_output=True, env=env)


def _repo(d: Path, *, remote=True, authors=("someone@else.example",)):
    d.mkdir(parents=True, exist_ok=True)
    _git(d, "init", "-q")
    for i, a in enumerate(authors):
        (d / f"f{i}.txt").write_text(str(i))
        _git(d, "add", f"f{i}.txt")
        _git(d, "commit", "-q", "-m", f"c{i}", email=a)
    if remote:
        _git(d, "remote", "add", "origin", "https://example.invalid/r.git")
    return d


def test_trust_not_a_repo(tmp_path):
    t, why = trust.suggest_trust(tmp_path, ["me@org.example"])
    assert (t, why) == ("own", trust.R_NOT_REPO)


def test_trust_no_remote_is_own(tmp_path):
    t, why = trust.suggest_trust(_repo(tmp_path / "r", remote=False), ["me@org.example"])
    assert t == "own" and "remote" in why


def test_trust_foreign_authors_is_external(tmp_path):
    t, why = trust.suggest_trust(_repo(tmp_path / "r", authors=("a@x.example", "b@y.example")),
                                 ["me@org.example", "you@org.example"])
    assert t == "external" and why


def test_trust_member_commit_is_own(tmp_path):
    d = _repo(tmp_path / "r", authors=("a@x.example", "Me@Org.Example", "b@y.example"))
    assert trust.suggest_trust(d, ["you@org.example", " me@org.example "])[0] == "own"
    # on another branch only (git log --all)
    d2 = _repo(tmp_path / "r2", authors=("a@x.example",))
    _git(d2, "checkout", "-q", "-b", "side")
    (d2 / "s.txt").write_text("s")
    _git(d2, "add", "s.txt")
    _git(d2, "commit", "-q", "-m", "side", email="me@org.example")
    _git(d2, "checkout", "-q", "-")
    assert trust.suggest_trust(d2, ["me@org.example"])[0] == "own"


def test_trust_email_is_matched_whole(tmp_path):
    d = _repo(tmp_path / "r", authors=("notme@org.example",))
    assert trust.suggest_trust(d, ["me@org.example"])[0] == "external"


def test_trust_no_member_emails_is_own_with_reason(tmp_path):
    d = _repo(tmp_path / "r")
    t, why = trust.suggest_trust(d, [])
    assert t == "own" and "not checked" in why
    assert trust.suggest_trust(d, ["", None, "  "])[0] == "own"


def test_trust_unborn_head_is_own(tmp_path):
    d = _repo(tmp_path / "r", authors=())
    assert trust.suggest_trust(d, ["me@org.example"])[0] == "own"


def test_trust_git_failure_after_remote_is_external(tmp_path, monkeypatch):
    d = _repo(tmp_path / "r", authors=("me@org.example",))
    real = trust._git

    def failing(directory, args, deadline):
        if args[0] not in ("rev-parse", "remote"):
            return trust._Result(ok=False, out="", failed=True)
        return real(directory, args, deadline)

    monkeypatch.setattr(trust, "_git", failing)
    t, why = trust.suggest_trust(d, ["me@org.example"])
    assert t == "external" and why


def test_trust_timeout_after_remote_is_external(tmp_path, monkeypatch):
    d = _repo(tmp_path / "r", authors=("me@org.example",))
    real = subprocess.run
    calls = []

    def slow(cmd, *a, **kw):
        calls.append(kw.get("timeout"))
        if "log" in cmd:
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout") or 0)
        return real(cmd, *a, **kw)

    monkeypatch.setattr(trust.subprocess, "run", slow)
    assert trust.suggest_trust(d, ["me@org.example"])[0] == "external"
    assert all(c is not None and 0 < c <= trust.GIT_TIMEOUT for c in calls)


def test_trust_git_timeout_constant():
    assert trust.GIT_TIMEOUT == 5


# ---- fix round 1: repo-controlled git config never runs (C1, Ruling P21), I1, I2 ---------------


def _hostile(tmp_path, name, url, **config):
    """A promisor (partial-clone) repo whose HEAD commit object is gone: any
    git command that reads it lazily fetches from `url` with the repo's own
    config (forge review C1 reproduction)."""
    d = _repo(tmp_path / name, remote=False, authors=("a@x.example",))
    _git(d, "remote", "add", "origin", url)
    _git(d, "config", "extensions.partialClone", "origin")
    _git(d, "config", "remote.origin.promisor", "true")
    for k, v in config.items():
        _git(d, "config", k.replace("__", "."), v)
    head = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (d / ".git" / "objects" / head[:2] / head[2:]).unlink()
    return d


@pytest.mark.parametrize("variant", ["ssh", "credential-helper", "ext"])
def test_trust_never_runs_repo_config(tmp_path, variant):
    marker = tmp_path / "PWNED"
    cmd = f"touch {marker}"
    if variant == "ssh":
        d = _hostile(tmp_path, "r", "ssh://example.invalid/x.git", core__sshCommand=f"sh -c '{cmd}' --")
    elif variant == "credential-helper":
        d = _hostile(tmp_path, "r", "https://example.invalid/x.git", credential__helper=f"!{cmd}; echo")
    else:
        d = _hostile(tmp_path, "r", f"ext::sh -c {cmd.replace(' ', '% ')}", protocol__ext__allow="always",
                     protocol__allow="always")
    t, _ = trust.suggest_trust(d, ["me@org.example"])
    assert not marker.exists(), "repo-controlled git config ran"
    assert t == "external"


def test_trust_ignores_inherited_git_env(tmp_path, monkeypatch):
    other = _repo(tmp_path / "other", authors=("me@org.example",))
    d = _repo(tmp_path / "r", authors=("a@x.example",))
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.sshCommand")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", f"touch {tmp_path / 'PWNED'}")
    assert trust.suggest_trust(d, ["me@org.example"])[0] == "external"  # d's history, not other's


def test_trust_git_missing_is_own(tmp_path, monkeypatch):
    d = _repo(tmp_path / "r")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))  # no git here
    assert trust.suggest_trust(d, ["me@org.example"]) == ("own", trust.R_NO_GIT)


@pytest.mark.parametrize("breakage", ["bad-config", "format-99", "garbage-git-file"])
def test_trust_broken_repo_before_remote_is_external(tmp_path, breakage):
    d = _repo(tmp_path / "r", authors=("me@org.example",))
    cfg = d / ".git" / "config"
    if breakage == "bad-config":
        cfg.write_text(cfg.read_text() + "\n[broken\n")
    elif breakage == "format-99":
        _git(d, "config", "core.repositoryformatversion", "99")
    else:
        import shutil

        shutil.rmtree(d / ".git")
        (d / ".git").write_text("gitdir: /nonexistent/elsewhere\n")
    t, why = trust.suggest_trust(d, ["me@org.example"])
    assert (t, why) == ("external", trust.R_FAILED)


def test_trust_unborn_head_with_history_is_judged_by_history(tmp_path):
    d = _repo(tmp_path / "r", authors=("a@x.example",))
    _git(d, "checkout", "-q", "--orphan", "empty")  # HEAD on an unborn branch, history elsewhere
    t, why = trust.suggest_trust(d, ["me@org.example"])
    assert t == "external" and why == trust.R_FOREIGN
    # a genuinely empty repo (no refs at all) is still own
    e = _repo(tmp_path / "e", authors=())
    assert trust.suggest_trust(e, ["me@org.example"]) == ("own", trust.R_NO_COMMITS)


def test_gitsafe_environment(tmp_path, monkeypatch):
    from app import gitsafe

    monkeypatch.setenv("GIT_DIR", "/elsewhere")
    monkeypatch.setenv("GIT_INDEX_FILE", "/elsewhere/index")
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", "/elsewhere/objects")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.sshcommand'='x'")
    env = gitsafe.git_env(tmp_path / "proj")
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_CONFIG_PARAMETERS"):
        assert k not in env
    assert env["GIT_ALLOW_PROTOCOL"] == "none" and env["GIT_NO_LAZY_FETCH"] == "1"
    assert env["GIT_SSH_COMMAND"] == "false" and env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull and env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_PAGER"] == "cat"
    assert env["GIT_CEILING_DIRECTORIES"] == os.path.dirname(os.path.realpath(tmp_path / "proj"))
    args = gitsafe.SAFE_ARGS
    for kv in ("core.sshCommand=false", "credential.helper=", "core.fsmonitor=false", "core.hooksPath=/dev/null",
               "core.pager=cat"):
        assert kv in args
    r = gitsafe.run_git(tmp_path, "--version", timeout=5)
    assert r.ok and r.out.startswith("git version")
    r = gitsafe.run_git(tmp_path / "missing-dir", "rev-parse", "--git-dir", timeout=5)
    assert not r.ok and r.failed


# ---- fix round 2: monorepo subfolders, run_git allowlist --------------------------------------


def test_trust_monorepo_subfolder_is_judged_by_its_repo(tmp_path):
    foreign = _repo(tmp_path / "foreign", authors=("a@x.example",))
    sub = foreign / "packages" / "app"
    sub.mkdir(parents=True)
    assert trust.suggest_trust(sub, ["me@org.example"]) == ("external", trust.R_FOREIGN)
    mine = _repo(tmp_path / "mine", authors=("me@org.example",))
    sub2 = mine / "packages" / "app"
    sub2.mkdir(parents=True)
    assert trust.suggest_trust(sub2, ["me@org.example"]) == ("own", trust.R_MEMBER)


def test_trust_walk_stops_before_home(tmp_path, monkeypatch):
    home = _repo(tmp_path / "home", authors=("a@x.example",))  # a dotfiles repo in home
    proj = home / "code" / "proj"
    proj.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    assert trust.suggest_trust(proj, ["me@org.example"]) == ("own", trust.R_NOT_REPO)


@pytest.mark.parametrize("args", [
    ("status",), ("fetch",), ("remote", "update"), ("log", "-p"), ("log", "--show-signature"),
    ("log", "--format=%G?"), ("log", "--pretty=%G?"), ("log", "--stat"), ("rev-list", "--all", "-G", "x"),
    ("config", "core.sshCommand", "x"), ("config", "--list"), ("for-each-ref", "--contains", "x"),
    ("checkout", "x"), ("clone", "x"), (),
])
def test_run_git_allowlist(tmp_path, args):
    from app import gitsafe

    with pytest.raises(ValueError):
        gitsafe.run_git(tmp_path, *args, timeout=5)


def test_run_git_signature_formats_never_run_gpg(tmp_path):
    from app import gitsafe

    d = _repo(tmp_path / "r", authors=("a@x.example",))
    marker = tmp_path / "PWNED"
    script = tmp_path / "fakegpg.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    script.chmod(0o755)
    _git(d, "config", "gpg.program", str(script))
    _git(d, "config", "log.showSignature", "true")
    with pytest.raises(ValueError):
        gitsafe.run_git(d, "log", "--format=%G?", timeout=5)
    r = gitsafe.run_git(d, "log", "--all", "--max-count=1", "--format=%H", timeout=5)
    assert r.ok and r.out
    assert not marker.exists()
    for kv in ("gpg.program=false", "gpg.ssh.program=false", "gpg.x509.program=false", "diff.external=",
               "core.attributesFile=/dev/null"):
        assert kv in gitsafe.SAFE_ARGS


def test_run_git_allows_what_trust_uses(tmp_path):
    from app import gitsafe

    for args in (("--version",), ("rev-parse", "--git-dir"), ("remote",), ("remote", "-v"),
                 ("for-each-ref", "--count=1", "--format=%(objectname)", "refs/"), ("config", "--get", "x.y"),
                 ("log", "--all", "-i", "--fixed-strings", "--max-count=1", "--format=%H", "--author=<a@b>"),
                 ("rev-list", "--all", "--max-count=1")):
        gitsafe.check_args(args)
