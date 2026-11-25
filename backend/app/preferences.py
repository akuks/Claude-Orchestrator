"""Personal preferences (phase 8b): each person's short list of standing preferences, written by them alone
(``/me/preferences``), added to the main session's system prompt of every task they start.

* ``validate(body)`` checks a PUT body (ValueError -> the router's 422): at most ``MAX_ITEMS`` items, each text
  1-``TEXT_MAX`` characters after ``clean``; no newlines; no real-looking key or URL password
  (``looks_like_secret``). Each item error starts "Preference {n}: " (1-based).
* ``paragraph(enabled, items, secrets)`` is the exact text added (None when nothing would be added); each item is
  redacted with the run's secrets.
* ``load_paragraph(user_id, secrets)`` reads the person's row in its own short session: ``(text, count)``."""

from __future__ import annotations

import re
import uuid

from .database import SessionLocal
from .models import UserPreferences
from .redaction import TOKEN_SHAPES, URL_USERINFO, clean_text, redact

MAX_ITEMS = 12
TEXT_MAX = 240
ID_MAX = 32
HEADING = "# Preferences of the person who started this task"
INTRO = ("They set these themselves. Follow them unless the task, the project's instructions or a recorded decision "
         "says otherwise.")

SECRET_MSG = "This looks like a password or key — leave secrets out of your preferences."  # after "Preference {n}: "
# any line break: a preference is one line and can never start a new line (heading) in the prompt
_NEWLINES = re.compile("[\n\r\u000b\u000c\u0085  ]")
_SPACES = re.compile(r"\s+")
_ID = re.compile(r"^[A-Za-z0-9_-]{1," + str(ID_MAX) + r"}$")


def clean(text) -> str:
    """One line: ValueError for a line break; hidden/bidi characters stripped, whitespace collapsed, trimmed."""
    if not isinstance(text, str):
        raise ValueError("it must be text")
    if _NEWLINES.search(text):
        raise ValueError("it must be one line (no line breaks)")
    return _SPACES.sub(" ", clean_text(text)).strip()


def looks_like_secret(text: str) -> bool:
    """A real-looking key (``TOKEN_SHAPES``) or a ``user:password@`` URL. Not the labelled-value or Bearer rules
    of ``redact``: ordinary advice ("token: reviewers expect JSDoc") must save. Injection still runs the full
    ``redact``."""
    return bool(TOKEN_SHAPES.search(text) or URL_USERINFO.search(text))


def validate(body) -> tuple[bool, list[dict]]:
    """``(enabled, items)`` from a PUT body ``{enabled, items:[{id?, text, on}]}``; ``enabled`` and ``on`` default
    to true. A missing, malformed or repeated id gets a new one."""
    if not isinstance(body, dict):
        raise ValueError("the body must be an object")
    enabled = body.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be true or false")
    raw = body.get("items")
    if not isinstance(raw, list):
        raise ValueError("items must be a list")
    if len(raw) > MAX_ITEMS:
        raise ValueError(f"at most {MAX_ITEMS} preferences")
    items, seen = [], set()
    for n, it in enumerate(raw, 1):
        if not isinstance(it, dict):
            raise ValueError(f"Preference {n}: must be an object")
        on = it.get("on", True)
        if not isinstance(on, bool):
            raise ValueError(f"Preference {n}: on must be true or false")
        try:
            text = clean(it.get("text"))
        except ValueError as e:
            raise ValueError(f"Preference {n}: {e}") from None
        if not text:
            raise ValueError(f"Preference {n}: it is empty")
        if len(text) > TEXT_MAX:
            raise ValueError(f"Preference {n}: it is longer than {TEXT_MAX} characters")
        if looks_like_secret(text):
            raise ValueError(f"Preference {n}: {SECRET_MSG}")
        pid = it.get("id")
        if not isinstance(pid, str) or not _ID.match(pid) or pid in seen:
            pid = uuid.uuid4().hex[:12]
        seen.add(pid)
        items.append({"id": pid, "text": text, "on": on})
    return enabled, items


def _on_texts(enabled, items) -> list[str]:
    if enabled is not True or not isinstance(items, list):
        return []
    out = []
    for it in items[:MAX_ITEMS]:  # stored rows are re-checked: a bad one is skipped, never breaks the list
        if not isinstance(it, dict) or it.get("on") is not True:
            continue
        try:
            text = clean(it.get("text"))
        except ValueError:
            continue
        if text:
            out.append(text[:TEXT_MAX])
    return out


def count_on(enabled, items) -> int:
    return len(_on_texts(enabled, items))


def paragraph(enabled, items, secrets: list[str] | None = None) -> str | None:
    texts = _on_texts(enabled, items)
    if not texts:
        return None
    lines = [HEADING, INTRO] + [f"- {_SPACES.sub(' ', redact(t, secrets or []))}" for t in texts]
    return "\n".join(lines)


def shape(row: UserPreferences | None) -> dict:
    """The API's ``{enabled, items, updated_at, preview}``. ``preview`` masks token shapes only; a run also masks
    its own secrets (its env and ``.env`` values), which the preview cannot know."""
    if row is None:
        return {"enabled": True, "items": [], "updated_at": None, "preview": None}
    items = [it for it in (row.items or []) if isinstance(it, dict)]
    return {"enabled": bool(row.enabled), "items": items, "updated_at": row.updated_at,
            "preview": paragraph(row.enabled, items)}


async def load_paragraph(user_id: str | None, secrets: list[str] | None = None) -> tuple[str | None, int]:
    """The paragraph for a task started by ``user_id`` and how many items it holds; ``(None, 0)`` without one."""
    if not user_id:
        return None, 0
    async with SessionLocal() as s:
        row = await s.get(UserPreferences, user_id)
        if row is None:
            return None, 0
        enabled, items = row.enabled, row.items
    text = paragraph(enabled, items, secrets)
    return text, (count_on(enabled, items) if text else 0)
