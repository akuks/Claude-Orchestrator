"""Validation for the launch list (phase 5b). Both functions raise ValueError with a plain reason."""

from __future__ import annotations

from ..decisions.validate import path_list
from ..redaction import clean_text, redact

SECTIONS = ("before", "after")
STATUSES = ("open", "started", "done", "dropped")
MAX_TITLE = 200
MAX_PATHS = 20
_BREAKS = ("\n", "\r", " ", " ", "\x0b", "\x0c", "\x85")


def clean_title(text) -> str:
    """One line, 1-200 characters after cleaning and redaction. A line break is refused, not collapsed."""
    if not isinstance(text, str):
        raise ValueError("title must be text")
    if any(b in text for b in _BREAKS):
        raise ValueError("title must be one line")
    out = redact(clean_text(text), []).strip()
    if not out:
        raise ValueError("title is required")
    if len(out) > MAX_TITLE:
        raise ValueError(f"title may be at most {MAX_TITLE} characters")
    return out


def clean_paths(value) -> list[str]:
    """Critical-path globs: at most 20, the decision `touches` rules (relative, no `..`, no leading / or ~,
    no backslash, 300 characters at most), normalised and de-duplicated."""
    return path_list(value, "a critical path entry", MAX_PATHS)


def title_key(title: str) -> str:
    return title.strip().casefold()
