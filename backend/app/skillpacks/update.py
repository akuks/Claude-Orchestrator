"""Check for and apply skill pack updates.

Network git, so deliberately NOT routed through ``gitsafe`` (which is
network-free). The hardening is copied from it: no credential helpers, no
prompts, no hooks, no fsmonitor, no system/global config, no inherited GIT_*
variables, stdin /dev/null, a timeout that kills the whole process group.
Nothing from the pack is ever run: hooks are off, and only fetch, status,
rev-parse, ls-remote and ``merge --ff-only`` are issued.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import stat
from pathlib import Path
from types import SimpleNamespace

DEFAULT_PROTOCOLS = ("https", "ssh")
TIMEOUT = 20
_HEX = re.compile(r"^[0-9a-f]{7,64}$")
_locks: dict[str, asyncio.Lock] = {}


def _scrub(text: str) -> str:
    text = re.sub(r"(://)[^/@\s]+@", r"\1", text)
    return text.replace(str(Path.home()), "~")[:300]


def check_url(url: str | None, allow_protocols=DEFAULT_PROTOCOLS) -> str:
    """The stored URL, if it is an allowed form; ValueError otherwise."""
    if not url or not url.strip():
        raise ValueError("This skill pack has no source URL, so it cannot be updated.")
    if url != url.strip() or url.startswith("-") or any(c in url for c in "\0\n\r"):
        raise ValueError("The source URL is not allowed.")
    low = url.lower()
    if low.startswith("https://") and "https" in allow_protocols:
        return url
    if (low.startswith("ssh://") or re.match(r"^[\w.-]+@[\w.-]+:", url)) and "ssh" in allow_protocols:
        return url
    if "file" in allow_protocols and (low.startswith("file://") or url.startswith("/")):
        return url
    raise ValueError("Only https and ssh source URLs can be updated.")


def _env(allow_protocols) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "SSH_AUTH_SOCK", "LANG")}
    env.update({
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "",
        "SSH_ASKPASS": "",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_ALLOW_PROTOCOL": ":".join(allow_protocols),
        "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o StrictHostKeyChecking=yes",
        "LC_ALL": "C",
    })
    return env


# A pack's own .git/config is not trusted. Before any network call the keys in
# it must be ones a plain clone writes; anything else (filter drivers,
# core.sshCommand, url.*.insteadOf, remote.<n>.vcs, include.path ...) refuses.
_ALLOWED_KEY = re.compile(
    r"^(core\.(repositoryformatversion|filemode|bare|logallrefupdates|ignorecase|precomposeunicode|symlinks)"
    r"|remote\.origin\.(url|fetch)|branch\..+\.(remote|merge))$", re.I)
_DRIVER_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MAX_FILTER_DRIVERS = 50
MERGE_TIMEOUT = 120  # local checkout: never kill it half way for the network budget
log = logging.getLogger(__name__)


def _argv(args, allow_protocols, overrides=()) -> list[str]:
    file_ok = "file" in allow_protocols
    return [
        "git", "--no-pager", "--no-optional-locks",
        "-c", "credential.helper=",
        "-c", "core.hooksPath=/dev/null",
        "-c", "core.fsmonitor=false",
        "-c", "core.attributesFile=/dev/null",
        "-c", "core.alternateRefsCommand=",
        "-c", "http.followRedirects=false",
        "-c", "submodule.recurse=false",
        "-c", f"protocol.file.allow={'always' if file_ok else 'never'}",
        "-c", "protocol.ext.allow=never",
        "-c", "core.pager=cat",
        *overrides,
        *args,
    ]


async def _run(argv, cwd, allow_protocols, timeout) -> tuple[int, str, str]:
    cwd = str(cwd)
    env = _env(allow_protocols)
    env["GIT_CEILING_DIRECTORIES"] = str(Path(cwd).parent)
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd, env=env, start_new_session=True,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()
        raise ValueError("The remote did not answer in time.") from None
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


async def _filter_overrides(cwd, allow_protocols) -> list[str]:
    """``-c`` switches blanking every filter driver the repo defines (as
    gitsafe._filter_overrides does; copied because gitsafe is network-free
    and its allowlist would refuse these commands)."""
    rc, out, err = await _run(
        _argv(["config", "--get-regexp", "-z", r"^filter\."], allow_protocols), cwd, allow_protocols, TIMEOUT)
    if rc == 1 and not out:
        return []
    if rc != 0:
        log.warning("skill pack filter listing failed: %s", _scrub(err))
        raise ValueError("This skill pack's git settings could not be read.")
    names: list[str] = []
    for entry in out.split("\0"):
        key = entry.split("\n", 1)[0]
        if not key.startswith("filter.") or "." not in key[7:]:
            continue
        name = key[7:key.rindex(".")]
        if name not in names:
            names.append(name)
    if len(names) > MAX_FILTER_DRIVERS or any(not _DRIVER_NAME_RE.match(n) for n in names):
        raise ValueError("This skill pack's git settings were changed by hand.")
    over: list[str] = []
    for n in names:
        for k, v in (("clean", ""), ("smudge", ""), ("process", ""), ("required", "false")):
            over += ["-c", f"filter.{n}.{k}={v}"]
    return over


async def _git(args, cwd, allow_protocols=DEFAULT_PROTOCOLS, timeout: float = TIMEOUT, what: str = "git") -> str:
    """Run one hardened git command; stdout on success, ValueError otherwise."""
    over = await _filter_overrides(cwd, allow_protocols) if os.path.exists(os.path.join(str(cwd), ".git")) else []
    rc, out, err = await _run(_argv(args, allow_protocols, over), cwd, allow_protocols, timeout)
    if rc != 0:
        log.warning("skill pack git %s failed: %s", args[0], _scrub(err.strip()))
        raise ValueError(f"Could not {what}.")
    return out


def _clean_tmp_packs(path) -> None:
    """A killed fetch leaves tmp_pack_* files behind; remove them."""
    for f in Path(str(path), ".git", "objects", "pack").glob("tmp_pack_*"):
        try:
            f.unlink()
        except OSError:
            pass


async def check_config(path, allow_protocols=DEFAULT_PROTOCOLS) -> None:
    """Refuse a pack whose local .git/config holds keys a clone would not write."""
    for alt in ("alternates", "http-alternates"):
        if os.path.lexists(os.path.join(str(path), ".git", "objects", "info", alt)):
            raise ValueError("This skill pack's git settings were changed by hand.")
    rc, out, _ = await _run(
        _argv(["config", "--local", "--list", "-z"], allow_protocols), path, allow_protocols, TIMEOUT)
    if rc != 0:
        raise ValueError("This skill pack's git settings could not be read.")
    for entry in out.split("\0"):
        key = entry.split("\n", 1)[0]
        if not key:
            continue
        if not _ALLOWED_KEY.match(key):
            raise ValueError("This skill pack's git settings were changed by hand.")


async def pin(path, url: str, sha: str, allow_protocols=DEFAULT_PROTOCOLS) -> None:
    """Leave a fresh clone at exactly ``sha`` (detached)."""
    sha = (sha or "").lower()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("The pinned commit is not valid.")
    check_url(url, allow_protocols)
    try:
        await _git(["fetch", "--depth", "1", "--no-tags", "--no-recurse-submodules", "--", url, sha], path,
                   allow_protocols, what="fetch the pinned commit")
    except ValueError:
        _clean_tmp_packs(path)
        raise
    await _git(["checkout", "-q", "--detach", sha], path, allow_protocols, MERGE_TIMEOUT,
               what="check out the pinned commit")
    if (await current_sha(SimpleNamespace(local_path=str(path)), allow_protocols)) != sha:
        raise ValueError("The pinned commit could not be checked out.")


def _pack_dir(pack) -> str:
    path = getattr(pack, "local_path", None)
    if not path or not os.path.isdir(path):
        raise ValueError("The skill pack directory was not found.")
    return path


async def current_sha(pack, allow_protocols=DEFAULT_PROTOCOLS) -> str | None:
    path = getattr(pack, "local_path", None)
    if not path or not os.path.isdir(os.path.join(path, ".git")):
        return None
    try:
        sha = (await _git(["rev-parse", "HEAD"], path, allow_protocols)).strip()
    except ValueError:
        return None
    return sha if _HEX.match(sha) else None


def _read_small(path: str, cap: int = 4096) -> str | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        return os.read(fd, cap).decode("utf-8", errors="replace")
    except OSError:
        return None
    finally:
        os.close(fd)


def current_sha_fast(path) -> str | None:
    """The checked-out commit, read from .git/HEAD and its ref file. No git is
    run, nothing is followed through a symlink, and every read is bounded."""
    if not path:
        return None
    git_dir = os.path.join(str(path), ".git")
    head = _read_small(os.path.join(git_dir, "HEAD"))
    if head is None:
        return None
    head = head.strip()
    if head.startswith("ref:"):
        ref = head[4:].strip()
        if not re.match(r"^refs/[A-Za-z0-9._/-]+$", ref) or ".." in ref:
            return None
        val = _read_small(os.path.join(git_dir, *ref.split("/")))
        if val is None:
            packed = _read_small(os.path.join(git_dir, "packed-refs"), 1 << 20) or ""
            for line in packed.splitlines():
                parts = line.split(" ")
                if len(parts) == 2 and parts[1] == ref:
                    val = parts[0]
                    break
        head = (val or "").strip()
    return head if _HEX.match(head) else None


async def _remote_head(url: str, cwd, allow_protocols) -> str:
    out = await _git(["ls-remote", "--", url, "HEAD"], cwd, allow_protocols, what="reach the remote repository")
    for line in out.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.strip() == "HEAD" and _HEX.match(sha):
            return sha
    raise ValueError("The remote has no HEAD to compare with.")


async def check(pack, allow_protocols=DEFAULT_PROTOCOLS) -> dict:
    url = check_url(pack.source_url, allow_protocols)
    path = _pack_dir(pack)
    await check_config(path, allow_protocols)
    cur = await current_sha(pack, allow_protocols)
    latest = await _remote_head(url, path, allow_protocols)
    return {"current": cur, "latest": latest, "update_available": cur is not None and cur != latest}


async def apply(pack, allow_protocols=DEFAULT_PROTOCOLS) -> dict:
    url = check_url(pack.source_url, allow_protocols)
    path = _pack_dir(pack)
    await check_config(path, allow_protocols)
    lock = _locks.setdefault(pack.id, asyncio.Lock())
    async with lock:
        before = await current_sha(pack, allow_protocols)
        if before is None:
            raise ValueError("This skill pack is not a git checkout, so it cannot be updated.")
        if (await _git(["status", "--porcelain"], path, allow_protocols, what="read the pack's state")).strip():
            raise ValueError("This skill pack has local changes. Remove them before updating.")
        # A shallow pack (installs are --depth 1, a pin adds a second boundary)
        # needs its real history to know the remote continues from it.
        shallow = (await _git(["rev-parse", "--is-shallow-repository"], path, allow_protocols)).strip() == "true"
        try:
            await _git(["fetch", "--no-tags", "--no-recurse-submodules", *(["--unshallow"] if shallow else []),
                        "--", url, "HEAD"], path, allow_protocols, what="fetch from the remote repository")
        except ValueError:
            _clean_tmp_packs(path)
            raise
        target = (await _git(["rev-parse", "FETCH_HEAD"], path, allow_protocols)).strip()
        if not _HEX.match(target):
            raise ValueError("The remote returned something unexpected.")
        if target != before:
            try:
                await _git(["merge", "--ff-only", target], path, allow_protocols,
                           MERGE_TIMEOUT)
            except ValueError:
                raise ValueError(
                    "The remote history no longer continues from this copy, so it cannot be updated automatically."
                ) from None
            if await current_sha(pack, allow_protocols) != target:
                raise ValueError("This copy is not behind the remote, so there is nothing to update.")
        from ..routers.skills import _lint_pack_dir  # late: the router imports this module

        results = await asyncio.to_thread(_lint_pack_dir, path)
    return {"from": before, "to": target, "lint": [r for r in results if r["problems"]]}
