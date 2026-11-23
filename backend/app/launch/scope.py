"""Scope nudge (phase 5b): does a task prompt look like new scope for the launch list?

Ported from forge v0.7.0 scripts/lib/launch.js `looksLikeNewScope` (BUILD verbs, PHRASES, SCOPE,
QUESTION_ABOUT_EXISTING, NOT_SCOPE, the curly-quote fold, the 400-character and slash-command skips).
Prompts of MAX_PROMPT characters or more are refused before any pattern runs; every pattern is anchored on a
literal phrase and has no nested quantifier, so matching is linear in the (bounded) input."""

from __future__ import annotations

import re
import time

MAX_PROMPT = 400
COOLDOWN_SECONDS = 30 * 60

_BUILD = r"(?:add|build|implement|support|create|make|introduce|integrate)"
_PHRASES = [
    rf"(?:let'?s|let us)(?: also)? {_BUILD}",
    r"can we (?:also )?add",
    r"could we (?:also )?add",
    rf"what if we (?:also )?{_BUILD}",
    rf"{_BUILD} (?:a |an |the |another |one more )?new features?",
    r"add support for",
    r"while we'?re at it",
    r"while we are at it",
    r"how about adding",
    r"one more (?:thing|feature)",
    r"it would be nice to",
    r"we should also add",
    r"i'?d like to add",
    r"and also add",
]
_FLAGS = re.IGNORECASE | re.ASCII
_SCOPE = re.compile(rf"(?<![\w'])(?:{'|'.join(_PHRASES)})(?![\w'])", _FLAGS)
_QUESTION_ABOUT_EXISTING = re.compile(r"^\s*(?:explain|describe|what does|what do|what is|why|how does|how do)\b", _FLAGS)
_NOT_SCOPE = re.compile(r"(?<![\w'])(?:tests?|lint(?:er|ing|s)?|fix(?:es|ed|ing)?|bugs?)(?![\w'])", _FLAGS)
_SLASH = re.compile(r"^\s*/")


def looks_like_new_scope(prompt: str) -> bool:
    p = (prompt or "") if isinstance(prompt, str) else ""
    if len(p) >= MAX_PROMPT:
        return False
    p = p.replace("’", "'")
    if _SLASH.match(p) or _QUESTION_ABOUT_EXISTING.match(p) or _NOT_SCOPE.search(p):
        return False
    return _SCOPE.search(p) is not None


class Cooldown:
    """In-memory (user_id, project_id) -> monotonic time of the last nudge. Lost on restart, which is fine."""

    def __init__(self, ttl: float = COOLDOWN_SECONDS):
        self.ttl = ttl
        self._at: dict[tuple[str, str], float] = {}

    def active(self, user_id: str, project_id: str) -> bool:
        t = self._at.get((user_id, project_id))
        return t is not None and time.monotonic() - t < self.ttl

    def mark(self, user_id: str, project_id: str) -> None:
        now = time.monotonic()
        if len(self._at) > 5000:  # drop expired entries so the map stays bounded
            self._at = {k: v for k, v in self._at.items() if now - v < self.ttl}
        self._at[(user_id, project_id)] = now

    def clear(self) -> None:
        self._at.clear()


COOLDOWN = Cooldown()


def reset() -> None:
    COOLDOWN.clear()
