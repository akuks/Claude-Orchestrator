"""Code-state hash for flaky-test detection (Phase 5a, F118).

A sha256 over HEAD, the tracked diff against HEAD and the name, size and mtime
of each untracked-not-ignored file. The diff is hashed only, never kept.
"""

from __future__ import annotations

import asyncio
import hashlib
import os

from app import gitsafe

TIMEOUT_S = 30
MAX_DIFF_BYTES = 50 * 1024 * 1024


def _compute(directory: str) -> str | None:
    if not gitsafe.git_available():
        return None
    head = gitsafe.run_git(directory, "rev-parse", "HEAD", timeout=TIMEOUT_S)
    if not head.ok or not head.out:
        return None
    diff = gitsafe.run_git(directory, "diff", "--no-ext-diff", "--no-textconv", "--binary", "HEAD",
                           timeout=TIMEOUT_S)
    if not diff.ok or len(diff.out) > MAX_DIFF_BYTES:
        return None
    others = gitsafe.run_git(directory, "ls-files", "--others", "--exclude-standard", "-z", timeout=TIMEOUT_S)
    if not others.ok:
        return None
    h = hashlib.sha256()
    h.update(head.out.encode())
    h.update(b"\0diff\0")
    h.update(diff.out.encode("utf-8", "surrogateescape"))
    h.update(b"\0untracked\0")
    for name in sorted(n for n in others.out.split("\0") if n):
        try:
            st = os.lstat(os.path.join(directory, name))
        except OSError:
            continue
        h.update(f"{name}\0{st.st_size}\0{st.st_mtime_ns}\0".encode("utf-8", "surrogateescape"))
    return h.hexdigest()


async def code_state(directory: str) -> str | None:
    """64-hex code-state hash, or None (not a repo, git error, diff over 50 MB)."""
    try:
        return await asyncio.to_thread(_compute, directory)
    except (ValueError, OSError):
        return None
