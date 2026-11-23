"""Does a shell command look like a test run? Used to pick the Bash calls of an
agent whose output is worth parsing for a test-result chip."""
from __future__ import annotations

import re
import shlex

_ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_RUNNERS = {"vitest", "jest", "mocha", "py.test", "pytest"}
_PKG = {"npm", "yarn", "pnpm"}


def looks_like_test(command: str) -> bool:
    if not isinstance(command, str) or not command.strip():
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    while words and _ASSIGN.match(words[0]):
        words = words[1:]
    if not words:
        return False
    first, rest = words[0].rsplit("/", 1)[-1], words[1:]
    if first in _RUNNERS:
        return True
    if first in ("python", "python3") or re.fullmatch(r"python3(\.\d+)?", first):
        return rest[:2] == ["-m", "pytest"]
    if first in _PKG:
        if not rest:
            return False
        if rest[0] == "test":
            return True
        return first != "yarn" and rest[0] in ("run", "run-script") and len(rest) > 1 and rest[1].startswith("test")
    if first == "npx":
        rest = [w for w in rest if w not in ("-y", "--yes", "--no-install")]
        if rest[:1] and rest[0] in ("vitest", "jest"):
            return True
        return rest[:2] == ["playwright", "test"]
    if first == "node":
        return "--test" in rest[:1]
    if first == "go":
        return rest[:1] == ["test"]
    return False
