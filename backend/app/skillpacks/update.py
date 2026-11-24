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
import os
import re
import signal
from pathlib import Path

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


async def _git(args, cwd, allow_protocols=DEFAULT_PROTOCOLS, timeout: float = TIMEOUT) -> str:
    """Run one hardened git command; stdout on success, ValueError otherwise."""
    file_ok = "file" in allow_protocols
    argv = [
        "git", "--no-pager", "--no-optional-locks",
        "-c", "credential.helper=",
        "-c", "core.hooksPath=/dev/null",
        "-c", "core.fsmonitor=false",
        "-c", f"protocol.file.allow={'always' if file_ok else 'never'}",
        "-c", "protocol.ext.allow=never",
        "-c", "core.pager=cat",
        *args,
    ]
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
    if proc.returncode != 0:
        raise ValueError("git failed: " + (_scrub(err.decode(errors="replace").strip()) or "no details"))
    return out.decode(errors="replace")


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


async def _remote_head(url: str, cwd, allow_protocols) -> str:
    out = await _git(["ls-remote", "--", url, "HEAD"], cwd, allow_protocols)
    for line in out.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.strip() == "HEAD" and _HEX.match(sha):
            return sha
    raise ValueError("The remote has no HEAD to compare with.")


async def check(pack, allow_protocols=DEFAULT_PROTOCOLS) -> dict:
    url = check_url(pack.source_url, allow_protocols)
    path = _pack_dir(pack)
    cur = await current_sha(pack, allow_protocols)
    latest = await _remote_head(url, path, allow_protocols)
    return {"current": cur, "latest": latest, "update_available": cur is not None and cur != latest}


async def apply(pack, allow_protocols=DEFAULT_PROTOCOLS) -> dict:
    url = check_url(pack.source_url, allow_protocols)
    path = _pack_dir(pack)
    lock = _locks.setdefault(pack.id, asyncio.Lock())
    async with lock:
        before = await current_sha(pack, allow_protocols)
        if before is None:
            raise ValueError("This skill pack is not a git checkout, so it cannot be updated.")
        if (await _git(["status", "--porcelain"], path, allow_protocols)).strip():
            raise ValueError("This skill pack has local changes. Remove them before updating.")
        await _git(["fetch", "--no-tags", "--", url, "HEAD"], path, allow_protocols)
        target = (await _git(["rev-parse", "FETCH_HEAD"], path, allow_protocols)).strip()
        if not _HEX.match(target):
            raise ValueError("The remote returned something unexpected.")
        if target != before:
            try:
                await _git(["merge", "--ff-only", target], path, allow_protocols)
            except ValueError:
                raise ValueError(
                    "The remote history no longer continues from this copy, so it cannot be updated automatically."
                ) from None
    from ..routers.skills import _lint_pack_dir  # late: the router imports this module

    results = await asyncio.to_thread(_lint_pack_dir, path)
    return {"from": before, "to": target, "lint": [r for r in results if r["problems"]]}
