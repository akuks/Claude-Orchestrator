"""Editor keys (phase 8c): a person's key for their own Claude Code or Cursor to read decisions over ``POST /mcp``.

* The key text is ``coe_`` + ``secrets.token_urlsafe(32)``; it is returned once and only its sha256 is stored
  (``prefix``, its first 8 characters, is kept to tell keys apart). Never the ApiToken "personal" purpose, which
  carries full user rights.
* ``resolve`` checks a key fresh on every call: not revoked, not expired, owner active. ``project_ids`` is the
  owner's **live** grants (``access.visible_project_ids``) intersected with the key's ``projects`` list when set,
  so losing access to a project removes it from the key at once.
* In-memory limits, like the login throttle (single process): ``CALLS_PER_MINUTE`` per key and
  ``BAD_PER_MINUTE`` wrong keys per client address (checked only after a failed lookup, so a valid key never
  meets it); each map holds at most ``TRACKED_MAX`` entries. ``touch`` writes ``last_used_at``/``use_count`` at most once a
  minute per key (the calls in between are counted in memory and added when it writes).

Functions take an AsyncSession and never commit, except ``touch``."""

from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update

from ..access import ALL, visible_project_ids
from ..auth import Principal, _sha
from ..decisions.validate import one_line
from ..guard import policy_store
from ..models import EditorKey, Project, User
from ..redaction import clean_text

KEY_PREFIX = "coe_"
SHOWN_PREFIX = 8
NAME_MAX = 60
DAYS = (30, 90, 365)
DEFAULT_DAYS = 90
MAX_KEYS = 5
PROJECTS_MAX = 200
CALLS_PER_MINUTE = 60
BAD_PER_MINUTE = 20
WINDOW_SECONDS = 60.0
TOUCH_SECONDS = 60
TRACKED_MAX = 10_000  # entries per limit map (addresses, keys)

SWITCH = "editor_connections"
OFF = "Editor connections are turned off by an admin."
NOT_VISIBLE = "You can only pick projects you can see."
TOO_MANY = f"You can have at most {MAX_KEYS} editor keys. Turn one off first."
TOO_MANY_CALLS = "Too many calls with this key. Try again in a minute."
TOO_MANY_BAD = "Too many wrong keys. Try again in a minute."


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def new_key_text() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


# --------------------------------------------------------------------------- the switch


async def switch_on(s) -> bool:
    return (await policy_store.org_settings(s))["ground_rules"].get(SWITCH) is True


# --------------------------------------------------------------------------- validation


def validate_new(data) -> tuple[str, int, list[str] | None]:
    """A POST body ``{name, days?, projects?}`` -> (name, days, projects). ValueError names the rule, never the
    text."""
    if not isinstance(data, dict):
        raise ValueError("the body must be an object")
    unknown = sorted(str(k)[:40] for k in data if k not in ("name", "days", "projects"))
    if unknown:
        raise ValueError(f"unknown field: {', '.join(unknown)}")
    name = data.get("name")
    name = one_line(clean_text(name)).strip() if isinstance(name, str) else ""
    if not 1 <= len(name) <= NAME_MAX:
        raise ValueError(f"name must be 1 to {NAME_MAX} characters")
    days = data.get("days", DEFAULT_DAYS)
    if type(days) is not int or days not in DAYS:
        raise ValueError("days must be 30, 90 or 365")
    projects = data.get("projects")
    if projects is not None:
        if (not isinstance(projects, list) or not projects or len(projects) > PROJECTS_MAX
                or not all(isinstance(p, str) and 0 < len(p) <= 64 for p in projects)):
            raise ValueError("projects must be a list of project ids, or left out for every project you can see")
        projects = list(dict.fromkeys(projects))
    return name, days, projects


async def live_count(s, user_id: str) -> int:
    return (await s.execute(select(func.count()).select_from(EditorKey).where(
        EditorKey.user_id == user_id, EditorKey.revoked_at.is_(None), EditorKey.expires_at > _now()))).scalar_one()


async def all_visible(s, user: User) -> set[str]:
    """Every project id the person can see right now (admins: every project)."""
    visible = await visible_project_ids(s, Principal(user))
    if visible is ALL:
        return set((await s.execute(select(Project.id))).scalars())
    return set(visible)


async def create(s, user: User, name: str, days: int, projects: list[str] | None) -> tuple[EditorKey, str]:
    """Add a key (no commit). The caller checked the switch and the count; ``projects`` must all be visible."""
    if projects is not None and not set(projects) <= await all_visible(s, user):
        raise ValueError(NOT_VISIBLE)
    raw = new_key_text()
    now = _now()
    row = EditorKey(user_id=user.id, name=name, key_hash=_sha(raw), prefix=raw[:SHOWN_PREFIX], projects=projects,
                    created_at=now, expires_at=now + timedelta(days=days), use_count=0)
    s.add(row)
    await s.flush()
    return row, raw


# --------------------------------------------------------------------------- resolving


async def resolve(s, raw: str) -> tuple[User, EditorKey] | None:
    if not raw.startswith(KEY_PREFIX) or len(raw) > 200:
        return None
    key = (await s.execute(select(EditorKey).where(EditorKey.key_hash == _sha(raw)))).scalar_one_or_none()
    if key is None or key.revoked_at is not None or _aware(key.expires_at) <= _now():
        return None
    user = await s.get(User, key.user_id)
    if user is None or not user.active:
        return None
    return user, key


async def project_ids(s, user: User, key: EditorKey) -> set[str]:
    """What this key may read now: the owner's live grants, narrowed to the key's list when it has one."""
    visible = await all_visible(s, user)
    if key.projects is not None:
        visible &= {p for p in key.projects if isinstance(p, str)}
    return visible


# --------------------------------------------------------------------------- limits and bookkeeping

_calls: dict[str, deque] = defaultdict(deque)
_bad: dict[str, deque] = defaultdict(deque)
_pending: dict[str, int] = defaultdict(int)


def reset_limits() -> None:
    _calls.clear()
    _bad.clear()
    _pending.clear()


def _recent(store: dict, k: str) -> deque:
    q = store[k]
    cutoff = time.monotonic() - WINDOW_SECONDS
    while q and q[0] < cutoff:
        q.popleft()
    if not q:
        store.pop(k, None)
    return q


def ip_blocked(ip: str) -> bool:
    return len(_recent(_bad, ip)) >= BAD_PER_MINUTE


def _bound(store: dict) -> None:
    """Keep a limit map at most ``TRACKED_MAX`` entries: drop the ones with nothing in the window, then (an
    attacker rotating addresses) the oldest-added."""
    if len(store) < TRACKED_MAX:
        return
    for k in list(store):
        _recent(store, k)
    while len(store) >= TRACKED_MAX:
        store.pop(next(iter(store)))


def note_bad(ip: str) -> None:
    if ip not in _bad:
        _bound(_bad)
    _recent(_bad, ip)
    _bad[ip].append(time.monotonic())


def allow_call(key_id: str) -> bool:
    if key_id not in _calls:
        _bound(_calls)
    if len(_recent(_calls, key_id)) >= CALLS_PER_MINUTE:
        return False
    _calls[key_id].append(time.monotonic())
    return True


async def revoke_user_keys(s, user_id: str) -> None:
    """Turn off every live key of a person (no commit): when an admin disables them or resets their password."""
    await s.execute(update(EditorKey).where(EditorKey.user_id == user_id, EditorKey.revoked_at.is_(None))
                    .values(revoked_at=_now()))


async def touch(s, key: EditorKey) -> None:
    """Count the call; write ``last_used_at``/``use_count`` at most once a minute per key (commits then)."""
    _pending[key.id] += 1
    now = _now()
    if key.last_used_at is not None and (now - _aware(key.last_used_at)).total_seconds() < TOUCH_SECONDS:
        return
    n = _pending.pop(key.id, 0)
    await s.execute(update(EditorKey).where(EditorKey.id == key.id).values(
        use_count=EditorKey.use_count + n, last_used_at=now))
    await s.commit()


# --------------------------------------------------------------------------- shapes


def iso(dt: datetime | None) -> str | None:
    return _aware(dt).isoformat() if dt is not None else None


def shape(key: EditorKey, names: dict[str, str]) -> dict:
    """A key as the person sees it (never the key text or its hash). ``project_names`` only for projects they
    can see now."""
    projects = list(key.projects) if key.projects is not None else None
    return {
        "id": key.id, "name": key.name, "prefix": key.prefix, "created_at": iso(key.created_at),
        "expires_at": iso(key.expires_at), "last_used_at": iso(key.last_used_at), "use_count": key.use_count or 0,
        "revoked_at": iso(key.revoked_at), "projects": projects,
        "project_names": [names[p] for p in projects if p in names] if projects is not None else None,
        "expired": _aware(key.expires_at) <= _now(),
    }
