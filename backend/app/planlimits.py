"""Claude subscription-plan limits (phase 7c).

Headless ``claude --print --output-format stream-json`` sends
``{"type": "rate_limit_event", "rate_limit_info": {"status": "allowed" | "allowed_warning" | "rejected",
"resetsAt"?: number, "utilization"?: number, ...}}``. The latest reading is kept per credential owner (a user
id, or ``"org"`` for the org fallback key) in ``plan_limit_status``: the raw info (capped) plus our reading of
it. While an owner's latest status is ``rejected`` with a reset time still ahead, no new task using that
credential starts; it waits until the reset time + 1 minute.

Only what is documented is read: which window the event is about, the unit of ``resetsAt`` and the scale of
``utilization`` are not, so ``resetsAt`` is read as milliseconds above 10^12 and seconds otherwise (anything
outside now - 1 day ... now + 8 days is dropped) and ``utilization`` is stored raw.

No secret is ever stored: the owner is a user id or "org", the credential is identified only by its keyed
fingerprint (``agent_env.credential_ref``, the same value tasks already store)."""

import json
import logging
import math
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import credentials, crypto
from .database import SessionLocal
from .models import OrgSetting, PlanLimitStatus, UserCredential

log = logging.getLogger(__name__)

ORG = "org"
REJECTED = "rejected"
WARNING = "allowed_warning"
ALLOWED = "allowed"
RAW_MAX = 2048  # bytes of the stored raw rate_limit_info
RESUME_AFTER = timedelta(minutes=1)  # a held task starts at the reset time + 1 minute
WARNING_FRESH = timedelta(hours=5)  # a warning older than this is not shown as a banner
UNCONFIRMED_MAX = timedelta(hours=6)  # a hold further ahead needs confirming (see hold_until)
REQUEUE_MAX = 3  # plan-limit hits in a row before a task stops
CHECK_EVERY = 300.0  # seconds: a waiting task's hold is checked again this often
_PAST = timedelta(days=1)
_AHEAD = timedelta(days=8)

# Event type and the texts people read (spec 7c §2/§3).
EVENT = "plan_limit"
PAUSED_TEXT = "Paused — your Claude plan limit was reached. Starts again at {hhmm}."
WARNING_TEXT = "Your Claude plan is close to its limit. It resets at {hhmm}."
WARNING_TEXT_NO_TIME = "Your Claude plan is close to its limit."
BANNER_LIMIT = "Your Claude plan limit was reached. Work starts again at {hhmm}."
BANNER_WARNING = WARNING_TEXT
STEP_HELD = "waiting for the Claude plan limit to reset"
STOPPED_TEXT = "Stopped — the Claude plan limit was reached three times in a row."

# A run's own words for a plan limit ("You've hit your session limit", "...weekly limit", "...Opus limit",
# "...Sonnet limit"), any apostrophe.
_LIMIT_RE = re.compile(r"hit your (session|weekly|opus|sonnet) limit", re.IGNORECASE)
# stderr: the rate-limit words of the worker's transient markers.
_STDERR_MARKERS = ("rate limit", "rate_limit", "429")

_logged: set[str] = set()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _log_once(key: str, msg: str, *args) -> None:
    if key in _logged:
        return
    _logged.add(key)
    log.warning(msg, *args)


def hhmm(dt: datetime | None) -> str:
    """The time people read (UTC, HH:MM); the exact instant travels as ISO next to it."""
    return _aware(dt).astimezone(timezone.utc).strftime("%H:%M") if dt is not None else "--:--"


def _number(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def normalise_resets_at(value, now: datetime | None = None) -> datetime | None:
    """``resetsAt`` as a UTC datetime: a number above 10^12 is milliseconds, otherwise seconds; missing,
    not a number, or outside now - 1 day ... now + 8 days -> None (logged once)."""
    f = _number(value)
    if f is None:
        if value is not None:
            _log_once("resets-type", "plan limits: resetsAt is not a number (%s); ignored", type(value).__name__)
        return None
    secs = f / 1000.0 if f > 1e12 else f
    now = now or _now()
    try:
        dt = datetime.fromtimestamp(secs, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        dt = None
    if dt is None or not (now - _PAST <= dt <= now + _AHEAD):
        _log_once("resets-range", "plan limits: resetsAt %r is outside the expected range; ignored", value)
        return None
    return dt


def cap_raw(info) -> dict:
    """The raw rate_limit_info as stored: the dict itself when its JSON fits RAW_MAX bytes, else its JSON
    text cut to fit (``{"truncated": true, "text": ...}``)."""
    if not isinstance(info, dict):
        info = {"value": info}
    try:
        text = json.dumps(info, default=str)
    except (TypeError, ValueError):
        text = str(info)
    if len(text.encode()) <= RAW_MAX:
        return info
    cut = text[:RAW_MAX]
    while True:  # the stored JSON (with its escaping) fits RAW_MAX bytes
        out = {"truncated": True, "text": cut}
        size = len(json.dumps(out).encode())
        if size <= RAW_MAX:
            return out
        cut = cut[: max(0, len(cut) - max(1, (size - RAW_MAX + 5) // 2))]


def reading(info, now: datetime | None = None) -> dict:
    """Our reading of one rate_limit_info: status (text, <= 32), resets_at (datetime | None), utilization
    (raw float | None), raw (capped)."""
    info = info if isinstance(info, dict) else {}
    status = info.get("status")
    status = str(status)[:32] if status is not None else "unknown"
    return {
        "status": status,
        "resets_at": normalise_resets_at(info.get("resetsAt"), now),
        "utilization": _number(info.get("utilization")),
        "raw": cap_raw(info),
    }


def limit_text_in(text) -> bool:
    """A plan-limit sentence in a result / stderr text."""
    if not isinstance(text, str) or not text:
        return False
    return bool(_LIMIT_RE.search(text.replace("’", "'")))


def stderr_says_limit(stderr: str) -> bool:
    low = (stderr or "").lower()
    return any(m in low for m in _STDERR_MARKERS) or limit_text_in(stderr)


# ---- whose plan ---------------------------------------------------------------------------------------------


async def credential_owner(s, user_id: str | None) -> dict | None:
    """Whose plan a task of ``user_id`` runs on, as build_agent_env picks the credential: the user's own
    Claude credential -> owner = the user id; none of their own but the org fallback key -> owner "org"
    (always an API key). ``ref`` is the credential's keyed fingerprint (agent_env.credential_ref). None
    when there is no user or no credential (the task fails for that reason anyway)."""
    if not user_id:
        return None
    own = await credentials.get_claude_secret(s, user_id)
    if own is not None:
        name, value = own
        return {"owner": user_id, "api_key": name == credentials.CLAUDE_ENV["api_key"],
                "ref": crypto.fingerprint(name, value)}
    fb = await credentials.get_fallback_key(s)
    if fb:
        name = credentials.CLAUDE_ENV["api_key"]
        return {"owner": ORG, "api_key": True, "ref": crypto.fingerprint(name, fb)}
    return None


def owner_of_env(env, user_id: str | None, ref: str | None) -> dict | None:
    """The same answer from a built agent env (the run's own credential)."""
    if not user_id:
        return None
    labels = list(getattr(env, "credentials", None) or [])
    var = getattr(env, "claude_var", None)
    return {"owner": ORG if "claude (org fallback)" in labels else user_id,
            "api_key": var == credentials.CLAUDE_ENV["api_key"], "ref": ref}


async def has_fallback_owner(s, user_id: str) -> bool:
    """The user's tasks run on the org key (no Claude credential of their own)."""
    row = await s.get(UserCredential, user_id)
    if row is not None and row.claude_enc and row.claude_kind in credentials.CLAUDE_ENV:
        return False
    return (await s.get(OrgSetting, credentials.FALLBACK_KEY)) is not None


# ---- the stored status --------------------------------------------------------------------------------------


def row_out(row: PlanLimitStatus | None, now: datetime | None = None) -> dict | None:
    if row is None:
        return None
    now = now or _now()
    resets = _aware(row.resets_at)
    return {
        "credential_owner": row.credential_owner,
        "status": row.status,
        "resets_at": resets.isoformat() if resets else None,
        "utilization": row.utilization,
        "raw": row.raw,
        "api_key": bool(row.api_key),
        "seen_at": _aware(row.seen_at).isoformat() if row.seen_at else None,
        "task_id": row.task_id,
        "confirmed": bool(row.confirmed),
        "hold_until": hold_until(row).isoformat() if hold_until(row) else None,
        "held": is_holding(row, now),
        "banner": banner(row, now),
    }


def hold_until(row: PlanLimitStatus | None) -> datetime | None:
    """Until when a rejected row holds work: its reset time, but at most 6 hours after it was seen unless
    the hold is confirmed (a limit sentence, or rejected events from two different tasks); it is checked
    again then. None: not a rejected row with a reset time."""
    if row is None or row.status != REJECTED:
        return None
    resets = _aware(row.resets_at)
    if resets is None:
        return None
    seen = _aware(row.seen_at)
    if not row.confirmed and seen is not None and resets > seen + UNCONFIRMED_MAX:
        return seen + UNCONFIRMED_MAX
    return resets


def is_holding(row: PlanLimitStatus | None, now: datetime | None = None) -> bool:
    """Rejected with a hold time still ahead, on a plan (never an API key: plan limits do not apply, and the
    org key is shared by many people; such rows are kept for display only). A stale row holds nothing."""
    if row is None or row.api_key:
        return False
    until = hold_until(row)
    return until is not None and until > (now or _now())


def banner(row: PlanLimitStatus | None, now: datetime | None = None) -> dict | None:
    now = now or _now()
    if row is None:
        return None
    if is_holding(row, now):
        return {"kind": "limit", "text": BANNER_LIMIT.format(hhmm=hhmm(start_after(hold_until(row)))),
                "resets_at": _aware(row.resets_at).isoformat()}
    seen = _aware(row.seen_at)
    if row.status == WARNING and not row.api_key and seen is not None and now - seen <= WARNING_FRESH:
        resets = _aware(row.resets_at)
        if resets is not None and resets <= now:
            return None  # the window it warned about has reset
        return {"kind": "warning",
                "text": WARNING_TEXT.format(hhmm=hhmm(resets)) if resets else WARNING_TEXT_NO_TIME,
                "resets_at": resets.isoformat() if resets else None}
    return None


async def record(owner: dict, read: dict, task_id: str | None, run_ref: str | None = None,
                 confirmed: bool = False) -> PlanLimitStatus | None:
    """Latest wins: one row per owner, updated in place. A ``rejected`` reading is confirmed by
    ``confirmed`` (a limit sentence) or by the owner's row already being rejected by a different task; ``raw`` None keeps the stored raw. Returns the stored row (detached), or
    None. Never raises."""
    if not owner or not owner.get("owner"):
        return None
    for attempt in range(2):
        try:
            async with SessionLocal() as s:
                row = (await s.execute(select(PlanLimitStatus).where(
                    PlanLimitStatus.credential_owner == owner["owner"]))).scalar_one_or_none()
                rejected = read["status"] == REJECTED
                before_rejected = row is not None and row.status == REJECTED
                # a second confirming rejected must come from a different task (a re-run of the same
                # task could repeat a forged line)
                other_run = (before_rejected and task_id is not None and row.task_id is not None
                             and row.task_id != task_id)
                was_confirmed = bool(before_rejected and row.confirmed and run_ref is not None
                                     and row.run_ref == run_ref)
                if row is None:
                    row = PlanLimitStatus(credential_owner=owner["owner"], raw={})
                    s.add(row)
                row.status = read["status"]
                row.resets_at = read["resets_at"]
                row.utilization = read["utilization"]
                if read.get("raw") is not None:
                    row.raw = read["raw"]
                row.api_key = bool(owner.get("api_key"))
                row.credential_ref = owner.get("ref")
                row.seen_at = _now()
                row.task_id = task_id
                row.confirmed = bool(rejected and (confirmed or other_run or was_confirmed))
                if run_ref is not None or not rejected:
                    row.run_ref = run_ref if rejected else None
                await s.commit()
                await s.refresh(row)
                s.expunge(row)
            if rejected:
                log.info("plan limits: %s rejected until %s (confirmed: %s), written by task %s",
                         owner["owner"], read["resets_at"], row.confirmed, task_id)
            return row
        except IntegrityError:
            if attempt:
                log.warning("plan limits: could not store the status for %s", owner["owner"])
        except Exception:  # noqa: BLE001 - recording never breaks a run
            log.warning("plan limits: could not store the status for %s", owner["owner"], exc_info=True)
            return None
    return None


async def get_row(s, owner: str) -> PlanLimitStatus | None:
    return (await s.execute(select(PlanLimitStatus).where(
        PlanLimitStatus.credential_owner == owner))).scalar_one_or_none()


async def hold_for(s, who: dict | None, now: datetime | None = None) -> PlanLimitStatus | None:
    """The owner's row when it holds work of this credential now: rejected, reset ahead, and recorded for the
    same credential (a person who changed their credential since is not held by the old one's limit)."""
    if not who:
        return None
    row = await get_row(s, who["owner"])
    if not is_holding(row, now):
        return None
    if row.credential_ref and who.get("ref") and row.credential_ref != who["ref"]:
        return None
    return row


async def hold_for_user(s, user_id: str | None, now: datetime | None = None) -> PlanLimitStatus | None:
    return await hold_for(s, await credential_owner(s, user_id), now)


def start_after(resets_at: datetime) -> datetime:
    """The time a held task starts again (its hold time + 1 minute)."""
    return _aware(resets_at) + RESUME_AFTER


def paused_text(resets_at: datetime) -> str:
    return PAUSED_TEXT.format(hhmm=hhmm(start_after(resets_at)))
