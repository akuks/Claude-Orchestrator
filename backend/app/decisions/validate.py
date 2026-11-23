"""Validation and numbering for project decisions (phase 4a)."""

from __future__ import annotations

from sqlalchemy import update

from ..models import Project
from ..redaction import clean_text, redact

KINDS = ("decision", "rule", "lesson", "reference")
STATUSES = ("active", "retired", "superseded")
MAX_TITLE, MAX_WHY, MAX_SEE, MAX_SOURCE = 200, 2000, 300, 300
MAX_TOUCHES, MAX_TOUCH = 20, 300
_LIMITS = {"title": MAX_TITLE, "why": MAX_WHY, "see": MAX_SEE, "source": MAX_SOURCE}
_TEXT = ("title", "why", "see", "source")
_ONE_LINE = ("title", "see", "source")  # one line: every whitespace run (newlines included) becomes one space


def one_line(text: str) -> str:
    """Collapse all whitespace (CR, LF, U+2028/2029, ...) to single spaces, so the text is one line."""
    return " ".join(text.split())


def display_id(number: int) -> str:
    return f"D-{number:03d}"


def _clean(value, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    return redact(clean_text(value), []).strip()


def _rel_path(raw: str, what: str) -> str:
    """A relative in-project path or glob, normalised (collapses ./ and //)."""
    p = raw.strip()
    if not p:
        raise ValueError(f"{what} must not be empty")
    if p[0] in "/~" or "\\" in p:
        raise ValueError(f"{what} must be a relative path without a leading / or ~ or a backslash: {p[:60]}")
    parts = p.split("/")
    if ".." in parts:
        raise ValueError(f"{what} must not contain a '..' segment: {p[:60]}")
    out = "/".join(x for x in parts if x not in ("", "."))
    if not out:
        raise ValueError(f"{what} must not be empty")
    return out


def path_list(value, what: str, max_items: int, *, max_wildcards: int | None = None) -> list[str]:
    """A list of relative in-project paths or globs (``what`` is "a <name> entry"), normalised and
    deduplicated. Raises ValueError with a plain reason."""
    name = what.removeprefix("a ").removesuffix(" entry")
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be a list of paths")
    if len(value) > max_items:
        raise ValueError(f"{name} may have at most {max_items} entries")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{name} entries must be text")
        item = redact(clean_text(item), []).strip()
        if len(item) > MAX_TOUCH:
            raise ValueError(f"{what} may be at most {MAX_TOUCH} characters")
        norm = _rel_path(item, what)
        if max_wildcards is not None and norm.count("*") + norm.count("?") > max_wildcards:
            raise ValueError(f"{what} may have at most {max_wildcards} wildcards")
        if norm not in out:
            out.append(norm)
    return out


def _touches(value) -> list[str]:
    return path_list(value, "a touches entry", MAX_TOUCHES)


def clean_fields(data: dict, *, partial: bool) -> dict:
    """Clean and validate the user-settable fields (title, why, see, source, kind, touches).
    partial=False: all are checked and filled with defaults (title required).
    partial=True: only the keys present are checked; the kind/see rule is checked
    only when both are present (the caller checks it against the stored row).
    Raises ValueError with a plain reason."""
    out: dict = {}
    for f in _TEXT:
        if f in data:
            raw = data[f]
            if isinstance(raw, str):  # line breaks first: CR and U+2028/2029 are hidden characters to clean_text
                raw = one_line(raw) if f in _ONE_LINE else "\n".join(raw.splitlines())
            out[f] = _clean(raw, f)
            if f in _ONE_LINE:
                out[f] = one_line(out[f])
            if len(out[f]) > _LIMITS[f]:
                raise ValueError(f"{f} may be at most {_LIMITS[f]} characters")
    if "title" in out and not out["title"]:
        raise ValueError("title is required")
    if "kind" in data:
        if data["kind"] not in KINDS:
            raise ValueError(f"kind must be one of: {', '.join(KINDS)}")
        out["kind"] = data["kind"]
    if "touches" in data:
        out["touches"] = _touches(data["touches"])
    if out.get("see"):
        out["see"] = _rel_path(out["see"], "see")
    if not partial:
        if "title" not in out:
            raise ValueError("title is required")
        out.setdefault("kind", "decision")
        out.setdefault("why", "")
        out.setdefault("see", "")
        out.setdefault("source", "")
        out.setdefault("touches", [])
    if "kind" in out and "see" in out:
        if out["kind"] == "reference" and not out["see"]:
            raise ValueError("a reference needs see (a path in the project)")
        if out["kind"] != "reference" and out["see"]:
            raise ValueError("see is only for a reference")
    return out


async def next_number(s, project_id: str) -> int:
    """The project's next decision number. Increments the high-water mark in the
    caller's transaction (the row lock serialises writers), so a number is never reused."""
    n = (await s.execute(
        update(Project).where(Project.id == project_id)
        .values(decision_seq=Project.decision_seq + 1).returning(Project.decision_seq))).scalar_one_or_none()
    if n is None:
        raise ValueError("project not found")
    return n
